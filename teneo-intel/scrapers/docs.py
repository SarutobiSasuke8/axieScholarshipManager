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

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.progress import track

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


async def scrape() -> dict[str, Any]:
    """Crawl the Teneo docs site and return health/completeness metrics.

    Raises httpx.HTTPError on unrecoverable network failure (caller handles).
    """
    raise NotImplementedError("Docs scraper not yet implemented — scaffold only.")
