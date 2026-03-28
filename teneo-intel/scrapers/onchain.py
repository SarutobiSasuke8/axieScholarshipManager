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
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from rich.console import Console

RICH = Console()

SOLSCAN_API = "https://public-api.solscan.io"
JUPITER_PRICE_API = "https://price.jup.ag/v4/price"

# Set these once the team confirms the addresses
TENEO_TOKEN_MINT: str | None = os.environ.get("TENEO_TOKEN_MINT", None)
TENEO_PROGRAM_ID: str | None = os.environ.get("TENEO_PROGRAM_ID", None)

WHALE_THRESHOLD_USD = 10_000
LAMPORTS_PER_SOL = 1_000_000_000


async def scrape() -> dict[str, Any]:
    """Fetch on-chain metrics for Teneo Protocol via Solscan public API.

    Returns a structured dict. If no token mint is configured, returns
    a partial result with configured=False and a note to the caller.
    Never raises — all errors are caught and reflected in the notes field.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    notes: list[str] = []

    mint = os.environ.get("TENEO_TOKEN_MINT", TENEO_TOKEN_MINT)

    if not mint:
        return {
            "configured": False,
            "token": None,
            "holder_count": None,
            "volume_24h_usd": None,
            "volume_7d_usd": None,
            "unique_wallets_7d": None,
            "whale_transactions": [],
            "fetched_at": fetched_at,
            "notes": [
                "Set TENEO_TOKEN_MINT env var to enable on-chain metrics",
            ],
        }

    token_meta: dict | None = None
    holder_count: int | None = None
    volume_24h_usd: float | None = None
    volume_7d_usd: float | None = None
    unique_wallets_7d: int | None = None
    whale_transactions: list[dict] = []
    sol_price_usd: float | None = None

    headers = {
        "User-Agent": "teneo-intel/1.0",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:

        # --- Jupiter price API for SOL/USD price ---
        try:
            sol_resp = await client.get(
                JUPITER_PRICE_API,
                params={"ids": "So11111111111111111111111111111111111111112"},
                headers=headers,
            )
            if sol_resp.is_success:
                sol_data = sol_resp.json()
                sol_price_usd = (
                    sol_data.get("data", {})
                    .get("So11111111111111111111111111111111111111112", {})
                    .get("price")
                )
        except Exception as exc:
            notes.append(f"Jupiter price API unavailable: {exc}")

        # --- Token price from Jupiter ---
        token_price_usd: float | None = None
        try:
            price_resp = await client.get(
                JUPITER_PRICE_API,
                params={"ids": mint},
                headers=headers,
            )
            if price_resp.is_success:
                price_data = price_resp.json()
                token_price_usd = (
                    price_data.get("data", {}).get(mint, {}).get("price")
                )
        except Exception as exc:
            notes.append(f"Token price unavailable from Jupiter: {exc}")

        # --- Token metadata ---
        try:
            meta_resp = await client.get(
                f"{SOLSCAN_API}/token/meta",
                params={"tokenAddress": mint},
                headers=headers,
            )
            if meta_resp.is_success:
                meta_json = meta_resp.json()
                token_meta = {
                    "name": meta_json.get("name"),
                    "symbol": meta_json.get("symbol"),
                    "decimals": meta_json.get("decimals"),
                    "supply": meta_json.get("supply"),
                    "mint": mint,
                }
            elif meta_resp.status_code in (401, 403):
                notes.append(
                    "Solscan /token/meta requires a Pro API key — endpoint not accessible."
                )
            else:
                notes.append(
                    f"Solscan /token/meta returned {meta_resp.status_code}"
                )
        except Exception as exc:
            notes.append(f"Token metadata fetch failed: {exc}")

        # --- Holder count ---
        try:
            holders_resp = await client.get(
                f"{SOLSCAN_API}/token/holders",
                params={"tokenAddress": mint, "limit": 10, "offset": 0},
                headers=headers,
            )
            if holders_resp.is_success:
                holders_json = holders_resp.json()
                holder_count = (
                    holders_json.get("data", {}).get("total")
                    if isinstance(holders_json.get("data"), dict)
                    else holders_json.get("total")
                )
            elif holders_resp.status_code in (401, 403):
                notes.append(
                    "Solscan /token/holders requires a Pro API key — endpoint not accessible."
                )
            else:
                notes.append(
                    f"Solscan /token/holders returned {holders_resp.status_code}"
                )
        except Exception as exc:
            notes.append(f"Holder count fetch failed: {exc}")

        # --- Recent transactions ---
        transactions: list[dict] = []
        try:
            txn_resp = await client.get(
                f"{SOLSCAN_API}/account/transactions",
                params={"account": mint, "limit": 50},
                headers=headers,
            )
            if txn_resp.is_success:
                txn_data = txn_resp.json()
                if isinstance(txn_data, list):
                    transactions = txn_data
                elif isinstance(txn_data, dict):
                    transactions = txn_data.get("data", [])
            elif txn_resp.status_code in (401, 403):
                notes.append(
                    "Solscan /account/transactions requires a Pro API key — endpoint not accessible."
                )
            else:
                notes.append(
                    f"Solscan /account/transactions returned {txn_resp.status_code}"
                )
        except Exception as exc:
            notes.append(f"Transaction fetch failed: {exc}")

        # --- Process transactions ---
        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)
        cutoff_7d = now - timedelta(days=7)

        wallets_7d: set[str] = set()
        vol_24h = 0.0
        vol_7d = 0.0

        whale_lamport_threshold: float | None = None
        if sol_price_usd and sol_price_usd > 0:
            whale_lamport_threshold = WHALE_THRESHOLD_USD / sol_price_usd * LAMPORTS_PER_SOL

        for txn in transactions:
            block_time = txn.get("blockTime") or txn.get("block_time")
            lamports = txn.get("lamport") or txn.get("fee") or 0
            signer = txn.get("signer") or txn.get("fee_payer")

            if block_time:
                try:
                    txn_dt = datetime.fromtimestamp(block_time, tz=timezone.utc)
                except (ValueError, OSError, OverflowError):
                    txn_dt = None

                if txn_dt:
                    # Rough USD value estimate from lamports
                    usd_val = 0.0
                    if sol_price_usd and lamports:
                        usd_val = (lamports / LAMPORTS_PER_SOL) * sol_price_usd

                    if txn_dt >= cutoff_24h:
                        vol_24h += usd_val
                    if txn_dt >= cutoff_7d:
                        vol_7d += usd_val
                        if signer:
                            if isinstance(signer, list):
                                wallets_7d.update(signer)
                            else:
                                wallets_7d.add(str(signer))

                    # Whale detection
                    if whale_lamport_threshold and lamports > whale_lamport_threshold:
                        whale_transactions.append(
                            {
                                "signature": txn.get("txHash") or txn.get("signature"),
                                "block_time": block_time,
                                "lamports": lamports,
                                "usd_estimate": round(usd_val, 2),
                                "signer": signer,
                            }
                        )
            elif signer:
                # No timestamp — still track wallet
                if isinstance(signer, list):
                    wallets_7d.update(signer)
                else:
                    wallets_7d.add(str(signer))

        if transactions:
            volume_24h_usd = round(vol_24h, 2) if sol_price_usd else None
            volume_7d_usd = round(vol_7d, 2) if sol_price_usd else None
            unique_wallets_7d = len(wallets_7d) if wallets_7d else None
        else:
            notes.append("No transactions retrieved — volume and wallet estimates unavailable.")

    return {
        "configured": True,
        "token": token_meta,
        "holder_count": holder_count,
        "volume_24h_usd": volume_24h_usd,
        "volume_7d_usd": volume_7d_usd,
        "unique_wallets_7d": unique_wallets_7d,
        "whale_transactions": whale_transactions,
        "sol_price_usd": sol_price_usd,
        "token_price_usd": token_price_usd,
        "fetched_at": fetched_at,
        "notes": notes,
    }
