"""
scrapers/docs.py

Crawls the Teneo Protocol documentation site to assess completeness and health.

Target URL: https://docs.teneo.pro  (confirm with team — may differ)

What we collect:
  - Total discoverable page count (proxy for docs completeness)
  - List of all internal links found on the root index
  - Broken internal links (HTTP 4xx/5xx responses)
  - Pages or sections with visible "last updated" dates (parsed where present)
  - Stub / under-construction markers:
      searches for common stub phrases:
        "coming soon", "under construction", "todo", "placeholder",
        "this page is empty", "work in progress"

Crawl strategy:
  1. Fetch the root page and extract all <a href> links.
  2. Follow only same-domain links up to MAX_DEPTH levels deep.
  3. Do not re-visit already-seen URLs.
  4. Respect a short delay between requests (CRAWL_DELAY_S) to be polite.

Limits:
  MAX_PAGES   — cap to avoid runaway crawls (default 200)
  MAX_DEPTH   — link-follow depth from root (default 3)
  CRAWL_DELAY_S — seconds between requests (default 0.3)

Returns:
    dict with keys:
        root_url (str)
        pages_found (int)
        broken_links (list[dict]) — {url, status_code}
        stub_pages (list[dict])   — {url, matched_phrases}
        last_updated_dates (list[dict]) — {url, date_text}
        fetched_at (str ISO timestamp)
        notes (list[str])
"""

import asyncio
import re
from collections import deque
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse, urldefrag

import httpx
from bs4 import BeautifulSoup
from rich.console import Console

RICH = Console()

DOCS_ROOT = "https://docs.teneo.pro"  # TODO: confirm URL with team
MAX_PAGES = 200
MAX_DEPTH = 3
CRAWL_DELAY_S = 0.3

STUB_PHRASES = [
    "coming soon",
    "under construction",
    "todo",
    "placeholder",
    "this page is empty",
    "work in progress",
    "wip",
]

# Regex patterns to detect "last updated" dates
LAST_UPDATED_PATTERNS = [
    re.compile(r"last\s+updated[:\s]+([^\n<]{3,40})", re.IGNORECASE),
    re.compile(r"last\s+modified[:\s]+([^\n<]{3,40})", re.IGNORECASE),
    re.compile(r"updated\s+on[:\s]+([^\n<]{3,40})", re.IGNORECASE),
    re.compile(r"published[:\s]+([^\n<]{3,40})", re.IGNORECASE),
]


def _is_same_domain(base_url: str, link_url: str) -> bool:
    """Return True if link_url is on the same domain as base_url."""
    try:
        base_netloc = urlparse(base_url).netloc.lower()
        link_netloc = urlparse(link_url).netloc.lower()
        # Same domain or relative (empty netloc handled by urljoin before calling)
        return base_netloc == link_netloc
    except Exception:
        return False


def _extract_links(html: str, base_url: str) -> tuple[list[str], list[str]]:
    """Extract internal and external links from an HTML page.

    Args:
        html: Raw HTML string.
        base_url: The URL of the page being parsed (for resolving relative links).

    Returns:
        (internal_links, external_links) — both are lists of absolute URLs.
        Fragment-stripped, deduplicated within each list.
    """
    soup = BeautifulSoup(html, "html.parser")
    internal: list[str] = []
    external: list[str] = []
    seen: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        raw_href = a_tag["href"].strip()
        if not raw_href or raw_href.startswith("javascript:") or raw_href.startswith("mailto:"):
            continue

        # Resolve to absolute URL
        abs_url = urljoin(base_url, raw_href)

        # Strip fragment
        abs_url, _ = urldefrag(abs_url)

        # Must be http/https
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue

        if abs_url in seen:
            continue
        seen.add(abs_url)

        if _is_same_domain(base_url, abs_url):
            internal.append(abs_url)
        else:
            external.append(abs_url)

    return internal, external


