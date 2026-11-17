import os
import platform
import subprocess

import cv2
import numpy as np
from rich.console import Console

from core.utils.media_files import find_video_files
from core.asr_backend.audio_preprocess import normalize_audio_volume
from core.utils import *
from core.utils.models import *
from core.utils.subprocess_log import run_logged
from core.services.encode_service import build_encode_args
from core.services.subtitle_source_service import subtitle_plan

console = Console()

DUB_VIDEO = "output/output_dub.mp4"
DUB_AUDIO = 'output/dub.mp3'

SRC_FONT_SIZE = 15
TRANS_FONT_SIZE = 17
FONT_NAME = 'Arial'
TRANS_FONT_NAME = 'Arial'
if platform.system() == 'Linux':
    FONT_NAME = 'NotoSansCJK-Regular'
    TRANS_FONT_NAME = 'NotoSansCJK-Regular'
if platform.system() == 'Darwin':
    FONT_NAME = 'Arial Unicode MS'
    TRANS_FONT_NAME = 'Arial Unicode MS'

SRC_FONT_COLOR = '&HFFFFFF'
SRC_OUTLINE_COLOR = '&H000000'
SRC_OUTLINE_WIDTH = 1
SRC_SHADOW_COLOR = '&H80000000'
TRANS_FONT_COLOR = '&H00FFFF'
TRANS_OUTLINE_COLOR = '&H000000'
TRANS_OUTLINE_WIDTH = 1 
TRANS_BACK_COLOR = '&H33000000'


def _subtitle_style(role):
    """Style params only. WHICH subtitles to burn is decided by the service."""
    if role == "src":
        return (f"FontSize={SRC_FONT_SIZE},FontName={FONT_NAME},"
                f"PrimaryColour={SRC_FONT_COLOR},OutlineColour={SRC_OUTLINE_COLOR},"
                f"OutlineWidth={SRC_OUTLINE_WIDTH},ShadowColour={SRC_SHADOW_COLOR},"
                f"BorderStyle=1")
    return (f"FontSize={TRANS_FONT_SIZE},FontName={TRANS_FONT_NAME},"
            f"PrimaryColour={TRANS_FONT_COLOR},OutlineColour={TRANS_OUTLINE_COLOR},"
            f"OutlineWidth={TRANS_OUTLINE_WIDTH},BackColour={TRANS_BACK_COLOR},"
            f"Alignment=2,MarginV=27,BorderStyle=4")


def merge_video_audio():
    """Merge video and audio, and reduce video volume"""
    from core.utils.media_files import is_audio_only_input
    if is_audio_only_input():
        rprint("[bold green]🎵 Audio-only input: skipping dubbing video merge. Dubbed audio is in the `output` directory.[/bold green]")
        return

    VIDEO_FILE = find_video_files()
    background_file = _BACKGROUND_AUDIO_FILE
    
    if not load_key("burn_subtitles"):
        rprint("[bold yellow]Warning: A 0-second black video will be generated as a placeholder as subtitles are not burned in.[/bold yellow]")

        # Create a black frame
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(DUB_VIDEO, fourcc, 1, (1920, 1080))
        out.write(frame)
        out.release()

        rprint("[bold green]Placeholder video has been generated.[/bold green]")
        return

    # Normalize dub audio
    normalized_dub_audio = 'output/normalized_dub.wav'
    normalize_audio_volume(DUB_AUDIO, normalized_dub_audio)
    
    # Merge video and audio with translated subtitles
    video = cv2.VideoCapture(VIDEO_FILE)
    TARGET_WIDTH = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
    TARGET_HEIGHT = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    video.release()
    rprint(f"[bold green]Video resolution: {TARGET_WIDTH}x{TARGET_HEIGHT}[/bold green]")
    
    # FR-36: single decision point. legacy -> one filter, byte-identical to
    # the old hard-coded subtitle_filter; unified -> English + Chinese.
    plan = subtitle_plan("dub", "output")
    missing = [p for p, _ in plan if not os.path.isfile(p)]
    if missing:
        raise FileNotFoundError(f"Subtitle files for dubbing not found: {missing}")
    subtitle_filter = ",".join(
        f"subtitles={path}:force_style='{_subtitle_style(role)}'"
        for path, role in plan
    )
    
    cmd = [
        'ffmpeg', '-hide_banner', '-y', '-i', VIDEO_FILE, '-i', background_file, '-i', normalized_dub_audio,
        '-filter_complex',
        f'[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,'
        f'pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,'
        f'{subtitle_filter}[v];'
        f'[1:a][2:a]amix=inputs=2:duration=first:dropout_transition=3[a]'
    ]

    cmd.extend(['-map', '[v]', '-map', '[a]'])
    # 配音音轨是 amix 新生成的，不能直通；其余编码参数仍对齐原片（FR-29）
    enc_args, enc_desc = build_encode_args(VIDEO_FILE, audio_reencode=True)
    cmd.extend(enc_args)
    cmd.append(DUB_VIDEO)
    rprint(f"[cyan]🎛️ 输出编码对齐原片：{enc_desc}[/cyan]")
    
    proc = run_logged(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg 合成失败（返回码 {proc.returncode}），详见上方输出")
    rprint(f"[bold green]Video and audio successfully merged into {DUB_VIDEO}[/bold green]")

if __name__ == '__main__':
    merge_video_audio()
