#!/usr/bin/env python3
import logging
import argparse
from config import CONFIG
from bot import FlipBot
from csfloat_client import CSFloatClient
import tracker


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_run(args):
    if args.live:
        CONFIG.dry_run = False
    bot = FlipBot(sim_mode=args.sim)
    bot.run()


def cmd_account(args):
    if not CONFIG.api_key:
        print("Error: API key required. Set CSF_API_KEY env var.")
        return
    client = CSFloatClient(CONFIG.api_key, CONFIG.max_retries)
    balance = client.get_balance()
    inventory = client.get_inventory()

    print(f"Balance   : ${balance:.2f}")
    print(f"Inventory : {len(inventory)} item(s)")
    if inventory:
        total_ref = sum(i.get("reference_price", 0) for i in inventory) / 100.0
        print(f"  Est. value : ${total_ref:.2f} (sum of reference prices)")
        print()
        for item in inventory:
            name  = item.get("market_hash_name") or item.get("item_name", "Unknown")
            ref   = item.get("reference_price", 0) / 100.0
            float_val = item.get("float_value")
            float_str = f"  float={float_val:.6f}" if float_val is not None else ""
            print(f"  {name:<55} ref=${ref:.2f}{float_str}")


def cmd_sim_summary(args):
    tracker.init_db()
    s = tracker.get_paper_summary()
    print(f"── Sim (Live Dry-Run) P&L ──────────────────")
    print(f"Closed (sold)   : {s['sold_count']} trades  profit ${s['realized_profit']:.2f}")
    print(f"Expired         : {s['expired_count']} trades  profit ${s['expired_profit']:.2f}")
    print(f"Open positions  : {s['open_count']}  (${s['open_invested']:.2f} would be tied up)")
    total = s['realized_profit'] + s['expired_profit']
    print(f"Total P&L       : ${total:.2f}")


def cmd_reset_budget(args):
    tracker.init_db()
    import sqlite3
    with sqlite3.connect(CONFIG.db_path) as db:
        db.execute("DELETE FROM daily_spend")
        db.execute("DELETE FROM paper_trades WHERE date(bought_at) = date('now')")
    print("Budget reset. Today's spend and paper trades cleared.")


def cmd_summary(args):
    tracker.init_db()
    s = tracker.get_pnl_summary()
    print(f"Total trades     : {s['total_trades']}")
    print(f"Realized profit  : ${s['realized_profit_usd']:.2f}")
    print(f"Pending trades   : {s['pending_count']} (${s['pending_invested_usd']:.2f} invested)")
    print(f"Daily spend today: ${tracker.get_daily_spend():.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CSFloat Flip Bot")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="Start the bot")
    p_run.add_argument("--live", action="store_true", help="Disable dry-run and actually trade")
    p_run.add_argument("--sim",  action="store_true", help="Live dry-run: real data, simulated trades tracked in DB")
    p_run.set_defaults(func=cmd_run)

    p_acc = sub.add_parser("account", help="Show CSFloat balance and inventory")
    p_acc.set_defaults(func=cmd_account)

    p_sum = sub.add_parser("summary", help="Show P&L summary")
    p_sum.set_defaults(func=cmd_summary)

    p_sim = sub.add_parser("sim-summary", help="Show simulated (live dry-run) P&L")
    p_sim.set_defaults(func=cmd_sim_summary)

    p_rb = sub.add_parser("reset-budget", help="Clear today's spend and open sim positions")
    p_rb.set_defaults(func=cmd_reset_budget)

    args = parser.parse_args()
    setup_logging(CONFIG.log_level)

    if not args.cmd:
        parser.print_help()
    else:
        args.func(args)
