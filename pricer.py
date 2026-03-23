"""
Pricing logic: decide whether a listing is worth buying and at what price to relist.
CSFloat listings include a `reference_price` field (in cents) which is their
internally computed market value — we use that as our baseline.

Adjustments applied on top of reference_price:
  - Sticker value: each sticker's reference_price * sticker_discount added to ref
  - Low-float premium: if float < low_float_threshold, ref *= low_float_mult
"""

import logging
from dataclasses import dataclass
from config import CONFIG

log = logging.getLogger(__name__)


@dataclass
class Decision:
    should_buy: bool
    listing_id: str
    item_name: str
    ask_price_usd: float
    reference_price_usd: float      # raw CSFloat reference
    adjusted_ref_usd: float         # after sticker + float adjustments
    target_sell_usd: float
    expected_profit_usd: float
    sticker_value_usd: float        # total sticker contribution
    float_value: float
    reason: str


def _sticker_value(item: dict) -> float:
    """
    Sum the discounted sticker values from an item dict.
    Each sticker may have a 'reference_price' in cents.
    Returns USD value to add to the reference price.
    """
    if CONFIG.sticker_discount <= 0:
        return 0.0
    stickers = item.get("stickers") or []
    total = 0.0
    for s in stickers:
        ref_cents = s.get("reference_price") or 0
        if ref_cents > 0:
            total += (ref_cents / 100.0) * CONFIG.sticker_discount
    return round(total, 4)


def _apply_float_premium(ref_usd: float, float_val: float) -> float:
    """
    If float is below the low_float_threshold, multiply ref by low_float_mult.
    """
    if CONFIG.low_float_threshold > 0 and float_val < CONFIG.low_float_threshold:
        return ref_usd * CONFIG.low_float_mult
    return ref_usd


def evaluate_listing(listing: dict) -> Decision:
    """
    Returns a Decision for a single CSFloat listing dict.
    """
    listing_id = listing.get("id", "")
    item       = listing.get("item", {})
    item_name  = item.get("market_hash_name", "Unknown")
    ask_cents  = listing.get("price", 0)
    ref_cents  = listing.get("reference_price", 0)
    float_val  = item.get("float_value") or 0.0

    ask_usd = ask_cents / 100.0
    ref_usd = ref_cents / 100.0

    def reject(reason: str) -> Decision:
        return Decision(
            should_buy=False,
            listing_id=listing_id,
            item_name=item_name,
            ask_price_usd=ask_usd,
            reference_price_usd=ref_usd,
            adjusted_ref_usd=ref_usd,
            target_sell_usd=0.0,
            expected_profit_usd=0.0,
            sticker_value_usd=0.0,
            float_value=float_val,
            reason=reason,
        )

    # --- Basic sanity checks ---
    if ref_cents <= 0:
        return reject("No reference price available")

    if ask_usd < 0.01:
        return reject("Ask price too low to be valid")

    if ref_usd < CONFIG.min_reference_price_usd:
        return reject(f"Reference price ${ref_usd:.2f} below minimum ${CONFIG.min_reference_price_usd:.2f}")

    if CONFIG.max_spend_per_item_usd is not None and ask_usd > CONFIG.max_spend_per_item_usd:
        return reject(f"Ask ${ask_usd:.2f} exceeds per-item budget ${CONFIG.max_spend_per_item_usd:.2f}")

    # --- Name filters ---
    if CONFIG.whitelist_names:
        if not any(w.lower() in item_name.lower() for w in CONFIG.whitelist_names):
            return reject("Not in whitelist")

    if CONFIG.blacklist_names:
        if any(b.lower() in item_name.lower() for b in CONFIG.blacklist_names):
            return reject("Blacklisted item")

    # --- Float filter ---
    if float_val < CONFIG.min_float or float_val > CONFIG.max_float:
        return reject(f"Float {float_val:.6f} out of range [{CONFIG.min_float}, {CONFIG.max_float}]")

    # --- Adjusted reference price (stickers + float premium) ---
    sticker_val  = _sticker_value(item)
    adj_ref      = _apply_float_premium(ref_usd + sticker_val, float_val)

    if sticker_val > 0:
        log.debug(
            f"  [{item_name}] stickers +${sticker_val:.2f}  "
            f"ref ${ref_usd:.2f} → adj ${adj_ref:.2f}"
        )
    if CONFIG.low_float_threshold > 0 and float_val < CONFIG.low_float_threshold:
        log.debug(
            f"  [{item_name}] low-float bonus (float={float_val:.6f})  "
            f"ref ${ref_usd + sticker_val:.2f} → adj ${adj_ref:.2f}"
        )

    # --- Price check (against adjusted ref) ---
    ratio = ask_usd / adj_ref
    if ratio > CONFIG.buy_threshold:
        return reject(
            f"Price ratio {ratio:.2%} > buy threshold {CONFIG.buy_threshold:.2%}"
        )

    # --- Profit check ---
    target_sell_usd = adj_ref * CONFIG.sell_target
    fee             = target_sell_usd * CONFIG.csfloat_fee
    expected_profit = target_sell_usd - fee - ask_usd

    if expected_profit < CONFIG.min_profit_usd:
        return reject(
            f"Expected profit ${expected_profit:.2f} below minimum ${CONFIG.min_profit_usd:.2f}"
        )

    return Decision(
        should_buy=True,
        listing_id=listing_id,
        item_name=item_name,
        ask_price_usd=ask_usd,
        reference_price_usd=ref_usd,
        adjusted_ref_usd=round(adj_ref, 2),
        target_sell_usd=round(target_sell_usd, 2),
        expected_profit_usd=round(expected_profit, 2),
        sticker_value_usd=sticker_val,
        float_value=float_val,
        reason="OK",
    )
