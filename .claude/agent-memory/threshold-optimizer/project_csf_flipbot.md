---
name: CSFloat Flipbot Project Context
description: Key facts about the csf-flipbot project structure, data, and backtest setup
type: project
---

## Project: csf-flipbot (CS2 skin flipping bot on CSFloat marketplace)

Working directory: /home/quinn/csf-flipbot

### Backtest entry point
`backtest/run_backtest.py`

Key flags:
- `--buy`  float  (default 0.85): buy when CSFloat price <= ref_price * this
- `--sell` float  (default 0.95): target sell at ref_price * this
- `--live`: use live Steam Market data (Jan 2026 onwards)
- `--days`: simulation window in days
- `--items`: number of items to scan (200 = standard)
- `--max-trades-day`: competition cap (5 used for standard sweep)

### Simulation model
- Reference price = 30-day rolling median of Steam Market prices
- CSFloat prices modeled as ~88% of Steam Market (csf_price_factor = 0.88)
- Competition catch rate: 35% (bot wins listing 35% of the time)
- Buy slippage: up to +4% above floor
- Sell variance: 93-100% of target price (you must undercut to move item)
- Permanent dump filter: skip if price stays below threshold 3+ consecutive days
- Min daily volume: 3 sells/day to be considered liquid
- Max spend per item: $50 USD
- Starting budget: $200 USD
- Max concurrent positions: 10
- Forced exit after 7 days if price hasn't recovered

### Live data window (as of 2026-03-21)
Simulated period covers 2026-01-20 to 2026-03-21 when using --days 60.
