"""
scrapers/console.py

Collects Teneo Protocol agent data from two sources (in priority order):

1. Locally installed agent skill manifests — if `npx @teneo-protocol/cli`
   has been run, each agent installs a SKILL.md into `.agents/skills/` under
   the working directory. These files contain structured frontmatter (name,
   version, description, featured) and a Commands table (command, args,
   price, description). This is the most reliable source.

2. https://agent-console.ai — the public web console. Attempted first as
   a lightweight HTTP fetch. May require JS rendering; if so, returns
   partial data from whatever static HTML is visible and logs a note.

Agent Console URL: https://agent-console.ai
  Confirmed from @teneo-protocol/cli v2.0.64 README.

GitHub org for agent SDK / skills: TeneoProtocolAI
  https://github.com/TeneoProtocolAI/teneo-skills
  https://github.com/TeneoProtocolAI/teneo-agent-sdk

Returns:
    dict with keys:
        agent_count (int)
        featured_count (int)
        agents (list[dict]) — per-agent records, each containing:
            id (str)           — slug without -teneo suffix
            name (str)         — display name from SKILL.md heading
            description (str)
            version (str)
            featured (bool)
            status (str)       — "active" | "stub" (no active commands)
            category (str)     — inferred from description
            command_count (int)
            commands (list[dict]) — {name, args, price, description}
            pricing_min (str|None) — cheapest command price
            pricing_max (str|None) — most expensive command price
        categories (dict)      — {category: count}
        status_breakdown (dict) — {status: count}
        source (str)           — "local_skills" | "web_console" | "both" | "none"
        fetched_at (str ISO timestamp)
        notes (list[str])
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from rich.console import Console

RICH = Console()

CONSOLE_URL = "https://agent-console.ai"  # Confirmed from @teneo-protocol/cli v2.0.64

# Local skills path: check multiple candidate locations
_SKILLS_CANDIDATES = [
    Path(".agents/skills"),                          # project-relative (default after CLI install)
    Path.home() / "teneo-skill" / "skills",          # global install location
    Path("/root/teneo-skill/skills"),                # root home
]

# Agents to skip — not real network agents
_META_SKILLS = {"teneo-agents", "teneo-cli"}

# Category keywords for inference (order matters — first match wins)
_CATEGORY_KEYWORDS = [
    ("DeFi / On-chain",   ["uniswap", "aave", "layerzero", "squid", "bridge", "swap", "gas", "defi", "lending"]),
    ("Crypto Analytics",  ["coinmarketcap", "cryptoquant", "messari", "price", "crypto", "btc", "eth"]),
    ("Social Media",      ["instagram", "tiktok", "linkedin", "youtube", "x platform", "twitter", "social"]),
    ("E-commerce",        ["amazon", "product", "search", "review"]),
    ("Prediction Markets",["predexon", "prediction", "market", "trading"]),
    ("Data / Analytics",  ["google", "maps", "search", "vc attention"]),
    ("Infrastructure",    ["gas", "sniper", "monitor"]),
]


def _infer_category(name: str, description: str) -> str:
    """Infer a category from agent name and description."""
    text = (name + " " + description).lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(kw in text for kw in keywords):
            return category
    return "Other"


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML-style frontmatter from a SKILL.md file.

    Returns a dict of key: value pairs from the --- block.
    Values are unquoted and stripped.
    """
    fm: dict = {}
    if not text.startswith("---"):
        return fm
    end = text.find("\n---", 3)
    if end == -1:
        return fm
    block = text[3:end].strip()
    for line in block.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip('"')
    return fm


def _parse_display_name(text: str) -> str:
    """Extract display name from the first H1 heading in the SKILL.md."""
    m = re.search(r"^#\s+(.+?)(?:\s+-\s+powered by .+)?$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


def _parse_commands(text: str) -> list[dict]:
    """Parse the Commands table from a SKILL.md file.

    Looks for the markdown table under the ## Commands section.
    Returns a list of {name, args, price, description} dicts.
    """
    commands: list[dict] = []

    # Find ## Commands section
    m = re.search(r"^##\s+Commands\s*$", text, re.MULTILINE)
    if not m:
        return commands

    section = text[m.end():]

    # Find the table header row: | Command | Arguments | Price | Description |
    # Then parse each data row
    in_table = False
    for line in section.splitlines():
        stripped = line.strip()

        if not stripped.startswith("|"):
            if in_table:
                break  # End of table
            continue

        # Header row detection
        if re.search(r"\|\s*Command\s*\|", stripped, re.IGNORECASE):
            in_table = True
            continue

        # Separator row
        if re.match(r"^\|[-| ]+\|$", stripped):
            continue

        if not in_table:
            continue

        # Data row: | `cmd` | args | price | description |
        cols = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cols) < 2:
            continue

        cmd_name = cols[0].strip("`").strip()
        args = cols[1] if len(cols) > 1 else ""
        price = cols[2] if len(cols) > 2 else ""
        description = cols[3] if len(cols) > 3 else ""

        if not cmd_name or cmd_name.lower() in ("command", "---"):
            continue

        commands.append({
            "name": cmd_name,
            "args": args,
            "price": price,
            "description": description,
        })

    return commands


