import os
import time
import shutil
import subprocess
from typing import Tuple

import pandas as pd
from pydub import AudioSegment
from rich.console import Console
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.utils import *
from core.utils.models import *
from core.utils.progress import ChunkProgress
from core.tts_backend.tts_main import tts_main
from core.services.dub_timeline_service import (
    parse_excel_list, plain_time_pairs, wav_duration,
)

console = Console()

TEMP_FILE_TEMPLATE = f"{_AUDIO_TMP_DIR}/{{}}_temp.wav"
OUTPUT_FILE_TEMPLATE = f"{_AUDIO_SEGS_DIR}/{{}}.wav"
WARMUP_SIZE = 5

def parse_df_srt_time(time_str: str) -> float:
    """Convert SRT time format to seconds"""
    hours, minutes, seconds = time_str.strip().split(':')
    seconds, milliseconds = seconds.split('.')
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000

def adjust_audio_speed(input_file: str, output_file: str, speed_factor: float) -> None:
    """Adjust audio speed and handle edge cases"""
    # If the speed factor is close to 1, directly copy the file
    if abs(speed_factor - 1.0) < 0.001:
        shutil.copy2(input_file, output_file)
        return
        
    atempo = speed_factor
    cmd = ['ffmpeg', '-i', input_file, '-filter:a', f'atempo={atempo}', '-y', output_file]
    input_duration = wav_duration(input_file)
    max_retries = 2
    for attempt in range(max_retries):
        try:
            subprocess.run(cmd, check=True, stderr=subprocess.PIPE)
            output_duration = wav_duration(output_file)
            expected_duration = input_duration / speed_factor
            diff = output_duration - expected_duration
            # If the output duration exceeds the expected duration, but the input audio is less than 3 seconds, and the error is within 0.1 seconds, truncate to the expected length
            if output_duration >= expected_duration * 1.02 and input_duration < 3 and diff <= 0.1:
                audio = AudioSegment.from_wav(output_file)
                trimmed_audio = audio[:(expected_duration * 1000)]  # pydub uses milliseconds
                trimmed_audio.export(output_file, format="wav")
                print(f"✂️ Trimmed to expected duration: {expected_duration:.2f} seconds")
                return
            elif output_duration >= expected_duration * 1.02:
                raise Exception(f"Audio duration abnormal: input file={input_file}, output file={output_file}, speed factor={speed_factor}, input duration={input_duration:.2f}s, output duration={output_duration:.2f}s")
            return
        except subprocess.CalledProcessError as e:
            if attempt < max_retries - 1:
                rprint(f"[yellow]⚠️ Audio speed adjustment failed, retrying in 1s ({attempt + 1}/{max_retries})[/yellow]")
                time.sleep(1)
            else:
                rprint(f"[red]❌ Audio speed adjustment failed, max retries reached ({max_retries})[/red]")
                raise e

def process_row(row: pd.Series, tasks_df: pd.DataFrame) -> Tuple[int, float]:
    """Helper function for processing single row data (non-index-tts backends)."""
    number = row['number']
    lines = parse_excel_list(row['lines'])
    real_dur = 0
    for line_index, line in enumerate(lines):
        temp_file = TEMP_FILE_TEMPLATE.format(f"{number}_{line_index}")
        tts_main(line, temp_file, number, tasks_df, line_index)
        real_dur += wav_duration(temp_file)
    return number, real_dur


def _build_jobs(tasks_df: pd.DataFrame) -> list[tuple]:
    """Flatten task rows into one job per sub-line wav."""
    jobs = []
    for _, row in tasks_df.iterrows():
        number = int(row['number'])
        for line_index, line in enumerate(parse_excel_list(row['lines'])):
            jobs.append((number, line_index, line,
                         TEMP_FILE_TEMPLATE.format(f"{number}_{line_index}")))
    return jobs


