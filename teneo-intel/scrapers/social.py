"""
scrapers/social.py

Scrapes X/Twitter mentions for Teneo Protocol via public Nitter instances.

Why Nitter: X's API requires auth; Nitter mirrors public tweet data via HTML.

Search queries used:
  - "TeneoProtocol"
  - "Teneo agent"

Nitter instance strategy:
  We try a prioritised list of known public Nitter instances and fall back
  gracefully if they are down or rate-limiting. If all instances fail, the
  scraper returns a partial result with a "nitter_unavailable" flag rather
  than raising.

NITTER_INSTANCES — ordered list, tried top-to-bottom.
  Update this list if instances go offline; check https://status.d420.de/
  for current instance health.

Metrics collected:
  - Post volume per week for the past 4 weeks
  - Top 5 most-engaged posts (by likes + retweets)
  - Rough engagement totals (likes, retweets) per week
  - Sentiment flags:
      positive_count — posts containing: launch, shipped, built, live,
                        mainnet, released, deployed, new
      negative_count — posts containing: broken, issue, stuck, failed,
                        bug, error, down, not working

Returns:
    dict with keys:
        queries (list[str]) — search terms used
        nitter_instance_used (str|None)
        weekly_volumes (list[dict]) — [{week_start, post_count, likes, retweets}]
        top_posts (list[dict])      — [{text, likes, retweets, date, url}]
        sentiment (dict)            — {positive_count, negative_count, ratio}
        fetched_at (str ISO timestamp)
        notes (list[str])
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from rich.console import Console

RICH = Console()

NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
]

SEARCH_QUERIES = ["TeneoProtocol", "Teneo agent"]

POSITIVE_KEYWORDS = {"launch", "launched", "shipped", "built", "live", "mainnet", "released", "deployed", "new"}
NEGATIVE_KEYWORDS = {"broken", "issue", "stuck", "failed", "bug", "error", "down", "not working"}


def _parse_stat_number(text: str) -> int:
    """Parse a number string like '1,234' or '1.2K' into an integer."""
    if not text:
        return 0
    text = text.strip().replace(",", "")
    try:
        if text.upper().endswith("K"):
            return int(float(text[:-1]) * 1000)
        if text.upper().endswith("M"):
            return int(float(text[:-1]) * 1_000_000)
        # Strip any non-numeric trailing chars
        m = re.match(r"(\d+)", text)
        if m:
            return int(m.group(1))
    except (ValueError, AttributeError):
        pass
    return 0


def _parse_tweet_date(date_str: str) -> datetime | None:
    """Try to parse a tweet date string into a UTC-aware datetime."""
    if not date_str:
        return None
    formats = [
        "%b %d, %Y · %I:%M %p %Z",
        "%b %d, %Y · %H:%M %Z",
        "%Y-%m-%d %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%d %b %Y",
        "%b %d, %Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # Fallback: try dateutil if available
    try:
        from dateutil import parser as du_parser
        dt = du_parser.parse(date_str, fuzzy=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    return None


def _parse_tweets(html: str, instance: str) -> list[dict]:
    """Parse Nitter search results HTML and return a list of tweet dicts."""
    soup = BeautifulSoup(html, "html.parser")
    tweets = []

    for item in soup.select(".timeline-item"):
        # Skip pinned or promoted tweets
        if item.select_one(".pinned") or item.select_one(".unavailable-box"):
            continue

        # Text
        content_el = item.select_one(".tweet-content")
        text = content_el.get_text(separator=" ", strip=True) if content_el else ""

        # Date
        date_el = item.select_one(".tweet-date a")
        date_str = ""
        tweet_url = ""
        if date_el:
            date_str = date_el.get("title") or date_el.get_text(strip=True)
            href = date_el.get("href", "")
            if href:
                tweet_url = instance.rstrip("/") + href if href.startswith("/") else href

        parsed_date = _parse_tweet_date(date_str)

        # Stats: likes and retweets
        likes = 0
        retweets = 0

        for stat_span in item.select(".tweet-stat"):
            icon = stat_span.select_one(".icon-heart, .icon-retweet, [class*='heart'], [class*='retweet']")
            num_text = stat_span.get_text(strip=True)
            if icon:
                icon_class = " ".join(icon.get("class", []))
                if "heart" in icon_class or "like" in icon_class:
                    likes = _parse_stat_number(num_text)
                elif "retweet" in icon_class:
                    retweets = _parse_stat_number(num_text)
            else:
                # Try to infer from sibling structure
                # fallback: check all stat divs in order (retweets, quotes, likes)
                pass

        # Alternative: look for stat icons by SVG use href or data attributes
        if likes == 0 and retweets == 0:
            stat_els = item.select(".tweet-stat")
            if len(stat_els) >= 3:
                # Typical nitter order: retweet, quote, like
                retweets = _parse_stat_number(stat_els[0].get_text(strip=True))
                likes = _parse_stat_number(stat_els[2].get_text(strip=True))
            elif len(stat_els) == 2:
                retweets = _parse_stat_number(stat_els[0].get_text(strip=True))
                likes = _parse_stat_number(stat_els[1].get_text(strip=True))

        # Tweet link fallback
        if not tweet_url:
            link_el = item.select_one(".tweet-link")
            if link_el:
                href = link_el.get("href", "")
                tweet_url = instance.rstrip("/") + href if href.startswith("/") else href

        tweets.append({
            "text": text,
            "likes": likes,
            "retweets": retweets,
            "date": parsed_date.isoformat() if parsed_date else date_str,
            "date_dt": parsed_date,
            "url": tweet_url,
        })

    return tweets


async def _try_fetch_query(
    client: httpx.AsyncClient,
    instance: str,
    query: str,
) -> list[dict] | None:
    """Fetch one search query from a Nitter instance. Returns parsed tweets or None on failure."""
    url = f"{instance}/search"
    params = {"q": query, "f": "tweets"}
    try:
        resp = await client.get(url, params=params)
        if not resp.is_success:
            return None
        html = resp.text
        # Check for empty/error page
        if "No results" in html or len(html) < 500:
            return []  # Empty but reachable
        tweets = _parse_tweets(html, instance)
        return tweets
    except Exception:
        return None


async def scrape(queries: list[str] | None = None) -> dict[str, Any]:
    """Scrape X/Twitter mentions via public Nitter instances.

    Args:
        queries: List of search terms. Defaults to SEARCH_QUERIES module constant.

    Tries each Nitter instance in NITTER_INSTANCES order. Falls back
    gracefully if all instances are unavailable.

    Raises only on programming errors; network/parsing failures are caught
    and reflected in the returned dict's notes field.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    notes: list[str] = []
    active_queries = queries if queries is not None else SEARCH_QUERIES

    nitter_instance_used: str | None = None
    nitter_unavailable = False
    all_tweets: list[dict] = []

    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; teneo-intel/1.0)"},
    ) as client:

        # Find a working instance
        working_instance: str | None = None
        for instance in NITTER_INSTANCES:
            try:
                probe = await client.get(instance, timeout=10.0)
                if probe.is_success or probe.status_code in (301, 302):
                    working_instance = instance
                    break
            except Exception:
                continue

        if working_instance is None:
            nitter_unavailable = True
            notes.append(
                "All Nitter instances unreachable — social data unavailable. "
                "Check https://status.d420.de/ for instance health."
            )
        else:
            nitter_instance_used = working_instance
            RICH.print(f"[cyan][social] Using Nitter instance: {working_instance}[/]")

            seen_urls: set[str] = set()

            for query in active_queries:
                RICH.print(f"[cyan][social] Searching: {query}[/]")
                tweets = await _try_fetch_query(client, working_instance, query)

                if tweets is None:
                    # Try next instance for this query
                    found_alt = False
                    for alt_instance in NITTER_INSTANCES:
                        if alt_instance == working_instance:
                            continue
                        tweets = await _try_fetch_query(client, alt_instance, query)
                        if tweets is not None:
                            found_alt = True
                            break
                    if not found_alt:
                        notes.append(f"No results for query '{query}' across all instances.")
                        tweets = []

                # Deduplicate by URL
                for tweet in (tweets or []):
                    url = tweet.get("url", "")
                    key = url if url else tweet.get("text", "")[:80]
                    if key and key not in seen_urls:
                        seen_urls.add(key)
                        all_tweets.append(tweet)

            RICH.print(f"[cyan][social] {len(all_tweets)} unique tweets collected[/]")

    # --- Weekly volume bucketing ---
    now = datetime.now(timezone.utc)
    week_starts = [
        now - timedelta(days=now.weekday() + 7 * i)
        for i in range(4)
    ]
    # Normalize to start of day
    week_starts = [
        ws.replace(hour=0, minute=0, second=0, microsecond=0)
        for ws in week_starts
    ]
    week_starts.sort()  # oldest first

    weekly_buckets: dict[str, dict] = {}
    for ws in week_starts:
        key = ws.date().isoformat()
        weekly_buckets[key] = {"week_start": key, "post_count": 0, "likes": 0, "retweets": 0}

    for tweet in all_tweets:
        dt = tweet.get("date_dt")
        if not isinstance(dt, datetime):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # Find which week bucket
        assigned = None
        for ws in reversed(week_starts):
            if dt >= ws:
                assigned = ws.date().isoformat()
                break

        if assigned and assigned in weekly_buckets:
            weekly_buckets[assigned]["post_count"] += 1
            weekly_buckets[assigned]["likes"] += tweet.get("likes", 0)
            weekly_buckets[assigned]["retweets"] += tweet.get("retweets", 0)

    weekly_volumes = list(weekly_buckets.values())

    # --- Top 5 most-engaged posts ---
    sorted_tweets = sorted(
        all_tweets,
        key=lambda t: t.get("likes", 0) + t.get("retweets", 0),
        reverse=True,
    )
    top_posts = [
        {
            "text": t["text"],
            "likes": t.get("likes", 0),
            "retweets": t.get("retweets", 0),
            "date": t.get("date", ""),
            "url": t.get("url", ""),
        }
        for t in sorted_tweets[:5]
    ]

    # --- Sentiment ---
    positive_count = 0
    negative_count = 0
    for tweet in all_tweets:
        text_lower = tweet.get("text", "").lower()
        words = set(re.findall(r"\w+", text_lower))
        # Check multi-word negative phrase
        if "not working" in text_lower:
            negative_count += 1
            continue
        if words & POSITIVE_KEYWORDS:
            positive_count += 1
        elif words & NEGATIVE_KEYWORDS:
            negative_count += 1

    total_sentiment = positive_count + negative_count
    ratio = round(positive_count / total_sentiment, 3) if total_sentiment > 0 else None

    return {
        "queries": active_queries,
        "nitter_instance_used": nitter_instance_used,
        "nitter_unavailable": nitter_unavailable,
        "weekly_volumes": weekly_volumes,
        "top_posts": top_posts,
        "sentiment": {
            "positive_count": positive_count,
            "negative_count": negative_count,
            "ratio": ratio,
        },
        "fetched_at": fetched_at,
        "notes": notes,
    }
