#!/usr/bin/env python3
"""
Paper trading mode — simulates a live CSFloat market feed using real Steam Market
price history, applies all the bot logic, and logs what would have been bought/sold.
No money, no API key needed.

Usage:
    python paper_trade.py
"""

import time
import random
import logging
import sqlite3
import statistics
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

from config import CONFIG
from pricer import Decision
from backtest.backtester import load_index, load_price_history, BacktestConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

LIVE_DIR = Path(__file__).parent / "backtest" / "data" / "steam_live"

# ------------------------------------------------------------------ #
#  Paper portfolio tracker                                             #
# ------------------------------------------------------------------ #

class PaperPortfolio:
    def __init__(self, starting_balance: float = 200.0):
        self.balance = starting_balance
        self.starting_balance = starting_balance
        self.open_positions: dict = {}   # item_name -> {buy_price, ref_price, list_price, bought_at}
        self.closed_trades: list = []

    def buy(self, name: str, buy_price: float, ref_price: float, target_sell: float):
        self.balance -= buy_price
        self.open_positions[name] = {
            "buy_price": buy_price,
            "ref_price": ref_price,
            "list_price": target_sell,
            "bought_at": datetime.now(),
        }
        log.info(f"  📥 BUY  {name[:45]:<45}  "
                 f"paid=${buy_price:.2f}  list@${target_sell:.2f}  "
                 f"balance=${self.balance:.2f}")

    def try_sell(self, name: str, current_price: float, rng: random.Random):
        if name not in self.open_positions:
            return
        pos = self.open_positions[name]
        held_h = (datetime.now() - pos["bought_at"]).total_seconds() / 3600

        price_ok = current_price >= pos["list_price"]
        timed_out = held_h >= 168.0  # 7 days — matches backtester max_sell_days

        if price_ok or timed_out:
            sell_price = pos["list_price"] * rng.uniform(0.93, 1.0) if price_ok else current_price
            fee = sell_price * CONFIG.csfloat_fee
            profit = sell_price - fee - pos["buy_price"]
            self.balance += sell_price - fee
            self.closed_trades.append({
                "name": name,
                "buy": pos["buy_price"],
                "sell": sell_price,
                "profit": profit,
                "forced": timed_out and not price_ok,
            })
            tag = "⏰ TIMEOUT" if (timed_out and not price_ok) else "✅ SELL"
            log.info(f"  {tag} {name[:45]:<45}  "
                     f"sold=${sell_price:.2f}  profit=${profit:+.2f}  "
                     f"balance=${self.balance:.2f}")
            del self.open_positions[name]

    def summary(self):
        profits = [t["profit"] for t in self.closed_trades]
        total = sum(profits) if profits else 0
        wins = sum(1 for p in profits if p > 0)
        log.info("─" * 60)
        log.info(f"  Starting balance : ${self.starting_balance:.2f}")
        log.info(f"  Current balance  : ${self.balance:.2f}")
        log.info(f"  Open positions   : {len(self.open_positions)}")
        log.info(f"  Closed trades    : {len(self.closed_trades)}")
        log.info(f"  Realized profit  : ${total:.2f}")
        if profits:
            log.info(f"  Win rate         : {wins/len(profits)*100:.0f}%")
        log.info("─" * 60)


# ------------------------------------------------------------------ #
#  Simulated market feed                                               #
# ------------------------------------------------------------------ #

def build_feed(max_items: int = 200) -> list[dict]:
    """
    Build a list of simulated CSFloat listings from the live Steam price data.
    Each listing = one data point from history with realistic noise applied.
    Returns them sorted oldest→newest so we replay in order.
    """
    index = load_index(live=True)
    items = list(index.items())[:max_items]

    feed = []
    for name, file_name in items:
        history = load_price_history(file_name, live=True)
        if not history:
            continue
        for ts, price, sells in history:
            feed.append({
                "id": f"{name}_{int(ts.timestamp())}",
                "name": name,
                "timestamp": ts,
                "steam_price": price,
                "sells": sells,
            })

    feed.sort(key=lambda x: x["timestamp"])

    # Only keep last 90 days (enough for 30-day ref window + 60 days of sim)
    if feed:
        cutoff = feed[-1]["timestamp"] - __import__("datetime").timedelta(days=90)
        feed = [e for e in feed if e["timestamp"] >= cutoff]

    return feed


# ------------------------------------------------------------------ #
#  Main loop                                                           #
# ------------------------------------------------------------------ #

