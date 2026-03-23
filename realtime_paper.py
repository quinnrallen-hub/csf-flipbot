#!/usr/bin/env python3
"""
Real-time paper trader using Skinport bulk price API.
Fetches all ~19,000 CS2 skin prices in one request every N minutes.
Builds a rolling reference window, detects dips, simulates CSFloat trades.
No API key needed.

Usage:
    python realtime_paper.py
    python realtime_paper.py --buy 0.85 --interval 600 --window 6
"""

import json
import subprocess
import time
import random
import logging
import argparse
import statistics
from datetime import datetime
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SKINPORT_URL = "https://api.skinport.com/v1/items"
CSFLOAT_FEE  = 0.02

# ------------------------------------------------------------------ #
#  Watchlist — items to actively trade (must exist on Skinport)       #
# ------------------------------------------------------------------ #

WATCHLIST = [
    "AK-47 | Redline (Field-Tested)",
    "AK-47 | Redline (Minimal Wear)",
    "AK-47 | Asiimov (Field-Tested)",
    "AK-47 | Asiimov (Minimal Wear)",
    "AK-47 | Fire Serpent (Field-Tested)",
    "AK-47 | Vulcan (Field-Tested)",
    "AK-47 | Bloodsport (Field-Tested)",
    "AK-47 | Neon Rider (Field-Tested)",
    "AK-47 | Case Hardened (Field-Tested)",
    "AK-47 | Hydroponic (Field-Tested)",
    "AK-47 | Jaguar (Field-Tested)",
    "AK-47 | Head Shot (Field-Tested)",
    "AK-47 | Blue Laminate (Factory New)",
    "AK-47 | Black Laminate (Field-Tested)",
    "AK-47 | Baroque Purple (Field-Tested)",
    "AK-47 | Point Disarray (Field-Tested)",
    "AWP | Asiimov (Field-Tested)",
    "AWP | Wildfire (Field-Tested)",
    "AWP | Hyper Beast (Field-Tested)",
    "AWP | Lightning Strike (Factory New)",
    "AWP | Medusa (Field-Tested)",
    "M4A4 | Howl (Field-Tested)",
    "M4A1-S | Hot Rod (Factory New)",
    "M4A1-S | Hyper Beast (Field-Tested)",
    "M4A1-S | Icarus Fell (Factory New)",
    "M4A1-S | Mecha Industries (Field-Tested)",
    "Desert Eagle | Blaze (Factory New)",
    "Desert Eagle | Printstream (Factory New)",
    "USP-S | Kill Confirmed (Field-Tested)",
    "Glock-18 | Fade (Factory New)",
]


# ------------------------------------------------------------------ #
#  Skinport bulk price fetch                                           #
# ------------------------------------------------------------------ #

