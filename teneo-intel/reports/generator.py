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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _val(results: dict, *keys: str, default: Any = None) -> Any:
    """Safely navigate a nested dict by a sequence of keys.

    Returns `default` if any key is missing or the value is None.
    """
    node = results
    for key in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(key, default)
        if node is None:
            return default
    return node


def _diff_symbol(metric_name: str, diff_data: dict | None) -> str:
    """Return a trend symbol for a metric name based on diff_data.

    Returns "🟢", "🔴", "⚪", or "" (empty string when no diff).
    """
    if not diff_data:
        return ""
    for item in diff_data.get("improved", []):
        if item.get("metric") == metric_name:
            return "🟢"
    for item in diff_data.get("declined", []):
        if item.get("metric") == metric_name:
            return "🔴"
    for item in diff_data.get("unchanged", []):
        if item.get("metric") == metric_name:
            return "⚪"
    return "❓"


# ---------------------------------------------------------------------------
# Snapshot management
# ---------------------------------------------------------------------------

def save_snapshot(results: dict, run_timestamp: datetime) -> Path:
    """Serialise results dict to a timestamped JSON file in /data/.

    Args:
        results: The full results dict from main.run().
        run_timestamp: UTC datetime of the run.

    Returns:
        Path to the written snapshot file.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts_str = run_timestamp.strftime("%Y-%m-%dT%H-%M-%S")
    filename = f"snapshot_{ts_str}.json"
    path = DATA_DIR / filename

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)

    RICH.print(f"[green][generator] Snapshot saved: {path}[/]")
    return path


def load_previous_snapshot() -> dict[str, Any] | None:
    """Load the most recently saved snapshot from /data/.

    Returns None if the directory is empty or no snapshots exist.
    """
    if not DATA_DIR.exists():
        return None

    snapshots = sorted(DATA_DIR.glob("snapshot_*.json"))
    if not snapshots:
        return None

    # ISO timestamps sort lexicographically — last in sorted order is newest
    latest = snapshots[-1]
    try:
        with open(latest, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        RICH.print(f"[yellow][generator] Could not load snapshot {latest}: {exc}[/]")
        return None


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

# Metric definitions: (display_name, key_path_tuple, higher_is_better)
_METRICS = [
    ("github.aggregates.total_stars",        ("github", "aggregates", "total_stars"),         True),
    ("github.aggregates.commits_30d",         ("github", "aggregates", "commits_30d"),          True),
    ("github.aggregates.unique_contributors_90d", ("github", "aggregates", "unique_contributors_90d"), True),
    ("github.aggregates.total_open_issues",   ("github", "aggregates", "total_open_issues"),   False),
    ("onchain.holder_count",                  ("onchain", "holder_count"),                     True),
    ("docs.pages_found",                      ("docs", "pages_found"),                         True),
    ("docs.broken_links",                     None,                                            False),  # special: len()
    ("console.agent_count",                   ("console", "agent_count"),                      True),
    ("social.sentiment.ratio",                ("social", "sentiment", "ratio"),                True),
]

_UNCHANGED_THRESHOLD = 0.02  # 2% relative change = "unchanged" for floats


def build_diff(previous: dict, current: dict) -> dict[str, Any]:
    """Compare two snapshots and return a structured diff.

    Args:
        previous: Older snapshot dict.
        current:  Newer (current run) dict.

    Returns:
        dict with keys: improved, declined, unchanged, summary (str).
    """
    improved: list[dict] = []
    declined: list[dict] = []
    unchanged: list[dict] = []

    for metric_name, key_path, higher_is_better in _METRICS:
        # Special case: docs.broken_links is len of a list
        if metric_name == "docs.broken_links":
            prev_val = _val(previous, "docs", "broken_links", default=None)
            curr_val = _val(current, "docs", "broken_links", default=None)
            if prev_val is not None:
                prev_val = len(prev_val)
            if curr_val is not None:
                curr_val = len(curr_val)
        else:
            prev_val = _val(previous, *key_path, default=None)
            curr_val = _val(current, *key_path, default=None)

        if prev_val is None or curr_val is None:
            continue

        try:
            prev_num = float(prev_val)
            curr_num = float(curr_val)
        except (TypeError, ValueError):
            continue

        delta = curr_num - prev_num

        # Determine unchanged threshold
        if isinstance(curr_val, float) or isinstance(prev_val, float):
            denom = abs(prev_num) if prev_num != 0 else 1.0
            is_unchanged = abs(delta) / denom < _UNCHANGED_THRESHOLD
        else:
            is_unchanged = delta == 0

        entry = {
            "metric": metric_name,
            "previous_value": prev_val,
            "current_value": curr_val,
            "delta": round(delta, 4),
        }

        if is_unchanged:
            unchanged.append(entry)
        elif (delta > 0 and higher_is_better) or (delta < 0 and not higher_is_better):
            improved.append(entry)
        else:
            declined.append(entry)

    # Build summary string
    n_improved = len(improved)
    n_declined = len(declined)
    n_unchanged = len(unchanged)

    all_changes = improved + declined
    biggest_change: dict | None = None
    biggest_delta = 0.0
    for item in all_changes:
        if abs(item["delta"]) > biggest_delta:
            biggest_delta = abs(item["delta"])
            biggest_change = item

    summary_parts = [
        f"{n_improved} metric{'s' if n_improved != 1 else ''} improved, "
        f"{n_declined} declined, "
        f"{n_unchanged} unchanged since last run"
    ]
    if biggest_change and biggest_delta > 0:
        direction = "up" if biggest_change["delta"] > 0 else "down"
        summary_parts.append(
            f"(biggest move: {biggest_change['metric']} {direction} by {abs(biggest_change['delta']):.4g})"
        )

    return {
        "improved": improved,
        "declined": declined,
        "unchanged": unchanged,
        "summary": "; ".join(summary_parts),
    }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

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
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"ecosystem_audit_{run_timestamp.strftime('%Y-%m-%d')}.md"

    lines: list[str] = []

    def add(text: str = "") -> None:
        lines.append(text)

    sources_used = list(results.get("_data_log", {}).keys())

    # -----------------------------------------------------------------------
    # 1. Header
    # -----------------------------------------------------------------------
    add("# Teneo Protocol — Ecosystem Audit")
    add()
    add(f"**Run timestamp:** {run_timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    add(f"**Sources:** {', '.join(sources_used) if sources_used else 'none'}")
    add()

    # -----------------------------------------------------------------------
    # 2. Diff summary (only if diff_data provided)
    # -----------------------------------------------------------------------
    if diff_data:
        add("## Change Summary vs Previous Run")
        add()
        add(diff_data.get("summary", ""))
        add()

        changed = diff_data.get("improved", []) + diff_data.get("declined", [])
        if changed:
            add("| Metric | Previous | Current | Delta | Trend |")
            add("|--------|----------|---------|-------|-------|")
            for item in diff_data.get("improved", []):
                add(
                    f"| {item['metric']} | {item['previous_value']} | "
                    f"{item['current_value']} | {item['delta']:+.4g} | 🟢 |"
                )
            for item in diff_data.get("declined", []):
                add(
                    f"| {item['metric']} | {item['previous_value']} | "
                    f"{item['current_value']} | {item['delta']:+.4g} | 🔴 |"
                )
            for item in diff_data.get("unchanged", []):
                add(
                    f"| {item['metric']} | {item['previous_value']} | "
                    f"{item['current_value']} | {item['delta']:+.4g} | ⚪ |"
                )
        add()

    # -----------------------------------------------------------------------
    # 3. Executive summary — 5 bullet points
    # -----------------------------------------------------------------------
    add("## Executive Summary")
    add()

    # Bullet 1: GitHub health
    gh_commits_30d = _val(results, "github", "aggregates", "commits_30d", default=0)
    gh_stars = _val(results, "github", "aggregates", "total_stars", default=0)
    gh_stale = _val(results, "github", "aggregates", "stale_repos", default=[])
    stale_count = len(gh_stale) if isinstance(gh_stale, list) else 0
    gh_symbol = _diff_symbol("github.aggregates.commits_30d", diff_data)
    add(
        f"- **GitHub** {gh_symbol}: {gh_commits_30d} commits in the past 30 days across "
        f"{gh_stars} total stars; {stale_count} stale repo(s) (no push >30 days)."
    )

    # Bullet 2: On-chain
    onchain_configured = _val(results, "onchain", "configured", default=False)
    holder_count = _val(results, "onchain", "holder_count", default=None)
    on_symbol = _diff_symbol("onchain.holder_count", diff_data)
    if onchain_configured:
        holder_str = f"{holder_count:,}" if holder_count is not None else "unknown"
        add(f"- **On-chain** {on_symbol}: Token configured — {holder_str} holders detected.")
    else:
        add(
            f"- **On-chain** {on_symbol}: Token mint address not configured. "
            "Set TENEO_TOKEN_MINT to enable on-chain metrics."
        )

    # Bullet 3: Docs health
    docs_pages = _val(results, "docs", "pages_found", default=0)
    docs_broken = _val(results, "docs", "broken_links", default=[])
    docs_stubs = _val(results, "docs", "stub_pages", default=[])
    broken_count = len(docs_broken) if isinstance(docs_broken, list) else 0
    stub_count = len(docs_stubs) if isinstance(docs_stubs, list) else 0
    docs_symbol = _diff_symbol("docs.pages_found", diff_data)
    add(
        f"- **Docs** {docs_symbol}: {docs_pages} pages crawled; "
        f"{broken_count} broken link(s), {stub_count} stub/placeholder page(s)."
    )

    # Bullet 4: Social
    weekly_vols = _val(results, "social", "weekly_volumes", default=[])
    last_week_posts = 0
    if isinstance(weekly_vols, list) and weekly_vols:
        last_week_posts = weekly_vols[-1].get("post_count", 0)
    sentiment = _val(results, "social", "sentiment", default={})
    sentiment_ratio = sentiment.get("ratio") if isinstance(sentiment, dict) else None
    soc_symbol = _diff_symbol("social.sentiment.ratio", diff_data)
    ratio_str = f"{sentiment_ratio:.2f}" if sentiment_ratio is not None else "N/A"
    add(
        f"- **Social/X** {soc_symbol}: {last_week_posts} mentions in the most recent week; "
        f"sentiment ratio (positive share): {ratio_str}."
    )

    # Bullet 5: Key unknown / gap
    gaps: list[str] = []
    if not onchain_configured:
        gaps.append("token mint address (TENEO_TOKEN_MINT not set)")
    nitter_unavail = _val(results, "social", "nitter_unavailable", default=False)
    if nitter_unavail:
        gaps.append("social data (all Nitter instances offline)")
    if broken_count > 0:
        gaps.append(f"{broken_count} broken doc link(s) need attention")
    if stale_count > 3:
        gaps.append(f"{stale_count} stale GitHub repos suggest reduced activity")
    console_agent_count = _val(results, "console", "agent_count", default=None)
    if console_agent_count is None:
        gaps.append("agent console data (scraper not yet configured)")
    if not gaps:
        gaps.append("no critical gaps detected this run")
    add(f"- **Key gap:** {gaps[0].capitalize()}.")

    add()

    # -----------------------------------------------------------------------
    # 4. Teneo section — one subsection per source
    # -----------------------------------------------------------------------
    add("## Teneo Protocol — Source Details")
    add()

    # 4a. GitHub
    add("### GitHub")
    add()
    gh_data = results.get("github", {})
    if not gh_data:
        add("_No GitHub data collected this run._")
    elif "error" in gh_data:
        add(f"**Error:** {gh_data['error']}")
    else:
        agg = gh_data.get("aggregates", {})
        gh_org = gh_data.get("org", "unknown")
        add(f"**Organisation:** `{gh_org}`  |  **Repos processed:** {len(gh_data.get('repos', []))}")
        add()
        add("| Metric | Value | Trend |")
        add("|--------|-------|-------|")
        add(f"| Total stars | {agg.get('total_stars', 0):,} | {_diff_symbol('github.aggregates.total_stars', diff_data)} |")
        add(f"| Total forks | {agg.get('total_forks', 0):,} | |")
        add(f"| Open issues | {agg.get('total_open_issues', 0):,} | {_diff_symbol('github.aggregates.total_open_issues', diff_data)} |")
        add(f"| Commits (7d) | {agg.get('commits_7d', 0)} | |")
        add(f"| Commits (30d) | {agg.get('commits_30d', 0)} | {_diff_symbol('github.aggregates.commits_30d', diff_data)} |")
        add(f"| Commits (90d) | {agg.get('commits_90d', 0)} | |")
        add(f"| Unique contributors (90d) | {agg.get('unique_contributors_90d', 0)} | {_diff_symbol('github.aggregates.unique_contributors_90d', diff_data)} |")
        stale_list = agg.get('stale_repos', [])
        add(f"| Stale repos (>30d no push) | {len(stale_list)} | |")
        add()
        if stale_list:
            add(f"**Stale repos:** {', '.join(f'`{r}`' for r in stale_list)}")
            add()
        add("_Interpretation:_ Commit velocity and contributor count are the primary health indicators. Stale repos may represent archived experiments or documentation.")

    add()

    # 4b. On-chain
    add("### On-chain (Solscan)")
    add()
    onchain = results.get("onchain", {})
    if not onchain:
        add("_No on-chain data collected this run._")
    elif not onchain.get("configured", False):
        add("**Status:** Not configured — set `TENEO_TOKEN_MINT` environment variable.")
        for note in onchain.get("notes", []):
            add(f"- {note}")
    else:
        token = onchain.get("token") or {}
        add("| Metric | Value | Trend |")
        add("|--------|-------|-------|")
        add(f"| Token name | {token.get('name', 'N/A')} | |")
        add(f"| Symbol | {token.get('symbol', 'N/A')} | |")
        add(f"| Supply | {token.get('supply', 'N/A')} | |")
        hc = onchain.get('holder_count')
        add(f"| Holder count | {hc if hc is not None else 'N/A'} | {_diff_symbol('onchain.holder_count', diff_data)} |")
        add(f"| Volume 24h (USD est.) | {onchain.get('volume_24h_usd', 'N/A')} | |")
        add(f"| Volume 7d (USD est.) | {onchain.get('volume_7d_usd', 'N/A')} | |")
        add(f"| Unique wallets 7d | {onchain.get('unique_wallets_7d', 'N/A')} | |")
        add(f"| Whale transactions | {len(onchain.get('whale_transactions', []))} | |")
        add(f"| SOL price (USD) | {onchain.get('sol_price_usd', 'N/A')} | |")
        add(f"| Token price (USD) | {onchain.get('token_price_usd', 'N/A')} | |")
        add()
        for note in onchain.get("notes", []):
            add(f"> **Note:** {note}")
        add()
        add("_Interpretation:_ Holder count growth and whale activity are key signals of token adoption and large-player interest.")

    add()

    # 4c. Docs
    add("### Documentation")
    add()
    docs = results.get("docs", {})
    if not docs:
        add("_No docs data collected this run._")
    elif "error" in docs:
        add(f"**Error:** {docs['error']}")
        for note in docs.get("notes", []):
            add(f"- {note}")
    else:
        add("| Metric | Value | Trend |")
        add("|--------|-------|-------|")
        add(f"| Pages crawled | {docs.get('pages_found', 0)} | {_diff_symbol('docs.pages_found', diff_data)} |")
        add(f"| Broken links | {len(docs.get('broken_links', []))} | {_diff_symbol('docs.broken_links', diff_data)} |")
        add(f"| Stub/placeholder pages | {len(docs.get('stub_pages', []))} | |")
        add(f"| Pages with 'last updated' | {len(docs.get('last_updated_dates', []))} | |")
        add(f"| External links found | {docs.get('external_links_count', 0)} | |")
        add()
        broken = docs.get("broken_links", [])
        if broken:
            add("**Broken links:**")
            for bl in broken[:10]:
                add(f"- `{bl['url']}` → HTTP {bl['status_code']}")
            if len(broken) > 10:
                add(f"- _…and {len(broken) - 10} more_")
            add()
        stub_pgs = docs.get("stub_pages", [])
        if stub_pgs:
            add("**Stub pages:**")
            for sp in stub_pgs[:10]:
                add(f"- `{sp['url']}` — phrases: {', '.join(sp.get('phrases', []))}")
            add()
        for note in docs.get("notes", []):
            add(f"> **Note:** {note}")
        add()
        add("_Interpretation:_ A high page count with few stubs and no broken links signals a well-maintained docs site.")

    add()

    # 4d. Agent Console
    add("### Agent Console")
    add()
    console_data = results.get("console", {})
    if not console_data:
        add("_No console data collected this run._")
    else:
        agent_count = console_data.get("agent_count")
        add("| Metric | Value | Trend |")
        add("|--------|-------|-------|")
        add(f"| Agent count | {agent_count if agent_count is not None else 'N/A'} | {_diff_symbol('console.agent_count', diff_data)} |")
        cats = console_data.get("categories", {})
        if cats:
            add(f"| Categories | {len(cats)} | |")
        add()
        for note in console_data.get("notes", []):
            add(f"> **Note:** {note}")
        add()
        add("_Interpretation:_ Agent count is a proxy for ecosystem adoption and developer activity on the Teneo platform.")

    add()

    # 4e. Social
    add("### Social / X (via Nitter)")
    add()
    social = results.get("social", {})
    if not social:
        add("_No social data collected this run._")
    elif social.get("nitter_unavailable"):
        add("**Status:** All Nitter instances unavailable — no social data this run.")
        for note in social.get("notes", []):
            add(f"- {note}")
    else:
        sentiment = social.get("sentiment", {})
        srat = sentiment.get("ratio")
        add(f"**Nitter instance:** `{social.get('nitter_instance_used', 'N/A')}`")
        add(f"**Queries:** {', '.join(social.get('queries', []))}")
        add()
        add("**Weekly post volumes (past 4 weeks):**")
        add()
        add("| Week start | Posts | Likes | Retweets |")
        add("|------------|-------|-------|----------|")
        for wv in social.get("weekly_volumes", []):
            add(f"| {wv.get('week_start', 'N/A')} | {wv.get('post_count', 0)} | {wv.get('likes', 0)} | {wv.get('retweets', 0)} |")
        add()
        add("**Sentiment:**")
        add()
        add("| Metric | Value | Trend |")
        add("|--------|-------|-------|")
        add(f"| Positive posts | {sentiment.get('positive_count', 0)} | |")
        add(f"| Negative posts | {sentiment.get('negative_count', 0)} | |")
        srat_str = f"{srat:.3f}" if srat is not None else "N/A"
        add(f"| Sentiment ratio | {srat_str} | {_diff_symbol('social.sentiment.ratio', diff_data)} |")
        add()
        top = social.get("top_posts", [])
        if top:
            add("**Top 5 most-engaged posts:**")
            add()
            for i, post in enumerate(top, 1):
                text_preview = (post.get("text") or "")[:120].replace("\n", " ")
                add(
                    f"{i}. [{text_preview}]({post.get('url', '')}) "
                    f"— {post.get('likes', 0)} likes, {post.get('retweets', 0)} RTs"
                    f" ({post.get('date', '')})"
                )
            add()
        for note in social.get("notes", []):
            add(f"> **Note:** {note}")

    add()

    # -----------------------------------------------------------------------
    # 5. Competitor benchmark table
    # -----------------------------------------------------------------------
    add("## Competitor Benchmark")
    add()

    comp_data_list = _val(results, "competitors", "competitors", default=[])

    # Build lookup by name
    comp_lookup: dict[str, dict] = {}
    for comp in (comp_data_list or []):
        comp_lookup[comp.get("name", "")] = comp

    def _comp_agg(name: str, key: str) -> str:
        comp = comp_lookup.get(name, {})
        agg = _val(comp, "github", "aggregates", default={}) or {}
        val = agg.get(key)
        return str(val) if val is not None else "N/A"

    def _comp_social(name: str, key: str) -> str:
        comp = comp_lookup.get(name, {})
        soc = comp.get("social", {}) or {}
        val = soc.get(key)
        if val is None:
            return "N/A"
        if isinstance(val, float):
            return f"{val:.3f}"
        return str(val)

    add("| Metric | Teneo | Bittensor | Fetch.ai | Autonolas |")
    add("|--------|-------|-----------|----------|-----------|")

    teneo_stars = _val(results, "github", "aggregates", "total_stars", default=None)
    add(
        f"| GitHub stars | {teneo_stars if teneo_stars is not None else 'N/A'} "
        f"| {_comp_agg('Bittensor / TAO', 'total_stars')} "
        f"| {_comp_agg('Fetch.ai', 'total_stars')} "
        f"| {_comp_agg('Autonolas / Olas', 'total_stars')} |"
    )

    teneo_commits_30d = _val(results, "github", "aggregates", "commits_30d", default=None)
    add(
        f"| Commits 30d | {teneo_commits_30d if teneo_commits_30d is not None else 'N/A'} "
        f"| {_comp_agg('Bittensor / TAO', 'commits_30d')} "
        f"| {_comp_agg('Fetch.ai', 'commits_30d')} "
        f"| {_comp_agg('Autonolas / Olas', 'commits_30d')} |"
    )

    teneo_contrib = _val(results, "github", "aggregates", "unique_contributors_90d", default=None)
    add(
        f"| Contributors 90d | {teneo_contrib if teneo_contrib is not None else 'N/A'} "
        f"| {_comp_agg('Bittensor / TAO', 'unique_contributors_90d')} "
        f"| {_comp_agg('Fetch.ai', 'unique_contributors_90d')} "
        f"| {_comp_agg('Autonolas / Olas', 'unique_contributors_90d')} |"
    )

    teneo_mentions = "N/A"
    if isinstance(weekly_vols, list) and weekly_vols:
        teneo_mentions = str(weekly_vols[-1].get("post_count", 0))
    add(
        f"| X mentions 7d | {teneo_mentions} "
        f"| {_comp_social('Bittensor / TAO', 'mentions_7d')} "
        f"| {_comp_social('Fetch.ai', 'mentions_7d')} "
        f"| {_comp_social('Autonolas / Olas', 'mentions_7d')} |"
    )

    teneo_sent = "N/A"
    if isinstance(sentiment_ratio, float):
        teneo_sent = f"{sentiment_ratio:.3f}"
    elif sentiment_ratio is not None:
        teneo_sent = str(sentiment_ratio)
    add(
        f"| Sentiment ratio | {teneo_sent} "
        f"| {_comp_social('Bittensor / TAO', 'sentiment_ratio')} "
        f"| {_comp_social('Fetch.ai', 'sentiment_ratio')} "
        f"| {_comp_social('Autonolas / Olas', 'sentiment_ratio')} |"
    )

    add()
    add("_Note: Teneo competitor columns are populated from scrapers run during this session. Teneo's own column uses results from this run's github/social scrapers._")
    add()

    # -----------------------------------------------------------------------
    # 6. Signals to watch
    # -----------------------------------------------------------------------
    add("## Signals to Watch")
    add()

    signals: list[str] = []

    # Stale repos
    if isinstance(gh_stale, list) and gh_stale:
        signals.append(
            f"**{len(gh_stale)} stale GitHub repo(s)** (no push in >30 days): "
            + ", ".join(f"`{r}`" for r in gh_stale[:5])
            + ("…" if len(gh_stale) > 5 else "")
        )

    # Missing token address
    if not onchain_configured:
        signals.append(
            "**Token mint address not configured.** Set `TENEO_TOKEN_MINT` to unlock on-chain analytics."
        )

    # Nitter unavailability
    if _val(results, "social", "nitter_unavailable", default=False):
        signals.append(
            "**Nitter instances all offline.** Social data is unavailable for this run. "
            "Check https://status.d420.de/ for instance health."
        )

    # Broken doc links
    if broken_count > 0:
        signals.append(
            f"**{broken_count} broken documentation link(s)** detected. "
            "Review and fix to maintain docs quality."
        )

    # Stub pages
    if stub_count > 0:
        signals.append(
            f"**{stub_count} stub/placeholder doc page(s)** detected. "
            "These may reduce developer confidence."
        )

    # Low commit velocity
    if isinstance(gh_commits_30d, int) and gh_commits_30d < 10:
        signals.append(
            f"**Low commit velocity** — only {gh_commits_30d} commits in the past 30 days. "
            "This may indicate reduced development activity."
        )

    # Console not implemented
    if _val(results, "console", "agent_count", default=None) is None:
        signals.append(
            "**Agent Console scraper not yet implemented.** "
            "Inspect https://console.teneo.pro and implement scraper to track agent count."
        )

    # Competitor notes
    comp_notes = _val(results, "competitors", "notes", default=[])
    for note in (comp_notes or [])[:5]:
        signals.append(f"**Competitor data note:** {note}")

    if not signals:
        signals.append("No anomalies detected in this run.")

    for signal in signals:
        add(f"- {signal}")

    add()

    # -----------------------------------------------------------------------
    # 7. Data quality log
    # -----------------------------------------------------------------------
    add("## Data Quality Log")
    add()
    add("| Source | Status | Notes |")
    add("|--------|--------|-------|")

    data_log = results.get("_data_log", {})
    for source, status in data_log.items():
        source_notes = ""
        # Pull notes from source result if available
        src_result = results.get(source, {})
        if isinstance(src_result, dict):
            src_notes_list = src_result.get("notes", [])
            if src_notes_list:
                source_notes = "; ".join(str(n) for n in src_notes_list[:3])
        # Escape pipe characters
        status_clean = str(status).replace("|", "\\|")
        source_notes_clean = source_notes.replace("|", "\\|")[:200]
        add(f"| {source} | {status_clean} | {source_notes_clean} |")

    add()
    add(f"_Report generated: {run_timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC_")

    # Write the report
    report_content = "\n".join(lines) + "\n"
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report_content)

    RICH.print(f"[bold green][generator] Report written: {report_path}[/]")
    return report_path
