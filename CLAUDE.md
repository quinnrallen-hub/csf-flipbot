# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the live bot (dry_run=True by default, no real money)
python main.py run
python main.py run --live       # sets dry_run=False — real purchases
python main.py run --sim        # live data, trades tracked in DB as paper trades

# Account / P&L
python main.py account          # show CSFloat balance + inventory (requires API key)
python main.py summary          # live trade P&L
python main.py sim-summary      # paper/sim trade P&L
python main.py reset-budget     # clear today's spend and open sim positions

# Real-time paper trader (Skinport data, no API key needed — what runs on the VPS)
python realtime_paper.py
python realtime_paper.py --buy 0.85 --sell 0.95 --balance 200

# Backtest on live Steam data (requires fetch_steam.py first)
python backtest/run_backtest.py --live --days 60 --items 200
python backtest/run_backtest.py --live --sweep   # grid search buy=0.70-0.90

# Fetch live Steam Market price history (needs STEAM_LOGIN_SECURE cookie)
export STEAM_LOGIN_SECURE="your_cookie_value"
python backtest/fetch_steam.py

# Download Kaggle dataset (needs KAGGLE_USERNAME + KAGGLE_KEY env vars)
python backtest/download_data.py

# Historical paper trading (replays steam_live data)
python paper_trade.py --speed 10000
python paper_trade.py --speed 50000 --buy 0.87 --sell 0.98
```

## Architecture

There are two separate trading pipelines that don't share code:

### Pipeline 1 — `main.py` / `bot.py` (CSFloat API bot)

`main.py` → `bot.py` (poll loop) → `csfloat_client.py` (API) → `pricer.py` (buy decision) → `tracker.py` (SQLite P&L)

- **`config.py`** — single `Config` dataclass, all parameters. `dry_run=True` by default. Reads `CSF_API_KEY` and `DISCORD_WEBHOOK_URL` from env.
- **`csfloat_client.py`** — wraps CSFloat REST API. All prices are in **cents** from the API; `pricer.py` converts to USD.
- **`pricer.py`** — `evaluate_listing(listing) -> Decision`. Uses CSFloat's `reference_price` field as baseline, applies sticker value (tiered recovery rates) and low-float premium, then checks `buy_threshold`. Returns a `Decision` dataclass.
- **`tracker.py`** — SQLite with two schemas: `trades` (live) and `paper_trades` (sim). Functions prefixed `paper_` operate on the sim table. `get_daily_spend()` / `get_paper_daily_spend()` enforce the daily budget.
- **`skinport_source.py`** — drop-in listing source for sim mode when no API key is set. Converts Skinport bulk prices into synthetic CSFloat-format listing dicts (float=0.5 placeholder, no sticker data). Caches for 10 min.
- **`bot.py`** has three modes: live (`dry_run=False`), dry-run (logs buys, no API calls), and sim (`--sim` flag, tracks paper trades in DB using real CSFloat listing data).

### Pipeline 2 — `realtime_paper.py` (standalone Skinport sim)

This is the script running on the VPS. It is completely independent of `bot.py`/`tracker.py`/`config.py`.

- Fetches all ~12k Skinport items in one curl request every **60 minutes** (Skinport updates ~daily)
- Evaluates trades every **10 seconds** against cached prices
- Uses `median_price` from the Skinport API directly as the reference — no warmup window needed
- Max spend per trade = **10% of that day's starting balance**, reset at midnight
- Sends a Discord embed status every **10 minutes** via `DISCORD_WEBHOOK_URL` env var
- `PaperPortfolio` class is inline — not connected to SQLite tracker

### Backtest pipeline

`backtest/run_backtest.py` → `backtest/backtester.py` (portfolio sim) → price CSVs

- `backtester.py` has its own `BacktestConfig` (mirrors `Config` but standalone, not imported from `config.py`)
- Applies 7 realism factors: CSFloat price factor (0.88×), competition catch rate (35%), buy slippage, sell variance, volume filter, permanent dump filter, max daily trades
- **Important**: dip ratio uses raw Steam prices (`price / steam_ref`); `csf_price_factor` only applies to absolute buy/sell prices so it cancels in the ratio
- Kaggle data: timestamps in **milliseconds**, item names base64-encoded in `item_hash_name_base64`
- Steam live data: fetched by `fetch_steam.py` using `steamLoginSecure` cookie

## Key numbers (optimal from sweep)

| Parameter | Value | Notes |
|-----------|-------|-------|
| `buy_threshold` | 0.87 | Buy when price ≤ 87% of reference |
| `sell_target` | 0.98 | List at 98% of reference |
| `csfloat_fee` | 0.02 | 2% CSFloat seller fee |
| `csf_price_factor` | 0.88 | CSFloat ≈ 88% of Steam Market price |
| `competition_catch_rate` | 0.35 | Bot wins ~35% of qualifying opportunities |

Sweep results (60 days, $200 budget): `buy=0.87/sell=0.98` → ~$200 profit, 30 trades, ~97% win rate.

## VPS deployment

The bot runs as a systemd service on `root@46.225.233.180` (SSH key: `~/.ssh/tcg-scanner`):

```bash
systemctl status csf-flipbot
journalctl -u csf-flipbot -f
# Deploy updated realtime_paper.py:
rsync -e "ssh -i ~/.ssh/tcg-scanner" realtime_paper.py root@46.225.233.180:/root/csf-flipbot/
ssh -i ~/.ssh/tcg-scanner root@46.225.233.180 "systemctl restart csf-flipbot"
```