def fetch_all_prices() -> dict[str, dict] | None:
    """
    Returns {market_hash_name: {min_price, median_price, quantity}} for all
    CS2 items on Skinport, or None on failure.
    Uses curl for Brotli-compressed response. Single request, no rate limits.
    """
    try:
        result = subprocess.run(
            [
                "curl", "-s", "--compressed",
                "-H", "Accept: application/json",
                "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64)",
                f"{SKINPORT_URL}?app_id=730&currency=USD&tradable=0",
            ],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode != 0:
            log.warning(f"curl failed: {result.stderr[:100]}")
            return None
        data = json.loads(result.stdout)
        out = {}
        for item in data:
            name  = item.get("market_hash_name")
            min_p = item.get("min_price")
            med_p = item.get("median_price")
            qty   = item.get("quantity") or 0
            if name and min_p and min_p > 0:
                out[name] = {
                    "min_price":    min_p,
                    "median_price": med_p or min_p,
                    "quantity":     qty,
                }
        return out
    except Exception as e:
        log.warning(f"Skinport fetch failed: {e}")
        return None


# ------------------------------------------------------------------ #
#  Paper portfolio                                                     #
# ------------------------------------------------------------------ #

class PaperPortfolio:
    def __init__(self, balance: float):
        self.balance = balance
        self.start   = balance
        self.positions: dict = {}
        self.trades:    list = []

    def buy(self, name, buy_price, list_price):
        self.balance -= buy_price
        self.positions[name] = {
            "buy_price":  buy_price,
            "list_price": list_price,
            "bought_at":  datetime.now(),
        }
        log.info(
            f"  BUY  {name:<48}  paid=${buy_price:.2f}"
            f"  list@${list_price:.2f}  bal=${self.balance:.2f}"
        )

    def try_sell(self, name, current_price, rng, max_hold_hours=48.0):
        if name not in self.positions:
            return
        pos    = self.positions[name]
        held_h = (datetime.now() - pos["bought_at"]).total_seconds() / 3600
        price_ok = current_price >= pos["list_price"]
        timed_out = held_h >= max_hold_hours

        if price_ok or timed_out:
            sell    = pos["list_price"] * rng.uniform(0.93, 1.0) if price_ok else current_price
            fee     = sell * CSFLOAT_FEE
            profit  = sell - fee - pos["buy_price"]
            self.balance += sell - fee
            self.trades.append(profit)
            tag = "TIMEOUT" if timed_out and not price_ok else "SELL "
            log.info(
                f"  {tag} {name:<48}  sold=${sell:.2f}"
                f"  profit=${profit:+.2f}  bal=${self.balance:.2f}"
            )
            del self.positions[name]

    def status(self):
        profits = self.trades
        wins    = sum(1 for p in profits if p > 0)
        log.info("─" * 70)
        log.info(
            f"  Balance : ${self.balance:.2f}"
            f"  (started ${self.start:.2f}  profit ${self.balance - self.start:+.2f})"
        )
        log.info(f"  Trades  : {len(profits)} closed  {len(self.positions)} open")
        if profits:
            log.info(
                f"  Win rate: {wins / len(profits) * 100:.0f}%"
                f"  avg ${statistics.mean(profits):.2f}/trade"
            )
        log.info("─" * 70)


# ------------------------------------------------------------------ #
#  Main loop                                                           #
# ------------------------------------------------------------------ #

def run(
    balance:          float = 200.0,
    buy_threshold:    float = 0.85,
    sell_target:      float = 0.95,
    reference_ticks:  int   = 12,    # polls needed to build reference (12 × 10min = 2hr window)
    poll_interval:    int   = 600,   # seconds between polls (10 min)
    competition_rate: float = 0.18,
    max_trades_per_poll: int = 2,
    min_quantity:     int   = 5,     # min active Skinport listings
    min_profit:       float = 0.50,
    max_spend:        float = 150.0,
):
    log.info("=" * 70)
    log.info("CSFloat Real-Time Paper Trader  (data: Skinport bulk API)")
    log.info(f"  buy_threshold  : {buy_threshold:.0%}")
    log.info(f"  sell_target    : {sell_target:.0%}")
    log.info(f"  poll_interval  : {poll_interval}s ({poll_interval//60} min)")
    log.info(f"  reference      : {reference_ticks} polls"
             f" ({reference_ticks * poll_interval // 60} min window)")
    log.info(f"  watching       : {len(WATCHLIST)} items")
    log.info("=" * 70)

    portfolio = PaperPortfolio(balance)
    rng       = random.Random()
    windows:  dict[str, list[float]] = defaultdict(list)
    poll = 0

    while True:
        poll += 1
        now = datetime.now().strftime("%H:%M:%S")
        log.info(f"\n[Poll #{poll} @ {now}] Fetching Skinport prices...")

        prices = fetch_all_prices()
        if prices is None:
            log.warning("  Failed to fetch — retrying next poll")
            time.sleep(poll_interval)
            continue

        log.info(f"  Got {len(prices)} items from Skinport")

        buys_this_poll = 0

        for name in WATCHLIST:
            data = prices.get(name)
            if not data:
                continue

            current_price = data["min_price"]
            quantity      = data["quantity"]

            # Update rolling window
            w = windows[name]
            w.append(current_price)
            if len(w) > reference_ticks:
                w.pop(0)

            # Try to exit any open position for this item
            portfolio.try_sell(name, current_price, rng)

            # Need reference_ticks polls before trading
            if len(w) < reference_ticks:
                continue

            ref_price = statistics.median(w)
            ratio     = current_price / ref_price

            if ratio > buy_threshold:
                continue
            if quantity < min_quantity:
                log.debug(f"  SKIP {name[:45]} — low qty ({quantity})")
                continue

            slippage   = rng.uniform(1.0, 1.04)
            buy_price  = current_price * slippage
            list_price = ref_price * sell_target * rng.uniform(0.93, 1.0)
            fee        = list_price * CSFLOAT_FEE
            expected   = list_price - fee - buy_price

            if expected < min_profit:
                continue
            if buy_price > max_spend:
                continue
            if buy_price > portfolio.balance:
                log.warning(f"  SKIP {name[:45]} — insufficient funds")
                continue
            if name in portfolio.positions:
                continue
            if buys_this_poll >= max_trades_per_poll:
                continue
            if rng.random() > competition_rate:
                log.info(f"  LOST {name[:45]}  ratio={ratio:.0%}  lost to competitor")
                continue

            portfolio.buy(name, round(buy_price, 2), round(list_price, 2))
            buys_this_poll += 1

        portfolio.status()
        log.info(f"Next poll in {poll_interval}s — Ctrl+C to stop")
        time.sleep(poll_interval)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--balance",  type=float, default=200.0)
    p.add_argument("--buy",      type=float, default=0.85)
    p.add_argument("--sell",     type=float, default=0.95)
    p.add_argument("--interval", type=int,   default=600,
                   help="Poll interval seconds (default 600 = 10min)")
    p.add_argument("--window",   type=int,   default=12,
                   help="Reference window in polls (default 12 = 2hr warmup)")
    args = p.parse_args()

    try:
        run(
            balance=args.balance,
            buy_threshold=args.buy,
            sell_target=args.sell,
            poll_interval=args.interval,
            reference_ticks=args.window,
        )
    except KeyboardInterrupt:
        log.info("Stopped.")
