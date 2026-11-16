"""Per-video speaker voice map - the producer layer for index-tts refer_mode 4.

Long-term design
----------------
This module is deliberately the ONLY place that decides "who speaks when".
It reads the per-line vocal clips that pipeline step 9 always produces
(``output/audio/refers/{number}.wav``) plus the dubbing task table, clusters
the clips by timbre, and writes a stable, algorithm-independent contract:

    output/audio/voice_map.json   ->  number / speaker / representative wav

``core/tts_backend/index_tts.py`` only READS that contract. Replacing this
producer later (pyannote embedding, whisperX diarization, a hand-written
table) needs no change on the dubbing side - only the ``producer`` field
changes. That is the whole point of the experiment branch.

v1 (mfcc-ward-v1) was fully offline (numpy/scipy timbre features + ward),
but it clustered prosody / recording conditions rather than identity: on the
2026-11-13 interview it split one continuous narration into two speakers.

v2 (campplus-cosine-v2) uses the CAMPPlus speaker-verification model that
index-tts already ships (checkpoints/hf_cache/campplus_cn_common.bin) to
produce a 192-d L2-normalised speaker embedding per clip. No new dependency,
no model download, CPU only. Clustering adds three guard rails, because a
wrong split is far more audible (voice flips mid-sentence) than a missed one:
  1. turn-based majority smoothing of isolated label flips;
  2. a minimum speaker share and a maximum inter-cluster cosine similarity;
  3. a silhouette floor. All three values are printed for calibration.
Set index_tts.voice_map_speakers to an integer to force the speaker count
when the automatic estimate is known to be wrong (e.g. a 2-person interview).

Implementation note: librosa is avoided. Its first real computation triggers
a numba JIT compile that needs a writable cache directory; in restricted
environments (CI, read-only HOME, some sandboxes) that call hangs for minutes.
The v1 feature path (kept below for A/B comparison) is pure numpy/scipy.

CLI (run from D:/code/vl in the (vl) env)
----------------------------------------
    python -m core.tts_backend.voice_map                  # cheap self check
    python -m core.tts_backend.voice_map --build         # build / reuse map
    python -m core.tts_backend.voice_map --build --force # rebuild
    python -m core.tts_backend.voice_map --samples=3     # A/B listening
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from datetime import datetime

import numpy as np

from core.utils.log_hooks import rprint
from core.utils.models import (_AUDIO_DIR, _AUDIO_REFERS_DIR, _AUDIO_SEGS_DIR,
                              _AUDIO_TMP_DIR, _8_1_AUDIO_TASK)

VOICE_MAP_FILE = os.path.join(_AUDIO_DIR, "voice_map.json")
VOICE_REFS_DIR = os.path.join(_AUDIO_DIR, "voice_refs")
VOICE_EXP_DIR = os.path.join(_AUDIO_DIR, "voice_exp")
# Pure derivative of refers/*.wav - safe to delete, it rebuilds on demand.
VOICE_EMB_FILE = os.path.join(_AUDIO_DIR, "voice_embeddings.npz")

VERSION = 1
# Producer 版本必须随算法/参数变更递增：幂等校验会据此让旧契约自动失效，
# 避免「换了算法却静默复用上一次的说话人划分」。
PRODUCER_ID = "campplus-cosine-v2"
CAMPPPLUS_EMBED_DIM = 192
MAX_SPEAKERS = 6
# v2 判据（首次跑完请按日志里的实测值标定；宁可少分，不可制造音色跳变）
MIN_SILHOUETTE = 0.25        # L2 归一化 embedding 上的 euclidean silhouette
MIN_SPEAKER_SHARE = 0.08     # 任一簇至少占 8% 句子
MAX_CENTER_COS = 0.75        # 两簇中心余弦相似度上限，超过视为同一人
SMOOTH_WINDOW = 5            # 话轮平滑窗口（奇数，中心多数投票）
TARGET_REF_SECONDS = 6.0
# 种子半监督：top1-top2 余弦间距映射到置信度，过小则打 low_margin 待人工复核
SEED_MARGIN_FULL = 0.20
SEED_MARGIN_LOW = 0.03
# 混合句：一条配音任务里塞了多条原始字幕（常是两人交替）。整句声纹是混合的，
# 不参与种子传播；改由 chunk_speakers 逐子行判人（PRD FR-34）。
MIXED_FLAG = "mixed"
CHUNK_MARGIN_LOW = 0.03
TRANS_SRT_FILE = "output/trans.srt"


def _read_task_df():
    import pandas as pd

    if not os.path.isfile(_8_1_AUDIO_TASK):
        raise FileNotFoundError(
            f"配音任务表不存在：{_8_1_AUDIO_TASK}，请先跑流水线第 8 步。"
        )
    return pd.read_excel(_8_1_AUDIO_TASK)


def _fingerprint(df) -> str:
    """Bind the map to THIS cut of THIS video. Re-cut the task table -> stale."""
    payload = {
        "rows": int(len(df)),
        "first_start": str(df["start_time"].iloc[0]),
        "last_end": str(df["end_time"].iloc[-1]),
        "first_text": str(df["text"].iloc[0])[:80],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _mel_filterbank(sr: int, n_fft: int, n_mels: int = 40):
    """Triangular mel filterbank, shape (n_mels, n_fft // 2 + 1)."""
    fmax = sr / 2.0
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    mel_pts = np.linspace(0.0, 2595.0 * np.log10(1.0 + fmax / 700.0),
                          n_mels + 2)
    hz_pts = 700.0 * (10.0 ** (mel_pts / 2595.0) - 1.0)
    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype="float32")
    for i in range(n_mels):
        lo, mid, hi = hz_pts[i], hz_pts[i + 1], hz_pts[i + 2]
        fb[i] = np.maximum(
            0.0,
            np.minimum((freqs - lo) / max(mid - lo, 1e-8),
                       (hi - freqs) / max(hi - mid, 1e-8)),
        )
    return fb


def _f0_stats(y, sr: int):
    """Frame-level autocorrelation F0 (median / IQR). Pure numpy, no JIT."""
    frame, hop = 1024, 512
    if y.size < frame:
        return 0.0, 0.0
    n_frames = (y.size - frame) // hop + 1
    picks = np.unique(np.linspace(0, n_frames - 1,
                                  min(24, n_frames), dtype=int))
    lag_min = int(sr / 400)
    lag_max = min(int(sr / 60), frame - 1)
    fft_len = 1 << (2 * frame - 1).bit_length()
    values = []
    for i in picks:
        seg = y[i * hop:i * hop + frame].astype("float64")
        seg -= seg.mean()
        spec = np.fft.rfft(seg, fft_len)
        ac = np.fft.irfft(spec * np.conj(spec), fft_len)[:frame]
        if ac[0] <= 1e-9:
            continue
        ac /= ac[0]
        lag = lag_min + int(np.argmax(ac[lag_min:lag_max + 1]))
        if ac[lag] > 0.30:
            values.append(sr / float(lag))
    if len(values) < 3:
        return 0.0, 0.0
    arr = np.asarray(values)
    return (float(np.median(arr)),
            float(np.percentile(arr, 75) - np.percentile(arr, 25)))


