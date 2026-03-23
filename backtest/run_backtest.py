#!/usr/bin/env python3
"""
Run the backtest. Usage:

  # Default settings (200 items, all categories)
  python backtest/run_backtest.py

  # Tune thresholds
  python backtest/run_backtest.py --buy 0.80 --sell 0.93 --items 500

  # Only knives / specific skins
  python backtest/run_backtest.py --filter "Butterfly Knife" "Karambit"

  # Sweep multiple buy thresholds to find the best one
  python backtest/run_backtest.py --sweep
"""

import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.download_data import download
from backtest.backtester import BacktestConfig, run_backtest, print_results

logging.basicConfig(level=logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description="CSFloat flip strategy backtester")
    parser.add_argument("--buy",    type=float, default=0.85, help="Buy threshold (default 0.85)")
    parser.add_argument("--sell",   type=float, default=0.95, help="Sell target (default 0.95)")
    parser.add_argument("--fee",    type=float, default=0.02, help="CSFloat fee (default 0.02)")
    parser.add_argument("--min-profit", type=float, default=0.50, help="Min profit per trade USD")
    parser.add_argument("--max-item",   type=float, default=50.0, help="Max spend per item USD")
    parser.add_argument("--min-sell", type=float, default=5.0,  help="Min hours before sale (default 5)")
    parser.add_argument("--max-sell", type=float, default=10.0, help="Max hours before forced exit (default 10)")
    parser.add_argument("--window", type=int,   default=30,   help="Rolling reference price window (days)")
    parser.add_argument("--items",  type=int,   default=200,  help="Number of items to backtest")
    parser.add_argument("--days",   type=int,   default=30,   help="How many days to simulate (default 30)")
    parser.add_argument("--max-trades-day", type=int, default=3, help="Max buys per day to simulate competition (default 3)")
    parser.add_argument("--filter", nargs="*",  default=[],   help="Whitelist skin name keywords")
    parser.add_argument("--live",   action="store_true",      help="Use live Steam Market data (Jan 2026)")
    parser.add_argument("--sweep",  action="store_true",      help="Sweep buy thresholds 0.70-0.90")
    args = parser.parse_args()

    # Ensure data is downloaded
    download()

    if args.sweep:
        _sweep(args)
        return

    cfg = BacktestConfig(
        buy_threshold=args.buy,
        sell_target=args.sell,
        csfloat_fee=args.fee,
        min_profit_usd=args.min_profit,
        max_spend_per_item_usd=args.max_item,
        min_sell_hours=args.min_sell,
        max_sell_hours=args.max_sell,
        reference_window_days=args.window,
        whitelist_names=args.filter or [],
        backtest_days=args.days,
        max_trades_per_day=args.max_trades_day,
    )

    print(f"\nConfig: buy<={cfg.buy_threshold:.0%} ref | sell@{cfg.sell_target:.0%} ref | "
          f"sell window={cfg.min_sell_hours:.0f}-{cfg.max_sell_hours:.0f}h | fee={cfg.csfloat_fee:.0%} | "
          f"{cfg.backtest_days}d sim | max {cfg.max_trades_per_day} buys/day")

    results = run_backtest(cfg, max_items=args.items, live=args.live)
    print_results(results)


def _sweep(args):
    """Try multiple buy thresholds and print a comparison table."""
    thresholds = [0.70, 0.75, 0.80, 0.83, 0.85, 0.87, 0.90]
    print(f"\n{'Threshold':>10} {'Trades':>8} {'Win%':>7} {'TotalProfit':>13} {'Avg/Trade':>11} {'ForcedExit%':>12}")
    print("-" * 65)

    for t in thresholds:
        cfg = BacktestConfig(
            buy_threshold=t,
            sell_target=args.sell,
            csfloat_fee=args.fee,
            min_profit_usd=args.min_profit,
            max_spend_per_item_usd=args.max_item,
            min_sell_hours=args.min_sell,
            max_sell_hours=args.max_sell,
            reference_window_days=args.window,
            whitelist_names=args.filter or [],
            backtest_days=args.days,
            max_trades_per_day=args.max_trades_day,
        )
        r = run_backtest(cfg, max_items=args.items, live=args.live)
        if "error" in r:
            print(f"{t:>10.0%}  {'no trades':>50}")
            continue
        print(
            f"{t:>10.0%} "
            f"{r['total_trades']:>8} "
            f"{r['win_rate']:>7} "
            f"${r['total_profit_usd']:>12.2f} "
            f"${r['avg_profit_per_trade']:>10.4f} "
            f"{r['forced_exit_rate']:>12}"
        )


if __name__ == "__main__":
    main()
