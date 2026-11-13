"""广告位提醒 / 预览 / 裁剪 REST API（流水线页「广告位提醒」卡片）。

薄适配层：参数校验 + HTTP 包装，决策都在 core/services/ 下的
ad_service.py（识别 + 边界定点）与 ad_cut_service.py（取补集裁剪）。

* detect：一次 LLM 调用 + 本地 ffmpeg 切点检测，不占流水线任务线程、
  不进 11 步注册表（与 B站文案卡片同模式）。
* preview：`PreviewFileResponse` 支持 HTTP Range，页面 <video> 才能拖动进度条。
  只按固定 id 白名单解析文件，不接受任意路径（G-09）；读句柄由后端登记，
  归档 / 重跑 / 显式 release 时可主动关闭（见 server/preview_stream.py）。
* cut：按 EDL 补集裁剪，输出 output/output_no_ads.mp4，原视频不动。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.services.ad_service import (
    context, detect_ads, load_edl, update_edl_segments)
from core.services.ad_cut_service import (
    cut_video, default_source, list_sources, media_info, resolve_source)
from server.preview_stream import PreviewFileResponse, close_preview_streams

router = APIRouter(prefix="/api/ad", tags=["ad"])

_WORKSPACE = "output"


@router.get("/context")
def ad_context():
    """卡片预填：转录状态 + 视频/音频 + 是否已有 EDL + 可用裁剪源。

    转录只回统计，不回全文（几万字不该塞进一次页面请求）。
    """
    sources = list_sources(_WORKSPACE)
    return {**context(_WORKSPACE), "sources": sources,
            "default_source": default_source(sources=sources)}


@router.api_route("/preview", methods=["GET", "HEAD"])
def ad_preview(source: str = "burned"):
    """视频预览（支持 Range → 可拖动）。source 只能是白名单 id。"""
    try:
        path = resolve_source(_WORKSPACE, source)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return PreviewFileResponse(path, media_type="video/mp4")


@router.post("/preview/release")
def ad_preview_release():
    """主动断开所有预览读句柄（归档 / force 重跑前由前端调用）。

    不依赖浏览器「取消 Range 请求」这件事本身：Windows 上只要有一个读句柄
    没关，`output_sub.mp4` 就删不掉。
    """
    return {"ok": True, "released": close_preview_streams()}


@router.get("/media-info")
@router.get("/frame-rate")
def ad_media_info(source: str = "burned"):
    """预览源的真实 fps / 时长 / 编码参数。

    fps 供逐帧步进与帧网格吸附（FR-28），时长供「切到片尾」用（FR-30）。
    `/frame-rate` 是旧路径别名，保留兼容；两者都按固定 id 白名单解析（G-09）。
    """
    try:
        return media_info(_WORKSPACE, source)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class DetectRequest(BaseModel):
    extra_hint: str = ""


@router.post("/detect")
def ad_detect(req: DetectRequest):
    try:
        return {"ok": True, "result": detect_ads(
            _WORKSPACE, extra_hint=req.extra_hint)}
    except ValueError as e:
        # 无转录 / LLM 校验最终失败
        raise HTTPException(400, str(e)) from e
    except Exception as e:                                          # noqa: BLE001
        raise HTTPException(502, f"广告位识别失败: {type(e).__name__}: {e}") from e


@router.get("/edl")
def ad_get_edl():
    edl = load_edl(_WORKSPACE)
    if edl is None:
        raise HTTPException(404, "还没有 EDL，请先点「识别广告位」")
    return {"ok": True, "edl": edl}


class SegmentsRequest(BaseModel):
    segments: list[dict]
    # None = 让服务层按「哪些自动段消失了」增量记录删除；
    # 显式传 [] = 用户点了「恢复已删除」，清空记录。
    dismissed: list[dict] | None = None


@router.put("/edl")
def ad_put_edl(req: SegmentsRequest):
    """页面手工改起止秒 / 勾选 / 删除后回写；MD 表格同步重渲染。

    `segments=[]` 合法（清空全部切点），FR-32。
    """
    try:
        return {"ok": True, "edl": update_edl_segments(
            _WORKSPACE, req.segments, dismissed=req.dismissed)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class CutRequest(BaseModel):
    source: str = "burned"
    segments: list[dict] | None = None   # 页面当前编辑值；None = 用已保存 EDL


@router.post("/cut")
def ad_cut(req: CutRequest):
    """预览微调确认后执行裁剪；先把页面上的时间回写 EDL，保证产物与所见一致。"""
    try:
        if req.segments is not None:
            update_edl_segments(_WORKSPACE, req.segments)
        return {"ok": True,
                "result": cut_video(_WORKSPACE, req.source, req.segments)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:                                          # noqa: BLE001
        raise HTTPException(502, f"裁剪失败: {type(e).__name__}: {e}") from e
