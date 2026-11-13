"""Audio subtitle trimming — shared tool, not a pipeline step.

Extracted from core/_8_1_audio_task.py (2026-09-08). Step 8-1 uses
time_diff_seconds to build audio tasks; step 4-2 uses check_len_then_trim
to keep translated lines speakable within their time budget.

Note: this module lives inside the core.utils package, so it imports from
core.utils submodules directly — `from core.utils import *` would be
circular.
"""
import datetime
import re

from core.utils.log_hooks import rprint
from rich.console import Console
from rich.panel import Panel

from core.prompts import get_subtitle_trim_prompt
from core.tts_backend.estimate_duration import init_estimator, estimate_duration
from core.utils.ask_gpt import ask_gpt
from core.utils.config_utils import load_key

console = Console()
speed_factor = load_key("speed_factor")

ESTIMATOR = None


def check_len_then_trim(text, duration, verbose=False):
    """Shorten `text` when it cannot be spoken within `duration`.

    `verbose` controls ONLY the per-line "Subtitle text: ..." trace. The caller
    runs this over thousands of lines, and rich re-parses markup on every
    console.print — a print on every line dominated step 4-2's runtime and
    flooded the UI log. Lines that actually need shortening always log (the
    Panel below), so nothing is silently lost; overall progress comes from
    ChunkProgress in the caller.
    """
    global ESTIMATOR
    if ESTIMATOR is None:
        ESTIMATOR = init_estimator()
    estimated_duration = estimate_duration(text, ESTIMATOR) / speed_factor['max']

    if estimated_duration > duration:
        if verbose:
            console.print(f"Subtitle text: {text}, "
                          f"[bold green]Estimated reading duration: {estimated_duration:.2f} seconds[/bold green]")
        rprint(Panel(f"Estimated reading duration {estimated_duration:.2f} seconds exceeds given duration {duration:.2f} seconds, shortening...", title="Processing", border_style="yellow"))
        original_text = text
        prompt = get_subtitle_trim_prompt(text, duration)
        def valid_trim(response):
            if 'result' not in response:
                return {'status': 'error', 'message': 'No result in response'}
            return {'status': 'success', 'message': ''}
        try:
            response = ask_gpt(prompt, resp_type='json', log_title='sub_trim', valid_def=valid_trim)
            shortened_text = response['result']
        except Exception:
            rprint("[bold red]🚫 AI refused to answer due to sensitivity, so manually remove punctuation[/bold red]")
            shortened_text = re.sub(r'[,.!?;:，。！？；：]', ' ', text).strip()
        rprint(Panel(f"Subtitle before shortening: {original_text}\nSubtitle after shortening: {shortened_text}", title="Subtitle Shortening Result", border_style="green"))
        return shortened_text
    else:
        return text


def time_diff_seconds(t1, t2, base_date):
    """Calculate the difference in seconds between two time objects"""
    dt1 = datetime.datetime.combine(base_date, t1)
    dt2 = datetime.datetime.combine(base_date, t2)
    return (dt2 - dt1).total_seconds()
