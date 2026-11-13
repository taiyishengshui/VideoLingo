import pandas as pd
import math
from typing import List, Tuple
import concurrent.futures

from core.utils.sentence_split import split_sentence
from core.prompts import get_align_prompt
from rich.panel import Panel
from rich.console import Console
from rich.table import Table
from core.utils import *
from core.utils.models import *
console = Console()

# ! You can modify your own weights here
# Chinese and Japanese 2.5 characters, Korean 2 characters, Thai 1.5 characters, full-width symbols 2 characters, other English-based and half-width symbols 1 character
def calc_len(text: str) -> float:
    text = str(text) # force convert
    def char_weight(char):
        code = ord(char)
        if 0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF:  # Chinese and Japanese
            return 1.75
        elif 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF:  # Korean
            return 1.5
        elif 0x0E00 <= code <= 0x0E7F:  # Thai
            return 1
        elif 0xFF01 <= code <= 0xFF5E:  # full-width symbols
            return 1.75
        else:  # other characters (e.g. English and half-width symbols)
            return 1

    return sum(char_weight(char) for char in text)

def get_target_parts(response_data, num_parts):
    """Require exactly one nonempty translated string for every source part."""
    aligned = response_data.get('align') if isinstance(response_data, dict) else None
    if not isinstance(aligned, list) or len(aligned) != num_parts:
        return None
    parts = []
    for i, item in enumerate(aligned):
        part = item.get(f'target_part_{i+1}') if isinstance(item, dict) else None
        if not isinstance(part, str) or not part.strip():
            return None
        parts.append(part.strip())
    return parts


def align_subs(src_sub: str, tr_sub: str, src_part: str) -> Tuple[List[str], List[str], str]:
    align_prompt = get_align_prompt(src_sub, tr_sub, src_part)
    
    src_parts = src_part.split('\n')
    
    def valid_align(response_data):
        if get_target_parts(response_data, len(src_parts)) is None:
            return {"status": "error", "message": f"Expected {len(src_parts)} nonempty target parts"}
        return {"status": "success", "message": "Align completed"}
    parsed = ask_gpt(align_prompt, resp_type='json', valid_def=valid_align, log_title='align_subs')
    tr_parts = get_target_parts(parsed, len(src_parts))
    if tr_parts is None:
        raise ValueError("Subtitle alignment contains empty or mismatched target parts")
    
    whisper_language = load_key("whisper.language")
    language = load_key("whisper.detected_language") if whisper_language == 'auto' else whisper_language
    joiner = get_joiner(language)
    tr_remerged = joiner.join(tr_parts)
    
    table = Table(title="🔗 Aligned parts")
    table.add_column("Language", style="cyan")
    table.add_column("Parts", style="magenta")
    table.add_row("SRC_LANG", "\n".join(src_parts))
    table.add_row("TARGET_LANG", "\n".join(tr_parts))
    console.print(table)
    
    return src_parts, tr_parts, tr_remerged

