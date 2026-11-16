"""B站投稿文案 REST API（流水线页「B站投稿文案」卡片）。

薄适配层：参数校验 + HTTP 包装，决策都在 core/services/bili_meta_service。
generate 是一次 LLM 调用（同步返回，走任务链 bili_meta → default 兜底），
与实验场接口同模式：不占用任务线程、不写流水线步骤产物。
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.services.bili_meta_service import (
    generate_bili_meta, load_saved_bili_meta, load_transcript,
    load_video_context, suggest_speaker, transcript_brief)

router = APIRouter(prefix="/api/bili", tags=["bili"])

_SAVE_PATH = "output"


@router.get("/context")
def bili_context():
    """卡片预填：转录状态（决定性一手素材）+ 原视频信息（次要参考）。

    转录只回统计、不回全文，避免把几万字塞进一次页面请求。
    `existing` 是上次落盘的三件套（FR-27 v1.37），供刷新后回显；无文件 /
    格式损坏时为 None，纯只读零 LLM 费用。
    """
    ctx = load_video_context(_SAVE_PATH)
    tr = load_transcript(_SAVE_PATH)
    return {**ctx, "speaker_suggest": suggest_speaker(ctx, tr["text"]),
            "meta_file": os.path.join(_SAVE_PATH, "video_meta.json"),
            "transcript": transcript_brief(tr),
            "existing": load_saved_bili_meta(_SAVE_PATH)}


class GenerateRequest(BaseModel):
    ep: int
    speaker: str = ""
    extra_hint: str = ""
    title: str | None = None        # 次要参考：原视频标题；None = video_meta/文件名
    description: str | None = None  # 次要参考：原视频简介；None = video_meta


@router.post("/generate")
def bili_generate(req: GenerateRequest):
    try:
        return {"ok": True,
                "result": generate_bili_meta(
                    ep=req.ep, speaker=req.speaker, extra_hint=req.extra_hint,
                    title=req.title, description=req.description,
                    workspace=_SAVE_PATH)}
    except ValueError as e:
        # 参数问题（缺标题/缺讲者/EP 越界）与 LLM 校验失败都在这里
        raise HTTPException(400, str(e)) from e
    except Exception as e:                                          # noqa: BLE001
        raise HTTPException(502, f"生成失败: {type(e).__name__}: {e}") from e
