"""Settings REST API: read/write config.yaml through a whitelisted schema.

- GET /api/settings  -> grouped, typed settings for the UI
- PUT /api/settings  -> validate + persist a subset of keys (whitelist only)
- POST /api/settings/test-llm -> one-round connectivity test of a profile

The whitelist is intentional: config.yaml also holds API keys we do NOT
want the frontend to overwrite blindly. Keys sent by the UI are matched
against this schema; unknown keys are rejected with 400.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.utils.config_utils import load_key, update_key

router = APIRouter(prefix="/api/settings", tags=["settings"])

# key -> (label, type, options?) — the editable surface for the UI
SCHEMA: dict = {
    "display_language": ("界面语言", "select", ["zh-CN", "en-US"]),
    "target_language": ("目标语言", "select",
                        ["简体中文", "繁体中文", "English", "日本語", "한국어"]),
    "max_workers": ("并发工作线程", "number", None),
    # whisper / ASR
    "whisper.model": ("Whisper 模型", "select",
                      ["tiny", "base", "small", "medium", "large-v2", "large-v3"]),
    "whisper.language": ("源音频语言", "select",
                         ["en", "zh", "ja", "ko", "de", "fr", "es", "ru", "auto"]),
    "whisper.runtime": ("ASR 运行时", "select",
                        ["local", "cloud", "elevenlabs", "qwen3_asr"]),
    "demucs": ("人声分离 (Demucs)", "bool", None),
    # Qwen3-ASR 本地后端（FR-35）：独立 conda 环境，绝不装进主环境
    "qwen3_asr.python_exe": ("Qwen3-ASR 独立环境 python.exe", "text", None),
    "qwen3_asr.model": ("Qwen3-ASR 模型", "text", None),
    "qwen3_asr.aligner": ("Qwen3 强制对齐模型", "text", None),
    "qwen3_asr.language": ("Qwen3-ASR 语言", "select",
                           ["auto", "zh", "en", "yue", "fr", "de", "it",
                            "ja", "ko", "pt", "ru", "es"]),
    "qwen3_asr.max_window_seconds": ("Qwen3 单窗口上限(秒)", "number", None),
    "qwen3_asr.overlap_seconds": ("Qwen3 窗口重叠(秒)", "number", None),
    "qwen3_asr.max_inference_batch_size": ("Qwen3 推理 batch", "number", None),
    "qwen3_asr.max_new_tokens": ("Qwen3 max_new_tokens", "number", None),
    "qwen3_asr.device": ("Qwen3 设备", "text", None),
    "qwen3_asr.dtype": ("Qwen3 精度", "select",
                        ["bfloat16", "float16", "float32"]),
    # subtitles
    "subtitle.max_length": ("单行字幕最大长度", "number", None),
    "subtitle.target_multiplier": ("字幕时长系数", "number", None),
    "subtitle.source": ("第12步字幕源", "select", ["legacy", "unified"]),
    "max_split_length": ("切句长度阈值", "number", None),
    "min_subtitle_duration": ("最短字幕时长(秒)", "number", None),
    "tolerance": ("时间容差(秒)", "number", None),
    "burn_subtitles": ("烧录字幕到视频", "bool", None),
    "ffmpeg_gpu": ("FFmpeg GPU 加速", "bool", None),
    "summary_length": ("摘要长度上限", "number", None),
    "reflect_translate": ("反思翻译(二次校验)", "bool", None),
    # translation chunking (step 4.2)
    "translate.chunk_size": ("翻译分块字符数", "number", None),
    "translate.max_sentences": ("翻译分块句数上限", "number", None),
    # tts
    "tts_method": ("TTS 方式", "select",
                   ["azure_tts", "openai_tts", "sf_fish_tts", "sf_cosyvoice2",
                    "edge_tts", "gpt_sovits", "f5tts", "index_tts"]),
    "speed_factor.min": ("语速下限", "number", None),
    "speed_factor.accept": ("可接受语速", "number", None),
    "speed_factor.max": ("语速上限", "number", None),
    # index-tts 2.5 本地配音（仅 tts_method='index_tts' 时生效）
    "index_tts.lang": ("index-tts 语言", "select",
                        ["auto", "ZH", "EN", "JA", "ES", "AR", "YUE"]),
    "index_tts.refer_mode": ("index-tts 音色来源 1固定/2首句/3逐句/4按人", "number", None),
    "index_tts.spk_audio_prompt": ("index-tts 参考音色 wav 路径", "text", None),
    "index_tts.model_dir": ("index-tts 权重目录(绝对路径)", "text", None),
    "index_tts.use_bf16": ("index-tts BF16 加速", "bool", None),
    # network
    "network.proxy_mode": ("代理模式", "select", ["auto", "direct", "proxy"]),
    "network.proxy_url": ("代理地址", "text", None),
    "network.no_proxy": ("不走代理的主机", "text", None),
}

GROUPS: list = [
    ("general", "通用", ["display_language", "target_language", "max_workers"]),
    ("network", "网络代理", ["network.proxy_mode", "network.proxy_url",
                             "network.no_proxy"]),
    ("asr", "转录 ASR", ["whisper.model", "whisper.language", "whisper.runtime",
                          "demucs",
                          "qwen3_asr.python_exe", "qwen3_asr.model",
                          "qwen3_asr.aligner", "qwen3_asr.language",
                          "qwen3_asr.max_window_seconds",
                          "qwen3_asr.overlap_seconds",
                          "qwen3_asr.max_inference_batch_size",
                          "qwen3_asr.max_new_tokens", "qwen3_asr.device",
                          "qwen3_asr.dtype"]),
    ("subtitle", "字幕与切句", ["subtitle.source", "subtitle.max_length", "subtitle.target_multiplier",
                                "max_split_length", "min_subtitle_duration",
                                "tolerance", "burn_subtitles", "ffmpeg_gpu",
                                "summary_length", "reflect_translate",
                                "translate.chunk_size", "translate.max_sentences"]),
    ("tts", "配音 TTS", ["tts_method", "speed_factor.min", "speed_factor.accept",
                          "speed_factor.max", "index_tts.lang", "index_tts.refer_mode",
                          "index_tts.spk_audio_prompt", "index_tts.model_dir",
                          "index_tts.use_bf16"]),
]

# keys never sent raw to the frontend (masked in GET; PUT ignores them)
SECRET_KEYS = {"key", "api_key", "cookies_path"}


@router.get("")
def get_settings():
    items = []
    for gid, gtitle, keys in GROUPS:
        entries = []
        for k in keys:
            label, typ, options = SCHEMA[k]
            try:
                value = load_key(k)
            except KeyError:
                value = None
            entries.append({"key": k, "label": label, "type": typ,
                            "options": options, "value": value})
        items.append({"id": gid, "title": gtitle, "items": entries})
    return {"groups": items}


class SettingsUpdate(BaseModel):
    changes: dict


@router.put("")
def put_settings(req: SettingsUpdate):
    unknown = [k for k in req.changes if k not in SCHEMA]
    if unknown:
        raise HTTPException(400, f"unknown or read-only key(s): {', '.join(unknown)}")

    applied, rejected = [], []
    for k, v in req.changes.items():
        _, typ, _ = SCHEMA[k]
        if typ == "number":
            try:
                v = float(v) if not float(v).is_integer() else int(v)
            except (TypeError, ValueError):
                rejected.append(f"{k}: not a number")
                continue
        elif typ == "bool":
            v = bool(v)
        elif typ == "select":
            if v not in (SCHEMA[k][2] or []):
                rejected.append(f"{k}: invalid option {v}")
                continue
        elif typ == "text":
            v = "" if v is None else str(v).strip()
        try:
            if not update_key(k, v):
                rejected.append(f"{k}: update failed")
                continue
            applied.append(k)
        except KeyError as e:
            rejected.append(f"{k}: {e}")
    return {"applied": applied, "rejected": rejected}


class LlmTestRequest(BaseModel):
    profile: str


@router.post("/test-llm")
def test_llm(req: LlmTestRequest):
    """One cheap round-trip to a profile to verify key/base_url/model work.

    Thin adapter — ALL decisions (key-pool walking, 401/402/403 -> mark
    dead -> rotate) live in core/services/llm_service.probe_pool, the same
    code path the pipeline uses."""
    from core.services.llm_service import probe_pool
    result = probe_pool(req.profile)
    result.setdefault("profile", req.profile)
    if result.get("ok") is False and "error" not in result:
        raise HTTPException(404, result.get("message", "profile not found"))
    return result
