import os, subprocess, time
from core.utils.media_files import find_video_files
from core.services.subtitle_source_service import subtitle_plan
import cv2
import numpy as np
import platform
from core.utils import *
from core.utils.subprocess_log import run_logged
from core.services.encode_service import build_encode_args

SRC_FONT_SIZE = 15
TRANS_FONT_SIZE = 17
FONT_NAME = 'Arial'
TRANS_FONT_NAME = 'Arial'

# Linux need to install google noto fonts: apt-get install fonts-noto
if platform.system() == 'Linux':
    FONT_NAME = 'NotoSansCJK-Regular'
    TRANS_FONT_NAME = 'NotoSansCJK-Regular'
# Mac OS has different font names
elif platform.system() == 'Darwin':
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

OUTPUT_DIR = "output"
OUTPUT_VIDEO = f"{OUTPUT_DIR}/output_sub.mp4"
    
def check_gpu_available():
    try:
        result = subprocess.run(['ffmpeg', '-encoders'], capture_output=True, text=True)
        return 'h264_nvenc' in result.stdout
    except:
        return False

def merge_subtitles_to_video():
    from core.utils.media_files import is_audio_only_input
    if is_audio_only_input():
        rprint("[bold green]🎵 Audio-only input: skipping video merge. Subtitle files are ready in the `output` directory.[/bold green]")
        return

    video_file = find_video_files()
    os.makedirs(os.path.dirname(OUTPUT_VIDEO), exist_ok=True)

    # Check resolution
    if not load_key("burn_subtitles"):
        rprint("[bold yellow]Warning: A 0-second black video will be generated as a placeholder as subtitles are not burned in.[/bold yellow]")

        # Create a black frame
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(OUTPUT_VIDEO, fourcc, 1, (1920, 1080))
        out.write(frame)
        out.release()

        rprint("[bold green]Placeholder video has been generated.[/bold green]")
        return

    # FR-36: the subtitle set is decided once, in subtitle_source_service.
    paths = {role: path for path, role in subtitle_plan("sub", OUTPUT_DIR)}
    SRC_SRT, TRANS_SRT = paths["src"], paths["trans"]
    if not os.path.exists(SRC_SRT) or not os.path.exists(TRANS_SRT):
        # Do NOT call exit() here: this function runs inside the Streamlit
        # TaskRunner's background thread, where SystemExit would kill the
        # thread silently and leave the runner stuck in "running" state.
        # Raising a normal exception lets TaskRunner surface it as an error.
        raise FileNotFoundError(
            f"Subtitle files not found in the 'output' directory: "
            f"missing {[p for p in (SRC_SRT, TRANS_SRT) if not os.path.exists(p)]}"
        )

    video = cv2.VideoCapture(video_file)
    TARGET_WIDTH = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
    TARGET_HEIGHT = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    video.release()
    rprint(f"[bold green]Video resolution: {TARGET_WIDTH}x{TARGET_HEIGHT}[/bold green]")
    ffmpeg_cmd = [
        'ffmpeg', '-hide_banner', '-y', '-i', video_file,
        # 显式选流：视频走滤镜重编码、音频尽量直通（FR-29）
        '-map', '0:v:0', '-map', '0:a:0?',
        '-vf', (
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
            f"subtitles={SRC_SRT}:force_style='FontSize={SRC_FONT_SIZE},FontName={FONT_NAME}," 
            f"PrimaryColour={SRC_FONT_COLOR},OutlineColour={SRC_OUTLINE_COLOR},OutlineWidth={SRC_OUTLINE_WIDTH},"
            f"ShadowColour={SRC_SHADOW_COLOR},BorderStyle=1',"
            f"subtitles={TRANS_SRT}:force_style='FontSize={TRANS_FONT_SIZE},FontName={TRANS_FONT_NAME},"
            f"PrimaryColour={TRANS_FONT_COLOR},OutlineColour={TRANS_OUTLINE_COLOR},OutlineWidth={TRANS_OUTLINE_WIDTH},"
            f"BackColour={TRANS_BACK_COLOR},Alignment=2,MarginV=27,BorderStyle=4'"
        ).encode('utf-8'),
    ]

    # 编码参数（码率 / 帧率 / 像素格式 / 音频策略）由服务层按原片决定，
    # 这里只负责拼接 —— 别在本文件再写第二份码率判断（G-01）。
    enc_args, enc_desc = build_encode_args(video_file)
    ffmpeg_cmd.extend(enc_args)
    ffmpeg_cmd.append(OUTPUT_VIDEO)
    rprint(f"[cyan]🎛️ 输出编码对齐原片：{enc_desc}[/cyan]")

    rprint("🎬 Start merging subtitles to video...")
    start_time = time.time()

    try:
        # run_logged：ffmpeg 的进度与报错原本只落在启动窗口，页面日志看不到
        proc = run_logged(ffmpeg_cmd)
        if proc.returncode == 0:
            rprint(f"\n✅ Done! Time taken: {time.time() - start_time:.2f} seconds")
        else:
            rprint("\n❌ FFmpeg execution error — 详见上方 ffmpeg 输出")
    except Exception as e:
        rprint(f"\n❌ Error occurred: {e}")

if __name__ == "__main__":
    merge_subtitles_to_video()