def run_paper_trade(
    speed_multiplier: int = 500,   # 1 sim-day per (86400/speed_multiplier) real seconds
    max_items: int = 200,
    buy_threshold: float = 0.85,
    sell_target: float = 0.95,
    csf_factor: float = 0.88,
    competition_rate: float = 0.18,
    max_trades_day: int = 3,
    reference_window: int = 30,
    min_volume: int = 10,
    dump_days: int = 6,
):
    log.info("=" * 60)
    log.info("CSFloat Paper Trader — SIMULATION MODE (no real money)")
    log.info(f"  buy_threshold : {buy_threshold:.0%}")
    log.info(f"  sell_target   : {sell_target:.0%}")
    log.info(f"  competition   : {competition_rate:.0%} catch rate")
    log.info(f"  speed         : {speed_multiplier}x realtime")
    log.info("=" * 60)

    log.info("Loading market data...")
    feed = build_feed(max_items)
    if not feed:
        log.error("No data. Run: python backtest/fetch_steam.py")
        return

    portfolio = PaperPortfolio(starting_balance=200.0)
    rng = random.Random()

    windows: dict[str, list[float]] = defaultdict(list)
    vol_windows: dict[str, list[int]] = defaultdict(list)
    consec_dip: dict[str, int] = defaultdict(int)
    trades_today: dict[str, int] = defaultdict(int)
    last_day = None
    # Sleep once per simulated day regardless of how many items fire that day
    day_delay = 86400 / speed_multiplier   # real seconds per simulated day
    current_day_events = 0
    current_sim_day = None

    log.info(f"Replaying {len(feed)} market events...")
    log.info("Press Ctrl+C to stop and see summary.\n")

    try:
        for event in feed:
            name = event["name"]
            ts = event["timestamp"]
            price = event["steam_price"]
            sells = event["sells"]

            # Day boundary — sleep once per sim day and print summary
            day = ts.date()
            if day != last_day:
                if last_day is not None:
                    log.info(f"── {day} ── balance=${portfolio.balance:.2f}  "
                             f"open={len(portfolio.open_positions)}  "
                             f"closed={len(portfolio.closed_trades)}")
                    time.sleep(day_delay)
                last_day = day

            # Build rolling windows
            w = windows[name]
            w.append(price)
            if len(w) > reference_window:
                w.pop(0)
            vw = vol_windows[name]
            vw.append(sells)
            if len(vw) > reference_window:
                vw.pop(0)

            if len(w) < reference_window:
                continue

            steam_ref = statistics.median(w)
            ref_price = steam_ref * csf_factor
            csf_price = price * csf_factor

            # Try to sell any open position for this item
            portfolio.try_sell(name, csf_price, rng)

            # Evaluate for entry
            ratio = price / steam_ref
            if ratio <= buy_threshold:
                consec_dip[name] += 1
            else:
                consec_dip[name] = 0

            if (name not in portfolio.open_positions
                    and ratio <= buy_threshold
                    and consec_dip[name] <= dump_days
                    and statistics.mean(vw) >= min_volume
                    and len(portfolio.open_positions) < 10):

                day_key = ts.strftime("%Y-%m-%d")
                if trades_today[day_key] < max_trades_day:
                    if rng.random() <= competition_rate:
                        slippage = rng.uniform(1.0, 1.04)
                        buy_price = csf_price * slippage
                        if buy_price <= CONFIG.max_spend_per_item_usd and buy_price <= portfolio.balance:
                            sell_price = ref_price * sell_target * rng.uniform(0.93, 1.0)
                            fee_on_sell = sell_price * CONFIG.csfloat_fee
                            if sell_price - fee_on_sell - buy_price >= CONFIG.min_profit_usd:
                                portfolio.buy(name, round(buy_price, 2),
                                              round(ref_price, 2), round(sell_price, 2))
                                trades_today[day_key] += 1

            # No per-event sleep — day boundary handles pacing

    except KeyboardInterrupt:
        log.info("\nStopped.")

    portfolio.summary()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--speed", type=int, default=500,
                   help="Sim speed multiplier (default 500 = ~3 min per sim-month)")
    p.add_argument("--buy",   type=float, default=0.85)
    p.add_argument("--sell",  type=float, default=0.95)
    args = p.parse_args()
    run_paper_trade(speed_multiplier=args.speed, buy_threshold=args.buy, sell_target=args.sell)
