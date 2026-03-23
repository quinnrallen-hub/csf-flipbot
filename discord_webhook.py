"""
Discord webhook notifications for flipbot events.
Set DISCORD_WEBHOOK_URL in config (or env) to enable.
"""

import logging
import requests

log = logging.getLogger(__name__)

# Discord embed colors
COLOR_GREEN  = 0x2ECC71
COLOR_RED    = 0xE74C3C
COLOR_YELLOW = 0xF1C40F
COLOR_ORANGE = 0xE67E22
COLOR_BLUE   = 0x3498DB
COLOR_GRAY   = 0x95A5A6


def _send(webhook_url: str, embeds: list[dict]):
    """POST one or more embeds to a Discord webhook."""
    if not webhook_url:
        return
    try:
        resp = requests.post(
            webhook_url,
            json={"embeds": embeds},
            timeout=5,
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"Discord webhook failed: {e}")


def _field(name: str, value: str, inline: bool = True) -> dict:
    return {"name": name, "value": value, "inline": inline}


def notify_startup(webhook_url: str, dry_run: bool, buy_threshold: float,
                   sell_target: float, daily_budget: float | None,
                   max_per_item: float | None):
    _send(webhook_url, [{
        "title": "Bot Started",
        "description": f"{'**[DRY RUN]** ' if dry_run else ''}CSFloat Flipbot is online.",
        "color": COLOR_GRAY,
        "fields": [
            _field("Buy Threshold", f"{buy_threshold:.0%}"),
            _field("Sell Target",   f"{sell_target:.0%}"),
            _field("Daily Budget",  f"${daily_budget:.2f}" if daily_budget is not None else "Unlimited"),
            _field("Max Per Item",  f"${max_per_item:.2f}" if max_per_item is not None else "Unlimited"),
        ],
    }])


def notify_buy_signal(webhook_url: str, item_name: str, ask: float,
                      ref: float, profit: float, dry_run: bool):
    """Fired when evaluate_listing says buy (regardless of dry_run)."""
    ratio = ask / ref if ref else 0
    prefix = "[DRY RUN] " if dry_run else ""
    _send(webhook_url, [{
        "title": f"{prefix}Buy Signal — {item_name}",
        "color": COLOR_YELLOW if dry_run else COLOR_GREEN,
        "fields": [
            _field("Ask",            f"${ask:.2f}"),
            _field("Reference",      f"${ref:.2f}"),
            _field("Ask/Ref",        f"{ratio:.1%}"),
            _field("Est. Profit",    f"${profit:.2f}"),
        ],
    }])


def notify_buy_success(webhook_url: str, item_name: str, buy_price: float,
                       list_price: float, profit: float):
    _send(webhook_url, [{
        "title": f"Bought & Listed — {item_name}",
        "color": COLOR_GREEN,
        "fields": [
            _field("Bought For",  f"${buy_price:.2f}"),
            _field("Listed At",   f"${list_price:.2f}"),
            _field("Est. Profit", f"${profit:.2f}"),
        ],
    }])


def notify_buy_error(webhook_url: str, item_name: str, error: str):
    _send(webhook_url, [{
        "title": f"Buy Failed — {item_name}",
        "color": COLOR_RED,
        "fields": [_field("Error", error, inline=False)],
    }])


def notify_budget_hit(webhook_url: str, spent: float, budget: float):
    _send(webhook_url, [{
        "title": "Daily Budget Reached",
        "description": f"Spent **${spent:.2f}** of **${budget:.2f}** today. No more buys until midnight.",
        "color": COLOR_ORANGE,
    }])


def notify_summary(webhook_url: str, total_trades: int, realized_profit: float,
                   pending_count: int, pending_invested: float):
    _send(webhook_url, [{
        "title": "Session Summary",
        "color": COLOR_BLUE,
        "fields": [
            _field("Total Trades",    str(total_trades)),
            _field("Realized Profit", f"${realized_profit:.2f}"),
            _field("Pending Trades",  str(pending_count)),
            _field("Pending Capital", f"${pending_invested:.2f}"),
        ],
    }])