def _generate_index_tts(tasks_df: pd.DataFrame, jobs: list, p) -> pd.DataFrame:
    """One resident GPU model → main thread, fully serial.

    Threading cannot help here (a 3.3 GB singleton behind _INFER_LOCK would
    just queue; a second process would OOM the 12 GB card). What DOES help is
    the ORDER: group jobs by speaker prompt so IndexTTS2's own
    cache_spk_cond / cache_s2mel_style / cache_mel survive between segments.
    Output filenames encode (number, line_index), so step 11 is unaffected.
    """
    from core.tts_backend.index_tts import synthesis_order

    order, stat = synthesis_order([(n, li) for n, li, _, _ in jobs])
    if stat.get("enabled"):
        rprint(
            f"[bold green]🎯 音色分组：{stat['groups']} 个参考音色，"
            f"speaker condition 切换 {stat['switches_before']} → "
            f"{stat['switches_after']} 次[/bold green]")

    durs: dict[int, float] = {}
    for idx in order:
        check_cancel()
        number, line_index, line, temp_file = jobs[idx]
        tts_main(line, temp_file, number, tasks_df, line_index)
        durs[number] = durs.get(number, 0.0) + wav_duration(temp_file)
        p.advance()
    tasks_df['real_dur'] = tasks_df['number'].map(
        lambda n: durs.get(int(n), 0.0)).fillna(0.0)
    return tasks_df


def generate_tts_audio(tasks_df: pd.DataFrame) -> pd.DataFrame:
    """Generate TTS audio and calculate actual duration."""
    tasks_df['real_dur'] = 0
    rprint("[bold green]🎯 Starting TTS audio generation...[/bold green]")
    tts_method = load_key("tts_method")

    if tts_method == "index_tts":
        jobs = _build_jobs(tasks_df)
        rprint(f"🧩 共 {len(tasks_df)} 条 / {len(jobs)} 段待合成")
        with ChunkProgress("Generating TTS audio", len(jobs), unit="段") as p:
            tasks_df = _generate_index_tts(tasks_df, jobs, p)
        rprint("[bold green]✨ TTS audio generation completed![/bold green]")
        return tasks_df

    rprint(f"🧩 共 {len(tasks_df)} 条待合成")
    with ChunkProgress("Generating TTS audio", len(tasks_df), unit="条") as p:
        # warm up for first 5 rows
        warmup_size = min(WARMUP_SIZE, len(tasks_df))
        for _, row in tasks_df.head(warmup_size).iterrows():
            try:
                check_cancel()
                number, real_dur = process_row(row, tasks_df)
                tasks_df.loc[tasks_df['number'] == number, 'real_dur'] = real_dur
                p.advance()
            except Exception as e:
                rprint(f"[red]❌ Error in warmup: {str(e)}[/red]")
                raise e

        # gpt_sovits 是本地服务，保持单线程；云端后端按 max_workers 并发。
        max_workers = 1 if tts_method == "gpt_sovits" else load_key("max_workers")
        if len(tasks_df) > warmup_size:
            remaining_tasks = tasks_df.iloc[warmup_size:].copy()
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(process_row, row, tasks_df.copy())
                    for _, row in remaining_tasks.iterrows()
                ]
                try:
                    for future in as_completed(futures):
                        check_cancel()
                        try:
                            number, real_dur = future.result()
                            tasks_df.loc[tasks_df['number'] == number, 'real_dur'] = real_dur
                            p.advance()
                        except Exception as e:
                            rprint(f"[red]❌ Error: {str(e)}[/red]")
                            raise e
                except BaseException:
                    for f in futures:
                        f.cancel()
                    raise

    rprint("[bold green]✨ TTS audio generation completed![/bold green]")
    return tasks_df

