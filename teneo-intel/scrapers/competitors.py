"""
scrapers/competitors.py

Collects the same GitHub and social signals as the Teneo scrapers, but
applied to three comparable AI-agent / DePIN networks. Used to produce the
benchmark comparison table in the report.

Competitors tracked:
  ┌─────────────────┬──────────────────┬────────────────────┐
  │ Project         │ GitHub org       │ Twitter handle     │
  ├─────────────────┼──────────────────┼────────────────────┤
  │ Bittensor / TAO │ opentensor       │ @bittensor_        │
  │ Fetch.ai        │ fetchai          │ @Fetch_ai          │
  │ Autonolas/Olas  │ valory-xyz       │ @autonolas         │
  └─────────────────┴──────────────────┴────────────────────┘

This module reuses logic from github.py and social.py rather than
duplicating HTTP calls. It calls the same underlying helpers with
different org/query parameters.

Metrics collected per competitor (mirrors Teneo):
  GitHub:
    - total_stars, total_forks
    - commits_7d, commits_30d, commits_90d
    - unique_contributors_90d

  Social (Nitter):
    - mentions_7d (post count, last 7 days)
    - sentiment_ratio (positive / (positive + negative))

Returns:
    dict with keys:
        competitors (list[dict]) — one entry per competitor, each containing:
            name (str)
            github_org (str)
            twitter_handle (str)
            github (dict)   — same structure as github.scrape() aggregates
            social (dict)   — {mentions_7d, sentiment_ratio, notes}
        fetched_at (str ISO timestamp)
        notes (list[str])
"""

from datetime import datetime, timezone
from typing import Any

from rich.console import Console

RICH = Console()

COMPETITORS = [
    {
        "name": "Bittensor / TAO",
        "github_org": "opentensor",
        "twitter_handle": "bittensor_",
        "twitter_search": "bittensor TAO",
    },
    {
        "name": "Fetch.ai",
        "github_org": "fetchai",
        "twitter_handle": "Fetch_ai",
        "twitter_search": "fetch.ai FET",
    },
    {
        "name": "Autonolas / Olas",
        "github_org": "valory-xyz",
        "twitter_handle": "autonolas",
        "twitter_search": "autonolas olas",
    },
]


async def scrape() -> dict[str, Any]:
    """Collect GitHub and social benchmark metrics for all competitor projects.

    Reuses helpers from github.py and social.py with competitor-specific
    parameters. Each competitor's data collection is independent — a failure
    for one does not abort the others.

    Returns a structured dict with per-competitor results and a comparison
    table ready for the report generator.
    """
    raise NotImplementedError("Competitors scraper not yet implemented — scaffold only.")
