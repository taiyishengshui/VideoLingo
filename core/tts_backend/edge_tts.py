from pathlib import Path
import edge_tts
from core.utils import *
import subprocess
from core.utils.subprocess_log import run_logged

# Available voices can be listed using edge-tts --list-voices command
# Common English voices:
# en-US-JennyNeural - Female
# en-US-GuyNeural - Male  
# en-GB-SoniaNeural - Female British
# Common Chinese voices:
# zh-CN-XiaoxiaoNeural - Female
# zh-CN-YunxiNeural - Male
# zh-CN-XiaoyiNeural - Female
def edge_tts(text, save_path):
    # Load settings from config file
    edge_set = load_key("edge_tts")
    voice = edge_set.get("voice", "en-US-JennyNeural")
    
    # Create output directory if it doesn't exist
    speech_file_path = Path(save_path)
    speech_file_path.parent.mkdir(parents=True, exist_ok=True)
    
    cmd = ["edge-tts", "--voice", voice, "--text", text, "--write-media", str(speech_file_path)]
    # run_logged：edge-tts 的报错原本只落在启动窗口，页面日志看不到
    run_logged(cmd, check=True)
    print(f"Audio saved to {speech_file_path}")

if __name__ == "__main__":
    edge_tts("Today is a good day!", "edge_tts.wav")