def _extract_price_value(price_str: str) -> float | None:
    """Extract a numeric USD value from a price string like '$0.0025/per-query'."""
    m = re.search(r"\$([\d.]+)", price_str)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _parse_skill_file(skill_path: Path) -> dict | None:
    """Parse a single agent SKILL.md file into a structured dict.

    Returns None if the file cannot be parsed or is a meta-skill.
    """
    try:
        text = skill_path.read_text(encoding="utf-8")
    except OSError:
        return None

    fm = _parse_frontmatter(text)
    if not fm:
        return None

    skill_name = fm.get("name", "")

    # Strip -teneo suffix to get the agent ID
    agent_id = re.sub(r"-teneo$", "", skill_name)

    display_name = _parse_display_name(text) or agent_id.replace("-", " ").title()
    description = fm.get("description", "")
    version = fm.get("version", "")
    featured = fm.get("featured", "").lower() == "true"

    commands = _parse_commands(text)

    # Determine status
    # "stub" = no real commands or only a help command
    real_commands = [c for c in commands if c["name"].lower() not in ("help", "")]
    status = "active" if real_commands else "stub"

    # Pricing
    prices = [_extract_price_value(c["price"]) for c in real_commands]
    prices = [p for p in prices if p is not None]
    pricing_min = f"${min(prices)}" if prices else None
    pricing_max = f"${max(prices)}" if prices else None

    category = _infer_category(display_name, description)

    return {
        "id": agent_id,
        "name": display_name,
        "description": description,
        "version": version,
        "featured": featured,
        "status": status,
        "category": category,
        "command_count": len(real_commands),
        "commands": commands,
        "pricing_min": pricing_min,
        "pricing_max": pricing_max,
    }


def _find_skills_dir() -> Path | None:
    """Return the first skills directory that exists and contains agent skill dirs."""
    for candidate in _SKILLS_CANDIDATES:
        if candidate.exists():
            subdirs = [d for d in candidate.iterdir() if d.is_dir()]
            if subdirs:
                return candidate
    return None


def _scrape_local_skills() -> list[dict]:
    """Parse all locally installed agent SKILL.md files.

    Returns a list of agent dicts, or empty list if no skills dir found.
    """
    skills_dir = _find_skills_dir()
    if not skills_dir:
        return []

    agents: list[dict] = []
    seen_ids: set[str] = set()

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        dir_name = skill_dir.name

        # Skip meta skills
        if dir_name in _META_SKILLS:
            continue

        # Nested teneo-agents/ subdirectory — scan inside it too
        if dir_name == "teneo-agents":
            for sub_dir in sorted(skill_dir.iterdir()):
                if not sub_dir.is_dir():
                    continue
                skill_file = sub_dir / "SKILL.md"
                if skill_file.exists():
                    agent = _parse_skill_file(skill_file)
                    if agent and agent["id"] not in seen_ids:
                        seen_ids.add(agent["id"])
                        agents.append(agent)
            continue

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        agent = _parse_skill_file(skill_file)
        if agent and agent["id"] not in seen_ids:
            seen_ids.add(agent["id"])
            agents.append(agent)

    return agents


