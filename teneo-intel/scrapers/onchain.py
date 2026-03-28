"""
scrapers/onchain.py

Queries Solscan public API for on-chain activity related to Teneo Protocol.

Base URL: https://public-api.solscan.io  (no API key required for basic endpoints)

What we try to collect:
  - Token metadata if a Teneo token contract address is known:
      holder_count, supply, decimals
  - 24h and 7d transaction volume (USD equivalent where available)
  - Unique wallet interactions over past 7 days
  - Recent large transactions > $10k equivalent (flagged as "whale signals")

Configuration:
  TENEO_TOKEN_MINT — Solana mint address for the Teneo token.
                     Set this once confirmed by the team.
                     If unset, this scraper returns a "needs_config" flag
                     rather than failing.

  TENEO_PROGRAM_ID — Optional program ID if Teneo runs an on-chain program.
                     Used as fallback if no token mint is configured.

Solscan public API docs: https://public-api.solscan.io/docs/

Returns:
    dict with keys:
        configured (bool) — False if no contract address is available
        token (dict|None) — token metadata
        volume_24h_usd (float|None)
        volume_7d_usd (float|None)
        unique_wallets_7d (int|None)
        whale_transactions (list) — txns > $10k equivalent
        fetched_at (str ISO timestamp)
        notes (list[str]) — any caveats or missing-data flags
"""

import os
from datetime import datetime, timezone
from typing import Any

import httpx
from rich.console import Console

RICH = Console()

SOLSCAN_API = "https://public-api.solscan.io"

# Set these once the team confirms the addresses
TENEO_TOKEN_MINT: str | None = os.environ.get("TENEO_TOKEN_MINT", None)
TENEO_PROGRAM_ID: str | None = os.environ.get("TENEO_PROGRAM_ID", None)

WHALE_THRESHOLD_USD = 10_000


async def scrape() -> dict[str, Any]:
    """Fetch on-chain metrics for Teneo Protocol via Solscan public API.

    Returns a structured dict. If no token mint is configured, returns
    a partial result with configured=False and a note to the caller.
    Raises httpx.HTTPError on unrecoverable network failure (caller handles).
    """
    raise NotImplementedError("On-chain scraper not yet implemented — scaffold only.")