def split_align_subs(src_lines: List[str], tr_lines: List[str]):
    subtitle_set = load_key("subtitle")
    MAX_SUB_LENGTH = subtitle_set["max_length"]
    TARGET_SUB_MULTIPLIER = subtitle_set["target_multiplier"]
    remerged_tr_lines = tr_lines.copy()
    
    to_split = []
    for i, (src, tr) in enumerate(zip(src_lines, tr_lines)):
        src, tr = str(src), str(tr)
        if len(src) > MAX_SUB_LENGTH or calc_len(tr) * TARGET_SUB_MULTIPLIER > MAX_SUB_LENGTH:
            to_split.append(i)
            table = Table(title=f"📏 Line {i} needs to be split")
            table.add_column("Type", style="cyan")
            table.add_column("Content", style="magenta")
            table.add_row("Source Line", src)
            table.add_row("Target Line", tr)
            console.print(table)
    
    @except_handler("Error in split_align_subs")
    def process(i):
        # 2026-09-08: num_parts is now derived from how much the line exceeds
        # the subtitle budget (both sides), instead of a fixed 2. Pre-translation
        # splitting was relaxed so complete sentences reach the translator; this
        # step is now the one place where shortening happens, and it must be
        # able to cut a long line into as many parts as needed in one pass.
        src, tr = str(src_lines[i]), str(tr_lines[i])
        overflow = max(len(src), calc_len(tr) * TARGET_SUB_MULTIPLIER)
        n_parts = max(2, math.ceil(overflow / MAX_SUB_LENGTH))
        word_budget = max(3, math.ceil(len(src.split()) / n_parts))
        split_src = split_sentence(src, num_parts=n_parts, word_limit=word_budget).strip()
        source_parts = [part.strip() for part in split_src.split('\n') if part.strip()]
        if len(source_parts) < 2 or ''.join(''.join(source_parts).split()) != ''.join(src.split()):
            console.print(f"[yellow]Line {i} split is not lossless; keeping original[/yellow]")
            return
        src_parts, tr_parts, tr_remerged = align_subs(src, tr, '\n'.join(source_parts))
        if len(src_parts) != len(tr_parts) or any(not part for part in tr_parts):
            raise ValueError("Source and target subtitle parts do not match")
        src_lines[i] = src_parts
        tr_lines[i] = tr_parts
        remerged_tr_lines[i] = tr_remerged

    if to_split:
        # NOTE: executor.map() is lazy — its result iterator MUST be consumed
        # for the work to actually run. Previously the return value was
        # discarded, so no splitting ever happened. Use explicit futures and
        # consume every result (and every raised exception) instead.
        with concurrent.futures.ThreadPoolExecutor(max_workers=load_key("max_workers")) as executor:
            futures = {executor.submit(process, i): i for i in to_split}
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                try:
                    exc = future.exception()
                except concurrent.futures.CancelledError:
                    exc = concurrent.futures.CancelledError(f"line {i} cancelled")
                if exc is not None:
                    console.print(f"[yellow]⚠️ Split of line {i} failed after retries, keeping original line:[/yellow]")
                    console.print(f"[yellow]  {exc}[/yellow]")
                    # Leave this line unsplit; flatten handles plain strings.
    
    # Flatten `src_lines` and `tr_lines`
    src_lines = [item for sublist in src_lines for item in (sublist if isinstance(sublist, list) else [sublist])]
    tr_lines = [item for sublist in tr_lines for item in (sublist if isinstance(sublist, list) else [sublist])]
    
    return src_lines, tr_lines, remerged_tr_lines

def split_for_sub_main():
    console.print("[bold green]🚀 Start splitting subtitles...[/bold green]")
    
    df = pd.read_excel(_4_2_TRANSLATION)
    src = df['Source'].tolist()
    trans = df['Translation'].tolist()
    
    subtitle_set = load_key("subtitle")
    MAX_SUB_LENGTH = subtitle_set["max_length"]
    TARGET_SUB_MULTIPLIER = subtitle_set["target_multiplier"]
    
    for attempt in range(3):  # 多次切割
        console.print(Panel(f"🔄 Split attempt {attempt + 1}", expand=False))
        split_src, split_trans, remerged = split_align_subs(src.copy(), trans)
        
        # 检查是否所有字幕都符合长度要求
        if all(len(src) <= MAX_SUB_LENGTH for src in split_src) and \
           all(calc_len(tr) * TARGET_SUB_MULTIPLIER <= MAX_SUB_LENGTH for tr in split_trans):
            break
        
        # 更新源数据继续下一轮分割
        src, trans = split_src, split_trans

    # 确保二者有相同的长度，防止报错
    if len(src) > len(remerged):
        remerged += [None] * (len(src) - len(remerged))
    elif len(remerged) > len(src):
        src += [None] * (len(remerged) - len(src))
    
    pd.DataFrame({'Source': split_src, 'Translation': split_trans}).to_excel(_5_SPLIT_SUB, index=False)
    pd.DataFrame({'Source': src, 'Translation': remerged}).to_excel(_5_REMERGED, index=False)

if __name__ == '__main__':
    split_for_sub_main()