def process_chunk(chunk_df: pd.DataFrame, accept: float, min_speed: float) -> tuple[float, bool]:
    """Process audio chunk and calculate speed factor"""
    chunk_durs = chunk_df['real_dur'].sum()
    tol_durs = chunk_df['tol_dur'].sum()
    durations = tol_durs - chunk_df.iloc[-1]['tolerance']
    all_gaps = chunk_df['gap'].sum() - chunk_df.iloc[-1]['gap']
    
    keep_gaps = True
    speed_var_error = 0.1

    if (chunk_durs + all_gaps) / accept < durations:
        speed_factor = max(min_speed, (chunk_durs + all_gaps) / (durations-speed_var_error))
    elif chunk_durs / accept < durations:
        speed_factor = max(min_speed, chunk_durs / (durations-speed_var_error))
        keep_gaps = False
    elif (chunk_durs + all_gaps) / accept < tol_durs:
        speed_factor = max(min_speed, (chunk_durs + all_gaps) / (tol_durs-speed_var_error))
    else:
        speed_factor = chunk_durs / (tol_durs-speed_var_error)
        keep_gaps = False
        
    return float(round(speed_factor, 3)), keep_gaps

def _speed_plan(tasks_df: pd.DataFrame, accept: float, min_speed: float) -> dict:
    """Pre-compute (speed_factor, keep_gaps, chunk_end_time) per row.

    Needed because the atempo jobs are submitted up-front to a thread pool.
    ``process_chunk`` stays the single decision for speed_factor.
    """
    plan, chunk_start = {}, 0
    for pos in range(len(tasks_df)):
        if tasks_df.iloc[pos]['cut_off'] != 1:
            continue
        chunk_df = tasks_df.iloc[chunk_start:pos + 1].reset_index(drop=True)
        speed_factor, keep_gaps = process_chunk(chunk_df, accept, min_speed)
        # gap/tolerance 是 numpy.float64；不转 float 会把整条时间轴升级成
        # numpy 标量，numpy>=2 落盘后变成 np.float64(...)（v1.27 的坑）。
        chunk_end_time = (float(parse_df_srt_time(chunk_df.iloc[-1]['end_time']))
                          + float(chunk_df.iloc[-1]['tolerance']))
        for k in range(chunk_start, pos + 1):
            plan[tasks_df.index[k]] = (speed_factor, keep_gaps, chunk_end_time)
        chunk_start = pos + 1
    return plan


def _shutdown_speed_executor(executor: ThreadPoolExecutor) -> None:
    """Cancel queued work and wait for already-running ffmpeg jobs to finish."""
    executor.shutdown(wait=True, cancel_futures=True)


