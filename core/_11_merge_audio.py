import os
import shutil
import subprocess
import tempfile
import pandas as pd
from rich.console import Console
from core.utils import *
from core.utils.models import *
from core.utils.progress import ChunkProgress
from core.services.dub_timeline_service import parse_excel_list
console = Console()

DUB_VOCAL_FILE = 'output/dub.mp3'
DUB_SUB_FILE = 'output/dub.srt'
OUTPUT_FILE_TEMPLATE = f"{_AUDIO_SEGS_DIR}/{{}}.wav"
SAMPLE_RATE = 16000
BITRATE = '64k'

# Windows caps the CreateProcess command line at ~32767 chars. Feeding 1448
# `-i` inputs + one giant filter_complex overflows (WinError 206). We split
# into batches so each ffmpeg command stays well under that limit.
BATCH_SIZE = 80


def load_and_flatten_data(excel_file):
    """Load and flatten Excel data"""
    df = pd.read_excel(excel_file)
    lines = [parse_excel_list(line) for line in df['lines'].tolist()]
    lines = [item for sublist in lines for item in sublist]

    new_sub_times = [parse_excel_list(time) for time in df['new_sub_times'].tolist()]
    new_sub_times = [item for sublist in new_sub_times for item in sublist]

    return df, lines, new_sub_times


def get_audio_files(df):
    """Generate a list of audio file paths"""
    audios = []
    for index, row in df.iterrows():
        number = row['number']
        line_count = len(parse_excel_list(row['lines']))
        for line_index in range(line_count):
            temp_file = OUTPUT_FILE_TEMPLATE.format(f"{number}_{line_index}")
            audios.append(temp_file)
    return audios


def iter_segments(df, new_sub_times):
    """Yield (audio_file, start, end) in the exact order get_audio_files uses,
    so the flattened new_sub_times stays aligned with the segment list."""
    idx = 0
    for _, row in df.iterrows():
        number = row['number']
        line_count = len(parse_excel_list(row['lines']))
        for line_index in range(line_count):
            audio = OUTPUT_FILE_TEMPLATE.format(f"{number}_{line_index}")
            start, end = new_sub_times[idx]
            idx += 1
            yield audio, float(start), float(end)


def build_batch_filtergraph(batch, prev_end):
    """Pure builder: produce one ffmpeg filter_complex that assembles a single
    BATCH of segments on a local timeline, inserting silence gaps via a single
    shared anullsrc input (so subtitle alignment is preserved).

    `batch` is a list of (audio_file, start, end). `prev_end` is the global end
    time of the segment immediately before this batch (0.0 for the first).
    Returns (graph, new_prev_end, has_gap). The caller appends the anullsrc
    `-f lavfi -i anullsrc=...` input ONLY when has_gap is True (it then becomes
    input index == len(batch), referenced by the [SIL0] node).
    """
    n = len(batch)
    parts = []
    chain = []
    pe = prev_end
    has_gap = False
    for j, (audio, start, end) in enumerate(batch):
        gap = start - pe
        if gap > 0.001:
            has_gap = True
            parts.append("[SIL0]atrim=0:%.3f,asetpts=PTS-STARTPTS[sil%d]" % (gap, j))
            chain.append("sil%d" % j)
        parts.append("[%d:a]aresample=%d,aformat=sample_fmts=s16:channel_layouts=mono,"
                     "asetpts=PTS-STARTPTS[clip%d]" % (j, SAMPLE_RATE, j))
        chain.append("clip%d" % j)
        pe = end
    if has_gap:
        # anullsrc appended as the LAST input (index n); normalize once.
        parts.insert(0, "[%d:a]aformat=sample_fmts=s16:channel_layouts=mono[SIL0]" % n)
    chain_str = "".join("[%s]" % c for c in chain)
    parts.append("%sconcat=n=%d:v=0:a=1[out]" % (chain_str, len(chain)))
    return ";".join(parts), pe, has_gap


