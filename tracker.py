"""
SQLite-backed trade tracker for P&L reporting.
"""

import sqlite3
import logging
from config import CONFIG

log = logging.getLogger(__name__)


def _conn():
    return sqlite3.connect(CONFIG.db_path)


def init_db():
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id  TEXT NOT NULL,
                item_name   TEXT NOT NULL,
                buy_price   REAL NOT NULL,
                sell_price  REAL,
                status      TEXT NOT NULL DEFAULT 'bought',  -- bought | listed | sold
                bought_at   TEXT NOT NULL DEFAULT (datetime('now')),
                sold_at     TEXT,
                profit      REAL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS daily_spend (
                day     TEXT PRIMARY KEY,
                spent   REAL NOT NULL DEFAULT 0.0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id  TEXT NOT NULL UNIQUE,
                item_name   TEXT NOT NULL,
                buy_price   REAL NOT NULL,
                list_price  REAL NOT NULL,
                sell_price  REAL,
                status      TEXT NOT NULL DEFAULT 'open',  -- open | sold | expired
                bought_at   TEXT NOT NULL DEFAULT (datetime('now')),
                closed_at   TEXT,
                profit      REAL
            )
        """)


def record_buy(listing_id: str, item_name: str, buy_price_usd: float):
    with _conn() as db:
        db.execute(
            "INSERT INTO trades (listing_id, item_name, buy_price, status) VALUES (?,?,?,'bought')",
            (listing_id, item_name, buy_price_usd),
        )
        db.execute(
            "INSERT INTO daily_spend (day, spent) VALUES (date('now'), ?) "
            "ON CONFLICT(day) DO UPDATE SET spent = spent + excluded.spent",
            (buy_price_usd,),
        )


def record_list(listing_id: str, sell_price_usd: float):
    with _conn() as db:
        db.execute(
            "UPDATE trades SET status='listed', sell_price=? WHERE listing_id=? AND status='bought'",
            (sell_price_usd, listing_id),
        )


def record_sale(listing_id: str):
    with _conn() as db:
        row = db.execute(
            "SELECT buy_price, sell_price FROM trades WHERE listing_id=? AND status='listed'",
            (listing_id,),
        ).fetchone()
        if not row:
            log.warning(f"Sale recorded for unknown listing {listing_id}")
            return
        buy, sell = row
        fee = sell * CONFIG.csfloat_fee
        profit = sell - fee - buy
        db.execute(
            "UPDATE trades SET status='sold', profit=?, sold_at=datetime('now') "
            "WHERE listing_id=? AND status='listed'",
            (round(profit, 4), listing_id),
        )


def get_open_count_for_item(item_name: str) -> int:
    """Count live open positions (bought or listed) for a given item name."""
    with _conn() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM trades WHERE item_name=? AND status IN ('bought','listed')",
            (item_name,),
        ).fetchone()
    return row[0] if row else 0


def get_paper_open_count_for_item(item_name: str) -> int:
    with _conn() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE item_name=? AND status='open'",
            (item_name,),
        ).fetchone()
    return row[0] if row else 0


def get_paper_daily_weapon_count(weapon: str) -> int:
    """Count today's paper buys where item_name starts with weapon (e.g. 'AK-47')."""
    with _conn() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM paper_trades "
            "WHERE item_name LIKE ? AND date(bought_at) = date('now')",
            (f"{weapon}%",),
        ).fetchone()
    return row[0] if row else 0


def get_live_daily_weapon_count(weapon: str) -> int:
    with _conn() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM trades "
            "WHERE item_name LIKE ? AND date(bought_at) = date('now')",
            (f"{weapon}%",),
        ).fetchone()
    return row[0] if row else 0


def get_daily_spend() -> float:
    with _conn() as db:
        row = db.execute(
            "SELECT spent FROM daily_spend WHERE day = date('now')"
        ).fetchone()
    return row[0] if row else 0.0


def reset_paper_budget():
    import sqlite3 as _sqlite3
    with _conn() as db:
        db.execute("DELETE FROM paper_trades WHERE date(bought_at) = date('now')")
        db.execute("DELETE FROM daily_spend")
    log.info("Paper budget reset.")


def get_paper_daily_spend() -> float:
    with _conn() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(buy_price), 0) FROM paper_trades "
            "WHERE status IN ('open','sold','expired') "
            "AND date(bought_at) = date('now')"
        ).fetchone()
    return row[0] if row else 0.0


# ------------------------------------------------------------------ #
#  Paper trade tracking                                               #
# ------------------------------------------------------------------ #

def paper_record_buy(listing_id: str, item_name: str, buy_price: float, list_price: float):
    with _conn() as db:
        db.execute(
            "INSERT OR IGNORE INTO paper_trades (listing_id, item_name, buy_price, list_price, status) "
            "VALUES (?,?,?,?,'open')",
            (listing_id, item_name, buy_price, list_price),
        )


def paper_get_open() -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            "SELECT listing_id, item_name, buy_price, list_price, bought_at "
            "FROM paper_trades WHERE status='open'"
        ).fetchall()
    return [
        {"listing_id": r[0], "item_name": r[1], "buy_price": r[2],
         "list_price": r[3], "bought_at": r[4]}
        for r in rows
    ]


def paper_close(listing_id: str, sell_price: float, status: str):
    """status: 'sold' or 'expired'"""
    with _conn() as db:
        row = db.execute(
            "SELECT buy_price FROM paper_trades WHERE listing_id=? AND status='open'",
            (listing_id,),
        ).fetchone()
        if not row:
            return
        fee    = sell_price * CONFIG.csfloat_fee
        profit = sell_price - fee - row[0]
        db.execute(
            "UPDATE paper_trades SET status=?, sell_price=?, profit=?, "
            "closed_at=datetime('now') WHERE listing_id=?",
            (status, sell_price, round(profit, 4), listing_id),
        )


def get_paper_summary() -> dict:
    with _conn() as db:
        sold = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(profit),0) FROM paper_trades WHERE status='sold'"
        ).fetchone()
        expired = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(profit),0) FROM paper_trades WHERE status='expired'"
        ).fetchone()
        open_pos = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(buy_price),0) FROM paper_trades WHERE status='open'"
        ).fetchone()
    return {
        "sold_count":        sold[0],
        "realized_profit":   round(sold[1], 2),
        "expired_count":     expired[0],
        "expired_profit":    round(expired[1], 2),
        "open_count":        open_pos[0],
        "open_invested":     round(open_pos[1], 2),
    }


def get_pnl_summary() -> dict:
    with _conn() as db:
        total_profit = db.execute(
            "SELECT COALESCE(SUM(profit),0) FROM trades WHERE status='sold'"
        ).fetchone()[0]
        total_bought = db.execute(
            "SELECT COUNT(*) FROM trades"
        ).fetchone()[0]
        pending = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(buy_price),0) FROM trades WHERE status IN ('bought','listed')"
        ).fetchone()
    return {
        "total_trades": total_bought,
        "realized_profit_usd": round(total_profit, 2),
        "pending_count": pending[0],
        "pending_invested_usd": round(pending[1], 2),
    }
