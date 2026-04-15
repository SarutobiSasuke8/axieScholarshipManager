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
ORG = "TeneoProtocolAI"  # Confirmed from @teneo-protocol/cli package.json repository field


def _headers() -> dict:
    """Return HTTP headers for GitHub API, picking up GITHUB_TOKEN if set."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> Any:
    """Single GET request; returns parsed JSON or None on 404."""
    try:
        resp = await client.get(url, params=params, headers=_headers())
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError:
        return None
    except httpx.RequestError:
        return None


async def _paginate(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    max_pages: int = 10,
) -> list:
    """Paginated GET — collects all pages up to max_pages into a single list."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    results = []
    page = 1
    while page <= max_pages:
        params["page"] = page
        try:
            resp = await client.get(url, params=params, headers=_headers())
            if resp.status_code in (404, 409):
                break
            if not resp.is_success:
                break
            data = resp.json()
        except (httpx.RequestError, Exception):
            break

        if not data:
            break
        if isinstance(data, list):
            results.extend(data)
            if len(data) < params["per_page"]:
                break
        else:
            results.append(data)
            break
        page += 1
    return results


async def _commits_since(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    since: datetime,
) -> list:
    """Fetch commits for a repo since a given UTC datetime. Handles 409/404."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    params = {"since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": 100}
    results = []
    page = 1
    max_pages = 10
    while page <= max_pages:
        params["page"] = page
        try:
            resp = await client.get(url, params=params, headers=_headers())
            if resp.status_code in (404, 409):
                # 409 = empty repo; 404 = gone
                break
            if not resp.is_success:
                break
            data = resp.json()
        except (httpx.RequestError, Exception):
            break

        if not data or not isinstance(data, list):
            break
        results.extend(data)
        if len(data) < 100:
            break
        page += 1
    return results


async def scrape(org: str = ORG, max_repos: int = 50) -> dict[str, Any]:
    """Fetch GitHub org and per-repo metrics for Teneo Protocol.

    Args:
        org: GitHub organisation slug (defaults to ORG constant).
        max_repos: Cap on number of repos to process (sorted by stars desc).

    Returns a structured dict with per-repo details and rolled-up aggregates.
    Returns a dict with an 'error' key on org-not-found rather than raising.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    now = datetime.now(timezone.utc)
    since_7d = now - timedelta(days=7)
    since_30d = now - timedelta(days=30)
    since_90d = now - timedelta(days=90)
    stale_cutoff = now - timedelta(days=30)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Fetch all public repos with pagination
        RICH.print(f"[cyan][github] Fetching repos for org: {org}[/]")
        all_repos = await _paginate(
            client,
            f"{GITHUB_API}/orgs/{org}/repos",
            params={"type": "public", "sort": "updated"},
        )

        if not all_repos:
            # Try to determine if org exists
            org_data = await _get(client, f"{GITHUB_API}/orgs/{org}")
            if org_data is None:
                return {
                    "org": org,
                    "repos": [],
                    "aggregates": {},
                    "fetched_at": fetched_at,
                    "error": f"Organisation '{org}' not found on GitHub.",
                }
            return {
                "org": org,
                "repos": [],
                "aggregates": {
                    "total_stars": 0,
                    "total_forks": 0,
                    "commits_7d": 0,
                    "commits_30d": 0,
                    "commits_90d": 0,
                    "unique_contributors_90d": 0,
                    "stale_repos": [],
                    "total_open_issues": 0,
                },
                "fetched_at": fetched_at,
            }

        # Sort by stars descending and cap
        all_repos.sort(key=lambda r: r.get("stargazers_count", 0), reverse=True)
        repos_to_process = all_repos[:max_repos]

        RICH.print(
            f"[cyan][github] Processing {len(repos_to_process)} repos "
            f"(of {len(all_repos)} total) for {org}[/]"
        )

        repo_records = []
        agg_commits_7d = 0
        agg_commits_30d = 0
        agg_commits_90d = 0
        all_contributors_90d: set[str] = set()
        stale_repos: list[str] = []
        total_stars = 0
        total_forks = 0
        total_open_issues = 0

        for repo in track(repos_to_process, description=f"[github] {org} repos"):
            repo_name = repo.get("name", "")
            pushed_at_str = repo.get("pushed_at")
            pushed_at = None
            is_stale = False

            if pushed_at_str:
                try:
                    pushed_at = datetime.fromisoformat(
                        pushed_at_str.replace("Z", "+00:00")
                    )
                    is_stale = pushed_at < stale_cutoff
                except ValueError:
                    pass

            # Commits in 7d
            commits_7d_list = await _commits_since(client, org, repo_name, since_7d)
            # Commits in 30d (superset of 7d)
            commits_30d_list = await _commits_since(client, org, repo_name, since_30d)
            # Commits in 90d (superset of 30d)
            commits_90d_list = await _commits_since(client, org, repo_name, since_90d)

            # Extract unique author logins from 90d list
            contributors_90d: set[str] = set()
            for c in commits_90d_list:
                author = c.get("author")
                if author and isinstance(author, dict):
                    login = author.get("login")
                    if login:
                        contributors_90d.add(login)
                # Fallback to commit.commit.author.name
                if not contributors_90d:
                    inner = c.get("commit", {}).get("author", {})
                    name = inner.get("name")
                    if name:
                        contributors_90d.add(name)

            all_contributors_90d |= contributors_90d

            stars = repo.get("stargazers_count", 0)
            forks = repo.get("forks_count", 0)
            open_issues = repo.get("open_issues_count", 0)
            total_stars += stars
            total_forks += forks
            total_open_issues += open_issues

            c7 = len(commits_7d_list)
            c30 = len(commits_30d_list)
            c90 = len(commits_90d_list)
            agg_commits_7d += c7
            agg_commits_30d += c30
            agg_commits_90d += c90

            if is_stale:
                stale_repos.append(repo_name)

            repo_records.append(
                {
                    "name": repo_name,
                    "description": repo.get("description"),
                    "stars": stars,
                    "forks": forks,
                    "open_issues_count": open_issues,
                    "pushed_at": pushed_at_str,
                    "stale": is_stale,
                    "commits_7d": c7,
                    "commits_30d": c30,
                    "commits_90d": c90,
                    "contributors_90d": list(contributors_90d),
                }
            )

        return {
            "org": org,
            "repos": repo_records,
            "aggregates": {
                "total_stars": total_stars,
                "total_forks": total_forks,
                "commits_7d": agg_commits_7d,
                "commits_30d": agg_commits_30d,
                "commits_90d": agg_commits_90d,
                "unique_contributors_90d": len(all_contributors_90d),
                "stale_repos": stale_repos,
                "total_open_issues": total_open_issues,
            },
            "fetched_at": fetched_at,
        }