def merge_audio_segments_ffmpeg(segments):
    """Assemble the full dub with batched ffmpeg passes.

    Windows caps the CreateProcess command line at ~32767 chars, so feeding
    1448 `-i` inputs + one giant filter_complex overflows (WinError 206).
    We split into BATCH_SIZE-segment groups, each a short-enough command,
    then concatenate the batch WAVs with the concat demuxer (a tiny list
    file, no long command line). Silence gaps are inserted inline via a
    shared anullsrc so subtitle alignment is preserved.
    Returns True on success.
    """
    seg_files = [s[0] for s in segments]
    if not os.path.exists(seg_files[0]):
        console.print(f"[bold red]❌ Error: First audio file {seg_files[0]} does not exist![/bold red]")
        return False

    console.print(f"[bold cyan]🔄 分批 ffmpeg 装配 {len(seg_files)} 段（每批 {BATCH_SIZE} 段，规避 Windows 命令行长度限制）...[/bold cyan]")
    tmp = tempfile.mkdtemp(prefix="vl_merge_")
    try:
        batch_wavs = []
        prev_end = 0.0
        i = bi = 0
        n = len(segments)
        while i < n:
            batch = segments[i:i + BATCH_SIZE]
            graph, prev_end, has_gap = build_batch_filtergraph(batch, prev_end)
            batch_wav = os.path.join(tmp, "batch_%d.wav" % bi)
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
            for audio, _, _ in batch:
                cmd += ["-i", audio]
            if has_gap:
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=%d:cl=mono" % SAMPLE_RATE]
            cmd += ["-filter_complex", graph, "-map", "[out]", "-ar", str(SAMPLE_RATE),
                    "-ac", "1", batch_wav]
            try:
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.decode("utf-8", "ignore") if exc.stderr else ""
                console.print(f"[bold yellow]⚠️ ffmpeg 分批装配失败（批 {bi}）：{stderr[-400:]}[/bold yellow]")
                return False
            batch_wavs.append(batch_wav)
            i += len(batch)
            bi += 1

        # Final stitch: feed all batch WAVs (s16/16k/mono) as inputs and concat
        # via filter_complex. Avoids the concat-demuxer list file (its path
        # quoting/absolute-path handling is fragile across ffmpeg builds, e.g.
        # gyan.dev 8.x rejects `file 'C:/abs/path'`). Batch count is ~19 here,
        # so the command line stays far below Windows' ~32767-char limit.
        ins = "".join("[%d:a]" % k for k in range(len(batch_wavs)))
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        for bw in batch_wavs:
            cmd += ["-i", bw]
        cmd += ["-filter_complex", "%sconcat=n=%d:v=0:a=1[out]" % (ins, len(batch_wavs)),
                "-map", "[out]", "-ar", str(SAMPLE_RATE), "-ac", "1",
                "-b:a", BITRATE, DUB_VOCAL_FILE]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", "ignore") if exc.stderr else ""
            console.print(f"[bold yellow]⚠️ ffmpeg 终拼失败，回退 pydub：{stderr[-400:]}[/bold yellow]")
            return False
        console.print(f"[bold green]✅ Audio file successfully merged! ({DUB_VOCAL_FILE})[/bold green]")
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def process_audio_segment(audio_file):
    """Fallback path: process a single audio segment with MP3 compression"""
    from pydub import AudioSegment
    temp_file = f"{audio_file}_temp.mp3"
    ffmpeg_cmd = [
        'ffmpeg', '-y',
        '-i', audio_file,
        '-ar', str(SAMPLE_RATE),
        '-ac', '1',
        '-b:a', BITRATE,
        temp_file
    ]
    subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    audio_segment = AudioSegment.from_mp3(temp_file)
    os.remove(temp_file)
    return audio_segment


