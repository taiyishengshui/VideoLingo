import os
# Must be set BEFORE torch initialises CUDA: expandable segments let the
# allocator return/grow blocks instead of fragmenting into slivers. PyTorch
# itself recommends it when the OOM report shows large reserved-but-unallocated
# memory. A user-set value always wins.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import gc
import json
import warnings
import time
import subprocess
import numpy as np
import torch
import functools
from pathlib import Path

warnings.filterwarnings("ignore")

# =============================================================================
# Compatibility shim — applied BEFORE importing whisperx
# =============================================================================

# torch.load: default weights_only=False for pyannote checkpoints
# PyTorch >=2.6 changed torch.load default to weights_only=True.
# pyannote checkpoints contain omegaconf objects that fail the safety check.
# Monkey-patch torch.load to default to weights_only=False (matching <2.6
# behavior).  This is safe here because all model files come from trusted
# sources (HuggingFace / pyannote).
_original_torch_load = torch.load
@functools.wraps(_original_torch_load)
def _patched_torch_load(*args, **kwargs):
    if kwargs.get("weights_only") is None:
        kwargs["weights_only"] = False
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

# =============================================================================
# Now safe to import whisperx and the rest of the application
# =============================================================================
import whisperx
from whisperx.audio import SAMPLE_RATE as _WHISPERX_SR
from core.utils.log_hooks import rprint
from core.utils import *
from core.asr_backend.vad_gap_fill import gap_fill_settings, fill_gaps, prompt_echo
# model_dir in config.yaml may be relative ('./_model_cache') — anchor it to
# the PROJECT ROOT, not the cwd: batch jobs run with cwd = per-video workspace.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_model_dir_cfg = str(load_key("model_dir"))
MODEL_DIR = _model_dir_cfg if os.path.isabs(_model_dir_cfg) else os.path.join(PROJECT_ROOT, _model_dir_cfg)


def _hf_cache_dir_for_repo(cache_root, repo_id):
    return Path(cache_root) / f"models--{repo_id.replace('/', '--')}"


def _find_local_hf_model(repo_id):
    """Locate a fully downloaded HF model in the local caches (no network).

    Checks, in order: a user-set HF_HOME/HF_HUB_CACHE, the global HuggingFace
    cache (where whisperx/faster-whisper normally download to), then the
    project's own model_dir. Returns the snapshot directory on success, or
    None if no complete copy is found.
    """
    required_files = {"config.json", "model.bin", "tokenizer.json"}
    candidates = []
    hf_home = os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME")
    if hf_home:
        base = Path(hf_home)
        candidates.append(base / "hub" if (base / "hub").exists() else base)
    candidates.append(Path.home() / ".cache" / "huggingface" / "hub")
    candidates.append(Path(MODEL_DIR))
    for base in candidates:
        repo_dir = _hf_cache_dir_for_repo(base, repo_id)
        snapshots = repo_dir / "snapshots"
        if not snapshots.exists():
            continue
        for snapshot in snapshots.iterdir():
            if snapshot.is_dir() and all((snapshot / name).exists() for name in required_files):
                return snapshot
    return None

@except_handler("failed to check hf mirror", default_return=None)
def check_hf_mirror():
    mirrors = {'Official': 'huggingface.co', 'Mirror': 'hf-mirror.com'}
    fastest_url = f"https://{mirrors['Official']}"
    best_time = float('inf')
    rprint("[cyan]🔍 Checking HuggingFace mirrors...[/cyan]")
    for name, domain in mirrors.items():
        if os.name == 'nt':
            cmd = ['ping', '-n', '1', '-w', '3000', domain]
        else:
            cmd = ['ping', '-c', '1', '-W', '3', domain]
        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True)
        response_time = time.time() - start
        if result.returncode == 0:
            if response_time < best_time:
                best_time = response_time
                fastest_url = f"https://{domain}"
            rprint(f"[green]✓ {name}:[/green] {response_time:.2f}s")
    if best_time == float('inf'):
        rprint("[yellow]⚠️ All mirrors failed, using default[/yellow]")
    rprint(f"[cyan]🚀 Selected mirror:[/cyan] {fastest_url} ({best_time:.2f}s)")
    return fastest_url

