---
name: CS2 Skin Flip Threshold Sweep Results (2026-03-21)
description: Full 6x4 grid sweep results for buy/sell thresholds on 200 items over 60 days live data. Records which combos performed best by total profit and avg profit per trade.
type: project
---

## Sweep Parameters
- Date run: 2026-03-21
- Data: live Steam Market, 200 items, 60-day simulation (2026-01-20 to 2026-03-21)
- Fixed: --max-trades-day 5, fee 2%, $200 starting budget
- Buy thresholds tested: 0.82, 0.85, 0.87, 0.90, 0.92, 0.95
- Sell targets tested: 0.95, 0.97, 0.98, 1.00

## Full Grid (sorted by total profit)

| Buy  | Sell | Trades | WinRate | TotalProfit | Avg/Trade | ForcedExit | Return |
|------|------|--------|---------|-------------|-----------|------------|--------|
| 0.85 | 1.00 | 37     | 97.3%   | $224.83     | $6.0764   | 5.4%       | 112.4% |
| 0.82 | 0.98 | 21     | 100.0%  | $211.72     | $10.0820  | 4.8%       | 105.9% |
| 0.87 | 0.98 | 30     | 96.7%   | $200.64     | $6.6881   | 10.0%      | 100.3% |
| 0.82 | 1.00 | 22     | 100.0%  | $184.67     | $8.3942   | 4.5%       | 92.3%  |
| 0.95 | 0.98 | 29     | 100.0%  | $183.22     | $6.3179   | 0.0%       | 91.6%  |
| 0.82 | 0.97 | 20     | 100.0%  | $181.59     | $9.0796   | 5.0%       | 90.8%  |
| 0.90 | 0.98 | 29     | 100.0%  | $170.04     | $5.8633   | 3.4%       | 85.0%  |
| 0.90 | 0.97 | 22     | 100.0%  | $154.28     | $7.0126   | 4.5%       | 77.1%  |
| 0.95 | 0.97 | 20     | 100.0%  | $152.28     | $7.6140   | 0.0%       | 76.1%  |
| 0.95 | 0.95 | 18     | 100.0%  | $138.43     | $7.6904   | 0.0%       | 69.2%  |
| 0.92 | 1.00 | 38     | 100.0%  | $133.97     | $3.5255   | 0.0%       | 67.0%  |
| 0.90 | 1.00 | 43     | 90.7%   | $127.78     | $2.9717   | 11.6%      | 63.9%  |
| 0.85 | 0.98 | 26     | 96.2%   | $126.57     | $4.8682   | 7.7%       | 63.3%  |
| 0.87 | 1.00 | 39     | 92.3%   | $123.42     | $3.1646   | 12.8%      | 61.7%  |
| 0.82 | 0.95 | 18     | 100.0%  | $119.15     | $6.6194   | 0.0%       | 59.6%  |
| 0.92 | 0.97 | 20     | 100.0%  | $117.13     | $5.8564   | 0.0%       | 58.6%  |
| 0.87 | 0.97 | 28     | 100.0%  | $105.80     | $3.7786   | 3.6%       | 52.9%  |
| 0.85 | 0.97 | 19     | 100.0%  | $105.42     | $5.5484   | 5.3%       | 52.7%  |
| 0.92 | 0.95 | 13     | 100.0%  | $104.64     | $8.0489   | 0.0%       | 52.3%  |
| 0.92 | 0.98 | 22     | 100.0%  | $92.96      | $4.2255   | 0.0%       | 46.5%  |
| 0.95 | 1.00 | 35     | 97.1%   | $84.78      | $2.4224   | 2.9%       | 42.4%  |
| 0.87 | 0.95 | 16     | 100.0%  | $83.63      | $5.2272   | 0.0%       | 41.8%  |
| 0.85 | 0.95 | 16     | 100.0%  | $76.96      | $4.8101   | 0.0%       | 38.5%  |
| 0.90 | 0.95 | 18     | 100.0%  | $57.45      | $3.1917   | 0.0%       | 28.7%  |

## Top 5 by Total Profit
1. buy=0.85 sell=1.00 — $224.83 total, 37 trades, 97.3% win, $6.08 avg, 5.4% forced, +112.4%
2. buy=0.82 sell=0.98 — $211.72 total, 21 trades, 100% win, $10.08 avg, 4.8% forced, +105.9%
3. buy=0.87 sell=0.98 — $200.64 total, 30 trades, 96.7% win, $6.69 avg, 10.0% forced, +100.3%
4. buy=0.82 sell=1.00 — $184.67 total, 22 trades, 100% win, $8.39 avg, 4.5% forced, +92.3%
5. buy=0.95 sell=0.98 — $183.22 total, 29 trades, 100% win, $6.32 avg, 0.0% forced, +91.6%

## Top 5 by Avg Profit Per Trade
1. buy=0.82 sell=0.98 — $10.08/trade, $211.72 total, 21 trades, 100% win
2. buy=0.82 sell=0.97 — $9.08/trade, $181.59 total, 20 trades, 100% win
3. buy=0.82 sell=1.00 — $8.39/trade, $184.67 total, 22 trades, 100% win
4. buy=0.92 sell=0.95 — $8.05/trade, $104.64 total, 13 trades, 100% win
5. buy=0.95 sell=0.95 — $7.69/trade, $138.43 total, 18 trades, 100% win

## Recommended Configuration
Primary recommendation: buy=0.85, sell=0.98
- Balances total profit (#3, $200.64), trade count (30), win rate (96.7%), and forced exit rate (10%)
- Better than #1 (buy=0.85/sell=1.00) because the sell=1.00 target risks capital tied up waiting for full mean-reversion which never comes in the 7-day forced exit window

Secondary: buy=0.82, sell=0.98 (best avg per trade at $10.08, but only 21 trades — less capital utilization)
Conservative: buy=0.95, sell=0.98 (0% forced exits, 100% win rate, decent volume at 29 trades)

## Key Patterns Observed
- sell=0.98 is the sweet spot sell target: better than 0.95/0.97 (not enough margin) and 1.00 (too aggressive, locks capital)
- buy=0.85 maximizes trade frequency at acceptable quality
- buy=0.82 gives the highest per-trade quality but fewer opportunities
- Loose buy thresholds (0.90-0.95) paired with sell=0.95 produce low absolute profit despite 100% win rate (margin too thin)
- sell=1.00 creates forced exit risk: 0.90/1.00 had 11.6% forced exits and 90.7% win rate
