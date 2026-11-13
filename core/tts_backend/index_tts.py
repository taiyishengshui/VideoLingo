"""IndexTTS 2.5 local dubbing backend for VideoLingo.

Design notes
------------
* The engine (~3.3 GB GPT weights + s2mel + vocoder) is loaded ONCE per
  process and kept resident. A module-level singleton plus two locks guard
  construction and inference.
* ``indextts`` is imported lazily inside ``_get_engine`` so starting
  VideoLingo (or using any other TTS backend) never pays the import cost
  and never fails when the plugin is not installed.
* All model paths MUST be absolute: VideoLingo's cwd is ``D:/code/vl``
  while index-tts' defaults are relative to ``D:/code/index-tts``.
* Never ``os.chdir`` here. VideoLingo resolves ``output/audio/...``
  relative to its own cwd from several threads.

Activation: set ``tts_method: 'index_tts'`` in config.yaml.
Do NOT run index-tts' own webui.py at the same time (GPU memory contention).
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from core.utils.config_utils import load_key
from core.utils.log_hooks import rprint
from core.utils.models import _AUDIO_REFERS_DIR

_DEFAULT_MODEL_DIR = r"D:/code/index-tts/checkpoints"
_DEFAULT_SPK_PROMPT = r"D:/code/index-tts/examples/voice_01.wav"

# target_language (VideoLingo UI) -> index-tts lang token.
# ZH / EN / JA are the well-tested paths; other languages may need
# extra text-normalization packages (nemo_text_processing).
_EXACT_LANG = {
    "简体中文": "ZH", "繁体中文": "ZH", "中文": "ZH", "普通话": "ZH",
    "chinese": "ZH", "mandarin": "ZH",
    "english": "EN", "英文": "EN", "英语": "EN",
    "日本語": "JA", "日语": "JA", "japanese": "JA",
    "한국어": "KO", "韩语": "KO", "korean": "KO",
    "español": "ES", "spanish": "ES", "西班牙语": "ES",
    "français": "FR", "french": "FR", "法语": "FR",
    "deutsch": "DE", "german": "DE", "德语": "DE",
    "русский": "RU", "russian": "RU", "俄语": "RU",
    "粤语": "YUE", "cantonese": "YUE",
    "arabic": "AR", "阿拉伯语": "AR",
}

_ENGINE = None
_ENGINE_INIT_LOCK = threading.Lock()
_INFER_LOCK = threading.Lock()
_REFER_LOCK = threading.Lock()
_REFER_EXTRACTED = False
_VOICE_LOGGED = False


def _read_cfg() -> dict:
    """Read the ``index_tts`` section, with safe defaults if it is absent."""
    try:
        raw = load_key("index_tts") or {}
    except KeyError:
        raw = {}
        rprint("[yellow]⚠️ config.yaml 缺少 index_tts 段，使用内置默认值[/yellow]")

    model_dir = os.path.normpath(raw.get("model_dir") or _DEFAULT_MODEL_DIR)
    if not os.path.isabs(model_dir):
        raise ValueError(
            "index_tts.model_dir 必须是绝对路径（VideoLingo 的工作目录是 D:/code/vl），"
            f"当前值: {model_dir}"
        )

    cfg_path = raw.get("cfg_path") or os.path.join(model_dir, "config.yaml")
    cfg_path = os.path.normpath(cfg_path)
    if not os.path.isabs(cfg_path):
        cfg_path = os.path.normpath(os.path.join(model_dir, cfg_path))

    spk = os.path.normpath(raw.get("spk_audio_prompt") or _DEFAULT_SPK_PROMPT)
    device = (raw.get("device") or "").strip() or None

    return {
        "model_dir": model_dir,
        "cfg_path": cfg_path,
        "spk_audio_prompt": spk,
        "use_bf16": bool(raw.get("use_bf16", True)),
        "lang": raw.get("lang") or "auto",
        "refer_mode": int(raw.get("refer_mode", 1)),
        "device": device,
        "use_emo_text": bool(raw.get("use_emo_text", False)),
    }


def _resolve_lang(cfg_lang: str) -> str:
    """Resolve 'auto' from target_language, otherwise pass the token through."""
    value = str(cfg_lang or "auto").strip()
    if value.lower() != "auto":
        return value.upper()
    try:
        target = str(load_key("target_language")).strip()
    except KeyError:
        target = ""
    code = _EXACT_LANG.get(target.lower())
    if code is None:
        for needle, candidate in _EXACT_LANG.items():
            if needle in target.lower():
                code = candidate
                break
    if code is None:
        rprint(f"[yellow]⚠️ 无法从 target_language='{target}' 推断语言，回退 ZH[/yellow]")
        code = "ZH"
    return code


def _ensure_refers() -> None:
    """Trigger vl's own reference-audio extraction once (Demucs + slicing)."""
    global _REFER_EXTRACTED
    if _REFER_EXTRACTED:
        return
    with _REFER_LOCK:
        if _REFER_EXTRACTED:
            return
        # Demucs needs ~2.5 min and several GB of VRAM. Running it while the
        # IndexTTS engine is already resident would OOM the 12 GB card.
        if _ENGINE is not None:
            raise RuntimeError(
                "缺少原声参考音频，但 IndexTTS 权重已常驻显存；此时再跑 Demucs 会 OOM。"
                " 请停止当前任务，先执行流水线第 9 步『提取参考音频』，再重新『生成配音』。"
            )
        rprint("[bold yellow]🎧 首次使用原声克隆，正在抽取参考音频（Demucs 人声分离 + 逐句切片）...[/bold yellow]")
        from core.tts_backend.refer_audio import extract_refer_audio_main
        extract_refer_audio_main()
        _REFER_EXTRACTED = True


