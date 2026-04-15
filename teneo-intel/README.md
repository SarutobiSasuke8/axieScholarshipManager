# teneo-intel

Ecosystem intelligence CLI for [Teneo Protocol](https://teneo.pro) — a decentralised AI agent network on Solana.

Audits public health signals across GitHub, on-chain activity, documentation, the Agent Console, and social/X — with competitor benchmarking and run-over-run diff tracking.

---

## Setup

```bash
cd teneo-intel
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Python 3.11 or higher is required.

---

## Usage

### Full audit (all sources)
```bash
python main.py
```

### Specific sources only
```bash
python main.py --source github,onchain
python main.py --source github
python main.py --source social,competitors
```

Available source names: `github`, `onchain`, `docs`, `console`, `social`, `competitors`

### Include diff against previous run
```bash
python main.py --diff
python main.py --source github --diff
```

---

## Output

Each run produces two artefacts:

| Artefact | Location | Description |
|---|---|---|
| Markdown report | `ecosystem_audit_YYYY-MM-DD.md` | Human-readable audit |
| JSON snapshot | `data/snapshot_YYYY-MM-DDTHH-MM-SS.json` | Machine-readable, used for diffs |

The `data/` directory is gitignored — snapshots stay local.

---

## Data sources and URL confirmation

Sources confirmed from `@teneo-protocol/cli` v2.0.64 package inspection:

| Source | URL / identifier | Status |
|---|---|---|
| Agent Console | `https://agent-console.ai` | Confirmed — page structure needs inspection before scraper is implemented |
| GitHub org | `TeneoProtocolAI` | Confirmed from package.json repository field |
| Backend | `wss://backend.developer.chatroom.teneo-protocol.ai/ws` | Confirmed from SKILL.md metadata |
| Docs site | `https://docs.teneo-protocol.ai` | Inferred — verify with team |
| On-chain | EVM: Base, Peaq, Avalanche, X Layer | Confirmed — **NOT Solana** |
| Token | None — USDC x402 micropayments only | No native token to track |

**Note:** Teneo Protocol uses USDC x402 micropayments, not a native token.
The on-chain scraper tracks USDC supply and chain health across the 4 supported networks.
Set `TENEO_PAYMENT_CONTRACT` for x402 payment volume tracking once the facilitator address is confirmed.

---

## Environment variables (optional)

None are required for a basic run. These unlock additional data:

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | Raises GitHub rate limit from 60 to 5,000 req/hour |
| `TENEO_PAYMENT_CONTRACT` | x402 payment facilitator address — enables query-volume tracking |
| `TENEO_CHAIN` | Preferred chain for on-chain queries: `base` (default), `peaq`, `avalanche`, `xlayer` |

---

## Nitter / X social data

The social scraper uses public [Nitter](https://github.com/zedeus/nitter) instances
to fetch X/Twitter data without auth. Nitter instances can go offline without notice.

The scraper tries a prioritised list of instances (`scrapers/social.py → NITTER_INSTANCES`).
If all are down, it returns a partial result with a `nitter_unavailable` flag rather than
crashing the run.

To check current Nitter instance health: https://status.d420.de/

---

## Graceful failure

Every scraper is wrapped in error handling in `main.py`. If a source fails:
- The error and reason are logged to the terminal
- The run continues with remaining sources
- The failure is recorded in the report's **Data quality log** section
- The JSON snapshot records which sources succeeded and which did not

---

## Competitor benchmarks

The following projects are tracked as ecosystem comparisons:

| Project | GitHub org | Twitter |
|---|---|---|
| Bittensor / TAO | `opentensor` | `@bittensor_` |
| Fetch.ai | `fetchai` | `@Fetch_ai` |
| Autonolas / Olas | `valory-xyz` | `@autonolas` |

---

## Project structure

```
teneo-intel/
├── main.py                  # CLI entrypoint (--source, --diff flags)
├── scrapers/
│   ├── __init__.py
│   ├── github.py            # GitHub API — org repos, commits, contributors
│   ├── onchain.py           # Solscan public API — token + transaction data
│   ├── docs.py              # Docs site crawler — completeness + broken links
│   ├── console.py           # Agent Console — listed agents and metadata
│   ├── social.py            # X/Twitter via Nitter — volume + sentiment
│   └── competitors.py       # Benchmark data for 3 comparable projects
├── reports/
│   ├── __init__.py
│   └── generator.py         # Markdown report builder + snapshot/diff logic
├── data/                    # JSON snapshots (gitignored)
├── requirements.txt
└── README.md
```
