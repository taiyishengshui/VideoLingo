"""Progress reporting that survives a captured stdout.

The server swaps sys.stdout for a capture object while a pipeline step runs
(server/task_manager.py -> _StreamCapture) and forwards each line to the UI
over the WebSocket. rich detects "not a terminal" there and stops redrawing
its bar, so the UI used to see a single frozen frame and no counts at all.

`ChunkProgress` is THE implementation of "show how far we are":
a real bar in a terminal, plus one plain text line per unit when stdout is
captured. Steps must not hand-roll their own Progress(...) for this.
"""

from __future__ import annotations

from rich.console import Console
from rich.progress import (Progress, SpinnerColumn, TextColumn, BarColumn,
                           MofNCompleteColumn, TaskProgressColumn,
                           TimeElapsedColumn)


class ChunkProgress:
    """Count-based progress with a plain-text fallback for captured stdout.

    Usage:
        with ChunkProgress("Translating chunks", total=len(todo)) as p:
            for future in as_completed(futures):
                ...
                p.advance()
    """

    def __init__(self, label: str, total: int, unit: str = "块",
                 console: Console | None = None, transient: bool = True):
        self.label = label
        self.total = total
        self.unit = unit
        self.done = 0
        self.console = console or Console()
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(), MofNCompleteColumn(), TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self.console, transient=transient)
        self._task = None

    def __enter__(self) -> "ChunkProgress":
        self._progress.start()
        self._task = self._progress.add_task(
            f"{self.label} 0/{self.total}", total=self.total)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._progress.stop()
        return False

    def advance(self, n: int = 1):
        self.done += n
        left = max(self.total - self.done, 0)
        self._progress.update(
            self._task, advance=n,
            description=f"{self.label} {self.done}/{self.total} (left {left})")
        if not self.console.is_terminal:
            # captured stdout: the bar never gets redrawn, so print one line
            self.console.print(
                f"✅ {self.done}/{self.total} 完成，剩 {left} {self.unit}")