def _resolve_reference(number, cfg: dict, allow_extract: bool = True) -> str:
    """Pick the speaker prompt wav from the ORIGINAL VIDEO's vocals.

    refer_mode 1 -> fixed voice file (the ONLY mode allowed to use it)
    refer_mode 2 -> first line of original vocals (refers/1.wav)
    refer_mode 3 -> per-line original vocals (refers/{number}.wav)

    Modes 2/3 NEVER fall back to the bundled voice_01.wav: silently using a
    preset speaker defeats the whole point of original-voice cloning. If a
    per-line clip is missing we degrade to refers/1.wav (still the same
    speaker from the same video), then fail loudly.
    """
    mode = cfg["refer_mode"]
    global_ref = os.path.join(_AUDIO_REFERS_DIR, "1.wav")

    if mode == 1:
        fixed = cfg["spk_audio_prompt"]
        if not os.path.isfile(fixed):
            raise FileNotFoundError(
                f"refer_mode=1 的固定音色文件不存在：{fixed}。"
                " 请检查 config.yaml 的 index_tts.spk_audio_prompt。"
            )
        return fixed

    if mode == 2:
        chosen = global_ref
    elif mode == 3:
        chosen = os.path.join(_AUDIO_REFERS_DIR, f"{number}.wav")
    else:
        raise ValueError(f"index_tts.refer_mode 只能是 1/2/3，当前值: {mode}")

    if not os.path.isfile(chosen) and allow_extract:
        _ensure_refers()

    if os.path.isfile(chosen):
        return chosen

    if mode == 3 and os.path.isfile(global_ref):
        rprint(f"[yellow]⚠️ 第 {number} 句原声片段缺失，改用同视频第 1 句原声（不是预置音色）[/yellow]")
        return global_ref

    raise FileNotFoundError(
        f"原声参考音频不存在：{chosen}（refer_mode={mode}）。"
        " 请先在流水线第 9 步『提取参考音频』完成 Demucs 分离与切片，再生成配音。"
        " 原声模式不会回退到 spk_audio_prompt 预置音色。"
    )


