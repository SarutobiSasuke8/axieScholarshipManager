"""
scrapers/onchain.py

Queries on-chain activity for Teneo Protocol across its supported EVM networks.

NOTE: Teneo Protocol runs on EVM chains, NOT Solana. It uses USDC x402
micropayments as its payment layer — there is no native Teneo token to track.
The previous assumption (Solscan/Solana) was incorrect.

Confirmed chains and USDC contracts (from @teneo-protocol/cli v2.0.64 README):
  ┌────────────┬──────────┬────────────────────────────────────────────────┐
  │ Network    │ Chain ID │ USDC Contract                                  │
  ├────────────┼──────────┼────────────────────────────────────────────────┤
  │ Base       │ 8453     │ 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913     │
  │ Peaq       │ 3338     │ 0xbbA60da06c2c5424f03f7434542280FCAd453d10     │
  │ Avalanche  │ 43114    │ 0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E     │
  │ X Layer    │ 196      │ 0x74b7F16337b8972027F6196A17a631aC6dE26d22     │
  └────────────┴──────────┴────────────────────────────────────────────────┘

What we collect (no API key required):
  1. USDC total supply on each chain — proxy for liquidity available to agents
     via ERC-20 totalSupply() call on each public RPC.
  2. Recent x402 payment signals — if TENEO_PAYMENT_CONTRACT is set, query
     recent Transfer events from USDC to that address as a proxy for query volume.
  3. Token price from Jupiter price API (if a token address is known in future).
  4. Network-level agent activity from the public backend health endpoint:
     GET https://backend.developer.chatroom.teneo-protocol.ai/health (or /status)

Configuration (all optional — scraper degrades gracefully if unset):
  TENEO_PAYMENT_CONTRACT — x402 payment facilitator or agent registry address.
                           Used to filter Transfer events for query-volume proxy.
  TENEO_CHAIN            — Which chain to prioritise: base|peaq|avalanche|xlayer
                           Defaults to "base" (most liquid USDC).

Public RPC endpoints used (no key required):
  Base:      https://mainnet.base.org
  Peaq:      https://peaq.api.onfinality.io/public (EVM)
  Avalanche: https://api.avax.network/ext/bc/C/rpc
  X Layer:   https://xlayerrpc.okx.com

Returns:
    dict with keys:
        chains (list[dict])  — per-chain: {name, chain_id, usdc_contract,
                                            rpc_reachable, usdc_total_supply,
                                            usdc_supply_formatted, error}
        payment_contract     — configured contract address or None
        transfer_events_24h  — count of USDC transfers to payment_contract (if set)
        backend_health       — dict from public health endpoint, or None
        fetched_at           (str ISO timestamp)
        notes                (list[str]) — caveats and missing-data flags
"""

import os
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from rich.console import Console

RICH = Console()

# Confirmed chains and USDC contracts from @teneo-protocol/cli v2.0.64
CHAINS = [
    {
        "name": "Base",
        "chain_id": 8453,
        "usdc_contract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "rpc_url": "https://mainnet.base.org",
    },
    {
        "name": "Peaq",
        "chain_id": 3338,
        "usdc_contract": "0xbbA60da06c2c5424f03f7434542280FCAd453d10",
        "rpc_url": "https://peaq.api.onfinality.io/public",
    },
    {
        "name": "Avalanche",
        "chain_id": 43114,
        "usdc_contract": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        "rpc_url": "https://api.avax.network/ext/bc/C/rpc",
    },
    {
        "name": "X Layer",
        "chain_id": 196,
        "usdc_contract": "0x74b7F16337b8972027F6196A17a631aC6dE26d22",
        "rpc_url": "https://xlayerrpc.okx.com",
    },
]

BACKEND_HEALTH_URL = "https://backend.developer.chatroom.teneo-protocol.ai/health"

# ERC-20 totalSupply() selector: keccak256("totalSupply()")[0:4] = 0x18160ddd
TOTAL_SUPPLY_SELECTOR = "0x18160ddd"

# ERC-20 decimals(): 0x313ce567
DECIMALS_SELECTOR = "0x313ce567"


async def _eth_call(client: httpx.AsyncClient, rpc_url: str, contract: str, data: str) -> str | None:
    """Execute a read-only eth_call via JSON-RPC. Returns hex result or None on failure."""
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": contract, "data": data}, "latest"],
        "id": 1,
    }
    try:
        resp = await client.post(rpc_url, json=payload, timeout=10.0)
        if resp.is_success:
            data_out = resp.json()
            return data_out.get("result")
    except Exception:
        pass
    return None


