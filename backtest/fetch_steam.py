#!/usr/bin/env python3
"""
Fetch CS2 price history from Steam Market for a given date range.

Requires your Steam session cookie:
  1. Open Chrome/Firefox, go to steamcommunity.com and log in
  2. Open DevTools (F12) → Application → Cookies → steamcommunity.com
  3. Copy the value of 'steamLoginSecure'
  4. Set it: export STEAM_LOGIN_SECURE="your_value_here"
     or paste it into STEAM_COOKIE below
"""

import os
import csv
import time
import json
import base64
import logging
from pathlib import Path
from datetime import datetime, timedelta

import requests

log = logging.getLogger(__name__)

STEAM_COOKIE = os.getenv("STEAM_LOGIN_SECURE", "")
APP_ID = 730  # CS2

DATA_DIR = Path(__file__).parent / "data" / "steam_live"
INDEX_PATH = Path(__file__).parent / "data" / "dataset_publish" / "item_index.csv"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Referer": "https://steamcommunity.com/market/",
    })
    if STEAM_COOKIE:
        s.cookies.set("steamLoginSecure", STEAM_COOKIE, domain="steamcommunity.com")
    return s


def fetch_item_history(session: requests.Session, market_hash_name: str) -> list[dict]:
    """
    Returns list of {timestamp, price_dollar, sells} dicts.
    Steam returns ~1 year of daily data.
    """
    url = "https://steamcommunity.com/market/pricehistory/"
    params = {"appid": APP_ID, "market_hash_name": market_hash_name}

    for attempt in range(4):
        try:
            r = session.get(url, params=params, timeout=15)
        except requests.RequestException as e:
            log.warning(f"Network error: {e}")
            time.sleep(2 ** attempt)
            continue

        if r.status_code == 429:
            log.warning("Rate limited by Steam — sleeping 60s")
            time.sleep(60)
            continue
        if r.status_code == 401 or r.status_code == 403:
            raise RuntimeError(
                "Steam returned 401/403 — your steamLoginSecure cookie is missing or expired.\n"
                "See instructions at the top of fetch_steam.py"
            )
        if not r.ok:
            log.warning(f"Steam error {r.status_code} for {market_hash_name}")
            return []

        data = r.json()
        if not data.get("success"):
            return []

        records = []
        for entry in data.get("prices", []):
            # entry: ["Jan 01 2026 01: +0", 5.50, "10"]
            try:
                dt = datetime.strptime(entry[0][:11].strip(), "%b %d %Y")
                records.append({
                    "timestamp": int(dt.timestamp() * 1000),
                    "price_dollar": float(entry[1]),
                    "sells": int(entry[2]),
                })
            except (ValueError, IndexError):
                continue
        return records

    return []


def load_item_names(max_items: int = 200) -> list[str]:
    names = []
    with open(INDEX_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = base64.b64decode(row["item_hash_name_base64"]).decode("utf-8")
            names.append(name)
            if len(names) >= max_items:
                break
    return names


def fetch_all(max_items: int = 200, delay: float = 1.5):
    """
    Download live Steam Market price history for up to max_items skins.
    Saves each item as data/steam_live/{file_index}.csv
    Also writes data/steam_live/item_index.csv
    """
    if not STEAM_COOKIE:
        print("ERROR: Steam cookie not set.")
        print("  export STEAM_LOGIN_SECURE='your_cookie_value'")
        print("  (See instructions at the top of fetch_steam.py)")
        return False

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    names = load_item_names(max_items)
    session = _session()
    index_rows = []

    print(f"Fetching Steam Market history for {len(names)} items...")
    print("(This will take a few minutes due to rate limiting)")

    for i, name in enumerate(names):
        out_path = DATA_DIR / f"{i}.csv"

        if out_path.exists():
            log.debug(f"  [{i}] cached — {name[:50]}")
            index_rows.append({"item_hash_name": name, "file_name": f"{i}.csv"})
            continue

        if i % 10 == 0:
            print(f"  [{i}/{len(names)}] {name[:60]}")

        records = fetch_item_history(session, name)
        if records:
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["timestamp", "price_dollar", "sells"])
                w.writeheader()
                w.writerows(records)
            index_rows.append({"item_hash_name": name, "file_name": f"{i}.csv"})

        time.sleep(delay)

    # Write index
    index_out = DATA_DIR / "item_index.csv"
    with open(index_out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["item_hash_name", "file_name"])
        w.writeheader()
        w.writerows(index_rows)

    print(f"Done. {len(index_rows)} items saved to {DATA_DIR}")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetch_all()