def _get_engine(cfg: dict):
    """Lazily build and cache the IndexTTS2 singleton."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_INIT_LOCK:
        if _ENGINE is not None:
            return _ENGINE

        missing = [
            p for p in (cfg["cfg_path"], os.path.join(cfg["model_dir"], "gpt.pth"))
            if not os.path.isfile(p)
        ]
        if missing:
            raise FileNotFoundError(
                "index-tts 模型文件缺失: " + ", ".join(missing) +
                "。请先执行: hf download IndexTeam/IndexTTS-2.5 --local-dir=checkpoints"
            )

        # Lazy import: other TTS backends never touch this dependency.
        from indextts.infer_v2_5 import IndexTTS2

        rprint("[bold green]🚀 首次加载 IndexTTS-2.5 权重（约 1-2 分钟，仅此一次）...[/bold green]")
        _ENGINE = IndexTTS2(
            cfg_path=cfg["cfg_path"],
            model_dir=cfg["model_dir"],
            use_bf16=cfg["use_bf16"],
            device=cfg["device"],
            use_qwen_emo=cfg["use_emo_text"],
        )
        rprint("[bold green]✅ IndexTTS-2.5 权重已常驻显存[/bold green]")
    return _ENGINE


def index_tts_for_videolingo(text, save_as, number=None, task_df=None) -> None:
    """VideoLingo TTS adapter. Signature mirrors the other *_for_videolingo hooks."""
    global _VOICE_LOGGED
    cfg = _read_cfg()
    # Resolve reference BEFORE taking the infer lock: mode 2/3 may run Demucs
    # on its very first call, which should not block/serialize model init.
    spk_prompt = _resolve_reference(number, cfg)
    lang = _resolve_lang(cfg["lang"])

    if not _VOICE_LOGGED:
        _VOICE_LOGGED = True
        if cfg["refer_mode"] == 1:
            rprint(f"[bold cyan]🎙️ 音色来源：固定音色文件 {spk_prompt}[/bold cyan]")
        else:
            source = "原视频第 1 句人声（全片统一音色）" if cfg["refer_mode"] == 2 else "逐句原视频人声（随句克隆）"
            rprint(f"[bold cyan]🎙️ 音色来源：{source} → {spk_prompt}[/bold cyan]")

    Path(save_as).parent.mkdir(parents=True, exist_ok=True)

    # One GPU, one resident multi-GB model: inference MUST be serialized.
    with _INFER_LOCK:
        engine = _get_engine(cfg)
        engine.infer(
            spk_audio_prompt=spk_prompt,
            text=text,
            output_path=save_as,
            lang=lang,
            use_emo_text=cfg["use_emo_text"],
            verbose=False,
        )

    if not os.path.isfile(save_as) or os.path.getsize(save_as) < 1024:
        raise RuntimeError(f"index-tts 未生成有效音频: {save_as}")


def self_check() -> None:
    """Cheap static check: config / model files / reference audio / import."""
    cfg = _read_cfg()
    print("=== index-tts 接入自检（不加载模型）===")
    print(f"  model_dir        : {cfg['model_dir']}")
    print(f"  cfg_path         : {cfg['cfg_path']}  exists={os.path.isfile(cfg['cfg_path'])}")
    print(f"  gpt.pth          : exists={os.path.isfile(os.path.join(cfg['model_dir'], 'gpt.pth'))}")
    print(f"  use_bf16         : {cfg['use_bf16']}")
    print(f"  lang             : {cfg['lang']} -> {_resolve_lang(cfg['lang'])}")
    print(f"  refer_mode       : {cfg['refer_mode']}  "
          f"({ {1: '固定音色', 2: '原声/第1句', 3: '原声/逐句'}.get(cfg['refer_mode'], '?') })")
    print(f"  spk_audio_prompt : {cfg['spk_audio_prompt']}  (仅 refer_mode=1 使用)")
    try:
        # allow_extract=False: self_check must stay cheap and must NOT kick off
        # a 2.5-minute Demucs run just because refers/ is empty.
        ref = _resolve_reference(1, cfg, allow_extract=False)
        print(f"  第 1 条参考音频   : {ref}")
    except Exception as e:
        print(f"  第 1 条参考音频   : 尚未提取 → 先跑第 9 步『提取参考音频』({type(e).__name__})")
    try:
        from indextts.infer_v2_5 import IndexTTS2  # noqa: F401
        import torch
        print(f"  indextts import  : OK")
        print(f"  torch/cuda       : {torch.__version__} / cuda={torch.cuda.is_available()}")
    except Exception as e:
        print(f"  indextts import  : FAILED {type(e).__name__}: {e}")
    print("=== 自检结束 ===")


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        # Real one-line synthesis. Run from D:/code/vl in the (vl) env:
        #   python -m core.tts_backend.index_tts --smoke
        out = os.path.join("output", "audio", "tmp", "index_tts_smoke.wav")
        index_tts_for_videolingo("你好，这是 IndexTTS 接入 VideoLingo 的测试。", out, number=1)
        print(f"smoke test output: {out}")
    else:
        self_check()
