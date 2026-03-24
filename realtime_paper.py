#!/usr/bin/env python3
import json, subprocess, time, random, logging, argparse, statistics, os
from datetime import datetime, timezone

try:
    import requests as _requests
except ImportError:
    _requests = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

SKINPORT_URL    = "https://api.skinport.com/v1/items"
CSFLOAT_FEE     = 0.02
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")

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


def fetch_all_prices():
    try:
        result = subprocess.run(
            ["curl", "-s", "--compressed",
             "-H", "Accept: application/json",
             "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64)",
             f"{SKINPORT_URL}?app_id=730&currency=USD&tradable=0"],
            capture_output=True, text=True, timeout=45)
        if result.returncode != 0:
            log.warning(f"curl failed: {result.stderr[:100]}")
            return None
        out = {}
        for item in json.loads(result.stdout):
            name = item.get("market_hash_name")
            min_p = item.get("min_price")
            qty = item.get("quantity") or 0
            if name and min_p and min_p > 0:
                out[name] = {"min_price": min_p,
                             "median_price": item.get("median_price") or min_p,
                             "quantity": qty}
        return out
    except Exception as e:
        log.warning(f"Skinport fetch failed: {e}")
        return None


def send_discord(portfolio, poll_num):
    if not DISCORD_WEBHOOK or not _requests:
        return
    profits = portfolio.trades
    wins = sum(1 for p in profits if p > 0)
    profit = portfolio.balance - portfolio.start
    color = 0x2ecc71 if profit >= 0 else 0xe74c3c
    win_str = (f"{wins/len(profits)*100:.0f}%  avg ${statistics.mean(profits):.2f}/trade"
               if profits else "warming up...")
    open_pos = "\n".join(
        f"• {n}  @${v['buy_price']:.2f}" for n, v in list(portfolio.positions.items())[:5]
    ) or "none"
    payload = {"embeds": [{
        "title": f"📊 CSFloat Sim — Poll #{poll_num}",
        "color": color,
        "fields": [
            {"name": "Balance",  "value": f"${portfolio.balance:.2f}",                              "inline": True},
            {"name": "Profit",   "value": f"${profit:+.2f}",                                        "inline": True},
            {"name": "Trades",   "value": f"{len(profits)} closed  {len(portfolio.positions)} open", "inline": True},
            {"name": "Win Rate", "value": win_str,                                                   "inline": False},
            {"name": "Open Positions", "value": open_pos,                                            "inline": False},
        ],
        "footer": {"text": "sim mode — no real money spent"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]}
    try:
        _requests.post(DISCORD_WEBHOOK, json=payload, timeout=5)
    except Exception as e:
        log.warning(f"Discord failed: {e}")


class PaperPortfolio:
    def __init__(self, balance):
        self.balance = balance
        self.start = balance
        self.positions = {}
        self.trades = []

    def buy(self, name, buy_price, list_price):
        self.balance -= buy_price
        self.positions[name] = {"buy_price": buy_price, "list_price": list_price, "bought_at": datetime.now()}
        log.info(f"  BUY  {name:<48}  paid=${buy_price:.2f}  list@${list_price:.2f}  bal=${self.balance:.2f}")

    def try_sell(self, name, current_price, rng, max_hold_hours=48.0):
        if name not in self.positions:
            return
        pos = self.positions[name]
        held_h = (datetime.now() - pos["bought_at"]).total_seconds() / 3600
        price_ok = current_price >= pos["list_price"]
        timed_out = held_h >= max_hold_hours
        if price_ok or timed_out:
            sell = pos["list_price"] * rng.uniform(0.93, 1.0) if price_ok else current_price
            fee = sell * CSFLOAT_FEE
            profit = sell - fee - pos["buy_price"]
            self.balance += sell - fee
            self.trades.append(profit)
            tag = "TIMEOUT" if timed_out and not price_ok else "SELL "
            log.info(f"  {tag} {name:<48}  sold=${sell:.2f}  profit=${profit:+.2f}  bal=${self.balance:.2f}")
            del self.positions[name]

    def status(self):
        profits = self.trades
        wins = sum(1 for p in profits if p > 0)
        log.info("─" * 70)
        log.info(f"  Balance : ${self.balance:.2f}  (started ${self.start:.2f}  profit ${self.balance - self.start:+.2f})")
        log.info(f"  Trades  : {len(profits)} closed  {len(self.positions)} open")
        if profits:
            log.info(f"  Win rate: {wins/len(profits)*100:.0f}%  avg ${statistics.mean(profits):.2f}/trade")
        log.info("─" * 70)


FETCH_INTERVAL = 3600  # re-fetch Skinport prices every hour
TRADE_INTERVAL = 10    # evaluate trades every 10 seconds
DISCORD_INTERVAL = 600 # status update every 10 minutes


def run(balance=200.0, buy_threshold=0.85, sell_target=0.95,
        competition_rate=0.18, min_quantity=5, min_profit=0.50):
    log.info("=" * 70)
    log.info("CSFloat Real-Time Paper Trader  (data: Skinport bulk API)")
    log.info(f"  buy_threshold  : {buy_threshold:.0%}")
    log.info(f"  sell_target    : {sell_target:.0%}")
    log.info(f"  trade interval : {TRADE_INTERVAL}s")
    log.info(f"  price refresh  : every {FETCH_INTERVAL//60} min")
    log.info(f"  reference      : Skinport median_price (no warmup)")
    log.info(f"  watching       : {len(WATCHLIST)} items")
    log.info("=" * 70)

    portfolio = PaperPortfolio(balance)
    rng = random.Random()
    prices = None
    tick = 0
    last_fetch = 0
    last_discord = 0
    daily_start_balance = balance
    today = datetime.now().date()

    while True:
        now_ts = time.time()

        # Reset daily starting balance at midnight
        if datetime.now().date() != today:
            today = datetime.now().date()
            daily_start_balance = portfolio.balance
            log.info(f"  New day — daily budget reset, start balance ${daily_start_balance:.2f}")

        max_spend = daily_start_balance * 0.10

        # Re-fetch Skinport prices once per hour
        if now_ts - last_fetch >= FETCH_INTERVAL or prices is None:
            log.info(f"\n[{datetime.now().strftime('%H:%M:%S')}] Refreshing Skinport prices...")
            fresh = fetch_all_prices()
            if fresh:
                prices = fresh
                last_fetch = now_ts
                log.info(f"  Got {len(prices)} items")
            else:
                log.warning("  Fetch failed — reusing cached prices")

        if prices is None:
            log.warning("No price data yet, retrying in 10s...")
            time.sleep(TRADE_INTERVAL)
            continue

        tick += 1
        buys_this_tick = 0

        for name in WATCHLIST:
            data = prices.get(name)
            if not data:
                continue
            current_price = data["min_price"]
            ref_price = data["median_price"]
            quantity = data["quantity"]

            portfolio.try_sell(name, current_price, rng)

            ratio = current_price / ref_price
            if ratio > buy_threshold:
                continue
            if quantity < min_quantity:
                continue
            slippage = rng.uniform(1.0, 1.04)
            buy_price = current_price * slippage
            list_price = ref_price * sell_target * rng.uniform(0.93, 1.0)
            fee = list_price * CSFLOAT_FEE
            expected = list_price - fee - buy_price
            if expected < min_profit:
                continue
            if buy_price > max_spend:
                continue
            if buy_price > portfolio.balance:
                continue
            if name in portfolio.positions:
                continue
            if rng.random() > competition_rate:
                continue
            portfolio.buy(name, round(buy_price, 2), round(list_price, 2))
            buys_this_tick += 1

        # Log status and send Discord every 10 minutes
        if now_ts - last_discord >= DISCORD_INTERVAL:
            portfolio.status()
            send_discord(portfolio, tick)
            last_discord = now_ts

        time.sleep(TRADE_INTERVAL)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--balance", type=float, default=200.0)
    p.add_argument("--buy",     type=float, default=0.85)
    p.add_argument("--sell",    type=float, default=0.95)
    args = p.parse_args()
    try:
        run(balance=args.balance, buy_threshold=args.buy, sell_target=args.sell)
    except KeyboardInterrupt:
        log.info("Stopped.")