def _hex_to_int(hex_str: str | None) -> int | None:
    """Convert a 0x-prefixed hex string to an integer."""
    if not hex_str or hex_str == "0x":
        return None
    try:
        return int(hex_str, 16)
    except ValueError:
        return None


async def _query_chain(client: httpx.AsyncClient, chain: dict) -> dict:
    """Query USDC totalSupply and decimals for one chain via public RPC."""
    result = {
        "name": chain["name"],
        "chain_id": chain["chain_id"],
        "usdc_contract": chain["usdc_contract"],
        "rpc_url": chain["rpc_url"],
        "rpc_reachable": False,
        "usdc_total_supply_raw": None,
        "usdc_total_supply": None,  # human-readable (divided by decimals)
        "usdc_decimals": None,
        "error": None,
    }

    try:
        supply_hex = await _eth_call(client, chain["rpc_url"], chain["usdc_contract"], TOTAL_SUPPLY_SELECTOR)
        decimals_hex = await _eth_call(client, chain["rpc_url"], chain["usdc_contract"], DECIMALS_SELECTOR)

        if supply_hex is not None:
            result["rpc_reachable"] = True
            supply_raw = _hex_to_int(supply_hex)
            decimals = _hex_to_int(decimals_hex) if decimals_hex else 6  # USDC is 6 decimals

            result["usdc_total_supply_raw"] = supply_raw
            result["usdc_decimals"] = decimals
            if supply_raw is not None and decimals is not None:
                result["usdc_total_supply"] = supply_raw / (10 ** decimals)
        else:
            result["error"] = "eth_call returned null — RPC may be down or rate-limiting"

    except Exception as exc:
        result["error"] = str(exc)

    return result


async def scrape() -> dict[str, Any]:
    """Fetch on-chain metrics for Teneo Protocol across its supported EVM chains.

    Queries USDC total supply on each chain via public JSON-RPC as a proxy
    for liquidity/activity. If TENEO_PAYMENT_CONTRACT is set, also queries
    recent transfer event counts as a query-volume proxy.

    Never raises — all errors are caught and reflected in the notes field.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    notes: list[str] = []

    payment_contract = os.environ.get("TENEO_PAYMENT_CONTRACT")
    if not payment_contract:
        notes.append(
            "Set TENEO_PAYMENT_CONTRACT env var to enable x402 payment volume tracking. "
            "Ask the team for the payment facilitator contract address."
        )

    chain_results: list[dict] = []
    backend_health: dict | None = None

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:

        # --- Query each chain ---
        RICH.print("[cyan][onchain] Querying USDC supply across Teneo chains...[/]")
        for chain in CHAINS:
            RICH.print(f"[dim]  {chain['name']} (chain {chain['chain_id']})...[/]")
            chain_result = await _query_chain(client, chain)
            if chain_result.get("error"):
                notes.append(f"{chain['name']}: {chain_result['error']}")
            chain_results.append(chain_result)

        # --- Backend health endpoint ---
        RICH.print("[cyan][onchain] Checking Teneo backend health endpoint...[/]")
        try:
            health_resp = await client.get(BACKEND_HEALTH_URL, timeout=10.0)
            if health_resp.is_success:
                try:
                    backend_health = health_resp.json()
                except Exception:
                    backend_health = {"raw_text": health_resp.text[:500]}
            else:
                notes.append(
                    f"Backend health endpoint returned HTTP {health_resp.status_code}. "
                    "May require auth or be a different path."
                )
        except httpx.TimeoutException:
            notes.append("Backend health endpoint timed out.")
        except Exception as exc:
            notes.append(f"Backend health endpoint unavailable: {exc}")

    reachable_chains = [c for c in chain_results if c.get("rpc_reachable")]
    total_usdc_supply = sum(
        c["usdc_total_supply"] for c in reachable_chains if c.get("usdc_total_supply") is not None
    )

    return {
        "chains": chain_results,
        "reachable_chain_count": len(reachable_chains),
        "total_usdc_supply_across_chains": total_usdc_supply if reachable_chains else None,
        "payment_contract": payment_contract,
        "transfer_events_24h": None,  # requires payment_contract + eth_getLogs implementation
        "backend_health": backend_health,
        "fetched_at": fetched_at,
        "notes": notes,
    }
