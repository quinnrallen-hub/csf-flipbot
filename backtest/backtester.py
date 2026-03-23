"""
Backtester for the CSFloat flip strategy using the Steam Market CS:GO price dataset.

Realistic simulation model:
  - Reference price = rolling N-day median of Steam Market prices
  - CSFloat prices are ~88% of Steam Market on average (csf_price_factor)
  - Competition: only catch a fraction of valid dips (competition_catch_rate)
  - Buy price has random slippage (you rarely get the exact floor price)
  - Sell price has variance (you have to undercut to move the item)
  - Low-volume items filtered out (hard to sell in practice)
  - Permanent dumps: if price stays down for dump_confirm_days, it's not a dip
  - CSFloat 2% fee applied on sale
"""

import sys
import csv
import gzip
import random
import logging
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

import base64
import statistics

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data" / "dataset_publish"
ITEMS_DIR = DATA_DIR / "items"
LIVE_DIR = Path(__file__).parent / "data" / "steam_live"

# ------------------------------------------------------------------ #
#  Config (mirrors config.py values, standalone for backtest)         #
# ------------------------------------------------------------------ #

@dataclass
class BacktestConfig:
    buy_threshold: float = 0.85       # buy when price <= ref * this
    sell_target: float = 0.95         # target sell price as fraction of ref
    csfloat_fee: float = 0.02
    min_profit_usd: float = 0.50
    max_spend_per_item_usd: float = 50.0
    min_reference_price_usd: float = 1.00
    reference_window_days: int = 30   # rolling window for reference price
    min_sell_hours: float = 5.0       # assumed sell time on the day price recovers (for P&L reporting)
    max_sell_hours: float = 10.0      # used for reporting only; forced exit in days below
    max_sell_days: int = 7            # forced exit if price hasn't recovered after this many days
    starting_budget_usd: float = 200.0
    max_concurrent_positions: int = 10
    max_trades_per_day: int = 3           # simulate competition — you won't catch every dip
    backtest_days: int = 30               # how many days of history to simulate

    # --- Realism factors ---
    # CSFloat listings average ~88% of Steam Market price
    csf_price_factor: float = 0.88
    # Chance your bot actually wins the listing before competitors (0.0-1.0)
    # Real CS2 flip market has heavy bot competition; 15-20% is realistic for liquid skins
    competition_catch_rate: float = 0.18
    # Random buy slippage: you pay slightly above the floor (1.0-1.05)
    buy_slippage_max: float = 1.04
    # Sell price variance: your listing sells at target × this (0.92-1.0)
    sell_variance_min: float = 0.93
    # Min daily Steam Market volume — CSFloat is ~10-20% of Steam, so need 10+ Steam sales/day
    min_daily_volume: int = 10
    # If price stays below buy_threshold for this many days → permanent dump, skip
    # CS2 prices can stay depressed 5-10 days during case releases before recovering
    dump_confirm_days: int = 6

    # Filter to specific skins (empty = all)
    whitelist_names: list = field(default_factory=list)


# ------------------------------------------------------------------ #
#  Data loading                                                        #
# ------------------------------------------------------------------ #

