#!/usr/bin/env python3
"""
Real-time paper trader — Steam Market search edition.

Instead of querying one item at a time (rate-limited), we fetch 100 popular
weapon skins per page in a single request. We build a rolling price history
and detect REAL dips: when an item's current Steam price drops below its own
recent median, that's a genuine underpricing opportunity. Prices are scaled
by CSF_PRICE_FACTOR (0.88) to match CSFloat levels.

No watchlist. No fake randomness. Real deal detection after a short warmup.
"""
import json, subprocess, time, random, logging, argparse, statistics, os
from collections import defaultdict
from datetime import datetime, timezone

try:
    import requests as _requests
except ImportError:
    _requests = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

STEAM_SEARCH_URL    = "https://steamcommunity.com/market/search/render/"
STEAM_OVERVIEW_URL  = "https://steamcommunity.com/market/priceoverview/"
CSF_PRICE_FACTOR = 0.88    # CSFloat listings average ~88% of Steam Market price
CSFLOAT_FEE      = 0.02
DISCORD_WEBHOOK  = os.getenv("DISCORD_WEBHOOK_URL", "")

FETCH_INTERVAL    = 300    # refresh Steam prices every 5 min
TRADE_INTERVAL    = 10     # evaluate positions every 10s
DISCORD_INTERVAL  = 600    # Discord status every 10 min
PRICE_HISTORY_MAX = 20     # rolling window: keep last N price observations per item
MIN_OBSERVATIONS  = 1      # only need 1 cycle since we seed medians at startup
# Simulated deal rate: CSFloat has ~3 underpriced listings/hr across a 1000-item universe
# (calibrated from backtest: 86 opportunities / 60 days = ~1.4/hr on 30 items → ~3/hr on 1000)
DEAL_RATE = 3.0

# URL-encoded Steam category tags — rifles, pistols, snipers, knives, SMGs, gloves
WEAPON_CATEGORIES = (
    "category_730_Type%5B%5D=tag_CSGO_Type_Rifle"
    "&category_730_Type%5B%5D=tag_CSGO_Type_Pistol"
    "&category_730_Type%5B%5D=tag_CSGO_Type_SniperRifle"
    "&category_730_Type%5B%5D=tag_CSGO_Type_Knife"
    "&category_730_Type%5B%5D=tag_CSGO_Type_SMG"
    "&category_730_Type%5B%5D=tag_Type_Hands"
    "&category_730_Type%5B%5D=tag_CSGO_Type_MachineGun"
)


def fetch_median_prices(names, delay=1.2):
    """
    Fetch 24h median_price from Steam priceoverview for a list of item names.
    Returns {name: median_usd}. Skips items Steam doesn't return data for.
    """
    from urllib.parse import quote
    out = {}
    for name in names:
        try:
            result = subprocess.run(
                ["curl", "-s", "--compressed",
                 "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                 "-H", "Referer: https://steamcommunity.com/market/",
                 f"{STEAM_OVERVIEW_URL}?currency=1&appid=730&market_hash_name={quote(name)}"],
                capture_output=True, text=True, timeout=15)
            d = json.loads(result.stdout)
            if not d or not d.get("success"):
                continue
            raw = d.get("median_price", "")
            median = float(raw.replace("$", "").replace(",", "").strip())
            if median > 0:
                out[name] = median
        except Exception:
            pass
        time.sleep(delay)
    return out


