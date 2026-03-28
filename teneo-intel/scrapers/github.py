"""
scrapers/github.py

Pulls public data from the GitHub API for the Teneo Protocol organisation.

Target org: TeneoProtocol (confirm slug with team if results are empty)
Base URL:   https://api.github.com

Metrics collected per repo:
  - name, description, stars, forks, open_issues_count
  - last_push_at  → flag as stale if > 30 days ago
  - commits in past 7 / 30 / 90 days  (via /repos/{org}/{repo}/commits?since=)
  - unique contributor count in past 90 days  (via /repos/{org}/{repo}/contributors)
  - open vs closed issues ratio

Aggregated across all repos:
  - total_stars, total_forks
  - commits_7d, commits_30d, commits_90d
  - unique_contributors_90d
  - stale_repos  (list of repos not pushed to in >30 days)

Rate limits:
  GitHub public API allows ~60 req/hour unauthenticated.
  We use conditional requests and batch carefully to stay within limits.
  If GITHUB_TOKEN env var is set, it will be picked up automatically
  for higher limits — but it is NOT required.

Returns:
    dict with keys: org, repos (list), aggregates, fetched_at
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from rich.console import Console
from rich.progress import track

RICH = Console()

GITHUB_API = "https://api.github.com"
ORG = "TeneoProtocol"  # TODO: confirm exact org slug with team


async def scrape() -> dict[str, Any]:
    """Fetch GitHub org and per-repo metrics for Teneo Protocol.

    Returns a structured dict with per-repo details and rolled-up aggregates.
    Raises httpx.HTTPError on unrecoverable network failure (caller handles).
    """
    raise NotImplementedError("GitHub scraper not yet implemented — scaffold only.")