def load_index(live: bool = False) -> dict[str, str]:
    """Returns {item_hash_name: file_name}"""
    if live:
        index_path = LIVE_DIR / "item_index.csv"
        if not index_path.exists():
            print("ERROR: Live data not found. Run: python backtest/fetch_steam.py")
            sys.exit(1)
        items = {}
        with open(index_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                items[row["item_hash_name"]] = row["file_name"]
        return items

    index_path = DATA_DIR / "item_index.csv"
    if not index_path.exists():
        print(f"ERROR: {index_path} not found. Run download_data.py first.")
        sys.exit(1)
    items = {}
    with open(index_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = base64.b64decode(row["item_hash_name_base64"]).decode("utf-8")
            items[name] = row["file_name"]
    return items


def load_price_history(file_name: str, live: bool = False) -> list[tuple[datetime, float, int]]:
    """
    Returns list of (timestamp, price_usd, sells) sorted by date.
    Handles both plain CSV and gzipped CSV.
    """
    records = []
    if live:
        path = LIVE_DIR / file_name
        paths = [path] if path.exists() else []
    else:
        paths = [ITEMS_DIR / (file_name + ext) for ext in ["", ".gz"]
                 if (ITEMS_DIR / (file_name + ext)).exists()]

    for path in paths:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    ts = datetime.fromtimestamp(int(row["timestamp"]) / 1000)
                    price = float(row["price_dollar"])
                    sells = int(row.get("sells", 0))
                    if price > 0:
                        records.append((ts, price, sells))
                except (ValueError, KeyError):
                    continue
        break

    return sorted(records, key=lambda r: r[0])


# ------------------------------------------------------------------ #
#  Single-item backtest                                                #
# ------------------------------------------------------------------ #

@dataclass
class Trade:
    item_name: str
    buy_date: datetime
    buy_price: float
    ref_price_at_buy: float
    sell_date: datetime | None = None
    sell_price: float | None = None
    profit: float | None = None
    forced_exit: bool = False
    assumed_hold_hours: float = 7.5


def backtest_item(
    item_name: str,
    history: list[tuple[datetime, float, int]],
    cfg: BacktestConfig,
) -> list[Trade]:
    """Run strategy on one item's price history. Returns list of completed trades."""
    if len(history) < cfg.reference_window_days + 1:
        return []

    trades: list[Trade] = []
    open_trade: Trade | None = None

    window: list[float] = []

    for i, (ts, price, _sells) in enumerate(history):
        window.append(price)
        if len(window) > cfg.reference_window_days:
            window.pop(0)

        if len(window) < cfg.reference_window_days:
            continue

        ref_price = statistics.median(window)

        if ref_price < cfg.min_reference_price_usd:
            continue

        # --- Check for exit ---
        # Data is daily. We model sell time as 7.5h (midpoint of 5-10h window) into the
        # day price recovers. Forced exit after max_sell_days at whatever price.
        if open_trade:
            held_days = (ts - open_trade.buy_date).days
            target_price = open_trade.ref_price_at_buy * cfg.sell_target
            price_recovered = price >= target_price
            past_max = held_days >= cfg.max_sell_days

            should_sell = price_recovered or past_max

            if should_sell:
                actual_sell = target_price if price_recovered else price
                fee = actual_sell * cfg.csfloat_fee
                # Simulate assumed sell time: 7.5h (midpoint of 5-10h window)
                assumed_hold_hours = held_days * 24 + (cfg.min_sell_hours + cfg.max_sell_hours) / 2
                open_trade.sell_date = ts
                open_trade.sell_price = round(actual_sell, 4)
                open_trade.profit = round(actual_sell - fee - open_trade.buy_price, 4)
                open_trade.forced_exit = past_max and not price_recovered
                open_trade.assumed_hold_hours = assumed_hold_hours
                trades.append(open_trade)
                open_trade = None

        # --- Check for entry ---
        if open_trade is None:
            ratio = price / ref_price
            if ratio <= cfg.buy_threshold:
                if price > cfg.max_spend_per_item_usd:
                    continue
                fee_on_sell = (ref_price * cfg.sell_target) * cfg.csfloat_fee
                expected_profit = (ref_price * cfg.sell_target) - fee_on_sell - price
                if expected_profit < cfg.min_profit_usd:
                    continue
                open_trade = Trade(
                    item_name=item_name,
                    buy_date=ts,
                    buy_price=round(price, 4),
                    ref_price_at_buy=round(ref_price, 4),
                )

    return trades


# ------------------------------------------------------------------ #
#  Portfolio-level backtest                                            #
# ------------------------------------------------------------------ #

def run_backtest(cfg: BacktestConfig, max_items: int = 200, live: bool = False) -> dict:
    print("Loading item index...")
    index = load_index(live=live)

    if cfg.whitelist_names:
        index = {
            k: v for k, v in index.items()
            if any(w.lower() in k.lower() for w in cfg.whitelist_names)
        }

    items = list(index.items())[:max_items]
    print(f"Running backtest on {len(items)} items...")

    # Load all histories first, then simulate a portfolio timeline
    # Include sells volume so we can filter low-liquidity items
    all_histories: dict[str, list] = {}
    for i, (name, file_name) in enumerate(items):
        if i % 25 == 0:
            print(f"  [{i}/{len(items)}] {name[:60]}")
        history = load_price_history(file_name, live=live)
        if history:
            all_histories[name] = history

    # Merge all (date, name, price, sells) events into a single timeline
    events: list[tuple[datetime, str, float, int]] = []
    for name, history in all_histories.items():
        for ts, price, sells in history:
            events.append((ts, name, price, sells))
    events.sort(key=lambda e: e[0])

    # Restrict to last N days of the dataset
    if events:
        end_ts = events[-1][0]
        start_ts = end_ts - timedelta(days=cfg.backtest_days + cfg.reference_window_days)
        events = [e for e in events if e[0] >= start_ts]
        sim_start = end_ts - timedelta(days=cfg.backtest_days)
        print(f"  Simulating {cfg.backtest_days} days: "
              f"{sim_start.strftime('%Y-%m-%d')} → {end_ts.strftime('%Y-%m-%d')}")

    # Rolling reference prices and volume per item
    windows: dict[str, list[float]] = defaultdict(list)
    vol_windows: dict[str, list[int]] = defaultdict(list)
    consec_dip: dict[str, int] = defaultdict(int)  # consecutive days below threshold
    open_trades: dict[str, Trade] = {}
    completed: list[Trade] = []
    balance = cfg.starting_budget_usd
    skipped_no_funds = 0
    skipped_competition = 0
    skipped_low_volume = 0
    skipped_dump = 0
    trades_today: dict[str, int] = defaultdict(int)

    rng = random.Random(42)  # fixed seed for reproducibility

    for ts, name, price, sells in events:
        in_sim = ts >= sim_start

        # Build rolling price window (uses Steam prices as reference)
        w = windows[name]
        w.append(price)
        if len(w) > cfg.reference_window_days:
            w.pop(0)

        # Build rolling volume window
        vw = vol_windows[name]
        vw.append(sells)
        if len(vw) > cfg.reference_window_days:
            vw.pop(0)

        if len(w) < cfg.reference_window_days:
            continue

        # Steam Market reference (used for dip ratio — CSFloat factor cancels in ratio)
        steam_ref = statistics.median(w)
        if steam_ref < cfg.min_reference_price_usd:
            continue

        # Absolute prices adjusted to CSFloat reality (~88% of Steam Market)
        ref_price = steam_ref * cfg.csf_price_factor
        csf_price = price * cfg.csf_price_factor

        # --- Exit open trade ---
        if name in open_trades and in_sim:
            t = open_trades[name]
            held_days = (ts - t.buy_date).days
            target_price = t.ref_price_at_buy * cfg.sell_target
            price_recovered = csf_price >= target_price
            past_max = held_days >= cfg.max_sell_days

            if price_recovered or past_max:
                if price_recovered:
                    # Sell variance: you have to undercut slightly to move the item
                    sell_variance = rng.uniform(cfg.sell_variance_min, 1.0)
                    actual_sell = target_price * sell_variance
                else:
                    actual_sell = csf_price  # forced exit at current market
                fee = actual_sell * cfg.csfloat_fee
                t.sell_date = ts
                t.sell_price = round(actual_sell, 4)
                t.profit = round(actual_sell - fee - t.buy_price, 4)
                t.forced_exit = past_max and not price_recovered
                t.assumed_hold_hours = held_days * 24 + (cfg.min_sell_hours + cfg.max_sell_hours) / 2
                balance += actual_sell - fee
                completed.append(t)
                del open_trades[name]

        # --- Enter new trade (sim window only) ---
        if name not in open_trades and in_sim:
            # Dip ratio uses raw Steam prices (CSFloat factor cancels out)
            ratio = price / steam_ref
            if ratio > cfg.buy_threshold:
                consec_dip[name] = 0
                continue

            # Permanent dump filter: if dip persists too many days, skip
            consec_dip[name] += 1
            if consec_dip[name] > cfg.dump_confirm_days:
                skipped_dump += 1
                continue

            # Volume filter: skip illiquid items
            avg_vol = statistics.mean(vw) if vw else 0
            if avg_vol < cfg.min_daily_volume:
                skipped_low_volume += 1
                continue

            # Buy slippage: you rarely get the exact floor price
            slippage = rng.uniform(1.0, cfg.buy_slippage_max)
            actual_buy = csf_price * slippage

            if actual_buy > cfg.max_spend_per_item_usd:
                continue

            fee_on_sell = (ref_price * cfg.sell_target) * cfg.csfloat_fee
            expected_profit = (ref_price * cfg.sell_target * cfg.sell_variance_min) - fee_on_sell - actual_buy
            if expected_profit < cfg.min_profit_usd:
                continue

            if len(open_trades) >= cfg.max_concurrent_positions:
                continue

            day_key = ts.strftime("%Y-%m-%d")
            if trades_today[day_key] >= cfg.max_trades_per_day:
                continue

            # Competition: you only win the listing X% of the time
            if rng.random() > cfg.competition_catch_rate:
                skipped_competition += 1
                continue

            if balance < actual_buy:
                skipped_no_funds += 1
                continue

            balance -= actual_buy
            trades_today[day_key] += 1
            open_trades[name] = Trade(
                item_name=name,
                buy_date=ts,
                buy_price=round(actual_buy, 4),
                ref_price_at_buy=round(ref_price, 4),
            )

    final_balance = balance + sum(t.buy_price for t in open_trades.values())
    return summarize(completed, cfg, skipped=0,
                     starting_budget=cfg.starting_budget_usd,
                     final_balance=final_balance,
                     skipped_no_funds=skipped_no_funds,
                     skipped_competition=skipped_competition,
                     skipped_low_volume=skipped_low_volume,
                     skipped_dump=skipped_dump)


# ------------------------------------------------------------------ #
#  Summary                                                             #
# ------------------------------------------------------------------ #

def summarize(trades: list[Trade], cfg: BacktestConfig, skipped: int,
              starting_budget: float = 200.0, final_balance: float = 0.0,
              skipped_no_funds: int = 0, skipped_competition: int = 0,
              skipped_low_volume: int = 0, skipped_dump: int = 0) -> dict:
    if not trades:
        return {"error": "No trades generated. Try adjusting thresholds."}

    profits = [t.profit for t in trades if t.profit is not None]
    winners = [p for p in profits if p > 0]
    losers  = [p for p in profits if p <= 0]
    forced  = [t for t in trades if t.forced_exit]

    hold_hours = [
        getattr(t, "assumed_hold_hours", 7.5)
        for t in trades if t.sell_date
    ]

    result = {
        "starting_budget":      starting_budget,
        "final_balance":        round(final_balance, 2),
        "total_return":         f"{(final_balance - starting_budget) / starting_budget * 100:.1f}%",
        "total_trades":         len(trades),
        "skipped_no_funds":     skipped_no_funds,
        "skipped_competition":  skipped_competition,
        "skipped_low_volume":   skipped_low_volume,
        "skipped_dump":         skipped_dump,
        "win_rate":             f"{len(winners)/len(profits)*100:.1f}%",
        "total_profit_usd":     round(sum(profits), 2),
        "avg_profit_per_trade": round(statistics.mean(profits), 4),
        "median_profit":        round(statistics.median(profits), 4),
        "best_trade":           round(max(profits), 4),
        "worst_trade":          round(min(profits), 4),
        "avg_hold_hours":       round(statistics.mean(hold_hours), 1) if hold_hours else 0,
        "forced_exits":         len(forced),
        "forced_exit_rate":     f"{len(forced)/len(trades)*100:.1f}%",
        "total_winners":        len(winners),
        "total_losers":         len(losers),
    }

    # Top 5 most profitable items
    per_item: dict[str, list] = defaultdict(list)
    for t in trades:
        if t.profit is not None:
            per_item[t.item_name].append(t.profit)

    top_items = sorted(
        [(name, sum(ps), len(ps)) for name, ps in per_item.items()],
        key=lambda x: x[1],
        reverse=True,
    )[:5]
    result["top_5_items"] = [
        {"name": name, "total_profit": round(p, 2), "trades": n}
        for name, p, n in top_items
    ]

    return result


# ------------------------------------------------------------------ #
#  Pretty print                                                        #
# ------------------------------------------------------------------ #

def print_results(r: dict):
    if "error" in r:
        print(f"\nERROR: {r['error']}")
        return

    print("\n" + "=" * 55)
    print("  BACKTEST RESULTS")
    print("=" * 55)
    print(f"  Starting budget    : ${r['starting_budget']:.2f}")
    print(f"  Final balance      : ${r['final_balance']:.2f}")
    print(f"  Total return       : {r['total_return']}")
    print(f"  Skipped (no funds) : {r['skipped_no_funds']}")
    print(f"  Lost to bots       : {r['skipped_competition']}")
    print(f"  Low volume skipped : {r['skipped_low_volume']}")
    print(f"  Permanent dumps    : {r['skipped_dump']}")
    print()
    print(f"  Total trades       : {r['total_trades']}")
    print(f"  Win rate           : {r['win_rate']}")
    print(f"  Total profit       : ${r['total_profit_usd']:.2f}")
    print(f"  Avg profit/trade   : ${r['avg_profit_per_trade']:.4f}")
    print(f"  Median profit      : ${r['median_profit']:.4f}")
    print(f"  Best trade         : ${r['best_trade']:.4f}")
    print(f"  Worst trade        : ${r['worst_trade']:.4f}")
    print(f"  Avg hold time      : {r['avg_hold_hours']}h (sell window: 5-10h)")
    print(f"  Forced exits       : {r['forced_exits']} ({r['forced_exit_rate']})")
    print(f"  Winners / Losers   : {r['total_winners']} / {r['total_losers']}")
    print()
    print("  Top 5 items by profit:")
    for item in r["top_5_items"]:
        print(f"    ${item['total_profit']:7.2f}  ({item['trades']} trades)  {item['name'][:45]}")
    print("=" * 55)
