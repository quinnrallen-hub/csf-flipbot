"""
Skinport bulk price feed as a drop-in listing source for sim mode.
Used when no CSFloat API key is available.

Skinport returns aggregate prices (min/median) for all CS2 items in one request.
We convert each item into a synthetic CSFloat-format listing dict so the normal
pricer and tracker pipeline works unchanged.

Limitations vs real CSFloat data:
  - No individual float values (uses 0.5 as placeholder)
  - No sticker data
  - Prices are market aggregates, not individual listings
"""

import json
import logging
import random
import subprocess
import time
from datetime import datetime

log = logging.getLogger(__name__)

SKINPORT_URL = "https://api.skinport.com/v1/items?app_id=730&currency=USD&tradable=0"
_cache: dict = {}          # {name: {min_price, median_price, quantity}}
_cache_ts: float = 0.0
CACHE_TTL = 600            # refresh at most every 10 minutes


def _fetch() -> dict[str, dict] | None:
    try:
        result = subprocess.run(
            ["curl", "-s", "--compressed",
             "-H", "Accept: application/json",
             "-H", "User-Agent: Mozilla/5.0",
             SKINPORT_URL],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode != 0:
            log.warning(f"Skinport fetch failed: {result.stderr[:100]}")
            return None
        items = json.loads(result.stdout)
        out = {}
        for item in items:
            name  = item.get("market_hash_name")
            min_p = item.get("min_price")
            med_p = item.get("median_price") or min_p
            qty   = item.get("quantity") or 0
            if name and min_p and min_p > 0:
                out[name] = {"min_price": min_p, "median_price": med_p, "quantity": qty}
        return out
    except Exception as e:
        log.warning(f"Skinport fetch error: {e}")
        return None


def get_listings(min_quantity: int = 3) -> list[dict]:
    """
    Return a list of synthetic CSFloat-format listing dicts sourced from Skinport.
    Each item with enough liquidity becomes one synthetic listing.
    """
    global _cache, _cache_ts

    now = time.time()
    if now - _cache_ts > CACHE_TTL or not _cache:
        log.info("Fetching Skinport prices...")
        fresh = _fetch()
        if fresh:
            _cache = fresh
            _cache_ts = now
            log.info(f"  Got {len(_cache)} items from Skinport")
        elif not _cache:
            log.warning("Skinport unavailable and no cached data")
            return []

    listings = []
    for name, data in _cache.items():
        if data["quantity"] < min_quantity:
            continue
        ask_cents = int(data["min_price"] * 100)
        ref_cents = int(data["median_price"] * 100)
        # ID is stable per item+price — only re-evaluates when the price changes
        listings.append({
            "id":              f"sp_{name}_{ask_cents}",
            "price":           ask_cents,
            "reference_price": ref_cents,
            "item": {
                "market_hash_name": name,
                "float_value":      0.5,   # unknown — placeholder
                "stickers":         [],
            },
        })
    # Sort by ask/ref ratio ascending — best discounts first
    listings.sort(key=lambda l: l["price"] / l["reference_price"] if l["reference_price"] else 1)
    return listings
