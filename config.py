import os
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Config:
    # --- Auth ---
    api_key: str = os.getenv("CSF_API_KEY", "")

    # --- Buy thresholds ---
    # Buy if listing price <= reference_price * buy_threshold
    buy_threshold: float = 0.87          # buy at 87% or below — matches backtested optimum
    min_profit_usd: float = 3.00         # minimum profit in USD after fees
    csfloat_fee: float = 0.02            # CSFloat takes 2% on sale

    # --- Sell settings ---
    # List at this fraction of reference price
    sell_target: float = 0.98            # list at 98% of reference

    # --- Budget limits ---
    daily_budget_usd: Optional[float] = 1000.0        # $1000 daily budget
    max_spend_per_item_usd: Optional[float] = 100.0   # max 10% of budget per item

    # --- Diversification ---
    # Max open positions with the same item name at once.
    max_per_item_name: int = 2
    # Max total buys per weapon type (e.g. "AK-47", "AWP") per session.
    # Keeps the bot from going all-in on one weapon family.
    max_daily_per_weapon: int = 3

    # --- Item filters ---
    # Leave empty to allow all. Example: ["AK-47", "AWP", "Butterfly Knife"]
    whitelist_names: list = field(default_factory=list)
    # Skip these items entirely
    blacklist_names: list = field(default_factory=list)
    # Max float value to consider (0.0 = FN, 1.0 = BS)
    max_float: float = 1.0
    min_float: float = 0.0
    # Only consider items above this reference price (USD)
    min_reference_price_usd: float = 1.00

    # --- Sticker pricing ---
    # Stickers contribute a tiered fraction of their CSFloat reference price.
    # Tiers are checked in order; the first matching ceiling is used.
    # Any sticker below sticker_min_value_usd contributes $0 (ignored entirely).
    #
    # Real-world recovery rates for a 5-10 day flip window:
    #   < $1.00  -> 0%   (no buyer pays extra for cheap stickers)
    #   $1-$5    -> 7%   (budget crafts, nearly zero resale premium)
    #   $5-$30   -> 12%  (mid-tier stickers, modest premium)
    #   $30-$100 -> 18%  (recognizable stickers, Cologne/Katowice lower-tier holos)
    #   $100+    -> 25%  (iconic stickers: Kato 2014, iBP, Crown foil, etc.)
    #
    # Set sticker_min_value_usd=0.0 and all tiers to the same value to restore
    # the old flat-rate behaviour.
    sticker_min_value_usd: float = 1.00   # stickers below this are ignored ($0)
    sticker_tiers: list = field(default_factory=lambda: [
        # (price_ceiling_usd, discount_rate)
        # Evaluated top-to-bottom; first ceiling >= sticker price wins.
        (5.00,   0.07),
        (30.00,  0.12),
        (100.00, 0.18),
        (200.00, 0.25),
        (float("inf"), 0.50),
    ])

    # --- Float premium ---
    # Apply a bonus multiplier to the reference price for items with a very
    # low float (collector-grade). Set low_float_threshold=0.0 to disable.
    # Example: threshold=0.01, mult=1.15 → FN skins under 0.01 float get
    # their reference price boosted by 15% before evaluation.
    low_float_threshold: float = 0.0   # 0.0 = disabled
    low_float_mult: float = 1.15       # multiplier applied when below threshold

    # --- Sell timing assumptions (used for profitability estimates) ---
    min_sell_hours: float = 5.0   # optimistic sell time
    max_sell_hours: float = 10.0  # pessimistic sell time (used for expected P&L calc)

    # --- Polling ---
    poll_interval_seconds: float = 10.0  # how often to check new listings
    max_retries: int = 5

    # --- Discord ---
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")

    # --- Misc ---
    dry_run: bool = True  # set False to actually buy/list
    db_path: str = "flipbot.db"
    log_level: str = "INFO"

CONFIG = Config()
