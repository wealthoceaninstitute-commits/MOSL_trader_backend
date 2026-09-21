"""
MOFSL OpenAPI v7 async service client.
Wraps the official MOFSLOPENAPI SDK using asyncio.to_thread so FastAPI stays async.
"""

import asyncio
import json
from typing import Any, Dict, Optional, Tuple

import pyotp

from app.core.config import settings

# Import the official SDK
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from MOFSLOPENAPI import MOFSLOPENAPI


class MofslClientService:
    """
    Async wrapper around the official MOFSLOPENAPI SDK.
    All blocking SDK calls run in a thread pool via asyncio.to_thread.
    """

    def __init__(
        self,
        client_id: str,
        api_key: str,
        api_secret: str,
        totp_secret: Optional[str],
        password_hash: str,       # plain trading password (field name from DB)
        two_fa: Optional[str],    # PAN number
        base_url: Optional[str] = None,
        public_ip: Optional[str] = None,
    ):
        self.client_id = client_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.totp_secret = totp_secret
        self.password_plain = password_hash
        self.two_fa = two_fa or ""
        self.base_url = (base_url or settings.MOFSL_BASE_URL).rstrip("/")

        # Instantiate the SDK (blocking but fast — no network calls in __init__)
        self._sdk = MOFSLOPENAPI(
            api_key,
            self.base_url,
            None,           # clientcode — None for self-trading accounts
            "Desktop",
            "chrome",
            "104",
            api_secret,
        )

    # ------------------------------------------------------------------
    # Internal: run a blocking SDK call in a thread
    # ------------------------------------------------------------------

    async def _run(self, func, *args):
        return await asyncio.to_thread(func, *args)

    def _parse(self, raw) -> Dict[str, Any]:
        """SDK returns JSON string or dict depending on the method."""
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {"status": "ERROR", "message": raw}
        return {"status": "ERROR", "message": str(raw)}

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def login(self) -> Tuple[str, str]:
        """
        Two-step MOFSL login using the official SDK.
        Returns (auth_token, access_token).
        """
        # Generate TOTP if secret is available
        totp_code = ""
        if self.totp_secret and pyotp is not None:
            totp_code = pyotp.TOTP(self.totp_secret).now()

        # Step 1: login
        raw1 = await self._run(
            self._sdk.login,
            self.client_id,
            self.password_plain,
            self.two_fa,
            totp_code,
            self.client_id,   # vendorinfo = client_id
        )
        resp1 = self._parse(raw1)
        if resp1.get("status") != "SUCCESS":
            raise RuntimeError(f"MOFSL login failed: {resp1.get('message', resp1)}")

        auth_token: str = resp1.get("AuthToken", "")
        if not auth_token:
            raise RuntimeError("MOFSL login returned no AuthToken")

        # Step 2: GetAccessToken
        raw2 = await self._run(self._sdk.GetAccessToken)
        resp2 = self._parse(raw2)
        if resp2.get("status") != "SUCCESS":
            raise RuntimeError(f"MOFSL GetAccessToken failed: {resp2.get('message', resp2)}")

        access_token: str = resp2.get("accesstoken", "") or (resp2.get("data") or {}).get("accesstoken", "")
        if not access_token:
            raise RuntimeError("MOFSL GetAccessToken returned no accesstoken")

        return auth_token, access_token

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    async def place_order(self, auth_token: str, access_token: str, order_data: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(order_data)
        payload.setdefault("clientcode", "")
        return self._parse(await self._run(self._sdk.PlaceOrder, payload))

    async def modify_order(self, auth_token: str, access_token: str, order_data: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(order_data)
        payload.setdefault("clientcode", "")
        return self._parse(await self._run(self._sdk.ModifyOrder, payload))

    async def cancel_order(self, auth_token: str, access_token: str, uniqueorderid: str, exchange: str = "") -> Dict[str, Any]:
        return self._parse(await self._run(self._sdk.CancelOrder, uniqueorderid, ""))

    # ------------------------------------------------------------------
    # Books
    # ------------------------------------------------------------------

    async def get_order_book(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        payload = {"clientcode": "", "dateandtime": ""}
        return self._parse(await self._run(self._sdk.GetOrderBook, payload))

    async def get_trade_book(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        return self._parse(await self._run(self._sdk.GetTradeBook, ""))

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------

    async def get_positions(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        return self._parse(await self._run(self._sdk.GetPosition, ""))

    async def get_holdings(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        return self._parse(await self._run(self._sdk.GetDPHolding, ""))

    async def get_margin_summary(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        return self._parse(await self._run(self._sdk.GetReportMarginSummary, ""))

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_ltp(self, auth_token: str, access_token: str, exchange: str, scripcode: int) -> Dict[str, Any]:
        payload = {"clientcode": "", "exchange": exchange, "scripcode": scripcode}
        return self._parse(await self._run(self._sdk.GetLtp, payload))

    async def get_scrips(self, auth_token: str, access_token: str, exchangename: str, searchscrip: str) -> Dict[str, Any]:
        payload = {"clientcode": "", "exchangename": exchangename, "searchscrip": searchscrip}
        return self._parse(await self._run(self._sdk.GetBrokerageDetail, payload))

    # ------------------------------------------------------------------
    # Profile / logout
    # ------------------------------------------------------------------

    async def get_profile(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        return self._parse(await self._run(self._sdk.GetProfile, ""))

    async def logout(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        return self._parse(await self._run(self._sdk.logout, ""))
