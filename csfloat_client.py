import time
import logging
import requests
from typing import Optional

log = logging.getLogger(__name__)

BASE_URL = "https://csfloat.com/api/v1"


class CSFloatError(Exception):
    pass


class RateLimitError(CSFloatError):
    pass


class CSFloatClient:
    def __init__(self, api_key: str, max_retries: int = 5):
        self.api_key = api_key
        self.session = requests.Session()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = api_key
        self.session.headers.update(headers)
        self.max_retries = max_retries

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{BASE_URL}{path}"
        for attempt in range(self.max_retries):
            try:
                resp = self.session.request(method, url, timeout=10, **kwargs)
            except requests.RequestException as e:
                log.warning(f"Network error (attempt {attempt+1}): {e}")
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                log.warning(f"Rate limited — sleeping {retry_after}s")
                time.sleep(retry_after)
                continue

            if resp.status_code == 401:
                raise CSFloatError("Invalid API key. Set CSF_API_KEY env var or update config.py")

            if resp.status_code >= 500:
                log.warning(f"Server error {resp.status_code} (attempt {attempt+1})")
                time.sleep(2 ** attempt)
                continue

            if not resp.ok:
                raise CSFloatError(f"API error {resp.status_code}: {resp.text}")

            return resp.json()

        raise CSFloatError(f"Max retries exceeded for {method} {path}")

    # ------------------------------------------------------------------ #
    #  Market listings                                                     #
    # ------------------------------------------------------------------ #

    def get_listings(
        self,
        sort: str = "lowest_price",
        limit: int = 50,
        min_price: Optional[int] = None,   # cents
        max_price: Optional[int] = None,   # cents
        min_float: Optional[float] = None,
        max_float: Optional[float] = None,
        category: Optional[int] = None,    # 1=knife,2=glove,3=agent,4=weapon
        market_hash_name: Optional[str] = None,
    ) -> list[dict]:
        params = {"sort_by": sort, "limit": limit, "type": "buy_now"}
        if min_price is not None:
            params["min_price"] = min_price
        if max_price is not None:
            params["max_price"] = max_price
        if min_float is not None:
            params["min_float"] = min_float
        if max_float is not None:
            params["max_float"] = max_float
        if category is not None:
            params["category"] = category
        if market_hash_name:
            params["market_hash_name"] = market_hash_name

        data = self._request("GET", "/listings", params=params)
        return data.get("data", [])

    def get_listing(self, listing_id: str) -> dict:
        return self._request("GET", f"/listings/{listing_id}")

    # ------------------------------------------------------------------ #
    #  Buying                                                              #
    # ------------------------------------------------------------------ #

    def buy_listing(self, listing_id: str, price_cents: int) -> dict:
        """
        Purchase a listing. price_cents must match the current ask price
        to prevent race-condition overpays.
        """
        return self._request(
            "POST",
            f"/listings/{listing_id}/buy",
            json={"price": price_cents},
        )

    # ------------------------------------------------------------------ #
    #  Selling / creating listings                                         #
    # ------------------------------------------------------------------ #

    def create_listing(
        self,
        asset_id: str,
        price_cents: int,
        description: str = "",
    ) -> dict:
        return self._request(
            "POST",
            "/listings",
            json={
                "asset_id": asset_id,
                "price": price_cents,
                "description": description,
                "type": "buy_now",
            },
        )

    def update_listing_price(self, listing_id: str, new_price_cents: int) -> dict:
        return self._request(
            "PATCH",
            f"/listings/{listing_id}",
            json={"price": new_price_cents},
        )

    def delete_listing(self, listing_id: str) -> dict:
        return self._request("DELETE", f"/listings/{listing_id}")

    # ------------------------------------------------------------------ #
    #  Account                                                             #
    # ------------------------------------------------------------------ #

    def get_me(self) -> dict:
        return self._request("GET", "/me")

    def get_balance(self) -> float:
        """Returns balance in USD."""
        me = self.get_me()
        return me.get("balance", 0) / 100.0

    def get_inventory(self) -> list[dict]:
        return self._request("GET", "/me/inventory").get("data", [])
