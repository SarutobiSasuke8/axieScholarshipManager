"""
reports/generator.py

Builds the Markdown ecosystem audit report and manages JSON snapshots.

Public interface
----------------
build_report(results, diff_data, run_timestamp) -> Path
    Writes  ecosystem_audit_YYYY-MM-DD.md  to the repo root and returns the path.

save_snapshot(results, run_timestamp) -> Path
    Serialises `results` to  data/snapshot_YYYY-MM-DDTHH-MM-SS.json.

load_previous_snapshot() -> dict | None
    Returns the most recently saved snapshot dict, or None if no prior run.

build_diff(previous, current) -> dict
    Compares two result dicts and returns a diff structure containing:
        improved  (list[dict]) — metrics that went up
        declined  (list[dict]) — metrics that went down
        unchanged (list[dict]) — metrics with no change
        summary   (str)        — one-line "what changed this week"

Report sections
---------------
1. Header + run metadata
2. Diff summary (only if diff_data is provided)
3. Executive summary — 5 bullet points
4. Teneo sources:
     - GitHub
     - On-chain
     - Docs
     - Agent Console
     - Social / X
5. Competitor benchmark table
6. Signals to watch
7. Data quality log

Trend symbols used inline:
    🟢  improved
    🔴  declined
    ⚪  unchanged
    ❓  unknown / no prior data
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

RICH = Console()

DATA_DIR = Path(__file__).parent.parent / "data"
REPORT_DIR = Path(__file__).parent.parent


def save_snapshot(results: dict, run_timestamp: datetime) -> Path:
    """Serialise results dict to a timestamped JSON file in /data/.

    Args:
        results: The full results dict from main.run().
        run_timestamp: UTC datetime of the run.

    Returns:
        Path to the written snapshot file.
    """
    raise NotImplementedError("save_snapshot not yet implemented — scaffold only.")


def load_previous_snapshot() -> dict[str, Any] | None:
    """Load the most recently saved snapshot from /data/.

    Returns None if the directory is empty or no snapshots exist.
    """
    raise NotImplementedError("load_previous_snapshot not yet implemented — scaffold only.")


def build_diff(previous: dict, current: dict) -> dict[str, Any]:
    """Compare two snapshots and return a structured diff.

    Args:
        previous: Older snapshot dict.
        current:  Newer (current run) dict.

    Returns:
        dict with keys: improved, declined, unchanged, summary (str).
    """
    raise NotImplementedError("build_diff not yet implemented — scaffold only.")


def build_report(
    results: dict[str, Any],
    diff_data: dict[str, Any] | None,
    run_timestamp: datetime,
) -> Path:
    """Build and write the Markdown ecosystem audit report.

    Args:
        results:       Full results dict from main.run().
        diff_data:     Output of build_diff(), or None for first run.
        run_timestamp: UTC datetime of the run.

    Returns:
        Path to the written .md file.
    """
    raise NotImplementedError("build_report not yet implemented — scaffold only.")