def _apply_hf_endpoint(mirror_url):
    """Make the selected HF mirror actually take effect at runtime.

    HF_ENDPOINT is only read when huggingface_hub is imported — and whisperx
    imports it at module load — so setting the env var here is a no-op.
    Patch the live constants instead so snapshot_download hits the mirror.
    """
    endpoint = mirror_url or "https://huggingface.co"
    os.environ["HF_ENDPOINT"] = endpoint
    if endpoint == "https://huggingface.co":
        return
    try:
        from huggingface_hub import constants
        constants.ENDPOINT = endpoint
        if hasattr(constants, "HUGGINGFACE_CO_URL_TEMPLATE"):
            constants.HUGGINGFACE_CO_URL_TEMPLATE = endpoint + "/{repo_id}/resolve/{revision}/{filename}"
        if hasattr(constants, "HUGGINGFACE_CO_RESOLVE_ENDPOINT"):
            constants.HUGGINGFACE_CO_RESOLVE_ENDPOINT = endpoint
        # hf-mirror.com is designed for direct (no-proxy) access; bypass the
        # system proxy, which may be down (e.g. 127.0.0.1:10808 refused).
        no_proxy = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
        domains = {d.strip() for d in no_proxy.split(",") if d.strip()}
        domains.add("hf-mirror.com")
        merged = ",".join(sorted(domains))
        os.environ["NO_PROXY"] = merged
        os.environ["no_proxy"] = merged
        rprint(f"[green]✓ HF endpoint patched to {endpoint} (direct, proxy bypassed)[/green]")
    except Exception as e:
        rprint(f"[yellow]⚠️ Failed to patch HF endpoint: {e}[/yellow]")

# =============================================================================
# Model session cache — load the whisper model ONCE per process, reuse it for
# every segment (and the domain probe).
#
# Why: on Windows/WDDM each load→del→empty_cache cycle returns MOST GPU
# memory to the system but not all of it. Over ~8 cycles (probe + 22 segments)
# the leaked slivers add up until the transient decode peak (beam×batch) can't
# find a contiguous block → "CUDA failed with error out of memory" killing a
# run that was only 27% done. One persistent model removes the leak source
# AND saves 30–60s of reload time per segment.
# If asr_options change (probe has no hotwords, main loop does), the old
# model is fully released BEFORE the new one loads — never two at once.
# =============================================================================

_MODEL_CACHE = {}
# language_code -> (model_a, metadata); the wav2vec2 aligner is small but was
# being reloaded 22 times (once per segment) — same WDDM leak pattern.
_ALIGN_CACHE = {}

# Process-wide run state. Kept in a dict so functions can mutate it without
# `global` declarations (a late `global` after a read is a SyntaxError, and
# this file has hit enough of those tonight).
#   last_good_batch: batch size that survived an OOM backoff — later segments
#       start there instead of repeating the same OOM dance (every OOM risks
#       poisoning the CUDA context).
#   cpu_fallback:    once CUDA proves unusable (context corruption), ALL
#       remaining segments run on CPU. Slow, but the run COMPLETES —
#       checkpoint keeps every finished segment either way.
#   vad_batch:       pyannote VAD batch size that survived so far. whisperx
#       hardcodes 32 at build time and it is NOT affected by the whisper
#       batch_size we pass to model.transcribe — so it needs its own knob.
#   vad_on_cpu:      VAD has been moved off the GPU (last resort before the
#       whole run falls back to CPU).
_RUN_STATE = {"last_good_batch": None, "cpu_fallback": False,
              "vad_batch": None, "vad_on_cpu": False}

# Errors that mean the CUDA context INSIDE CTranslate2 is corrupted (typically
# after an OOM inside parallel_for): every subsequent CUDA call on that
# engine instance fails with one of these. torch usually survives; the
# recovery is to rebuild the engine (fresh device handles), not to retry.
_STICKY_CUDA_ERRORS = (
    "invaliddeviceordinal",
    "illegaladdress",
    "illegalmemoryaccess",
    "unspecifiedlaunchfailure",
    "devicesideassert",
)


def _vad_batch_size_for(free_mem_gb: float) -> int:
    """Pick a pyannote VAD batch size that fits the CURRENTLY free VRAM.

    whisperx builds the VAD with pyannote's default batch_size=32, which is far
    too big when another process holds most of the card (or when the resident
    whisper model already claimed its share). The VAD LSTM's peak scales with
    the batch, so this is the knob that actually decides whether VAD OOMs.
    """
    if free_mem_gb >= 4.0:
        return 32
    if free_mem_gb >= 2.5:
        return 16
    if free_mem_gb >= 1.5:
        return 8
    if free_mem_gb >= 0.8:
        return 4
    return 2


