# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the live bot (dry_run=True by default, no real money)
python main.py run
python main.py run --live   # sets dry_run=False — real purchases

# View P&L summary
python main.py summary

# Backtest on live Steam data (requires fetch_steam.py first)
python backtest/run_backtest.py --live --days 60 --items 200
python backtest/run_backtest.py --live --days 60 --buy 0.87 --sell 0.98
python backtest/run_backtest.py --live --sweep   # grid search buy=0.70-0.90

# Fetch live Steam Market price history (needs STEAM_LOGIN_SECURE cookie)
export STEAM_LOGIN_SECURE="your_cookie_value"
python backtest/fetch_steam.py

# Download Kaggle dataset (needs KAGGLE_USERNAME + KAGGLE_KEY env vars)
python backtest/download_data.py

# Historical paper trading (replays steam_live data)
python paper_trade.py --speed 10000   # accelerated replay
python paper_trade.py --speed 50000 --buy 0.87 --sell 0.98

# Real-time paper trading (polls Skinport live prices, no API key needed)
python realtime_paper.py --interval 600 --window 4   # 4-poll warmup, 10min polls
```

## Architecture

The bot has two modes: **live trading** against CSFloat API, and **simulation** using historical Steam Market data.

### Live trading flow
`main.py` → `bot.py` (poll loop) → `csfloat_client.py` (API) → `pricer.py` (buy decision) → `tracker.py` (SQLite P&L)

- `config.py` — single `Config` dataclass, all parameters live here. `dry_run=True` by default.
- `csfloat_client.py` — wraps CSFloat API. All prices are in **cents** from the API; `pricer.py` converts to USD.
- `pricer.py` — `evaluate_listing(listing) -> Decision`. Uses CSFloat's built-in `reference_price` field as the baseline, then applies sticker value and low-float premium before checking `buy_threshold`.
- `tracker.py` — SQLite DB. Records buys, listings, and sales. `get_daily_spend()` enforces the daily budget.

### Backtest / simulation flow
`run_backtest.py` → `backtester.py` (portfolio sim) → price CSVs from Kaggle or Steam Market API

- `backtester.py` has its own `BacktestConfig` (mirrors `Config` but standalone). The portfolio simulation applies 7 realism factors: CSFloat price factor (0.88×), competition catch rate (35%), buy slippage, sell variance, volume filter, permanent dump filter, and max daily trades.
- **Important**: dip ratio is calculated using raw Steam prices (`price / steam_ref`); the `csf_price_factor` is only applied to absolute buy/sell prices. This ensures the factor cancels in the ratio.
- Data sources:
  - Kaggle (`backtest/data/dataset_publish/`) — multi-year history, timestamps in **milliseconds**, item names base64-encoded in `item_hash_name_base64`
  - Steam live (`backtest/data/steam_live/`) — fetched by `fetch_steam.py` using `steamLoginSecure` cookie, plain CSV, timestamps in milliseconds

### Real-time paper trader
`realtime_paper.py` — polls Skinport bulk API (single request returns ~12k items, no rate limits). Uses curl subprocess for Brotli decompression. Builds a rolling median reference over N polls; detects dips when `min_price < buy_threshold × median`. Skinport prices update ~daily so the effective trading window is hours, not seconds.

## Key numbers (optimal from sweep)

| Parameter | Value | Notes |
|-----------|-------|-------|
| `buy_threshold` | 0.87 | Buy when price ≤ 87% of rolling median |
| `sell_target` | 0.98 | List at 98% of reference |
| `csfloat_fee` | 0.02 | 2% CSFloat seller fee |
| `csf_price_factor` | 0.88 | CSFloat ≈ 88% of Steam Market price |
| `competition_catch_rate` | 0.35 | Bot wins ~35% of qualifying opportunities |
| `reference_window_days` | 30 | 30-day rolling median for reference price |

Sweep results (60 days, $200 budget): `buy=0.87/sell=0.98` → ~$200 profit, 30 trades, ~97% win rate. `buy=0.82/sell=0.98` gives better avg/trade ($10) but fewer opportunities.
