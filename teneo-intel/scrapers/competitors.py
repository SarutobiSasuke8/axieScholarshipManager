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

from scrapers import github as github_scraper
from scrapers import social as social_scraper

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


def _extract_social_summary(social_result: dict) -> dict:
    """Distil a social.scrape() result into the compact summary used in the benchmark table."""
    # mentions_7d: sum post_count for the most recent week bucket
    mentions_7d = 0
    weekly = social_result.get("weekly_volumes", [])
    if weekly:
        # Last entry in the list is the most recent week
        most_recent = weekly[-1]
        mentions_7d = most_recent.get("post_count", 0)

    sentiment = social_result.get("sentiment", {})
    sentiment_ratio = sentiment.get("ratio")

    return {
        "mentions_7d": mentions_7d,
        "sentiment_ratio": sentiment_ratio,
        "nitter_unavailable": social_result.get("nitter_unavailable", False),
        "notes": social_result.get("notes", []),
    }


async def scrape() -> dict[str, Any]:
    """Collect GitHub and social benchmark metrics for all competitor projects.

    Reuses helpers from github.py and social.py with competitor-specific
    parameters. Each competitor's data collection is independent — a failure
    for one does not abort the others.

    Returns a structured dict with per-competitor results and a comparison
    table ready for the report generator.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    notes: list[str] = []
    competitor_results: list[dict] = []

    for comp in COMPETITORS:
        name = comp["name"]
        github_org = comp["github_org"]
        twitter_handle = comp["twitter_handle"]
        twitter_search = comp["twitter_search"]

        RICH.print(f"[cyan][competitors] Processing {name}...[/]")

        comp_error: str | None = None
        github_result: dict | None = None
        social_result: dict | None = None

        # --- GitHub ---
        try:
            raw_github = await github_scraper.scrape(org=github_org, max_repos=15)
            if "error" in raw_github:
                github_result = {"error": raw_github["error"], "aggregates": {}}
                notes.append(f"{name} GitHub: {raw_github['error']}")
            else:
                github_result = {
                    "aggregates": raw_github.get("aggregates", {}),
                    "repo_count": len(raw_github.get("repos", [])),
                }
        except Exception as exc:
            err_msg = f"GitHub scrape failed for {name}: {exc}"
            RICH.print(f"[red][competitors] {err_msg}[/]")
            notes.append(err_msg)
            github_result = {"error": str(exc), "aggregates": {}}

        # --- Social ---
        try:
            raw_social = await social_scraper.scrape(queries=[twitter_search])
            social_result = _extract_social_summary(raw_social)
        except Exception as exc:
            err_msg = f"Social scrape failed for {name}: {exc}"
            RICH.print(f"[red][competitors] {err_msg}[/]")
            notes.append(err_msg)
            social_result = {
                "mentions_7d": None,
                "sentiment_ratio": None,
                "nitter_unavailable": True,
                "notes": [str(exc)],
            }

        competitor_results.append(
            {
                "name": name,
                "github_org": github_org,
                "twitter_handle": twitter_handle,
                "github": github_result,
                "social": social_result,
                "error": comp_error,
            }
        )

        RICH.print(f"[cyan][competitors] Done with {name}[/]")

    return {
        "competitors": competitor_results,
        "fetched_at": fetched_at,
        "notes": notes,
    }
