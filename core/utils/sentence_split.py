"""LLM sentence splitting — shared tool, not a pipeline step.

Extracted from core/_3_2_split_meaning.py (2026-09-08). Both step 3-2
(pre-translation safety valve) and step 5 (post-translation subtitle
shortening) need "split this sentence into N natural parts via the LLM";
that capability now lives here and steps only orchestrate.

Note: this module lives inside the core.utils package, so it imports from
core.utils submodules directly — `from core.utils import *` would be
circular.
"""
from difflib import SequenceMatcher

from rich.console import Console
from rich.table import Table

from core.prompts import get_split_prompt
from core.utils.ask_gpt import ask_gpt
from core.utils.config_utils import load_key, get_joiner

console = Console()


def tokenize_sentence(sentence, nlp):
    doc = nlp(sentence)
    return [token.text for token in doc]


def find_split_positions(original, modified):
    split_positions = []
    parts = modified.split('[br]')
    start = 0
    whisper_language = load_key("whisper.language")
    language = load_key("whisper.detected_language") if whisper_language == 'auto' else whisper_language
    joiner = get_joiner(language)

    for i in range(len(parts) - 1):
        max_similarity = 0
        best_split = None

        for j in range(start, len(original)):
            original_left = original[start:j]
            modified_left = joiner.join(parts[i].split())

            left_similarity = SequenceMatcher(None, original_left, modified_left).ratio()

            if left_similarity > max_similarity:
                max_similarity = left_similarity
                best_split = j

        if max_similarity < 0.9:
            console.print(f"[yellow]Warning: low similarity found at the best split point: {max_similarity}[/yellow]")
        if best_split is not None:
            split_positions.append(best_split)
            start = best_split
        else:
            console.print(f"[yellow]Warning: Unable to find a suitable split point for the {i+1}th part.[/yellow]")

    return split_positions


def split_sentence(sentence, num_parts, word_limit=20, index=-1, retry_attempt=0):
    """Split a long sentence using GPT and return the result as a string."""
    split_prompt = get_split_prompt(sentence, num_parts, word_limit)
    def valid_split(response_data):
        choice = response_data["choice"]
        if f'split{choice}' not in response_data:
            return {"status": "error", "message": "Missing required key: `split`"}
        if "[br]" not in response_data[f"split{choice}"]:
            return {"status": "error", "message": "Split failed, no [br] found"}
        return {"status": "success", "message": "Split completed"}

    response_data = ask_gpt(split_prompt + " " * retry_attempt, resp_type='json', valid_def=valid_split, log_title='split_by_meaning')
    choice = response_data["choice"]
    best_split = response_data[f"split{choice}"]
    split_points = find_split_positions(sentence, best_split)
    # split the sentence based on the split points
    for i, split_point in enumerate(split_points):
        if i == 0:
            best_split = sentence[:split_point] + '\n' + sentence[split_point:]
        else:
            parts = best_split.split('\n')
            last_part = parts[-1]
            parts[-1] = last_part[:split_point - split_points[i-1]] + '\n' + last_part[split_point - split_points[i-1]:]
            best_split = '\n'.join(parts)
    if index != -1:
        console.print(f'[green]✅ Sentence {index} has been successfully split[/green]')
    table = Table(title="")
    table.add_column("Type", style="cyan")
    table.add_column("Sentence")
    table.add_row("Original", sentence, style="yellow")
    table.add_row("Split", best_split.replace('\n', ' ||'), style="yellow")
    console.print(table)

    return best_split