async def scrape() -> dict[str, Any]:
    """Crawl the Teneo docs site and return health/completeness metrics.

    Uses BFS up to MAX_DEPTH, MAX_PAGES. Never raises — errors reflected
    in the returned dict's 'error' or 'notes' fields.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    notes: list[str] = []

    all_pages: list[str] = []
    broken_links: list[dict] = []
    stub_pages: list[dict] = []
    last_updated_dates: list[dict] = []
    external_links_count = 0

    timeout = httpx.Timeout(15.0)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": "teneo-intel-docs-crawler/1.0"},
    ) as client:

        # Fetch root page first
        try:
            root_resp = await client.get(DOCS_ROOT)
            if not root_resp.is_success:
                return {
                    "root_url": DOCS_ROOT,
                    "error": f"Root URL returned HTTP {root_resp.status_code}",
                    "pages_found": 0,
                    "broken_links": [],
                    "stub_pages": [],
                    "last_updated_dates": [],
                    "all_pages": [],
                    "external_links_count": 0,
                    "fetched_at": fetched_at,
                    "notes": [f"Root URL unreachable: HTTP {root_resp.status_code}"],
                }
        except httpx.TimeoutException as exc:
            return {
                "root_url": DOCS_ROOT,
                "error": f"Root URL timed out: {exc}",
                "pages_found": 0,
                "broken_links": [],
                "stub_pages": [],
                "last_updated_dates": [],
                "all_pages": [],
                "external_links_count": 0,
                "fetched_at": fetched_at,
                "notes": [f"Root URL unreachable: timeout — {exc}"],
            }
        except Exception as exc:
            return {
                "root_url": DOCS_ROOT,
                "error": f"Root URL failed: {exc}",
                "pages_found": 0,
                "broken_links": [],
                "stub_pages": [],
                "last_updated_dates": [],
                "all_pages": [],
                "external_links_count": 0,
                "fetched_at": fetched_at,
                "notes": [f"Root URL unreachable: {exc}"],
            }

        # BFS queue: (url, depth)
        # visited tracks URLs we've fetched or enqueued
        visited: set[str] = set()
        root_url_clean, _ = urldefrag(str(root_resp.url))
        visited.add(root_url_clean)
        visited.add(DOCS_ROOT)

        queue: deque[tuple[str, int]] = deque()

        # Process root page
        root_html = root_resp.text
        _process_page(
            url=root_url_clean,
            html=root_html,
            all_pages=all_pages,
            stub_pages=stub_pages,
            last_updated_dates=last_updated_dates,
        )

        internal_links, ext_links = _extract_links(root_html, root_url_clean)
        external_links_count += len(ext_links)

        for link in internal_links:
            if link not in visited and len(all_pages) + len(queue) < MAX_PAGES:
                visited.add(link)
                queue.append((link, 1))

        RICH.print(f"[cyan][docs] Root fetched — {len(internal_links)} internal links found[/]")

        pages_checked = 1  # root counts

        while queue and pages_checked < MAX_PAGES:
            url, depth = queue.popleft()
            pages_checked += 1

            RICH.print(f"[cyan][docs] Crawling ({pages_checked}/{MAX_PAGES}) depth={depth}: {url}[/]")

            await asyncio.sleep(CRAWL_DELAY_S)

            try:
                resp = await client.get(url)
                status = resp.status_code

                if status >= 400:
                    broken_links.append({"url": url, "status_code": status})
                    continue

                if not resp.is_success:
                    # 3xx that wasn't followed — shouldn't happen with follow_redirects
                    notes.append(f"Unexpected status {status} for {url}")
                    continue

                html = resp.text
                _process_page(
                    url=url,
                    html=html,
                    all_pages=all_pages,
                    stub_pages=stub_pages,
                    last_updated_dates=last_updated_dates,
                )

                if depth < MAX_DEPTH:
                    new_internal, new_ext = _extract_links(html, url)
                    external_links_count += len(new_ext)

                    for link in new_internal:
                        if link not in visited and pages_checked + len(queue) < MAX_PAGES:
                            visited.add(link)
                            queue.append((link, depth + 1))

            except httpx.TimeoutException:
                broken_links.append({"url": url, "status_code": 408})
                notes.append(f"Timeout fetching {url}")
            except Exception as exc:
                notes.append(f"Error fetching {url}: {exc}")

        if pages_checked >= MAX_PAGES:
            notes.append(f"Crawl capped at MAX_PAGES={MAX_PAGES}; site may have more pages.")

    RICH.print(
        f"[cyan][docs] Done — {len(all_pages)} pages, "
        f"{len(broken_links)} broken, {len(stub_pages)} stubs[/]"
    )

    return {
        "root_url": DOCS_ROOT,
        "pages_found": len(all_pages),
        "broken_links": broken_links,
        "stub_pages": stub_pages,
        "last_updated_dates": last_updated_dates,
        "all_pages": all_pages,
        "external_links_count": external_links_count,
        "fetched_at": fetched_at,
        "notes": notes,
    }


def _process_page(
    url: str,
    html: str,
    all_pages: list[str],
    stub_pages: list[dict],
    last_updated_dates: list[dict],
) -> None:
    """Extract metadata from a successfully fetched page and mutate the accumulator lists."""
    all_pages.append(url)

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True).lower()

    # Check for stub phrases
    matched: list[str] = []
    for phrase in STUB_PHRASES:
        if phrase in text:
            matched.append(phrase)
    if matched:
        stub_pages.append({"url": url, "phrases": matched})

    # Check for last-updated dates
    # Search in raw text (pre-lowercasing to preserve date strings)
    raw_text = soup.get_text(separator=" ", strip=True)
    for pattern in LAST_UPDATED_PATTERNS:
        m = pattern.search(raw_text)
        if m:
            date_text = m.group(1).strip()
            last_updated_dates.append({"url": url, "date_text": date_text})
            break  # one per page is enough