def _apply_vad_batch_size(model, batch_size: int) -> bool:
    """Retune the already-built pyannote VAD to `batch_size`.

    whisperx never exposes this (load_vad_model() hardcodes the default), so we
    reach into the pipeline: model.vad_model.vad_pipeline._segmentation is the
    pyannote Inference object, and its `batch_size` is read on every slide()
    pass — assigning it takes effect immediately, no reload needed.
    Returns False when the layout is unexpected (different whisperx version),
    in which case callers should just carry on with the default.
    """
    try:
        inference = getattr(getattr(getattr(model, "vad_model", None),
                                    "vad_pipeline", None), "_segmentation", None)
        if inference is None or not hasattr(inference, "batch_size"):
            return False
        old = inference.batch_size
        if old != batch_size:
            inference.batch_size = batch_size
            rprint(f"[cyan]🎚️ VAD batch_size:[/cyan] {old} → {batch_size}")
        _RUN_STATE["vad_batch"] = batch_size
        return True
    except Exception as e:
        rprint(f"[yellow]⚠️ Could not retune VAD batch size: {e}[/yellow]")
        return False


def _vad_to_cpu(model) -> bool:
    """Move ONLY the VAD off the GPU (last resort before a full CPU fallback).

    VAD is cheap on CPU and whisper is not, so when VRAM is too tight even for
    VAD batch_size=1 this keeps transcription on CUDA. Rebuilds the pyannote
    Inference on CPU, preserving whisperx's pre_aggregation_hook.
    """
    try:
        from pyannote.audio.core.inference import Inference
        pipe = getattr(getattr(model, "vad_model", None), "vad_pipeline", None)
        old = getattr(pipe, "_segmentation", None)
        if old is None:
            return False
        seg_model = getattr(pipe, "segmentation", None)
        if not isinstance(seg_model, torch.nn.Module):
            seg_model = getattr(old, "model", None)
        if seg_model is None:
            return False
        kwargs = {"device": torch.device("cpu"), "batch_size": 8}
        hook = getattr(old, "pre_aggregation_hook", None)
        if hook is not None:
            kwargs["pre_aggregation_hook"] = hook
        pipe._segmentation = Inference(seg_model, **kwargs)
        _RUN_STATE["vad_on_cpu"] = True
        _RUN_STATE["vad_batch"] = None  # no longer a GPU constraint
        rprint("[yellow]⚠️ VAD moved to CPU (VRAM too tight) — whisper stays on CUDA.[/yellow]")
        return True
    except Exception as e:
        rprint(f"[yellow]⚠️ Could not move VAD to CPU: {e}[/yellow]")
        return False


def _is_sticky_cuda_error(err) -> bool:
    # normalize both sides: lowercase, drop spaces/underscores/hyphens, so
    # "cudaErrorInvalidDevice: invalid device ordinal" and "CUDA_ERROR_ILLEGAL_ADDRESS"
    # all match their pattern regardless of spelling/space variant
    msg = str(err).lower().replace(" ", "").replace("_", "").replace("-", "")
    return any(p in msg for p in _STICKY_CUDA_ERRORS)


def _safe_cuda_cleanup():
    """empty_cache/synchronize, but never raise: these get called INSIDE error
    recovery paths, where a second exception would mask the original and
    break the fallback ladder. A poisoned context can make even torch's
    cleanup calls fail — swallow and continue."""
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except Exception:
        pass

def _release_cached_model():
    global _MODEL_CACHE
    if _MODEL_CACHE.get("model") is not None:
        del _MODEL_CACHE["model"]
        _MODEL_CACHE.clear()
        gc.collect()
        _safe_cuda_cleanup()
    # NOTE: run-state (last_good_batch / cpu_fallback) intentionally survives
    # an engine rebuild — the VRAM pressure that caused the OOM was real.