def _extract_features(path: str):
    """Return (feature_vector, duration) or None for a silent / broken clip.

    Pure numpy + scipy. See the module docstring for why librosa is avoided.
    """
    import math

    import soundfile as sf
    from scipy.fft import dct
    from scipy.signal import resample_poly, stft

    y, sr = sf.read(path, dtype="float32", always_2d=False)
    if y.ndim != 1:
        y = y.mean(axis=1)
    if sr != 16000:
        g = math.gcd(int(sr), 16000)
        y = resample_poly(y, 16000 // g, sr // g).astype("float32")
        sr = 16000
    duration = float(y.size) / sr
    if y.size < int(sr * 0.3):
        return None

    n_fft, hop = 512, 256
    freqs, _, spec = stft(y, fs=sr, nperseg=n_fft,
                          noverlap=n_fft - hop,
                          boundary=None, padded=False)
    mag = np.abs(spec) + 1e-8
    power = mag ** 2
    if power.shape[1] < 3:
        return None

    rms = np.sqrt(power.mean(axis=0))
    peak = float(rms.max())
    if peak <= 1e-6:
        return None
    active = rms > (0.2 * peak)
    voiced = power[:, active] if int(active.sum()) >= 3 else power

    fb = _mel_filterbank(sr, n_fft)
    mfcc = dct(np.log(fb @ voiced + 1e-8),
               type=2, axis=0, norm="ortho")[:20]
    centroid = (freqs[:, None] * mag).sum(axis=0) / mag.sum(axis=0)

    frames = 1 + (y.size - n_fft) // hop
    if frames > 0:
        idx = np.arange(n_fft)[:, None] + hop * np.arange(frames)[None, :]
        seg = y[idx]
        zcr = (np.diff(np.signbit(seg), axis=0) != 0).mean(axis=0)
    else:
        zcr = np.array([0.0])
    f0_med, f0_iqr = _f0_stats(y, sr)

    feat = np.concatenate([
        mfcc.mean(axis=1),
        mfcc.std(axis=1),
        np.array([centroid.mean(), centroid.std(),
                  zcr.mean(), zcr.std()], dtype="float32"),
        np.array([f0_med, f0_iqr], dtype="float32") * 0.5,
        np.array([rms.mean(), rms.std(), duration], dtype="float32"),
    ])
    feat = np.nan_to_num(feat, nan=0.0, posinf=0.0, neginf=0.0)
    return feat.astype("float32"), duration


_CAMPPLUS = None


def _get_campplus():
    """Load index-tts' own speaker-verification model once, on CPU.

    Lazy singleton: ``import core`` must stay light, and clustering must not
    compete with the resident TTS model for VRAM. The 28 MB of weights are
    already on disk in the index-tts checkpoint directory.
    """
    global _CAMPPLUS
    if _CAMPPLUS is not None:
        return _CAMPPLUS
    import torch
    from indextts.s2mel.modules.campplus.DTDNN import CAMPPlus

    model_dir = "D:/code/index-tts/checkpoints"
    try:
        from core.utils.config_utils import load_key
        model_dir = str(load_key("index_tts.model_dir") or model_dir)
    except Exception:
        pass
    ckpt = os.path.join(model_dir, "hf_cache", "campplus_cn_common.bin")
    if not os.path.isfile(ckpt):
        raise FileNotFoundError(
            f"CAMPPlus 声纹权重不存在：{ckpt}；请检查 config.yaml 的 "
            "index_tts.model_dir 是否指向 index-tts/checkpoints")

    model = CAMPPlus(feat_dim=80, embedding_size=CAMPPPLUS_EMBED_DIM)
    model.load_state_dict(torch.load(ckpt, map_location="cpu"))
    model.eval()
    _CAMPPLUS = model
    rprint(f"[voice_map] CAMPPlus 声纹模型已加载（CPU）: {ckpt}")
    return model


def _embedding_from_wave(y, sr):
    """192-d L2-normalised CAMPPlus embedding for an in-memory waveform.

    Mirrors ``IndexTTS2`` exactly: 16 kHz waveform -> kaldi 80-bin fbank ->
    per-bin mean subtraction -> CAMPPlus. Used both for whole clips and for
    sub-line windows sliced out of a merged clip (FR-34).
    """
    import math

    import torch
    import torchaudio
    from scipy.signal import resample_poly

    y = np.asarray(y, dtype="float32")
    if y.ndim != 1:
        y = y.mean(axis=1)
    if sr != 16000:
        g = math.gcd(int(sr), 16000)
        y = resample_poly(y, 16000 // g, sr // g).astype("float32")
        sr = 16000
    duration = float(y.size) / sr
    if y.size < int(sr * 0.3):
        return None
    if float(np.sqrt(np.mean(y.astype("float64") ** 2))) < 1e-5:
        return None

    wav = torch.from_numpy(np.ascontiguousarray(y)).unsqueeze(0)
    feat = torchaudio.compliance.kaldi.fbank(
        wav, num_mel_bins=80, dither=0, sample_frequency=16000)
    if feat.shape[0] < 10:
        return None
    feat = feat - feat.mean(dim=0, keepdim=True)
    with torch.no_grad():
        emb = _get_campplus()(feat.unsqueeze(0))[0]
    emb = torch.nn.functional.normalize(emb, dim=0)
    return emb.numpy().astype("float32"), duration


def _extract_embedding(path: str):
    """192-d L2-normalised speaker embedding for one vocal clip file."""
    import soundfile as sf

    y, sr = sf.read(path, dtype="float32", always_2d=False)
    return _embedding_from_wave(y, sr)


def _smooth_labels(labels, window: int = SMOOTH_WINDOW):
    """Majority vote in a centred window.

    Real conversation is turn-based: one speaker holds the floor for several
    consecutive lines. An isolated flip with no neighbours is almost always
    a prosody artefact, not a change of person. This is the single most
    effective fix for v1's mid-narration voice jumps.
    """
    labels = np.asarray(labels, dtype=int)
    n = labels.size
    half = max(1, window // 2)
    out = labels.copy()
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out[i] = int(np.bincount(labels[lo:hi]).argmax())
    return out


def _compact_labels(labels):
    """Renumber surviving clusters to 0..k-1 after smoothing."""
    uniq = np.unique(labels)
    remap = {int(u): i for i, u in enumerate(uniq)}
    return np.asarray([remap[int(x)] for x in labels], dtype=int)


def _forced_speaker_count():
    """config.yaml: index_tts.voice_map_speakers = 'auto' | 1..6."""
    try:
        from core.utils.config_utils import load_key
        raw = load_key("index_tts.voice_map_speakers")
    except Exception:
        return None
    if raw is None or str(raw).strip().lower() in ("", "auto", "0"):
        return None
    try:
        k = int(raw)
    except (TypeError, ValueError):
        return None
    return k if 1 <= k <= MAX_SPEAKERS else None


def _collect_features(df):
    """One speaker embedding per vocal clip. Pure CPU, never touches VRAM."""
    numbers, feats, durations = [], [], []
    total = len(df)
    for i, num in enumerate(df["number"].tolist(), 1):
        path = os.path.join(_AUDIO_REFERS_DIR, f"{num}.wav")
        if not os.path.isfile(path):
            continue
        try:
            got = _extract_embedding(path)
        except Exception as e:
            rprint(f"[yellow][voice_map] 跳过 {num}.wav: {type(e).__name__}: {e}[/yellow]")
            continue
        if got is None:
            continue
        feat, dur = got
        numbers.append(int(num))
        feats.append(feat)
        durations.append(dur)
        if i % 100 == 0:
            rprint(f"[voice_map] 声纹嵌入 {i}/{total}")

    if len(feats) < 2:
        raise RuntimeError(
            "可用的原声切片不足 2 条，无法聚类。请先跑流水线第 9 步『提取参考音频』。"
        )
    return numbers, np.vstack(feats), np.asarray(durations, dtype=float)


def _scaled(feats):
    """v2 embeddings are already L2-normalised; StandardScaler would destroy
    the cosine geometry the guard rails rely on."""
    return np.asarray(feats, dtype="float64")


def _choose_k(feats):
    """Estimate speaker count from L2-normalised speaker embeddings.

    Returns ``(labels, k, score, diagnostics)``. k=1 is a valid, deliberate
    result: one stable voice beats a wrong split that flips timbre in the
    middle of a sentence. Three independent gates must all pass before we
    accept k>1; the measured values are returned so the thresholds can be
    calibrated from a real run instead of guessed.
    """
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    X = _scaled(feats)
    n = X.shape[0]
    upper = min(MAX_SPEAKERS, n - 1)
    forced = _forced_speaker_count()
    diag = {"flips": 0, "min_share": 0.0, "center_cos": 0.0,
            "shares": [], "reason": "", "forced": forced,
            "estimated_k": 1, "smoothed_idx": []}

    if forced == 1:
        diag["reason"] = "config 强制单人"
        return np.zeros(n, dtype=int), 1, 0.0, diag

    candidates = [forced] if forced is not None else range(2, upper + 1)
    best_k, best_labels, best_score = 1, np.zeros(n, dtype=int), 0.0
    for k in candidates:
        labels = AgglomerativeClustering(n_clusters=k, linkage="ward").fit_predict(X)
        if len(set(labels.tolist())) < 2:
            continue
        score = float(silhouette_score(
            X, labels, sample_size=min(600, n), random_state=0))
        if score > best_score:
            best_k, best_labels, best_score = k, labels, score

    diag["estimated_k"] = best_k
    if best_k == 1:
        diag["reason"] = "无有效多簇"
        return np.zeros(n, dtype=int), 1, best_score, diag

    # Isolated flips are prosody artefacts, not speaker changes.
    raw = np.asarray(best_labels, dtype=int)
    smoothed = _smooth_labels(raw)
    diag["flips"] = int((smoothed != raw).sum())
    # 被平滑改过标签的句子本身就是「不可靠」，交给人工层高亮
    diag["smoothed_idx"] = np.where(smoothed != raw)[0].tolist()
    labels = _compact_labels(smoothed)
    k = int(labels.max()) + 1
    if k == 1:
        diag["reason"] = "话轮平滑后只剩一簇"
        return np.zeros(n, dtype=int), 1, best_score, diag

    score = float(silhouette_score(
        X, labels, sample_size=min(600, n), random_state=0))
    shares = [float((labels == c).mean()) for c in range(k)]
    diag["min_share"] = min(shares)
    diag["shares"] = [round(s, 3) for s in shares]
    centers = np.vstack([X[labels == c].mean(axis=0) for c in range(k)])
    centers /= (np.linalg.norm(centers, axis=1, keepdims=True) + 1e-8)
    cos = centers @ centers.T
    diag["center_cos"] = float(cos[np.triu_indices(k, 1)].max())

    if forced is None:
        reasons = []
        if score < MIN_SILHOUETTE:
            reasons.append(f"silhouette {score:.3f} < {MIN_SILHOUETTE}")
        if diag["min_share"] < MIN_SPEAKER_SHARE:
            reasons.append(
                f"最小簇占比 {diag['min_share']:.3f} < {MIN_SPEAKER_SHARE}")
        if diag["center_cos"] > MAX_CENTER_COS:
            reasons.append(
                f"簇间中心余弦 {diag['center_cos']:.3f} > {MAX_CENTER_COS}")
        if reasons:
            diag["reason"] = "；".join(reasons)
            return np.zeros(n, dtype=int), 1, score, diag

    return labels, k, score, diag


def _write_voice_ref(number: int, out_path: str) -> float:
    """Copy one clean 4-8 s clip as the speaker's stable reference."""
    import soundfile as sf

    src = os.path.join(_AUDIO_REFERS_DIR, f"{number}.wav")
    y, sr = sf.read(src, dtype="float32", always_2d=False)
    mono = y.mean(axis=1) if y.ndim > 1 else y

    # Trim leading silence with a plain RMS envelope (no librosa effects).
    win = max(1, int(0.02 * sr))
    n = mono.size // win
    if n > 3:
        energy = np.sqrt((mono[:n * win].reshape(n, win) ** 2).mean(axis=1))
        peak = float(energy.max())
        if peak > 1e-6:
            good = np.where(energy > peak * 0.06)[0]  # roughly -25 dB
            if good.size:
                y = y[int(good[0]) * win:]

    limit = int(TARGET_REF_SECONDS * sr)
    if y.shape[0] > limit:
        y = y[:limit]
    sf.write(out_path, y, sr, subtype="PCM_16")
    return float(y.shape[0]) / sr


def _read_overrides(fp: str) -> dict:
    """Human-assigned lines survive a producer rebuild for the same video.

    Only kept when the fingerprint still matches: a new video / re-cut task
    table must start from a clean automatic draft, never inherit old labels.
    """
    if not os.path.isfile(VOICE_MAP_FILE):
        return {}
    try:
        with open(VOICE_MAP_FILE, encoding="utf-8") as f:
            old = json.load(f)
        if old.get("fingerprint") != fp:
            return {}
        raw = old.get("overrides", {})
        return {str(k): str(v) for k, v in raw.items()}
    except Exception:
        return {}


def _build_segment_meta(numbers, labels, k, X, durations, diag, segments) -> dict:
    """Per-line review hints. Kept in a parallel dict so segments stays a flat
    ``number -> speaker`` mapping and the dubbing consumer needs no change."""
    n = len(numbers)
    centers = {}
    for ci in range(k):
        idx = np.where(labels == ci)[0]
        if idx.size:
            centers[ci] = X[idx].mean(axis=0)
    smoothed = set(diag.get("smoothed_idx", []))
    meta = {}
    for i, num in enumerate(numbers):
        flags = []
        if i in smoothed:
            flags.append("smoothed")
        if ((i > 0 and labels[i - 1] != labels[i]) or
                (i < n - 1 and labels[i + 1] != labels[i])):
            flags.append("turn_boundary")
        if durations[i] < 1.0:
            flags.append("short")
        conf = 1.0
        if k > 1 and len(centers) > 1:
            own = int(labels[i])
            d_own = float(np.linalg.norm(X[i] - centers[own]))
            d_other = min(float(np.linalg.norm(X[i] - c))
                          for ci, c in centers.items() if ci != own)
            margin = (d_other - d_own) / max(d_other, 1e-8)
            conf = round(max(0.0, min(1.0, margin)), 3)
            if margin < 0.10:
                flags.append("low_margin")
        meta[str(num)] = {"confidence": conf, "flags": flags, "manual": False}
    for key in segments:
        if key not in meta:
            meta[key] = {"confidence": 0.0, "flags": ["silent"], "manual": False}
    return meta


def build_voice_map(force: bool = False) -> dict:
    """Idempotent: reuse the map while its fingerprint still matches the task."""
    df = _read_task_df()
    fp = _fingerprint(df)
    overrides = _read_overrides(fp)

    # Previous contract for the SAME cut: carries seeds and any manually
    # added speakers / reference clips that automatic clustering cannot know
    # about (e.g. a two-person interview the guard rails degraded to k=1).
    prev = {}
    if os.path.isfile(VOICE_MAP_FILE):
        try:
            with open(VOICE_MAP_FILE, encoding="utf-8") as f:
                _p = json.load(f)
            if _p.get("fingerprint") == fp:
                prev = _p
        except Exception:
            pass

    if not force and os.path.isfile(VOICE_MAP_FILE):
        try:
            with open(VOICE_MAP_FILE, encoding="utf-8") as f:
                cached = json.load(f)
            refs_ok = all(os.path.isfile(v["ref"])
                          for v in cached.get("speakers", {}).values())
            if (cached.get("fingerprint") == fp
                    and cached.get("version") == VERSION
                    and cached.get("producer") == PRODUCER_ID
                    and refs_ok):
                # 旧 producer 留下的契约可能没有人工层字段；补齐以免前端缺列
                cached.setdefault("overrides", {})
                cached.setdefault("segment_meta", {})
                rprint(f"[voice_map] 命中已有契约（{cached['n_speakers']} 人，"
                       f"producer={cached['producer']}），跳过重算")
                return cached
        except Exception as e:
            rprint(f"[yellow][voice_map] 已有契约不可读，将重建: {e}[/yellow]")

    numbers, feats, durations = _load_or_compute_embeddings(df, fp)
    labels, k, sil, diag = _choose_k(feats)
    X = _scaled(feats)
    rprint(f"[voice_map] 聚类判据: silhouette={sil:.3f} (门槛 {MIN_SILHOUETTE}) | "
           f"最小簇占比={diag['min_share']:.3f} (门槛 {MIN_SPEAKER_SHARE}) | "
           f"簇间中心余弦={diag['center_cos']:.3f} (上限 {MAX_CENTER_COS}) | "
           f"话轮平滑消除 {diag['flips']} 次孤立跳变 | "
           f"估人数={diag['estimated_k']} | forced={diag['forced']}")
    if diag["reason"]:
        rprint(f"[yellow][voice_map] 未通过判据（{diag['reason']}），"
               "降级为全片统一音色[/yellow]")
    os.makedirs(VOICE_REFS_DIR, exist_ok=True)

    import numpy as np

    speakers, segments = {}, {}
    for ci in range(k):
        idx = np.where(labels == ci)[0]
        if idx.size == 0:
            continue
        name = f"SPEAKER_{ci:02d}"
        center = X[idx].mean(axis=0)
        dist = np.linalg.norm(X[idx] - center, axis=1)
        candidates = idx[np.argsort(dist)[:min(8, idx.size)]]

        def _score(i):
            d = durations[i]
            return abs(d - TARGET_REF_SECONDS) + (0.0 if 3.0 <= d <= 10.0 else 5.0)

        chosen = int(min(candidates, key=_score))
        ref_path = os.path.abspath(os.path.join(VOICE_REFS_DIR, f"{name}.wav"))
        ref_dur = _write_voice_ref(numbers[chosen], ref_path)
        speakers[name] = {
            "ref": ref_path,
            "n_clips": int(idx.size),
            "ref_duration": round(ref_dur, 2),
            "ref_source": numbers[chosen],
            "confidence": round(float(1.0 / (1.0 + float(dist.mean()))), 3),
        }

    # A human may have added speakers that the automatic estimate never
    # produced. Keep their manually chosen reference clips across rebuilds,
    # otherwise "rebuild draft" would silently delete the second voice.
    prev_speakers = prev.get("speakers", {})
    for name in sorted(set(list(prev_speakers) + list(overrides.values()))):
        if name in speakers:
            continue
        info = prev_speakers.get(name)
        if info and os.path.isfile(str(info.get("ref", ""))):
            speakers[name] = {**info, "n_clips": 0}

    for i, num in enumerate(numbers):
        segments[str(num)] = f"SPEAKER_{int(labels[i]):02d}"

    # 静音/损坏切片没有 embedding，但消费层会为每个 number 查表；
    # 归入人数最多的簇，避免整片跑到一半因缺句抛 KeyError。
    missing = [int(n) for n in df["number"].tolist()
               if str(int(n)) not in segments]
    if missing and speakers:
        default = max(speakers, key=lambda s: speakers[s]["n_clips"])
        for n in missing:
            segments[str(n)] = default
        rprint(f"[yellow][voice_map] {len(missing)} 条静音/损坏切片无声纹，"
               f"归入 {default}[/yellow]")

    segment_meta = _build_segment_meta(
        numbers, labels, k, X, durations, diag, segments)
    # 一行塞了多条原始字幕 → 整句声纹是混合的：单独打标，不参与种子传播，
    # 真正的音色归属由 chunk_speakers 逐子行决定（FR-34）。
    n_mixed = 0
    for key in mixed_numbers(df):
        m = segment_meta.setdefault(
            key, {"confidence": 1.0, "flags": [], "manual": False})
        m["flags"] = sorted(set(m.get("flags", [])) | {MIXED_FLAG})
        n_mixed += 1
    if n_mixed:
        rprint(f"[voice_map] 检出 {n_mixed} 条双人混句，已标记 mixed，"
               "逐子行音色由「重建子行判定」生成")

    # 人工分配是终审，覆盖自动初稿；重建同一个视频时原样保留。
    overrides = {n: s for n, s in overrides.items()
                 if n in segments and s in speakers}
    for num, name in overrides.items():
        segments[num] = name
        segment_meta.setdefault(num, {"confidence": 1.0, "flags": [],
                                     "manual": True})["manual"] = True
    for name in speakers:
        speakers[name]["n_clips"] = sum(
            1 for s in segments.values() if s == name)

    result = {
        "version": VERSION,
        "producer": PRODUCER_ID,
        "fingerprint": fp,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "n_speakers": len(speakers),
        "silhouette": round(float(sil), 4),
        "degraded": bool(k == 1 and len(speakers) == 1),
        "task_rows": int(len(df)),
        "mapped_segments": len(segments),
        "embedded_segments": len(numbers),
        "diagnostics": diag,
        "seeds": prev.get("seeds", {}),
        "overrides": overrides,
        "segment_meta": segment_meta,
        "speakers": speakers,
        "segments": segments,
    }
    os.makedirs(_AUDIO_DIR, exist_ok=True)
    with open(VOICE_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if k == 1 and len(speakers) == 1:
        rprint("[voice_map] 未检出可分离的多个说话人，降级为全片统一音色"
               "（等价 refer_mode=2，不会报错、不会变慢）")
    else:
        rprint(f"[voice_map] 自动估出 {k} 人 / 契约保留 {len(speakers)} 人，"
               f"silhouette={sil:.3f}；代表音色目录 {VOICE_REFS_DIR}")
    for name, info in speakers.items():
        rprint(f"  {name}: {info.get('n_clips', 0)} 句 | "
               f"代表片段 #{info.get('ref_source', '-')} "
               f"({info.get('ref_duration', '-')}s) | conf={info.get('confidence', '-')}")
    if overrides:
        rprint(f"[voice_map] 保留人工分配 {len(overrides)} 句（重建不覆盖）")
    rprint(f"[voice_map] 契约已写入 {os.path.abspath(VOICE_MAP_FILE)}")
    return result


def load_voice_map(verify: bool = True) -> dict:
    """Read the contract. Callers should cache the result; verify is not free."""
    if not os.path.isfile(VOICE_MAP_FILE):
        raise FileNotFoundError(
            "找不到 voice_map.json。请先跑流水线第 9 步，或执行 "
            "python -m core.tts_backend.voice_map --build"
        )
    with open(VOICE_MAP_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != VERSION:
        raise RuntimeError(
            f"voice_map.json 版本不匹配（期望 v{VERSION}），请用 --build --force 重建")
    if data.get("producer") != PRODUCER_ID:
        raise RuntimeError(
            f"voice_map.json 由旧 producer 生成（{data.get('producer')}），当前为 "
            f"{PRODUCER_ID}；算法参数已变更，请执行 "
            "python -m core.tts_backend.voice_map --build --force 重建契约，"
            "避免沿用旧的说话人划分。"
        )
    if verify:
        df = _read_task_df()
        if data.get("fingerprint") != _fingerprint(df):
            raise RuntimeError(
                "voice_map.json 与当前配音任务表不匹配（换了视频或重新切句），"
                "请重跑流水线第 9 步生成新契约，避免串用上一个视频的音色。"
            )
    missing = [name for name, info in data["speakers"].items()
               if not os.path.isfile(info["ref"])]
    if missing:
        raise FileNotFoundError(
            f"代表音色文件缺失：{missing}，请用 --build --force 重建")
    return data


# ---------------------------------------------------------------------------
# Human review layer — the UI writes here, the dubbing consumer only reads.
# ---------------------------------------------------------------------------

def _read_contract_raw() -> dict:
    """Read without producer/fingerprint validation.

    A stale contract is still useful to a human reviewer: the labels can be
    edited before the next rebuild. Validation stays in load_voice_map(),
    which is what the synthesis path calls.
    """
    if not os.path.isfile(VOICE_MAP_FILE):
        raise FileNotFoundError(
            "找不到 voice_map.json，请先跑流水线第 9 步或点「重建初稿」")
    with open(VOICE_MAP_FILE, encoding="utf-8") as f:
        data = json.load(f)
    for key, default in (("overrides", {}), ("segment_meta", {}),
                         ("speakers", {}), ("segments", {}),
                         ("chunk_speakers", {}), ("chunk_meta", {}),
                         ("chunk_splits", {})):
        data.setdefault(key, default)
    return data


def _write_contract_raw(data: dict) -> None:
    """Atomic replace: a half-written json must never reach the TTS path."""
    os.makedirs(_AUDIO_DIR, exist_ok=True)
    tmp = VOICE_MAP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, VOICE_MAP_FILE)


def _invalidate_consumer_cache() -> None:
    """index_tts caches the contract per process; drop it after every edit."""
    try:
        from core.tts_backend.index_tts import reload_voice_map
        reload_voice_map()
    except Exception:
        pass  # CLI / unit use: no consumer process to notify


def _load_or_compute_embeddings(df, fingerprint: str | None = None):
    """Cache CAMPPlus embeddings so repeated seed re-labelling stays instant.

    Keyed by (task fingerprint, producer). This file is a pure derivative of
    refers/*.wav: deleting it only costs one CPU recompute and never changes
    the contract.
    """
    fp = fingerprint or _fingerprint(df)
    if os.path.isfile(VOICE_EMB_FILE):
        try:
            with np.load(VOICE_EMB_FILE, allow_pickle=False) as z:
                if (str(z["fingerprint"].item()) == fp
                        and str(z["producer"].item()) == PRODUCER_ID
                        and z["numbers"].shape[0] > 1
                        and z["feats"].shape[1] == CAMPPPLUS_EMBED_DIM):
                    return (z["numbers"].astype(int).tolist(),
                            z["feats"].astype("float32"),
                            z["durations"].astype(float))
        except Exception as e:
            rprint(f"[yellow][voice_map] 声纹缓存不可读，重算：{type(e).__name__}[/yellow]")

    rprint("[voice_map] 提取逐句 CAMPPlus 声纹嵌入（纯 CPU，不占显存）...")
    numbers, feats, durations = _collect_features(df)
    os.makedirs(_AUDIO_DIR, exist_ok=True)
    tmp = VOICE_EMB_FILE + ".tmp.npz"
    np.savez_compressed(
        tmp,
        numbers=np.asarray(numbers, dtype="int64"),
        feats=np.asarray(feats, dtype="float32"),
        durations=np.asarray(durations, dtype="float64"),
        fingerprint=np.asarray(fp),
        producer=np.asarray(PRODUCER_ID),
    )
    os.replace(tmp, VOICE_EMB_FILE)
    rprint(f"[voice_map] 声纹缓存 {len(numbers)} 条 -> {VOICE_EMB_FILE}")
    return numbers, feats, durations


def propagate_from_seeds(seeds: dict, protect_manual: bool = False) -> dict:
    """A few certain lines per speaker -> whole-video assignment.

    The user listens to the ORIGINAL video and marks a handful of lines whose
    speaker is obvious; every other line is assigned by cosine similarity to
    the speaker prototype (mean of that speaker's seed embeddings). Results
    are stored as ``overrides`` so "rebuild draft (keep manual)" reproduces
    them, and ``seeds`` is persisted so the user can add better seeds and
    re-propagate as often as needed.

    This is deliberately NOT a new producer: the contract stays valid for
    synthesis, and the automatic clustering remains the no-seed fallback.
    """
    if not seeds:
        raise ValueError("请先给每位说话人标至少 1 句种子")
    clean = {}
    for name, nums in seeds.items():
        n = str(name).strip()
        try:
            ids = sorted({int(x) for x in nums})
        except (TypeError, ValueError) as e:
            raise ValueError(f"{n} 的种子编号必须是整数") from e
        if ids:
            clean[n] = ids
    if len(clean) < 2:
        raise ValueError("至少需要两位说话人各标 1 句种子")

    data = _read_contract_raw()
    for name in clean:
        if name not in data["speakers"]:
            raise KeyError(f"说话人 {name} 不存在，请先在「说话人与音色」里新增")

    df = _read_task_df()
    fp = _fingerprint(df)
    numbers, feats, _ = _load_or_compute_embeddings(df, fp)
    pos = {int(n): i for i, n in enumerate(numbers)}
    names = list(clean.keys())
    mixed = mixed_numbers(df)

    seed_pos = {}
    for name in names:
        # A merged line's embedding is a blend of two voices: never let it
        # pollute a speaker prototype.
        ids = [pos[n] for n in clean[name]
               if n in pos and str(n) not in mixed]
        if not ids:
            raise ValueError(
                f"{name} 的种子句子都没有可用的纯净原声切片"
                "（双人混句不能当种子，请换一句只含一个人的）")
        seed_pos[name] = ids

    X = np.asarray(feats, dtype="float64")
    protos = []
    for name in names:
        p = X[seed_pos[name]].mean(axis=0)
        protos.append(p / (float(np.linalg.norm(p)) + 1e-8))
    sims = X @ np.vstack(protos).T
    order = np.argsort(-sims, axis=1)
    raw = order[:, 0].astype(int)
    margin = (sims[np.arange(X.shape[0]), order[:, 0]]
              - sims[np.arange(X.shape[0]), order[:, 1]])

    labels = _smooth_labels(raw, window=SMOOTH_WINDOW)
    seed_keys = {}
    for li, name in enumerate(names):
        for n in clean[name]:
            seed_keys[str(n)] = name
            if n in pos:
                labels[pos[n]] = li

    old_overrides = dict(data.get("overrides", {}))
    old_meta = data.get("segment_meta", {})
    changes, meta_updates = {}, {}
    stat = {"seeds": len(seed_keys), "embedded": len(numbers),
            "assigned": 0, "low_margin": 0, "smoothed": 0,
            "protected": 0, "mixed": 0}
    for i, num in enumerate(numbers):
        key = str(int(num))
        # Seeds are ground truth: always (re)write them, even when the label
        # is unchanged, so they survive "rebuild draft (keep manual)".
        if key in seed_keys:
            changes[key] = seed_keys[key]
            meta_updates[key] = {"confidence": 1.0, "flags": []}
            continue
        # A line the user later corrected by hand is not a propagated guess.
        if (protect_manual and key in old_overrides
                and "propagated" not in old_meta.get(key, {}).get("flags", [])):
            stat["protected"] += 1
            continue
        # Merged clip: its blended embedding cannot be assigned to one voice.
        # Keep the existing label, tag it for the review UI, and leave the
        # real decision to chunk_speakers (one speaker per sub-line).
        if key in mixed:
            flags = sorted(set(old_meta.get(key, {}).get("flags", []))
                           | {MIXED_FLAG, "propagated"})
            meta_updates[key] = {"confidence": 0.0, "flags": flags}
            stat["mixed"] += 1
            continue
        conf = float(np.clip(margin[i] / SEED_MARGIN_FULL, 0.0, 1.0))
        flags = ["propagated"]
        if margin[i] < SEED_MARGIN_LOW:
            flags.append("low_margin")
            stat["low_margin"] += 1
        if int(labels[i]) != int(raw[i]):
            flags.append("smoothed")
            stat["smoothed"] += 1
        meta_updates[key] = {"confidence": round(conf, 3),
                             "flags": sorted(set(flags))}
        target = names[int(labels[i])]
        if data["segments"].get(key) != target:
            changes[key] = target
    stat["assigned"] = len(changes)

    data["seeds"] = clean
    out = apply_overrides(changes, meta_updates=meta_updates, _data=data)
    out["n_speakers"] = len(out["speakers"])
    out["degraded"] = False
    out.setdefault("diagnostics", {})["seed_propagation"] = stat
    _write_contract_raw(out)
    _invalidate_consumer_cache()

    stat["counts"] = {n: out["speakers"][n]["n_clips"] for n in out["speakers"]}
    rprint(f"[voice_map] 种子传播完成：{stat}")
    return stat


def apply_overrides(changes: dict, marks: dict | None = None,
                    unassign: list | None = None,
                    meta_updates: dict | None = None,
                    _data: dict | None = None) -> dict:
    """Apply human decisions and persist them.

    changes  : {number: speaker} — final say, recorded in ``overrides`` so a
               later producer rebuild cannot silently undo them.
    marks    : {number: bool} — manual "this one is unclear" flag.
    unassign : [number] — drop the manual lock; the next rebuild re-decides it.
    meta_updates : {number: {confidence, flags}} — used by seed propagation.
    _data    : reuse an already-loaded contract (avoids a redundant read).
    """
    data = _data if _data is not None else _read_contract_raw()
    speakers, segments = data["speakers"], data["segments"]
    overrides, meta = data["overrides"], data["segment_meta"]

    for raw_num, raw_name in (changes or {}).items():
        num, name = str(int(raw_num)), str(raw_name)
        if num not in segments:
            raise KeyError(f"第 {num} 句不在当前任务表中")
        if name not in speakers:
            raise KeyError(f"说话人 {name} 不存在")
        segments[num] = name
        overrides[num] = name
        # Whole-row human choice must also win over any (auto or manual)
        # sub-line decision, otherwise _resolve_reference() would keep reading
        # the old chunk_speakers[number][0] (v1.26).
        cs = data.setdefault("chunk_speakers", {})
        if cs.get(num):
            cs[num] = {k: name for k in cs[num]}
        m = meta.setdefault(num, {"confidence": 1.0, "flags": [],
                                 "manual": True})
        m["manual"] = True
        # A direct human edit is no longer a machine-propagated guess.
        m["flags"] = [f for f in m.get("flags", []) if f != "propagated"]

    for raw_num, upd in (meta_updates or {}).items():
        num = str(int(raw_num))
        m = meta.setdefault(num, {"confidence": 1.0, "flags": [],
                                  "manual": False})
        if "confidence" in upd:
            m["confidence"] = float(upd["confidence"])
        if "flags" in upd:
            m["flags"] = list(upd["flags"])

    for raw_num, val in (marks or {}).items():
        num = str(int(raw_num))
        m = meta.setdefault(num, {"confidence": 1.0, "flags": [],
                                 "manual": False})
        flags = set(m.get("flags", []))
        if val:
            flags.add("marked")
        else:
            flags.discard("marked")
        m["flags"] = sorted(flags)

    for raw_num in (unassign or []):
        num = str(int(raw_num))
        overrides.pop(num, None)
        if num in meta:
            meta[num]["manual"] = False

    for name in speakers:
        speakers[name]["n_clips"] = sum(
            1 for s in segments.values() if s == name)
    _write_contract_raw(data)
    _invalidate_consumer_cache()
    return data


def set_speaker_ref(name: str, number: int) -> dict:
    """Replace one speaker's reference clip with a chosen original-vocal line."""
    data = _read_contract_raw()
    if name not in data["speakers"]:
        raise KeyError(f"说话人 {name} 不存在")
    number = int(number)
    src = os.path.join(_AUDIO_REFERS_DIR, f"{number}.wav")
    if not os.path.isfile(src):
        raise FileNotFoundError(f"原声切片不存在：{src}")
    os.makedirs(VOICE_REFS_DIR, exist_ok=True)
    ref = os.path.abspath(os.path.join(VOICE_REFS_DIR, f"{name}.wav"))
    dur = _write_voice_ref(number, ref)
    info = data["speakers"][name]
    info.update({"ref": ref, "ref_duration": round(dur, 2),
                 "ref_source": number, "ref_manual": True})
    _write_contract_raw(data)
    _invalidate_consumer_cache()
    return data


def add_speaker(name: str, number: int) -> dict:
    """Create a new speaker whose timbre comes from one original-vocal line."""
    name = str(name).strip()
    if not name or not all(c.isalnum() or c in "_-" for c in name):
        raise ValueError("说话人名只能用字母、数字、下划线或连字符")
    data = _read_contract_raw()
    if name in data["speakers"]:
        raise ValueError(f"说话人 {name} 已存在")
    number = int(number)
    if not os.path.isfile(os.path.join(_AUDIO_REFERS_DIR, f"{number}.wav")):
        raise FileNotFoundError(f"原声切片 {number}.wav 不存在")
    os.makedirs(VOICE_REFS_DIR, exist_ok=True)
    ref = os.path.abspath(os.path.join(VOICE_REFS_DIR, f"{name}.wav"))
    dur = _write_voice_ref(number, ref)
    data["speakers"][name] = {
        "ref": ref, "n_clips": 0, "ref_duration": round(dur, 2),
        "ref_source": number, "confidence": 1.0, "ref_manual": True,
    }
    data["n_speakers"] = len(data["speakers"])
    _write_contract_raw(data)
    _invalidate_consumer_cache()
    return data


def remove_speaker(name: str) -> dict:
    """Delete a speaker and reassign its lines to the largest remaining one."""
    data = _read_contract_raw()
    speakers = data["speakers"]
    if name not in speakers:
        raise KeyError(f"说话人 {name} 不存在")
    if len(speakers) <= 1:
        raise ValueError("至少需要保留一位说话人")
    others = {k: v for k, v in speakers.items() if k != name}
    fallback = max(others, key=lambda s: others[s].get("n_clips", 0))
    for num, cur in data["segments"].items():
        if cur == name:
            data["segments"][num] = fallback
            data["overrides"][num] = fallback
            m = data["segment_meta"].setdefault(
                num, {"confidence": 1.0, "flags": [], "manual": True})
            m["manual"] = True
    del speakers[name]
    data["n_speakers"] = len(speakers)
    speakers[fallback]["n_clips"] = sum(
        1 for s in data["segments"].values() if s == fallback)
    _write_contract_raw(data)
    _invalidate_consumer_cache()
    return data


def clear_dubbed(numbers) -> dict:
    """Delete a line's already-synthesised wavs so step 10 regenerates it.

    Step 10 skips any existing temp file (tts_main.py:35) and re-writes the
    speed-adjusted segs file, so both must go. Everything else is untouched.
    """
    import re

    removed = []
    for raw_num in numbers or []:
        n = int(raw_num)
        for folder, pattern in (
            (_AUDIO_TMP_DIR, re.compile(rf"^{n}_\d+_temp\.wav$")),
            (_AUDIO_SEGS_DIR, re.compile(rf"^{n}_\d+\.wav$")),
        ):
            if not os.path.isdir(folder):
                continue
            for fn in os.listdir(folder):
                if pattern.match(fn):
                    os.remove(os.path.join(folder, fn))
                    removed.append(os.path.join(folder, fn))
    if removed:
        _invalidate_consumer_cache()
    return {"removed": removed, "count": len(removed)}


# ---------------------------------------------------------------------------
# Mixed lines & sub-line speaker assignment (FR-34)
#
# Step 8.1 merges very short subtitle lines and step 8.2 keeps the original
# subtitle texts in the ``lines`` column. Step 10 already synthesises one wav
# per sub-line (``{number}_{line_index}.wav``) and step 11 gives each its own
# time slot, so the ONLY missing piece was a per-sub-line speaker decision.
# No subtitle, translation, checkpoint or task-table row is changed here.
# ---------------------------------------------------------------------------

def _task_lines(raw) -> list:
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
    return list(raw) if isinstance(raw, (list, tuple)) else []


def _parse_srt_time(value) -> float:
    t = str(value).strip().replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _srt_blocks() -> list:
    """Ordered (start_sec, end_sec, text) from output/trans.srt."""
    if not os.path.isfile(TRANS_SRT_FILE):
        return []
    with open(TRANS_SRT_FILE, encoding="utf-8") as f:
        content = f.read()
    blocks = []
    for block in content.strip().split("\n\n"):
        lines = [x.strip() for x in block.split("\n") if x.strip()]
        if len(lines) < 3:
            continue
        try:
            a, b = lines[1].split("-->")
            blocks.append((_parse_srt_time(a), _parse_srt_time(b),
                           " ".join(lines[2:])))
        except (ValueError, IndexError):
            continue
    return blocks


def mixed_numbers(df=None) -> set:
    """Numbers whose dubbing task contains more than one original subtitle."""
    df = df if df is not None else _read_task_df()
    if "lines" not in df.columns:
        return set()
    return {str(int(row["number"])) for _, row in df.iterrows()
            if len(_task_lines(row.get("lines"))) > 1}


def mark_mixed(data: dict, df=None) -> int:
    """Tag merged rows in segment_meta; idempotent, never touches synthesis."""
    nums = mixed_numbers(df)
    meta = data.setdefault("segment_meta", {})
    for key in nums:
        m = meta.setdefault(key, {"confidence": 1.0, "flags": [],
                                  "manual": False})
        m["flags"] = sorted(set(m.get("flags", [])) | {MIXED_FLAG})
    return len(nums)


def _active_splits(data=None) -> dict:
    """Splits that still match the current task table. A step-8.2 rerun
    rewrites ``lines``; in that case the stale split is ignored (and the
    ordered srt cursor keeps its original meaning).
    """
    try:
        data = data if data is not None else _read_contract_raw()
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return {str(k): v for k, v in (data.get("chunk_splits") or {}).items()
            if isinstance(v, dict) and v.get("lines")}


def subline_windows(df=None) -> dict:
    """Per-number sub-line windows, in seconds relative to refers/{n}.wav.

    Mirrors step 8.2's ordered matching: each task row consumes exactly
    ``len(lines)`` consecutive trans.srt blocks. Falls back to an equal split
    when the srt is missing/short, so an already-built task table still works.

    v1.26: a manually split single-subtitle row consumes only ONE srt block
    (``n_src``), but exposes two sub-lines split at ``at`` seconds. Keeping
    the cursor on source blocks is essential — advancing it by the new line
    count would shift every following row by one subtitle.
    """
    df = df if df is not None else _read_task_df()
    blocks = _srt_blocks()
    splits = _active_splits()
    cursor, out = 0, {}
    for _, row in df.iterrows():
        num = str(int(row["number"]))
        lines = _task_lines(row.get("lines"))
        if not lines:
            continue
        row_start = _parse_srt_time(row["start_time"])
        row_end = _parse_srt_time(row["end_time"])
        span = max(0.1, row_end - row_start)

        split = splits.get(num)
        split_ok = bool(split and [str(x) for x in split["lines"]]
                        == [str(x) for x in lines])
        n_src = max(1, int(split.get("n_src", 1))) if split_ok else len(lines)
        picked = blocks[cursor:cursor + n_src]
        cursor += n_src
        exact = len(picked) == n_src
        if exact:
            a0 = max(0.0, picked[0][0] - row_start)
            b0 = min(span, picked[-1][1] - row_start)
        else:
            a0, b0 = 0.0, span

        if split_ok:
            at = float(split.get("at", (a0 + b0) / 2))
            at = min(max(at, a0 + 0.2), b0 - 0.2)
            wins = [(a0, at), (at, b0)]
            source = "split"
        elif exact:
            wins = [(max(0.0, s - row_start), min(span, e - row_start))
                    for s, e, _ in picked]
            source = "srt"
        else:
            step = span / len(lines)
            wins = [(i * step, (i + 1) * step) for i in range(len(lines))]
            source = "split"
        out[num] = {
            "lines": [str(x) for x in lines],
            "windows": [(round(a, 3), round(max(a + 0.1, b), 3)) for a, b in wins],
            "source": source,
            "split": split_ok,
        }
    return out


def chunk_rows(data=None, df=None, windows=None) -> dict:
    """Read-only sub-line view for the review UI (never writes, never spends)."""
    data = data if data is not None else _read_contract_raw()
    windows = windows if windows is not None else subline_windows(df)
    cs = data.get("chunk_speakers") or {}
    cm = data.get("chunk_meta") or {}
    out = {}
    for num, info in windows.items():
        # v1.26: EVERY row can be expanded. A single-subtitle row still has a
        # sub-line 0 whose speaker can be overridden (that fixes a wrong whole
        # -row label without touching the task table), or can be split in two.
        rows = []
        for i, text in enumerate(info["lines"]):
            idx = str(i)
            m = (cm.get(num) or {}).get(idx) or {}
            rows.append({
                "index": i,
                "text": text,
                "time": list(m.get("time") or info["windows"][i]),
                "speaker": (cs.get(num) or {}).get(idx) or data["segments"].get(num),
                "confidence": float(m.get("confidence", 0.0)),
                "flags": list(m.get("flags", [])),
                "manual": bool(m.get("manual", False)),
            })
        out[num] = {"rows": rows, "source": info["source"],
                    "mixed": len(info["lines"]) >= 2,
                    "split": bool(info.get("split"))}
    return out


def build_chunk_speakers(force: bool = False, numbers=None) -> dict:
    """Assign a speaker to every sub-line of a dubbing task.

    Pure CPU (CAMPPlus prototypes from the speaker reference clips), no GPT,
    no GPU, no subtitle/translation changes. Manual sub-line edits survive a
    rebuild unless ``force=True``.

    ``numbers=None`` keeps the historical behaviour (merged rows only, so a
    full rebuild never overwrites a human's whole-row choice). Passing an
    explicit list also re-judges single-subtitle rows — that is what the
    "重建选中行" button does (v1.26).
    """
    try:
        import soundfile as sf
        import torch  # noqa: F401
        import torchaudio  # noqa: F401
    except ImportError as e:
        raise ValueError(
            f"子行判定缺少声纹依赖 {e.name}；请在运行 VideoLingo server 的 (vl) "
            "环境中调用（流水线第 9 步用的就是这套依赖）。"
        ) from e

    data = _read_contract_raw()
    df = _read_task_df()
    windows = subline_windows(df)
    speakers = data["speakers"]

    protos = {}
    for name, info in speakers.items():
        ref = str(info.get("ref", ""))
        if os.path.isfile(ref):
            got = _extract_embedding(ref)
            if got:
                protos[name] = got[0]
    if len(protos) < 2:
        raise ValueError("至少需要两位说话人的有效代表音色，才能逐子行判定")
    names = list(protos)
    P = np.vstack([protos[n] for n in names])

    want = ({str(int(n)) for n in numbers}
            if numbers is not None else None)
    manual_rows = data.get("segment_meta") or {}
    chunk_speakers = dict(data.get("chunk_speakers") or {})
    chunk_meta = dict(data.get("chunk_meta") or {})
    stat = {"rows": 0, "chunks": 0, "assigned": 0, "kept_manual": 0,
            "fallback": 0, "low_margin": 0, "skipped_manual": 0,
            "source": {}}
    for num, info in windows.items():
        if want is not None and num not in want:
            continue
        single = len(info["lines"]) < 2
        # A full rebuild must never silently overwrite a human's whole-row
        # choice; an explicit "rebuild these rows" request may, unless force.
        if single and (want is None or
                       (manual_rows.get(num, {}).get("manual") and not force)):
            if single and want is not None:
                stat["skipped_manual"] += 1
            continue
        stat["rows"] += 1
        stat["source"][info["source"]] = stat["source"].get(info["source"], 0) + 1
        path = os.path.join(_AUDIO_REFERS_DIR, f"{num}.wav")
        y = sr = None
        if os.path.isfile(path):
            try:
                y, sr = sf.read(path, dtype="float32", always_2d=False)
            except Exception:
                y = None
        for i, (a, b) in enumerate(info["windows"]):
            stat["chunks"] += 1
            key, idx = num, str(i)
            old = (chunk_meta.get(key) or {}).get(idx) or {}
            if old.get("manual") and not force:
                stat["kept_manual"] += 1
                continue
            emb = None
            if y is not None and b > a:
                got = _embedding_from_wave(y[int(a * sr):int(b * sr)], sr)
                emb = got[0] if got else None
            if emb is None:
                chosen = data["segments"].get(key) or names[0]
                conf, flags = 0.0, ["silent"]
                stat["fallback"] += 1
            else:
                sims = P @ emb
                order = np.argsort(-sims)
                chosen = names[int(order[0])]
                margin = (float(sims[order[0]] - sims[order[1]])
                          if len(order) > 1 else 1.0)
                conf = float(np.clip(margin / SEED_MARGIN_FULL, 0.0, 1.0))
                flags = [] if margin >= CHUNK_MARGIN_LOW else ["low_margin"]
                if "low_margin" in flags:
                    stat["low_margin"] += 1
            chunk_speakers.setdefault(key, {})[idx] = chosen
            chunk_meta.setdefault(key, {})[idx] = {
                "confidence": round(conf, 3), "flags": flags,
                "time": [a, b], "manual": False, "source": info["source"],
            }
            stat["assigned"] += 1

    data["chunk_speakers"] = chunk_speakers
    data["chunk_meta"] = chunk_meta
    data["chunk_built_at"] = datetime.now().isoformat(timespec="seconds")
    _write_contract_raw(data)
    _invalidate_consumer_cache()
    rprint(f"[voice_map] 子行音色判定完成：{stat}")
    return stat


# ---------------------------------------------------------------------------
# v1.26 — splitting a single subtitle that actually contains two speakers.
# Step 8.2 matches task rows against trans.srt blocks; when ASR put both
# speakers into ONE srt block, ``lines`` has length 1 and the review row has
# no expand arrow. The user can now split that block at a chosen (or detected)
# time point: the task table gets two lines, and step 10/11 already synthesise
# and schedule one wav per line. The srt cursor stays on SOURCE blocks.
# ---------------------------------------------------------------------------

SWITCH_WIN = 0.45           # sliding window for speaker-switch detection (s)
SWITCH_HOP = 0.10
SWITCH_MIN_MARGIN = 0.04    # sum of top1-top2 margins on both sides of a flip


def _speaker_protos(data: dict):
    """(names, matrix) of CAMPPlus prototypes from the representative clips."""
    protos = {}
    for name, info in (data.get("speakers") or {}).items():
        ref = str(info.get("ref", ""))
        if os.path.isfile(ref):
            got = _extract_embedding(ref)
            if got:
                protos[name] = got[0]
    if len(protos) < 2:
        raise ValueError("至少需要两位说话人的有效代表音色，才能自动找切换点")
    names = list(protos)
    return names, np.vstack([protos[n] for n in names])


def _find_switch_point(y, sr, P, total: float):
    """Sliding-window timbre vote; return the strongest speaker flip in the
    middle 50% of the clip, or None when no convincing change-over exists.
    Never guesses: a None just means "give the human the manual slider".
    """
    if total < 0.9 or len(y) / sr < 0.9:
        return None
    scores = []
    t = 0.0
    limit = min(total, len(y) / sr)
    while t + SWITCH_WIN <= limit:
        got = _embedding_from_wave(y[int(t * sr):int((t + SWITCH_WIN) * sr)], sr)
        if got:
            sims = P @ got[0]
            order = np.argsort(-sims)
            label = int(order[0])
            margin = (float(sims[order[0]] - sims[order[1]])
                      if len(order) > 1 else 1.0)
            scores.append((t + SWITCH_WIN / 2, label, margin))
        t += SWITCH_HOP
    if len(scores) < 4:
        return None
    lo, hi = total * 0.25, total * 0.75
    best = None
    for a, b in zip(scores, scores[1:]):
        mid = (a[0] + b[0]) / 2
        if not lo <= mid <= hi or a[1] == b[1]:
            continue
        strength = a[2] + b[2]
        if strength < SWITCH_MIN_MARGIN:
            continue
        if best is None or strength > best[0]:
            best = (strength, mid)
    return best[1] if best else None


def _split_text_at(text: str, ratio: float):
    """Cut a subtitle near ``ratio``, preferring punctuation/space boundaries."""
    s = str(text).strip()
    n = len(s)
    if n < 6:
        return None
    lo, hi = max(2, int(n * 0.2)), min(n - 2, int(n * 0.8))
    target = max(lo, min(hi, int(n * ratio)))
    natural = [i + 1 for i, c in enumerate(s)
               if c.isspace() or c in "，。！？；、,.!?;:"]
    cands = [i for i in natural if lo <= i <= hi]
    cut = min(cands, key=lambda i: abs(i - target)) if cands else target
    a, b = s[:cut].strip(), s[cut:].strip()
    if len(a) < 2 or len(b) < 2:
        return None
    return [a, b]


def split_rows(changes: dict) -> dict:
    """Split one-subtitle task rows in two.

    ``changes = {number: at_seconds | None}`` — a number is seconds from the
    start of that row's vocal clip; ``None`` asks CAMPPlus to find the speaker
    change-over. Writes both ``tts_tasks.xlsx`` (the ``lines`` column) and the
    contract's ``chunk_splits``, then re-judges the new sub-lines.
    Only rows with exactly one source subtitle are eligible.
    """
    try:
        import soundfile as sf  # noqa: F401
        import torch  # noqa: F401
        import torchaudio  # noqa: F401
    except ImportError as e:
        raise ValueError(
            f"子行拆分缺少声纹依赖 {e.name}；请在运行 VideoLingo server 的 (vl) "
            "环境中调用。") from e

    data = _read_contract_raw()
    df = _read_task_df()
    windows = subline_windows(df)
    _, P = _speaker_protos(data)
    splits = dict(data.get("chunk_splits") or {})
    stat = {"split": [], "skipped": {}, "at": {}}

    for raw_num, raw_at in (changes or {}).items():
        num = str(int(raw_num))
        found = df.index[df["number"].astype(int) == int(num)]
        if not len(found):
            stat["skipped"][num] = "任务表里没有这句"
            continue
        i = found[0]
        lines = _task_lines(df.at[i, "lines"])
        old = splits.get(num)
        active = bool(old and [str(x) for x in old.get("lines", [])]
                      == [str(x) for x in lines])
        if active:
            original = str(old.get("original") or " ".join(lines))
            n_src = max(1, int(old.get("n_src", 1)))
        elif len(lines) == 1:
            original, n_src = str(lines[0]), 1
        else:
            stat["skipped"][num] = f"这句已有 {len(lines)} 条原始字幕，无需拆分"
            continue

        wins = (windows.get(num) or {}).get("windows") or []
        if not wins:
            stat["skipped"][num] = "找不到这句的时间窗"
            continue
        a0, b0 = float(wins[0][0]), float(wins[-1][1])
        span = b0 - a0
        if span < 0.8:
            stat["skipped"][num] = "这句太短，拆开后每段不足 0.4 秒"
            continue

        path = os.path.join(_AUDIO_REFERS_DIR, f"{num}.wav")
        y = sr = None
        if os.path.isfile(path):
            try:
                y, sr = sf.read(path, dtype="float32", always_2d=False)
            except Exception:
                y = None
        at = None
        if raw_at is not None:
            at = float(raw_at)
        elif y is not None:
            at = _find_switch_point(y, sr, P, len(y) / sr)
        if at is None:
            stat["skipped"][num] = "没找到明显的说话人切换点，请在展开区手填切点秒数"
            continue
        at = min(max(at, a0 + 0.25), b0 - 0.25)
        parts = _split_text_at(original, (at - a0) / span)
        if not parts:
            stat["skipped"][num] = "文本太短，无法按这个切点拆成两段"
            continue

        df.at[i, "lines"] = str(parts)
        splits[num] = {"at": round(at, 3), "n_src": n_src,
                       "lines": parts, "original": original,
                       "source": "manual" if raw_at is not None else "voice"}
        (data.get("chunk_speakers") or {}).pop(num, None)
        (data.get("chunk_meta") or {}).pop(num, None)
        stat["split"].append(int(num))
        stat["at"][num] = round(at, 3)

    if stat["split"]:
        tmp = _8_1_AUDIO_TASK + ".tmp"
        df.to_excel(tmp, index=False)
        os.replace(tmp, _8_1_AUDIO_TASK)
        data["chunk_splits"] = splits
        _write_contract_raw(data)
        _invalidate_consumer_cache()
        stat["judge"] = build_chunk_speakers(
            force=True, numbers=stat["split"])
    return stat


def apply_chunk_overrides(changes: dict) -> dict:
    """Human final say on sub-lines: ``{number: {line_index: speaker}}``."""
    data = _read_contract_raw()
    cs = data.setdefault("chunk_speakers", {})
    cm = data.setdefault("chunk_meta", {})
    windows = subline_windows()
    count = 0
    for raw_num, per_line in (changes or {}).items():
        num = str(int(raw_num))
        if num not in windows:
            raise KeyError(f"第 {num} 句不在当前任务表中")
        n = len(windows[num]["lines"])
        for raw_idx, name in (per_line or {}).items():
            idx = str(int(raw_idx))
            if not 0 <= int(raw_idx) < n:
                raise KeyError(f"第 {num} 句没有子行 {idx}")
            if name not in data["speakers"]:
                raise KeyError(f"说话人 {name} 不存在")
            cs.setdefault(num, {})[idx] = name
            cm.setdefault(num, {}).setdefault(idx, {}).update(
                {"confidence": 1.0, "flags": [], "manual": True})
            count += 1
    _write_contract_raw(data)
    _invalidate_consumer_cache()
    return {"ok": True, "changed": count}


def maybe_build_voice_map() -> None:
    """Hook for pipeline step 9. Strict no-op unless refer_mode == 4."""
    try:
        from core.utils.config_utils import load_key

        if load_key("tts_method") != "index_tts":
            return
        if int(load_key("index_tts.refer_mode")) != 4:
            return
    except (KeyError, ValueError, TypeError):
        return
    build_voice_map()


def self_check() -> None:
    print("=== voice_map 自检（不加载模型、不占 GPU）===")
    print(f"  契约路径 : {os.path.abspath(VOICE_MAP_FILE)}")
    try:
        df = _read_task_df()
        print(f"  任务表   : {len(df)} 行, fingerprint={_fingerprint(df)}")
    except Exception as e:
        print(f"  任务表   : ERROR {type(e).__name__}: {e}")
    n_ref = 0
    if os.path.isdir(_AUDIO_REFERS_DIR):
        n_ref = len([x for x in os.listdir(_AUDIO_REFERS_DIR)
                     if x.endswith(".wav")])
    print(f"  原声切片 : {n_ref} 个 wav")
    if os.path.isfile(VOICE_MAP_FILE):
        try:
            data = load_voice_map()
            print(f"  契约     : {data['n_speakers']} 人 | "
                  f"silhouette={data['silhouette']} | degraded={data['degraded']} | "
                  f"producer={data['producer']}")
            for name, info in data["speakers"].items():
                print(f"    {name}: {info['n_clips']} 句, "
                      f"代表 #{info['ref_source']} ({info['ref_duration']}s), "
                      f"conf={info['confidence']}")
        except Exception as e:
            print(f"  契约     : ERROR {type(e).__name__}: {e}")
    else:
        print("  契约     : 尚未生成（执行 --build）")
    print("=== 自检结束 ===")


def _run_samples(per_speaker: int) -> None:
    """A/B listening set: mode 4 voice vs the current single-clip baseline."""
    import torch

    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()
        used_gib = (total - free) / (1024 ** 3)
        if used_gib > 6.0:
            rprint(f"[red][voice_map] 显存已占用 {used_gib:.1f} GiB"
                   "（配音流水线或 webui 仍在跑）。请先停止再试听，避免 OOM。[/red]")
            return

    data = build_voice_map()
    df = _read_task_df()
    os.makedirs(VOICE_EXP_DIR, exist_ok=True)

    from core.tts_backend.index_tts import _get_engine, _read_cfg, _resolve_lang

    cfg = _read_cfg()
    lang = _resolve_lang(cfg["lang"])
    engine = _get_engine(cfg)
    baseline_ref = os.path.join(_AUDIO_REFERS_DIR, "1.wav")

    for name, info in data["speakers"].items():
        nums = [int(k) for k, v in data["segments"].items() if v == name]
        if len(nums) > per_speaker:
            step = max(1, len(nums) // per_speaker)
            nums = nums[::step][:per_speaker]
        for num in nums:
            row = df[df["number"] == num]
            if row.empty:
                continue
            text = str(row["text"].iloc[0])
            out4 = os.path.join(VOICE_EXP_DIR, f"m4_{name}_{num}.wav")
            engine.infer(spk_audio_prompt=info["ref"], text=text,
                         output_path=out4, lang=lang,
                         use_emo_text=cfg["use_emo_text"], verbose=False)
            out2 = os.path.join(VOICE_EXP_DIR, f"m2_baseline_{num}.wav")
            engine.infer(spk_audio_prompt=baseline_ref, text=text,
                         output_path=out2, lang=lang,
                         use_emo_text=cfg["use_emo_text"], verbose=False)
            rprint(f"[voice_map] A/B 样本: {out4}  vs  {out2}")

    rprint(f"[voice_map] 试听样本已生成到 {os.path.abspath(VOICE_EXP_DIR)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    sample_args = [a for a in args if a.startswith("--samples")]
    if sample_args:
        n = 3
        for a in sample_args:
            if a.startswith("--samples="):
                n = max(1, int(a.split("=", 1)[1]))
        _run_samples(n)
    elif "--build" in args or "--force" in args:
        build_voice_map(force="--force" in args)
    else:
        self_check()
