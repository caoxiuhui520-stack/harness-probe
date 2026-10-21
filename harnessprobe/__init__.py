"""HarnessProbe — measure how much a model's measured quality depends on its harness."""

__version__ = "0.1.0"

from .analysis import analyze, render_markdown, render_per_task
from .spec import HarnessConfig, Task, TrialResult

__all__ = [
    "__version__",
    "HarnessConfig",
    "Task",
    "TrialResult",
    "analyze",
    "render_markdown",
    "render_per_task",
]