def _get_or_load_model(model_name, device, compute_type, language, vad_options, asr_options,
                       force_reload=False):
    global _MODEL_CACHE
    # asr_options is part of the cache key: the domain probe runs WITHOUT
    # hotwords, the main loop WITH them → exactly one swap (release old,
    # load new, never two models resident at once), then all following
    # segments hit the cache. Simpler and more robust than mutating
    # TranscriptionOptions in place.
    key = json.dumps([model_name, device, compute_type, language, asr_options or {}],
                     sort_keys=True, ensure_ascii=False)
    if not force_reload:
        cached_model = _MODEL_CACHE.get("model")
        if cached_model is not None and _MODEL_CACHE.get("key") == key:
            return cached_model

    # different model/config (e.g. probe without hints → main loop with hints):
    # fully free the old one first — never hold two models in VRAM at once
    _release_cached_model()
    rprint(f"[cyan]📦 Loading whisper model (kept resident for all segments):[/cyan] {model_name}")
    model = whisperx.load_model(
        model_name,
        device=device,
        compute_type=compute_type,
        language=language,
        vad_options=vad_options,
        asr_options=dict(asr_options or {}),
    )
    _MODEL_CACHE = {"model": model, "key": key}
    return model


@except_handler("WhisperX processing error:")
def transcribe_audio(raw_audio_file, vocal_audio_file, start, end, asr_hints=None):
    WHISPER_LANGUAGE = load_key("whisper.language")
    # CPU fallback is sticky for the whole process: once CUDA proved unusable
    # (context corruption), don't reload a CUDA model each segment only to
    # throw it away again.
    device = "cuda" if torch.cuda.is_available() and not _RUN_STATE["cpu_fallback"] else "cpu"
    free_mem = None  # filled in below on CUDA; used by the OOM backoff ladder
    rprint(f"🚀 Starting WhisperX using device: {device} ...")
    
    if device == "cuda":
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        compute_type = "float16" if torch.cuda.is_bf16_supported() else "int8"
        rprint(f"[cyan]🎮 GPU memory:[/cyan] total {gpu_mem:.2f} GB, [cyan]⚙️ Compute type:[/cyan] {compute_type}")
        batch_size = None  # decided AFTER the resident model claims its VRAM
    else:
        batch_size = 1
        compute_type = "int8"
        rprint(f"[cyan]📦 Batch size:[/cyan] {batch_size}, [cyan]⚙️ Compute type:[/cyan] {compute_type}")
    rprint(f"[green]▶️ Starting WhisperX for segment {start:.2f}s to {end:.2f}s...[/green]")

    if WHISPER_LANGUAGE == 'zh':
        model_name = "Huan69/Belle-whisper-large-v3-zh-punct-fasterwhisper"
        local_model = os.path.join(MODEL_DIR, "Belle-whisper-large-v3-zh-punct-fasterwhisper")
    else:
        model_name = load_key("whisper.model")
        local_model = os.path.join(MODEL_DIR, model_name)
    ######
    # Resolve repo_id and check for a fully downloaded local copy FIRST.
    # Only touch the network (mirror check + download) when nothing usable
    # exists locally.
    repo_id = model_name if "/" in model_name else f"Systran/faster-whisper-{model_name}"
    local_snapshot = _find_local_hf_model(repo_id)
    download_root = None
    if local_snapshot is not None:
        rprint(f"[green]📥 Loading local WHISPER model (HF cache):[/green] {local_snapshot} ...")
        model_name = str(local_snapshot)
    elif os.path.exists(local_model):
        rprint(f"[green]📥 Loading local WHISPER model:[/green] {local_model} ...")
        model_name = local_model
    else:
        rprint(f"[green]📥 No local copy of {repo_id} found — downloading from HuggingFace...[/green]")
        _apply_hf_endpoint(check_hf_mirror())
        # 始终使用全局 HuggingFace 缓存，避免项目本地缓存不完整导致重复下载

    ######    

    vad_options = {"vad_onset": 0.500,"vad_offset": 0.363}
    hints = asr_hints or {}
    from core.services.asr_service import build_asr_options
    asr_options = build_asr_options(hints)
    rprint(f"[cyan]ASR解码：beam={asr_options['beam_size']}，"
           f"重复限制={asr_options['no_repeat_ngram_size']}，"
           f"领域提示={'开' if asr_options['initial_prompt'] else '关'}，"
           f"热词={'开' if asr_options['hotwords'] else '关'}[/cyan]")
    whisper_language = None if 'auto' in WHISPER_LANGUAGE else WHISPER_LANGUAGE
    rprint("[bold yellow] You can ignore warning of `Model was trained with torch 1.10.0+cu102, yours is 2.0.0+cu118...`[/bold yellow]")
    model = _get_or_load_model(model_name, device, compute_type, whisper_language,
                               vad_options, asr_options)

    # Batch size follows FREE (not total) VRAM measured AFTER the resident
    # model claimed its share: Windows desktop/browser can already hold a
    # chunk of the card, and beam 8 transient decode peaks on top of the
    # weights need headroom. Measured fresh per segment.
    # 4.5GB gate for batch 16: 12GB cards running float16 + beam 8 measured
    # OOM'ing at ~3.0GB free on speech-dense segments (18:19 & 18:48 runs),
    # both dying ~25% into a segment at batch 16. 8 needs ~half the decode
    # peak of 16 and has never OOM'd on this machine.
    if device == "cuda":
        try:
            free_mem = torch.cuda.mem_get_info(0)[0] / (1024**3)
        except Exception:
            free_mem = gpu_mem
        if free_mem >= 4.5:
            batch_size = 16
        elif free_mem >= 1.5:
            batch_size = 8
        elif free_mem >= 0.8:
            batch_size = 4
        else:
            batch_size = 2
        rprint(f"[cyan]📦 Batch size:[/cyan] {batch_size} (free VRAM {free_mem:.2f} GB)")
        # The VAD runs INSIDE model.transcribe() with its own batch size (32 by
        # default, invisible to the `batch_size` argument above) — size it too,
        # or VAD is the thing that OOMs on a crowded card.
        _apply_vad_batch_size(model, _RUN_STATE["vad_batch"] or _vad_batch_size_for(free_mem))
        if free_mem < 0.35 * gpu_mem:
            rprint(f"[yellow]⚠️ Only {free_mem:.2f} GB of {gpu_mem:.2f} GB VRAM is free — "
                   f"something else is holding this GPU (a crashed run left behind? "
                   f"browser hardware acceleration?). Check with: nvidia-smi[/yellow]")

    def load_audio_segment(audio_file, start, end):
        # Decode ONLY the [start, end) window via ffmpeg (-ss/-t) instead of
        # loading the whole file: whisperx's load_audio() decodes the ENTIRE
        # audio into memory, so a 10h+ file costs ~2.3 GiB per call (and two
        # calls per segment). A 30-min window at 16kHz mono is only ~115 MB.
        # Same decode pipeline as whisperx.audio.load_audio (s16le mono 16kHz),
        # just time-bounded.
        duration = max(0.0, end - start)
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-threads", "0",
            # input seek before -i is fast (keyframe) and sample-accurate
            # enough here: segment boundaries sit at silence points with a
            # 0.5s margin, and transcription errors at a boundary are capped
            # to the segment overlap.
            "-ss", f"{start:.3f}",
            "-i", audio_file,
            "-t", f"{duration:.3f}",
            "-f", "s16le",
            "-ac", "1",
            "-acodec", "pcm_s16le",
            "-ar", str(_WHISPERX_SR),
            "-",
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, check=True).stdout
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to load audio segment [{start:.2f}-{end:.2f}s]: {e.stderr.decode(errors='ignore')}") from e
        return np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0

    raw_audio_segment = load_audio_segment(raw_audio_file, start, end)
    vocal_audio_segment = load_audio_segment(vocal_audio_file, start, end)
    
    # -------------------------
    # 1. transcribe raw audio
    # -------------------------
    # Failure ladder (observed on this machine, 18:19 run):
    #  a) CUDA OOM inside decode (beam×batch peak) → halve batch, retry on
    #     the SAME engine — a cleanly-raised OOM leaves the context healthy.
    #  b) "invalid device ordinal" right after an OOM → CTranslate2's cached
    #     CUDA handles are poisoned. Retrying on the same engine is hopeless;
    #     rebuild the engine (release + reload), halve batch, retry once.
    #  c) rebuild still fails → give up on CUDA for the whole run: transcribe
    #     on CPU with a small batch. Slow, but this run COMPLETES (the
    #     checkpoint keeps every finished segment either way).
    transcribe_start_time = time.time()
    rprint("[bold green]Note: You will see Progress if working correctly ↓[/bold green]")
    if _RUN_STATE["cpu_fallback"]:
        bs = 1
        rprint("[yellow]🔽 CPU fallback active for this segment (CUDA abandoned for the run).[/yellow]")
    else:
        bs = _RUN_STATE["last_good_batch"] or batch_size
    engine_reloads = 0
    while True:
        try:
            result = model.transcribe(raw_audio_segment, batch_size=bs, print_progress=True)
            break
        except (RuntimeError, MemoryError) as e:
            # MemoryError is what pyannote's VAD raises on CUDA OOM
            # ("batch_size (32) is probably too large") — catching only
            # RuntimeError let it sail straight through the whole ladder.
            # sticky FIRST: "invalid device ordinal" and friends do NOT carry
            # "memory", but they mean the engine's CUDA context is poisoned —
            # they must be caught before the OOM check or they'd slip through.
            if isinstance(e, RuntimeError) and _is_sticky_cuda_error(e):
                if engine_reloads >= 1:
                    # one rebuild already failed → CUDA is a lost cause for
                    # this process. Fall back to CPU for this and all later
                    # segments rather than losing the run.
                    rprint("[red]❌ CUDA unusable after engine rebuild — switching this "
                           "run to CPU transcription (slower, but it completes).[/red]")
                    _RUN_STATE["cpu_fallback"] = True
                    model = _get_or_load_model(model_name, "cpu", compute_type="int8",
                                               language=whisper_language, vad_options=vad_options,
                                               asr_options=asr_options, force_reload=True)
                    bs = 1
                    continue
                engine_reloads += 1
                rprint("[yellow]⚠️ CUDA context corrupted after OOM (engine handles "
                       "poisoned) — rebuilding whisper engine...[/yellow]")
                try:
                    model = _get_or_load_model(model_name, device, compute_type,
                                               whisper_language, vad_options,
                                               asr_options, force_reload=True)
                    # A rebuilt engine ships a fresh VAD at the default 32 —
                    # re-apply whatever batch size actually survived.
                    _apply_vad_batch_size(
                        model, _RUN_STATE["vad_batch"] or _vad_batch_size_for(free_mem or 1.0))
                except Exception as rebuild_err:
                    # rebuild itself failed (VRAM still wedged) → straight to CPU
                    rprint(f"[red]❌ Engine rebuild failed too ({rebuild_err}) — "
                           f"switching to CPU transcription.[/red]")
                    _RUN_STATE["cpu_fallback"] = True
                    model = _get_or_load_model(model_name, "cpu", compute_type="int8",
                                               language=whisper_language, vad_options=vad_options,
                                               asr_options=asr_options, force_reload=True)
                    bs = 1
                    continue
                bs = max(1, bs // 2)
                rprint(f"[yellow]⚠️ Retrying segment at batch_size={bs} with a fresh engine.[/yellow]")
                continue
            msg = str(e).lower()
            was_cuda_oom = device == "cuda" and (
                isinstance(e, MemoryError) or "memory" in msg)
            if not was_cuda_oom:
                raise
            # ordinary OOM with healthy context: halve BOTH knobs and retry.
            # Halving only the whisper batch is a no-op when the OOM came from
            # the VAD (its batch size is independent).
            bs_next = max(1, bs // 2)
            vad_cur = _RUN_STATE["vad_batch"] or 32
            vad_next = max(1, vad_cur // 2)
            _safe_cuda_cleanup()
            if bs_next == bs and vad_next == vad_cur and not _vad_to_cpu(model):
                raise  # both floors reached and VAD can't move off GPU
            if vad_next != vad_cur:
                _apply_vad_batch_size(model, vad_next)
            rprint(f"[yellow]⚠️ CUDA OOM — retrying with whisper batch_size={bs_next}, "
                    f"VAD batch_size={vad_next} (VRAM pressure).[/yellow]")
            bs = bs_next
            continue
    # Remember the batch size that actually survived: when memory is empty,
    # `cap or bs` evaluates to bs making `bs < bs` always False (the bug —
    # the cap was never stored, so every segment re-ran the full OOM dance).
    # Correct logic: store whenever we have no cap yet, or the new value is
    # LOWER than an existing cap (ratchets down, never up — VRAM pressure
    # that bit once will bite again).
    cap = _RUN_STATE["last_good_batch"]
    if cap is None or bs < cap:
        _RUN_STATE["last_good_batch"] = bs
        rprint(f"[cyan]ℹ️ Capping future segments at batch_size={bs} after OOM backoff.[/cyan]")
    transcribe_time = time.time() - transcribe_start_time
    rprint(f"[cyan]⏱️ time transcribe:[/cyan] {transcribe_time:.2f}s")

    # model stays RESIDENT for the next segment (that's the fix — reloading
    # per segment leaked VRAM on WDDM until the 7th segment OOM'd). Only the
    # transient decode buffers need clearing.

    # Save language
    update_key("whisper.language", result['language'])
    # detected_language drives spacy model choice, prompts (src language) and
    # joiner logic downstream. Keep it in sync with what whisper actually
    # detected, otherwise 'auto' runs leave a stale value in config.
    update_key("whisper.detected_language", result['language'])
    if result['language'] == 'zh' and WHISPER_LANGUAGE != 'zh':
        raise ValueError("Please specify the transcription language as zh and try again!")

    # -------------------------
    # 2. align by vocal audio
    # -------------------------
    align_start_time = time.time()
    # Align timestamps using vocal audio — align model is cached per language
    # (it was ALSO being reloaded per segment, another VRAM leak source).
    # Follow the run's effective device: if CUDA was abandoned during
    # transcription, align on CPU too (no point betting on a broken context).
    align_device = "cpu" if _RUN_STATE["cpu_fallback"] or device == "cpu" else device
    cached = _ALIGN_CACHE.get(result["language"])
    if cached is not None:
        model_a, metadata = cached
    else:
        model_a, metadata = whisperx.load_align_model(language_code=result["language"], device=align_device)
        _ALIGN_CACHE[result["language"]] = (model_a, metadata)
    try:
        result = whisperx.align(result["segments"], model_a, metadata, vocal_audio_segment, align_device, return_char_alignments=False)
    except RuntimeError as e:
        # CUDA trouble during alignment (OOM, or the poisoned-context sticky
        # errors) → redo this segment's alignment on CPU. The transcription
        # itself is already done and safe in `result`.
        cuda_trouble = _is_sticky_cuda_error(e) or (
            "memory" in str(e).lower() and align_device != "cpu")
        if cuda_trouble and align_device != "cpu":
            rprint("[yellow]⚠️ CUDA error during align — retrying alignment on CPU "
                   "(slower, but the transcription itself is already done).[/yellow]")
            _safe_cuda_cleanup()
            model_a_cpu, metadata_cpu = whisperx.load_align_model(language_code=result["language"], device="cpu")
            _ALIGN_CACHE[result["language"]] = (model_a_cpu, metadata_cpu)
            result = whisperx.align(result["segments"], model_a_cpu, metadata_cpu, vocal_audio_segment, "cpu", return_char_alignments=False)
        else:
            raise
    align_time = time.time() - align_start_time
    rprint(f"[cyan]⏱️ time align:[/cyan] {align_time:.2f}s")

    # align model stays cached too; only transient buffers get cleared
    _safe_cuda_cleanup()

    # -------------------------
    # 2.5 补回被 VAD 漏掉的语音
    # -------------------------
    # pyannote VAD 偶发把整段真实语音判成非语音 → whisper 根本没看到那段音频，
    # 字幕会整段缺失（实测 68 分钟播客漏 14 处 / 179.6 秒）。这里扫描「有声但
    # VAD 没覆盖」的空档，绕开 VAD 单独转写，补进结果。失败只警告，不影响已有转写。
    gf = gap_fill_settings()
    if gf["enabled"]:
        try:
            added = fill_gaps(
                model=model, segments=result['segments'],
                raw_audio_segment=raw_audio_segment,
                vocal_audio_segment=vocal_audio_segment,
                seg_start=start, sample_rate=_WHISPERX_SR,
                raw_audio_file=raw_audio_file,
                model_a=model_a, metadata=metadata, align_device=align_device,
                batch_size=bs, settings=gf,
                repair_spans=[s for s in result['segments']
                              if prompt_echo(s, hints.get("initial_prompt"))])
            if added:
                result['segments'].extend(added)
                result['segments'].sort(key=lambda s: s.get('start', 0.0))
                rprint(f"[green]🩹 VAD 漏检补转：补回 {len(added)} 段、"
                       f"{sum(s['end'] - s['start'] for s in added):.1f}s[/green]")
        except Exception as e:
            rprint(f"[yellow]⚠️ VAD 漏检补转失败（已有转写不受影响）：{e}[/yellow]")

    # Adjust timestamps
    for segment in result['segments']:
        segment['start'] += start
        segment['end'] += start
        # 补转段可能没有 words（align 失败时仍保留文本）—— 不能让它崩掉
        # 整批结果，已转好的部分必须活下来
        for word in segment.get('words') or []:
            if 'start' in word:
                word['start'] += start
            if 'end' in word:
                word['end'] += start
    return result