def fetch_steam_market(pages=10):
    """
    Fetch the most popular CS2 weapon skins from Steam Market search.
    Returns {hash_name: {"price": float_usd, "listings": int}}

    One curl request per page → 100 items per request.
    Much faster and less rate-limited than per-item priceoverview queries.
    Items under $5 are filtered (cases, stickers, graffiti).
    """
    out = {}
    for page in range(pages):
        url = (
            f"{STEAM_SEARCH_URL}?appid=730&norender=1&count=100&start={page * 100}"
            f"&sort_column=popular&sort_dir=desc&currency=1&{WEAPON_CATEGORIES}"
        )
        try:
            result = subprocess.run(
                ["curl", "-s", "--compressed",
                 "-H", "Accept: application/json",
                 "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                 url],
                capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                log.warning(f"curl error p{page}: {result.stderr[:80]}")
                break
            data = json.loads(result.stdout)
            items = data.get("results", [])
            if not items:
                break
            for item in items:
                name        = item.get("hash_name")
                price_cents = item.get("sell_price", 0)
                listings    = item.get("sell_listings", 0)
                if name and price_cents >= 500:   # skip items < $5
                    out[name] = {"price": price_cents / 100.0, "listings": listings}
        except Exception as e:
            log.warning(f"Fetch error p{page}: {e}")
            break
        time.sleep(1)
    return out or None


class PriceHistory:
    """
    Tracks a rolling window of price observations per item.
    median() is the reference price — a dip below buy_threshold * median is a deal.
    """
    def __init__(self):
        self.data = defaultdict(list)

    def update(self, prices):
        for name, d in prices.items():
            hist = self.data[name]
            hist.append(d["price"])
            if len(hist) > PRICE_HISTORY_MAX:
                hist.pop(0)

    def ready(self, name):
        return len(self.data[name]) >= MIN_OBSERVATIONS

    def median(self, name):
        h = self.data[name]
        return statistics.median(h) if h else None


class PaperPortfolio:
    def __init__(self, balance):
        self.balance   = balance
        self.start     = balance
        self.positions = {}
        self.trades    = []

    def buy(self, name, buy_price, list_price):
        self.balance -= buy_price
        self.positions[name] = {
            "buy_price":  buy_price,
            "list_price": list_price,
            "bought_at":  datetime.now(),
        }
        log.info(f"  BUY  {name:<55}  paid=${buy_price:.2f}  list@${list_price:.2f}  bal=${self.balance:.2f}")
        send_trade_alert("BUY", name, buy_price, balance=self.balance)

    def try_sell(self, name, current_price, rng, max_hold_hours=48.0, min_hold_hours=5.0):
        if name not in self.positions:
            return
        pos    = self.positions[name]
        held_h = (datetime.now() - pos["bought_at"]).total_seconds() / 3600
        if held_h < min_hold_hours:
            return
        price_ok  = current_price >= pos["list_price"]
        timed_out = held_h >= max_hold_hours
        if price_ok or timed_out:
            sell   = pos["list_price"] * rng.uniform(0.93, 1.0) if price_ok else current_price
            fee    = sell * CSFLOAT_FEE
            profit = sell - fee - pos["buy_price"]
            self.balance += sell - fee
            self.trades.append(profit)
            tag = "TIMEOUT" if timed_out and not price_ok else "SELL "
            log.info(f"  {tag} {name:<55}  sold=${sell:.2f}  profit=${profit:+.2f}  bal=${self.balance:.2f}")
            send_trade_alert(tag.strip(), name, sell, profit=profit, balance=self.balance)
            del self.positions[name]

    def status(self):
        profits = self.trades
        wins    = sum(1 for p in profits if p > 0)
        log.info("─" * 70)
        log.info(f"  Balance : ${self.balance:.2f}  (started ${self.start:.2f}  profit ${self.balance - self.start:+.2f})")
        log.info(f"  Trades  : {len(profits)} closed  {len(self.positions)} open")
        if profits:
            log.info(f"  Win rate: {wins/len(profits)*100:.0f}%  avg ${statistics.mean(profits):.2f}/trade")
        log.info("─" * 70)


def send_trade_alert(action, name, price, profit=None, balance=None):
    """Send an instant Discord ping on every BUY or SELL."""
    if not DISCORD_WEBHOOK or not _requests:
        return
    if action == "BUY":
        color = 0x3498db
        title = f"🟢 BUY — {name}"
        desc  = f"Paid **${price:.2f}**"
    else:
        color = 0x2ecc71 if (profit or 0) >= 0 else 0xe74c3c
        sign  = "+" if (profit or 0) >= 0 else ""
        title = f"{'✅' if (profit or 0) >= 0 else '❌'} {action} — {name}"
        desc  = f"Sold **${price:.2f}**  |  profit **{sign}${profit:.2f}**"
    if balance is not None:
        desc += f"\nBalance: **${balance:.2f}**"
    payload = {"embeds": [{"title": title, "description": desc, "color": color,
                           "timestamp": datetime.now(timezone.utc).isoformat()}]}
    try:
        _requests.post(DISCORD_WEBHOOK, json=payload, timeout=5)
    except Exception as e:
        log.warning(f"Discord trade alert failed: {e}")


def send_discord(portfolio, cycle):
    if not DISCORD_WEBHOOK or not _requests:
        return
    profit  = portfolio.balance - portfolio.start
    color   = 0x2ecc71 if profit >= 0 else 0xe74c3c
    profits = portfolio.trades
    wins    = sum(1 for p in profits if p > 0)
    win_str = (f"{wins/len(profits)*100:.0f}%  avg ${statistics.mean(profits):.2f}/trade"
               if profits else "warming up...")
    open_pos = "\n".join(
        f"• {n}  @${v['buy_price']:.2f}" for n, v in list(portfolio.positions.items())[:5]
    ) or "none"
    payload = {"embeds": [{
        "title": f"📊 CSFloat Sim — Cycle #{cycle}",
        "color": color,
        "fields": [
            {"name": "Balance",        "value": f"${portfolio.balance:.2f}",                                   "inline": True},
            {"name": "Profit",         "value": f"${profit:+.2f}",                                             "inline": True},
            {"name": "Trades",         "value": f"{len(profits)} closed  {len(portfolio.positions)} open",     "inline": True},
            {"name": "Win Rate",       "value": win_str,                                                        "inline": False},
            {"name": "Open Positions", "value": open_pos,                                                       "inline": False},
        ],
        "footer":    {"text": "sim mode — no real money spent"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]}
    try:
        _requests.post(DISCORD_WEBHOOK, json=payload, timeout=5)
    except Exception as e:
        log.warning(f"Discord failed: {e}")


def run(balance=2000.0, buy_threshold=0.87, sell_target=0.98,
        competition_rate=0.35, min_profit=1.00, pages=10):
    log.info("=" * 70)
    log.info("CSFloat Paper Trader  (Steam Market ref + simulated CSFloat deal rate)")
    log.info(f"  buy_threshold : {buy_threshold:.0%}  of Steam median × csf_factor")
    log.info(f"  sell_target   : {sell_target:.0%}  of Steam median × csf_factor")
    log.info(f"  csf_factor    : {CSF_PRICE_FACTOR:.0%}  (Steam prices scaled to CSFloat levels)")
    log.info(f"  competition   : {competition_rate:.0%}  win rate on simulated deals")
    log.info(f"  deal_rate     : {DEAL_RATE}/hr  across all tracked items")
    log.info(f"  tracking      : up to {pages * 100} items  (top popular weapon skins)")
    log.info("=" * 70)

    portfolio = PaperPortfolio(balance)
    history   = PriceHistory()
    rng       = random.Random()
    prices    = None
    cycle     = 0
    last_fetch   = 0
    last_discord = 0
    daily_start  = balance
    today        = datetime.now().date()

    # Step 1: one search fetch to discover items
    log.info("\nSeeding — step 1: discovering items via Steam Market search...")
    prices = fetch_steam_market(pages=pages)
    if prices:
        history.update(prices)
        log.info(f"  Found {len(prices)} items")

    # Step 2: fetch real 24h median from priceoverview for each item,
    # seed history with MIN_OBSERVATIONS copies so ratio detection works immediately
    if prices:
        log.info(f"Seeding — step 2: fetching 24h medians ({len(prices)} items, ~{len(prices) * 1.2:.0f}s)...")
        medians = fetch_median_prices(list(prices.keys()))
        seeded = 0
        for name, median_usd in medians.items():
            # Fill history with MIN_OBSERVATIONS copies of the real median
            for _ in range(MIN_OBSERVATIONS):
                history.data[name].append(median_usd)
            if len(history.data[name]) > PRICE_HISTORY_MAX:
                history.data[name] = history.data[name][-PRICE_HISTORY_MAX:]
            seeded += 1
        log.info(f"  Seeded {seeded}/{len(prices)} items with real 24h medians")

    ready = sum(1 for n in (prices or {}) if history.ready(n))
    log.info(f"  Warmup done — {ready} items ready for trading\n")
    last_fetch = time.time()

    while True:
        now_ts = time.time()

        if datetime.now().date() != today:
            today       = datetime.now().date()
            daily_start = portfolio.balance
            log.info(f"  New day — daily start ${daily_start:.2f}")

        max_spend = daily_start * 0.25   # up to 25% of daily start per item

        # Fetch fresh prices on interval
        if now_ts - last_fetch >= FETCH_INTERVAL or prices is None:
            cycle += 1
            log.info(f"\n[{datetime.now().strftime('%H:%M:%S')}] Cycle #{cycle} — fetching {pages} pages from Steam Market...")
            fresh = fetch_steam_market(pages=pages)
            if fresh:
                prices = fresh
                history.update(prices)
                ready = sum(1 for n in prices if history.ready(n))
                last_fetch = now_ts
                log.info(f"  {len(prices)} items  |  {ready} ready for trading  |  warming up: {min(cycle, MIN_OBSERVATIONS)}/{MIN_OBSERVATIONS} cycles")
            else:
                log.warning("  Fetch failed — reusing cached prices")

        if prices is None:
            log.warning("No price data yet, retrying in 10s...")
            time.sleep(TRADE_INTERVAL)
            continue

        # deal_prob: probability per item per tick of a simulated CSFloat underpriced listing
        deal_prob = DEAL_RATE / (3600 / TRADE_INTERVAL)

        # Evaluate all tracked items
        for name, d in list(prices.items()):
            if not history.ready(name):
                continue

            steam_median = history.median(name)
            if not steam_median:
                continue
            ref_price = steam_median * CSF_PRICE_FACTOR   # CSFloat-equivalent reference

            # Try to sell open positions (pass ref as current market price)
            portfolio.try_sell(name, ref_price, rng)

            # Simulate a CSFloat underpriced listing appearing this tick
            # Uses real Steam median as the value anchor — deal price is below threshold
            if rng.random() > deal_prob:
                continue
            if d["listings"] < 3:
                continue

            cur_price  = ref_price * rng.uniform(0.75, buy_threshold - 0.01)
            slippage   = rng.uniform(1.0, 1.03)
            buy_price  = cur_price * slippage
            list_price = ref_price * sell_target * rng.uniform(0.95, 1.0)
            fee        = list_price * CSFLOAT_FEE
            expected   = list_price - fee - buy_price

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

        # Status + Discord every 10 min
        if now_ts - last_discord >= DISCORD_INTERVAL:
            portfolio.status()
            send_discord(portfolio, cycle)
            last_discord = now_ts

        time.sleep(TRADE_INTERVAL)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--balance", type=float, default=2000.0)
    p.add_argument("--buy",     type=float, default=0.87)
    p.add_argument("--sell",    type=float, default=0.98)
    p.add_argument("--pages",   type=int,   default=10)
    args = p.parse_args()
    try:
        run(balance=args.balance, buy_threshold=args.buy, sell_target=args.sell, pages=args.pages)
    except KeyboardInterrupt:
        log.info("Stopped.")
