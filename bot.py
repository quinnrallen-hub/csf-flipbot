"""
Main bot loop: polls CSFloat for new listings, evaluates them, buys and relists.
"""

import time
import logging
from datetime import datetime

from config import CONFIG
from csfloat_client import CSFloatClient, CSFloatError
from pricer import evaluate_listing
import skinport_source
import tracker
import discord_webhook as wh

log = logging.getLogger(__name__)

SEEN_IDS: set[str] = set()


class FlipBot:
    def __init__(self, sim_mode: bool = False):
        self.client   = CSFloatClient(CONFIG.api_key, CONFIG.max_retries)
        self.sim_mode = sim_mode
        tracker.init_db()

    # ------------------------------------------------------------------ #
    #  Main loop                                                           #
    # ------------------------------------------------------------------ #

    def run(self):
        log.info("=" * 60)
        log.info("CSFloat Flip Bot starting")
        log.info(f"  mode            : {'SIM (live dry-run)' if self.sim_mode else ('DRY RUN' if CONFIG.dry_run else 'LIVE')}")
        log.info(f"  dry_run         : {CONFIG.dry_run}")
        log.info(f"  buy_threshold   : {CONFIG.buy_threshold:.0%}")
        log.info(f"  sell_target     : {CONFIG.sell_target:.0%}")
        log.info(f"  daily_budget    : {f'${CONFIG.daily_budget_usd:.2f}' if CONFIG.daily_budget_usd is not None else 'unlimited'}")
        log.info(f"  max per item    : {f'${CONFIG.max_spend_per_item_usd:.2f}' if CONFIG.max_spend_per_item_usd is not None else 'unlimited'}")
        log.info("=" * 60)

        wh.notify_startup(
            CONFIG.discord_webhook_url,
            CONFIG.dry_run,
            CONFIG.buy_threshold,
            CONFIG.sell_target,
            CONFIG.daily_budget_usd,
            CONFIG.max_spend_per_item_usd,
        )

        if not CONFIG.api_key:
            if not CONFIG.dry_run and not self.sim_mode:
                log.error("API key required for live trading. Set CSF_API_KEY env var.")
                return
            log.warning("No API key — using Skinport as price source (sim/dry-run only)")
        elif not CONFIG.dry_run:
            try:
                balance = self.client.get_balance()
                inventory = self.client.get_inventory()
                inv_value = sum(i.get("reference_price", 0) for i in inventory) / 100.0
                log.info(f"Account balance : ${balance:.2f}")
                log.info(f"Inventory       : {len(inventory)} item(s)  est. value ${inv_value:.2f}")
            except CSFloatError as e:
                log.error(f"Auth check failed: {e}")
                return

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                self._print_summary()
                break
            except Exception as e:
                log.error(f"Unexpected error in main loop: {e}", exc_info=True)

            time.sleep(CONFIG.poll_interval_seconds)

    # ------------------------------------------------------------------ #
    #  Single poll cycle                                                   #
    # ------------------------------------------------------------------ #

    def _tick(self):
        daily_spend = tracker.get_paper_daily_spend() if self.sim_mode else tracker.get_daily_spend()
        budget = CONFIG.daily_budget_usd
        if budget is not None:
            log.info(f"  Budget: ${daily_spend:.2f} / ${budget:.2f} ({daily_spend/budget*100:.0f}%)")
            if daily_spend >= budget:
                log.warning(f"Daily budget hit (${daily_spend:.2f}). Skipping tick.")
                wh.notify_budget_hit(CONFIG.discord_webhook_url, daily_spend, budget)
                return

        if CONFIG.api_key:
            listings = self.client.get_listings(
                sort="newest",
                limit=50,
                min_float=CONFIG.min_float if CONFIG.min_float > 0 else None,
                max_float=CONFIG.max_float if CONFIG.max_float < 1 else None,
            )
        else:
            listings = skinport_source.get_listings()

        listings.sort(key=lambda l: l["price"] / l["reference_price"] if l.get("reference_price") else 1)
        new = [l for l in listings if l["id"] not in SEEN_IDS]
        if new:
            log.info(f"[{datetime.now().strftime('%H:%M:%S')}] {len(new)} new listings")

        # Build a reference-price map from this tick's listings for paper sell checks
        ref_map: dict[str, float] = {}
        for l in listings:
            name = l.get("item", {}).get("market_hash_name", "")
            ref  = l.get("reference_price", 0) / 100.0
            if name and ref > 0:
                ref_map[name] = ref

        if self.sim_mode:
            self._check_paper_sales(ref_map)

        for listing in new:
            SEEN_IDS.add(listing["id"])
            prev_spend = daily_spend
            self._evaluate_and_act(listing, daily_spend)
            daily_spend = tracker.get_paper_daily_spend() if self.sim_mode else tracker.get_daily_spend()
            if daily_spend > prev_spend:
                break  # one buy per tick — leave the rest unseen for next tick
            if CONFIG.daily_budget_usd is not None and daily_spend >= CONFIG.daily_budget_usd:
                break

    # ------------------------------------------------------------------ #
    #  Evaluate one listing                                                #
    # ------------------------------------------------------------------ #

    def _evaluate_and_act(self, listing: dict, daily_spend: float):
        decision = evaluate_listing(listing)

        if not decision.should_buy:
            log.debug(f"  SKIP [{decision.item_name}] — {decision.reason}")
            return

        # Per-item diversification check
        open_fn = tracker.get_paper_open_count_for_item if self.sim_mode else tracker.get_open_count_for_item
        if open_fn(decision.item_name) >= CONFIG.max_per_item_name:
            log.debug(f"  SKIP [{decision.item_name}] — max open positions for this skin")
            return

        # Per-weapon-type daily cap
        weapon = decision.item_name.split(" | ")[0]
        weapon_fn = tracker.get_paper_daily_weapon_count if self.sim_mode else tracker.get_live_daily_weapon_count
        if weapon_fn(weapon) >= CONFIG.max_daily_per_weapon:
            log.debug(f"  SKIP [{decision.item_name}] — {weapon} daily cap reached ({CONFIG.max_daily_per_weapon})")
            return

        # Budget headroom check
        if CONFIG.daily_budget_usd is not None and daily_spend + decision.ask_price_usd > CONFIG.daily_budget_usd:
            log.warning(
                f"  SKIP [{decision.item_name}] ${decision.ask_price_usd:.2f} "
                f"— would exceed daily budget"
            )
            return

        sticker_str = f"  stickers=+${decision.sticker_value_usd:.2f}" if decision.sticker_value_usd > 0 else ""
        float_str   = f"  float={decision.float_value:.4f}"
        adj_str     = f"  adj_ref=${decision.adjusted_ref_usd:.2f}" if decision.adjusted_ref_usd != decision.reference_price_usd else ""
        log.info(
            f"  BUY  [{decision.item_name}]  "
            f"ask=${decision.ask_price_usd:.2f}  "
            f"ref=${decision.reference_price_usd:.2f}"
            f"{adj_str}{sticker_str}{float_str}  "
            f"profit≈${decision.expected_profit_usd:.2f}"
        )

        if self.sim_mode:
            tracker.paper_record_buy(
                decision.listing_id,
                decision.item_name,
                decision.ask_price_usd,
                decision.target_sell_usd,
            )
            log.info(
                f"  [SIM] paper-bought {decision.item_name}  "
                f"buy=${decision.ask_price_usd:.2f}  list=${decision.target_sell_usd:.2f}"
            )
            wh.notify_buy_signal(
                CONFIG.discord_webhook_url,
                decision.item_name,
                decision.ask_price_usd,
                decision.reference_price_usd,
                decision.expected_profit_usd,
                dry_run=True,
            )
            return

        wh.notify_buy_signal(
            CONFIG.discord_webhook_url,
            decision.item_name,
            decision.ask_price_usd,
            decision.reference_price_usd,
            decision.expected_profit_usd,
            CONFIG.dry_run,
        )

        if CONFIG.dry_run:
            log.info("  [DRY RUN] would have bought — skipping actual purchase")
            return

        self._buy_and_list(decision)

    # ------------------------------------------------------------------ #
    #  Sim: check paper positions for exits                               #
    # ------------------------------------------------------------------ #

    def _check_paper_sales(self, ref_map: dict[str, float]):
        open_positions = tracker.paper_get_open()
        if not open_positions:
            return

        for pos in open_positions:
            item  = pos["item_name"]
            ref   = ref_map.get(item, 0.0)
            held_h = 0.0
            try:
                bought = datetime.fromisoformat(pos["bought_at"])
                held_h = (datetime.utcnow() - bought).total_seconds() / 3600
            except Exception:
                pass

            expired = held_h >= CONFIG.max_sell_hours

            if ref >= pos["list_price"]:
                # Market has recovered — simulate sale at list price
                tracker.paper_close(pos["listing_id"], pos["list_price"], "sold")
                fee    = pos["list_price"] * CONFIG.csfloat_fee
                profit = pos["list_price"] - fee - pos["buy_price"]
                log.info(
                    f"  [SIM] SOLD  {item}  "
                    f"sell=${pos['list_price']:.2f}  profit=${profit:+.2f}"
                )
            elif expired:
                # Held too long — close at current ref (or buy price as floor)
                close_price = max(ref, pos["buy_price"])
                tracker.paper_close(pos["listing_id"], close_price, "expired")
                fee    = close_price * CONFIG.csfloat_fee
                profit = close_price - fee - pos["buy_price"]
                log.info(
                    f"  [SIM] EXPRD {item}  "
                    f"sell=${close_price:.2f}  profit=${profit:+.2f}  (held {held_h:.1f}h)"
                )

    # ------------------------------------------------------------------ #
    #  Buy + relist                                                        #
    # ------------------------------------------------------------------ #

    def _buy_and_list(self, decision):
        # Step 1: buy
        try:
            buy_result = self.client.buy_listing(
                decision.listing_id,
                int(decision.ask_price_usd * 100),
            )
            log.info(f"  Bought {decision.item_name} for ${decision.ask_price_usd:.2f}")
            tracker.record_buy(decision.listing_id, decision.item_name, decision.ask_price_usd)
        except CSFloatError as e:
            log.error(f"  Buy failed for {decision.listing_id}: {e}")
            wh.notify_buy_error(CONFIG.discord_webhook_url, decision.item_name, str(e))
            return

        # Step 2: get the asset_id from the purchase result so we can relist
        asset_id = (
            buy_result.get("item", {}).get("asset_id")
            or buy_result.get("asset_id")
        )
        if not asset_id:
            log.warning("  Could not extract asset_id from buy result — item in inventory but not listed")
            return

        # Brief pause before relisting
        time.sleep(2)

        # Step 3: relist
        sell_cents = int(decision.target_sell_usd * 100)
        try:
            self.client.create_listing(asset_id, sell_cents)
            log.info(f"  Listed at ${decision.target_sell_usd:.2f}")
            tracker.record_list(decision.listing_id, decision.target_sell_usd)
            wh.notify_buy_success(
                CONFIG.discord_webhook_url,
                decision.item_name,
                decision.ask_price_usd,
                decision.target_sell_usd,
                decision.expected_profit_usd,
            )
        except CSFloatError as e:
            log.error(f"  Relist failed for asset {asset_id}: {e}")

    # ------------------------------------------------------------------ #
    #  Summary                                                             #
    # ------------------------------------------------------------------ #

    def _print_summary(self):
        s = tracker.get_pnl_summary()
        log.info("─" * 40)
        log.info("P&L Summary")
        log.info(f"  Total trades     : {s['total_trades']}")
        log.info(f"  Realized profit  : ${s['realized_profit_usd']:.2f}")
        log.info(f"  Pending trades   : {s['pending_count']} (${s['pending_invested_usd']:.2f} tied up)")
        log.info("─" * 40)
        wh.notify_summary(
            CONFIG.discord_webhook_url,
            s["total_trades"],
            s["realized_profit_usd"],
            s["pending_count"],
            s["pending_invested_usd"],
        )