def merge_audio_segments(audios, new_sub_times, sample_rate):
    """Fallback (pydub) merge — kept for correctness when ffmpeg path fails."""
    from pydub import AudioSegment
    merged_audio = AudioSegment.silent(duration=0, frame_rate=sample_rate)

    with ChunkProgress("Merging audio segments (fallback)", len(audios), unit="段") as p:
        for i, (audio_file, time_range) in enumerate(zip(audios, new_sub_times)):
            check_cancel()
            if not os.path.exists(audio_file):
                console.print(f"[bold yellow]⚠️  Warning: File {audio_file} does not exist, skipping...[/bold yellow]")
                p.advance()
                continue

            audio_segment = process_audio_segment(audio_file)
            start_time, end_time = time_range

            if i > 0:
                prev_end = new_sub_times[i - 1][1]
                silence_duration = start_time - prev_end
                if silence_duration > 0:
                    silence = AudioSegment.silent(duration=int(silence_duration * 1000), frame_rate=sample_rate)
                    merged_audio += silence
            elif start_time > 0:
                silence = AudioSegment.silent(duration=int(start_time * 1000), frame_rate=sample_rate)
                merged_audio += silence

            merged_audio += audio_segment
            p.advance()

    return merged_audio


def create_srt_subtitle():
    df, lines, new_sub_times = load_and_flatten_data(_8_1_AUDIO_TASK)

    with open(DUB_SUB_FILE, 'w', encoding='utf-8') as f:
        for i, ((start_time, end_time), line) in enumerate(zip(new_sub_times, lines), 1):
            start_str = f"{int(start_time//3600):02d}:{int((start_time%3600)//60):02d}:{int(start_time%60):02d},{int((start_time*1000)%1000):03d}"
            end_str = f"{int(end_time//3600):02d}:{int((end_time%3600)//60):02d}:{int(end_time%60):02d},{int((end_time*1000)%1000):03d}"

            f.write(f"{i}\n")
            f.write(f"{start_str} --> {end_str}\n")
            f.write(f"{line}\n\n")

    rprint(f"[bold green]✅ Subtitle file created: {DUB_SUB_FILE}[/bold green]")


def merge_full_audio():
    """Main function: Process the complete audio merging process"""
    console.print("\n[bold cyan]🎬 Starting audio merging process...[/bold cyan]")

    with console.status("[bold cyan]📊 Loading data from Excel...[/bold cyan]"):
        df, lines, new_sub_times = load_and_flatten_data(_8_1_AUDIO_TASK)
    console.print("[bold green]✅ Data loaded successfully[/bold green]")

    with console.status("[bold cyan]🔍 Getting audio file list...[/bold cyan]"):
        audios = get_audio_files(df)
    console.print(f"[bold green]✅ Found {len(audios)} audio segments[/bold green]")

    with console.status("[bold cyan]📝 Generating subtitle file...[/bold cyan]"):
        create_srt_subtitle()

    segments = list(iter_segments(df, new_sub_times))
    if not segments or not os.path.exists(segments[0][0]):
        console.print(f"[bold red]❌ Error: First audio file {segments[0][0] if segments else '?'} does not exist![/bold red]")
        return

    console.print(f"[bold green]✅ Sample rate: {SAMPLE_RATE}Hz[/bold green]")

    if merge_audio_segments_ffmpeg(segments):
        return

    # Fallback: old pydub path (guarantees correctness if ffmpeg graph fails)
    console.print("[bold cyan]🔄 Falling back to pydub merge...[/bold cyan]")
    merged_audio = merge_audio_segments(audios, new_sub_times, SAMPLE_RATE)
    with console.status("[bold cyan]💾 Exporting final audio file...[/bold cyan]"):
        merged_audio = merged_audio.set_frame_rate(SAMPLE_RATE).set_channels(1)
        merged_audio.export(DUB_VOCAL_FILE, format="mp3", parameters=["-b:a", BITRATE])
    console.print(f"[bold green]✅ Audio file successfully merged![/bold green]")
    console.print(f"[bold green]📁 Output file: {DUB_VOCAL_FILE}[/bold green]")


if __name__ == "__main__":
    merge_full_audio()