def merge_chunks(tasks_df: pd.DataFrame) -> pd.DataFrame:
    """Merge audio chunks and adjust timeline.

    ffmpeg atempo is pure CPU/IO, so all wavs are converted ahead in a small
    thread pool; the timeline still consumes the results strictly in subtitle
    order (cur_time accumulation is sequential and order-dependent).
    ``dub.speed_workers: 1`` restores the old fully-serial behaviour.
    """
    rprint("[bold blue]🔄 Starting audio chunks processing...[/bold blue]")
    accept = load_key("speed_factor.accept")
    plan = _speed_plan(tasks_df, accept, load_key("speed_factor.min"))
    try:
        speed_workers = max(1, int(load_key("dub.speed_workers")))
    except (KeyError, TypeError, ValueError):
        speed_workers = 4
    tasks_df['new_sub_times'] = None

    jobs = []
    for index, row in tasks_df.iterrows():
        number = int(row['number'])
        for line_index, _ in enumerate(parse_excel_list(row['lines'])):
            jobs.append(((number, line_index),
                         TEMP_FILE_TEMPLATE.format(f"{number}_{line_index}"),
                         OUTPUT_FILE_TEMPLATE.format(f"{number}_{line_index}"),
                         plan[index][0]))
    rprint(f"⚙️ 变速 {len(jobs)} 段 · {speed_workers} 线程（时间轴仍按字幕顺序累积）")

    executor = ThreadPoolExecutor(max_workers=speed_workers)
    futures = {
        key: executor.submit(adjust_audio_speed, src, dst, sf)
        for key, src, dst, sf in jobs
    }
    chunk_start = 0
    try:
        for index, row in tasks_df.iterrows():
            if row['cut_off'] == 1:
                check_cancel()
                chunk_df = tasks_df.iloc[chunk_start:index+1].reset_index(drop=True)
                speed_factor, keep_gaps, chunk_end_time = plan[index]
                chunk_start_time = float(parse_df_srt_time(chunk_df.iloc[0]['start_time']))
                cur_time = float(chunk_start_time)
                for i, row in chunk_df.iterrows():
                    if i != 0 and keep_gaps:
                        cur_time += float(chunk_df.iloc[i-1]['gap'])/speed_factor
                    new_sub_times = []
                    number = int(row['number'])
                    lines = parse_excel_list(row['lines'])
                    for line_index, line in enumerate(lines):
                        output_file = OUTPUT_FILE_TEMPLATE.format(f"{number}_{line_index}")
                        # 每个文件只由一个任务写；这里按序等它完成再读时长。
                        futures.pop((number, line_index)).result()
                        ad_dur = float(wav_duration(output_file))
                        new_sub_times.append([float(cur_time), float(cur_time+ad_dur)])
                        cur_time += ad_dur
                    main_df_idx = tasks_df[tasks_df['number'] == row['number']].index[0]
                    tasks_df.at[main_df_idx, 'new_sub_times'] = plain_time_pairs(new_sub_times)
                emoji = "⚡" if speed_factor <= accept else "⚠️"
                rprint(f"[cyan]{emoji} Processed chunk {chunk_start} to {index} with speed factor {speed_factor}[/cyan]")
                if cur_time > chunk_end_time:
                    time_diff = cur_time - chunk_end_time
                    if time_diff <= 0.6:
                        rprint(f"[yellow]⚠️ Chunk {chunk_start} to {index} exceeds by {time_diff:.3f}s, truncating last audio[/yellow]")
                        last_number = tasks_df.iloc[index]['number']
                        last_lines = parse_excel_list(tasks_df.iloc[index]['lines'])
                        last_line_index = len(last_lines) - 1
                        last_file = OUTPUT_FILE_TEMPLATE.format(f"{last_number}_{last_line_index}")
                        audio = AudioSegment.from_wav(last_file)
                        original_duration = len(audio) / 1000
                        new_duration = original_duration - time_diff
                        trimmed_audio = audio[:(new_duration * 1000)]
                        trimmed_audio.export(last_file, format="wav")
                        last_times = tasks_df.at[index, 'new_sub_times']
                        last_times[-1][1] = float(chunk_end_time)
                        tasks_df.at[index, 'new_sub_times'] = plain_time_pairs(last_times)
                    else:
                        raise Exception(f"Chunk {chunk_start} to {index} exceeds the chunk end time {chunk_end_time:.2f} seconds with current time {cur_time:.2f} seconds")
                chunk_start = index+1
    finally:
        # Cancel queued jobs; let active ffmpeg writers finish before returning.
        _shutdown_speed_executor(executor)

    rprint("[bold green]✅ Audio chunks processing completed![/bold green]")
    return tasks_df

def gen_audio() -> None:
    """Main function: Generate audio and process timeline"""
    rprint("[bold magenta]🚀 Starting audio generation process...[/bold magenta]")
    
    # 🎯 Step1: Create necessary directories
    os.makedirs(_AUDIO_TMP_DIR, exist_ok=True)
    os.makedirs(_AUDIO_SEGS_DIR, exist_ok=True)
    
    # 📝 Step2: Load task file
    tasks_df = pd.read_excel(_8_1_AUDIO_TASK)
    rprint("[green]📊 Loaded task file successfully[/green]")
    
    # 🔊 Step3: Generate TTS audio
    tasks_df = generate_tts_audio(tasks_df)
    
    # 🔄 Step4: Merge audio chunks
    tasks_df = merge_chunks(tasks_df)
    
    # 💾 Step5: Save results
    tasks_df.to_excel(_8_1_AUDIO_TASK, index=False)
    rprint("[bold green]🎉 Audio generation completed successfully![/bold green]")

if __name__ == "__main__":
    gen_audio()