async def _scrape_web_console(client: httpx.AsyncClient) -> tuple[list[dict], list[str]]:
    """Attempt to scrape agent data from https://agent-console.ai.

    Returns (agents_list, notes). agents_list may be empty if the page
    is JS-rendered or returns non-200.
    """
    agents: list[dict] = []
    notes: list[str] = []

    try:
        resp = await client.get(CONSOLE_URL, timeout=15.0)
        if not resp.is_success:
            notes.append(f"agent-console.ai returned HTTP {resp.status_code}")
            return agents, notes

        html = resp.text

        # Quick JS-render detection: if the body has very little text content
        # compared to script tags, it's almost certainly a JS bundle page.
        soup = BeautifulSoup(html, "html.parser")
        body_text = soup.get_text(separator=" ", strip=True)

        script_tags = len(soup.find_all("script"))
        if len(body_text) < 200 and script_tags > 2:
            notes.append(
                "agent-console.ai appears to be a JS-rendered SPA — "
                "static HTML scraping returned minimal content. "
                "Use local SKILL.md files (installed by CLI) for agent data."
            )
            return agents, notes

        # Attempt to find agent cards / list items
        # Common patterns: <div class="agent-card">, <article>, <li class="agent">
        agent_cards = (
            soup.select("[class*='agent-card']")
            or soup.select("[class*='agent_card']")
            or soup.select("article")
            or soup.select("[class*='agent-item']")
        )

        for card in agent_cards:
            name_el = card.select_one("h2, h3, h4, [class*='name'], [class*='title']")
            desc_el = card.select_one("p, [class*='description'], [class*='desc']")
            name = name_el.get_text(strip=True) if name_el else ""
            description = desc_el.get_text(strip=True) if desc_el else ""
            if name:
                agents.append({
                    "id": name.lower().replace(" ", "-"),
                    "name": name,
                    "description": description,
                    "source": "web_console",
                })

        if not agents:
            notes.append(
                "agent-console.ai returned HTML but no recognisable agent cards found. "
                "Page structure may have changed."
            )

    except httpx.TimeoutException:
        notes.append("agent-console.ai timed out.")
    except Exception as exc:
        notes.append(f"agent-console.ai fetch failed: {exc}")

    return agents, notes


async def scrape() -> dict[str, Any]:
    """Collect Teneo Protocol agent data.

    Sources (in priority order):
      1. Locally installed SKILL.md files (from CLI install)
      2. https://agent-console.ai web scrape

    Returns a structured dict. Never raises.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    notes: list[str] = []

    RICH.print("[cyan][console] Scanning for local agent skill manifests...[/]")
    local_agents = _scrape_local_skills()

    if local_agents:
        RICH.print(f"[cyan][console] Found {len(local_agents)} agents from local SKILL.md files[/]")
        notes.append(f"Data sourced from locally installed SKILL.md files (CLI v2.0.65)")
        source = "local_skills"
        agents = local_agents
    else:
        notes.append("No local SKILL.md files found — attempting agent-console.ai web scrape")
        RICH.print("[cyan][console] No local skills found — trying agent-console.ai...[/]")
        source = "none"
        agents = []

    # Always attempt the web console too for any agents not in local skills
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; teneo-intel/1.0)"},
    ) as client:
        web_agents, web_notes = await _scrape_web_console(client)
        notes.extend(web_notes)

    if web_agents and not local_agents:
        source = "web_console"
        agents = web_agents
    elif web_agents and local_agents:
        # Merge: web agents not already in local list
        local_ids = {a["id"] for a in local_agents}
        new_from_web = [a for a in web_agents if a.get("id") not in local_ids]
        if new_from_web:
            agents = local_agents + new_from_web
            source = "both"
            notes.append(f"Merged {len(new_from_web)} additional agents from web console")

    if not agents:
        notes.append(
            "No agent data available. Run 'npx @teneo-protocol/cli' to install "
            "local skill manifests, or ensure agent-console.ai is accessible."
        )

    # Build aggregates
    categories: dict[str, int] = {}
    status_breakdown: dict[str, int] = {}
    featured_count = 0

    for agent in agents:
        cat = agent.get("category", "Other")
        categories[cat] = categories.get(cat, 0) + 1

        status = agent.get("status", "unknown")
        status_breakdown[status] = status_breakdown.get(status, 0) + 1

        if agent.get("featured"):
            featured_count += 1

    # Log per-agent summary
    for agent in agents:
        RICH.print(
            f"[dim]  {agent['name']} "
            f"({'featured' if agent.get('featured') else 'standard'}, "
            f"{agent.get('command_count', '?')} cmds, "
            f"{agent.get('category', 'Other')})[/]"
        )

    return {
        "agent_count": len(agents),
        "featured_count": featured_count,
        "agents": agents,
        "categories": categories,
        "status_breakdown": status_breakdown,
        "source": source,
        "fetched_at": fetched_at,
        "notes": notes,
    }
