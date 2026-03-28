"""
teneo-intel — CLI entrypoint

Usage:
    python main.py [--source SOURCE] [--diff]

Flags:
    --source    Comma-separated list of sources to run.
                Options: github, onchain, docs, console, social, competitors
                Default: all sources

    --diff      Compare current run against the most recent JSON snapshot in
                /data/ and include a change summary at the top of the report.

Examples:
    python main.py
    python main.py --source github,onchain
    python main.py --diff
    python main.py --source github --diff
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

# Scrapers (imported lazily inside run() to allow partial --source runs)
from scrapers import console as console_scraper
from scrapers import github as github_scraper
from scrapers import social as social_scraper
from scrapers import docs as docs_scraper
from scrapers import onchain as onchain_scraper
from scrapers import competitors as competitors_scraper

# Report & diff
from reports.generator import build_report, save_snapshot, load_previous_snapshot, build_diff

RICH = Console()

ALL_SOURCES = ["github", "onchain", "docs", "console", "social", "competitors"]


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="teneo-intel",
        description="Teneo Protocol ecosystem intelligence CLI.",
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Comma-separated sources to run. Defaults to all.",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        default=False,
        help="Load previous snapshot and include a diff in the report.",
    )
    return parser.parse_args()


async def run(sources: list[str], include_diff: bool) -> None:
    """Orchestrate all scrapers and generate the report."""
    run_timestamp = datetime.utcnow()
    RICH.print(
        Panel(
            f"[bold cyan]teneo-intel[/] — run started at [green]{run_timestamp.isoformat()}Z[/]",
            expand=False,
        )
    )

    results: dict = {"_meta": {"timestamp": run_timestamp.isoformat(), "sources_requested": sources}}
    data_log: dict = {}  # tracks success / failure per source

    # --- GitHub ---
    if "github" in sources:
        try:
            results["github"] = await github_scraper.scrape()
            data_log["github"] = "success"
        except Exception as exc:
            RICH.print(f"[red][github] failed:[/] {exc}")
            data_log["github"] = f"error: {exc}"

    # --- On-chain (Solscan) ---
    if "onchain" in sources:
        try:
            results["onchain"] = await onchain_scraper.scrape()
            data_log["onchain"] = "success"
        except Exception as exc:
            RICH.print(f"[red][onchain] failed:[/] {exc}")
            data_log["onchain"] = f"error: {exc}"

    # --- Docs ---
    if "docs" in sources:
        try:
            results["docs"] = await docs_scraper.scrape()
            data_log["docs"] = "success"
        except Exception as exc:
            RICH.print(f"[red][docs] failed:[/] {exc}")
            data_log["docs"] = f"error: {exc}"

    # --- Agent Console ---
    if "console" in sources:
        try:
            results["console"] = await console_scraper.scrape()
            data_log["console"] = "success"
        except Exception as exc:
            RICH.print(f"[red][console] failed:[/] {exc}")
            data_log["console"] = f"error: {exc}"

    # --- Social / X via Nitter ---
    if "social" in sources:
        try:
            results["social"] = await social_scraper.scrape()
            data_log["social"] = "success"
        except Exception as exc:
            RICH.print(f"[red][social] failed:[/] {exc}")
            data_log["social"] = f"error: {exc}"

    # --- Competitors ---
    if "competitors" in sources:
        try:
            results["competitors"] = await competitors_scraper.scrape()
            data_log["competitors"] = "success"
        except Exception as exc:
            RICH.print(f"[red][competitors] failed:[/] {exc}")
            data_log["competitors"] = f"error: {exc}"

    results["_data_log"] = data_log

    # --- Diff against previous snapshot ---
    diff_data = None
    if include_diff:
        prev = load_previous_snapshot()
        if prev:
            diff_data = build_diff(prev, results)
            RICH.print("[cyan]Diff computed against previous snapshot.[/]")
        else:
            RICH.print("[yellow]No previous snapshot found — skipping diff.[/]")

    # --- Save this run's snapshot ---
    snapshot_path = save_snapshot(results, run_timestamp)
    RICH.print(f"[green]Snapshot saved:[/] {snapshot_path}")

    # --- Generate report ---
    report_path = build_report(results, diff_data, run_timestamp)
    RICH.print(f"[bold green]Report written:[/] {report_path}")


def main() -> None:
    """CLI entry point."""
    args = parse_args()

    if args.source:
        sources = [s.strip().lower() for s in args.source.split(",")]
        invalid = [s for s in sources if s not in ALL_SOURCES]
        if invalid:
            RICH.print(f"[red]Unknown sources:[/] {', '.join(invalid)}")
            RICH.print(f"Valid options: {', '.join(ALL_SOURCES)}")
            sys.exit(1)
    else:
        sources = ALL_SOURCES

    asyncio.run(run(sources, include_diff=args.diff))


if __name__ == "__main__":
    main()
