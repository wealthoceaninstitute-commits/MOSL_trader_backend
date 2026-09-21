"""
MOFSL OpenAPI v7 async service client.
Wraps all REST calls using httpx (async); no dependency on the legacy SDK.
Headers and endpoint paths are taken directly from the reference MOFSLOPENAPI.py SDK.
"""

import hashlib
import re
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

try:
    import pyotp
except ImportError:
    pyotp = None

from app.core.config import settings


# ---------------------------------------------------------------------------
# System-info helpers (same values as the reference SDK but without the WMI
# Windows dependency — we're running on Linux/Docker).
# ---------------------------------------------------------------------------

_SDK_VERSION = "V.1.1.0"
_USER_AGENT = f"MOSL/{_SDK_VERSION}"


def _mac_address() -> str:
    try:
        return ":".join(re.findall("..", "%012x" % uuid.getnode()))
    except Exception:
        return "00:00:00:00:00:00"


def _local_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "1.2.3.4"


def _installed_app_id(client_id: str = "") -> str:
    """
    Generate a stable installedappid derived from the client_id so it stays
    the same across server restarts. MOFSL ties the token to the app ID used
    at login time — a new random UUID each restart causes 'Invalid Token'.
    """
    if client_id:
        # Deterministic UUID based on client_id string
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mofsl-{client_id}"))
    return str(uuid.uuid1())


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------

class MofslClientService:
    """
    Async MOFSL OpenAPI v7 client.

    Credentials are stored at construction time.  The caller must call
    ``login()`` to obtain (auth_token, access_token) and then pass those
    into every trading/data method.
    """

    def __init__(
        self,
        client_id: str,
        api_key: str,
        api_secret: str,
        totp_secret: Optional[str],
        password_hash: str,          # plain trading password (we hash it on login)
        two_fa: Optional[str],       # PAN / 2FA string required by MOFSL
        base_url: Optional[str] = None,
        public_ip: Optional[str] = None,
    ):
        self.client_id = client_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.totp_secret = totp_secret
        self.password_plain = password_hash   # field name from DB is password_hash but stores the plain password
        self.two_fa = two_fa or ""
        self.base_url = (base_url or settings.MOFSL_BASE_URL).rstrip("/")
        self.public_ip = public_ip or settings.STATIC_IP or "1.2.3.4"
        self.local_ip = _local_ip()
        self.mac_address = _mac_address()
        self.installed_app_id = _installed_app_id(client_id)
        self.source_id = "Desktop"
        self.os_name = "Ubuntu 20.04.3 LTS"
        self.os_version = "20.04"
        self.device_model = "VMware Virtual Platform"
        self.manufacturer = "unknown"
        self.product_name = settings.MOFSL_PRODUCT_NAME
        self.product_version = settings.MOFSL_PRODUCT_VERSION
        self.latitude = "19.0760"
        self.longitude = "72.8777"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_headers(self, auth_token: str = "", access_token: str = "") -> Dict[str, str]:
        """Build the full set of MOFSL-required request headers."""
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": auth_token,
            "User-Agent": _USER_AGENT,
            "apikey": self.api_key,
            "apisecretkey": self.api_secret,
            "macaddress": self.mac_address,
            "clientlocalip": self.local_ip,
            "sourceid": self.source_id,
            "clientpublicip": self.public_ip,
            "vendorinfo": self.client_id,
            "osname": self.os_name,
            "osversion": self.os_version,
            "installedappid": self.installed_app_id,
            "devicemodel": self.device_model,
            "manufacturer": self.manufacturer,
            "productname": self.product_name,
            "productversion": self.product_version,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "sdkversion": "Python 3.0",
        }
        if access_token:
            headers["accesstoken"] = access_token
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def _post(self, path: str, data: Dict[str, Any], auth_token: str = "", access_token: str = "") -> Dict[str, Any]:
        url = self._url(path)
        headers = self._build_headers(auth_token, access_token)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=data)
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def login(self) -> Tuple[str, str]:
        """
        Perform MOFSL two-step login:
          1. POST /rest/login/v7/authdirectapi  -> AuthToken
          2. POST /rest/login/v1/getaccesstoken -> AccessToken

        Returns (auth_token, access_token).
        Raises RuntimeError on failure.
        """
        # Step 1: authdirectapi
        checksum = hashlib.sha256((self.password_plain + self.api_key).encode()).hexdigest()

        totp_code = ""
        if self.totp_secret and pyotp is not None:
            totp_code = pyotp.TOTP(self.totp_secret).now()

        login_payload = {
            "userid": self.client_id,
            "password": checksum,
            "2FA": self.two_fa,
            "totp": totp_code,
        }

        login_resp = await self._post("/rest/login/v7/authdirectapi", login_payload)
        if login_resp.get("status") != "SUCCESS":
            raise RuntimeError(f"MOFSL authdirectapi failed: {login_resp.get('message', login_resp)}")

        auth_token: str = login_resp.get("AuthToken", "")
        if not auth_token:
            raise RuntimeError("MOFSL login returned no AuthToken")

        # Step 2: getaccesstoken
        access_payload = {"userid": self.client_id}
        access_resp = await self._post("/rest/login/v1/getaccesstoken", access_payload, auth_token=auth_token)
        if access_resp.get("status") != "SUCCESS":
            raise RuntimeError(f"MOFSL getaccesstoken failed: {access_resp.get('message', access_resp)}")

        access_token: str = (access_resp.get("data") or {}).get("accesstoken", "")
        if not access_token:
            # Some versions return it at the top level
            access_token = access_resp.get("accesstoken", "")

        return auth_token, access_token

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    async def place_order(self, auth_token: str, access_token: str, order_data: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(order_data)
        payload.setdefault("clientcode", self.client_id)
        return await self._post("/rest/trans/v1/placeorder", payload, auth_token, access_token)

    async def modify_order(self, auth_token: str, access_token: str, order_data: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(order_data)
        payload.setdefault("clientcode", self.client_id)
        return await self._post("/rest/trans/v2/modifyorder", payload, auth_token, access_token)

    async def cancel_order(self, auth_token: str, access_token: str, uniqueorderid: str, exchange: str = "") -> Dict[str, Any]:
        payload = {
            "clientcode": self.client_id,
            "uniqueorderid": uniqueorderid,
        }
        if exchange:
            payload["exchange"] = exchange
        return await self._post("/rest/trans/v1/cancelorder", payload, auth_token, access_token)

    # ------------------------------------------------------------------
    # Books
    # ------------------------------------------------------------------

    async def get_order_book(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        today = datetime.now().strftime("%d-%b-%Y 09:00:00")
        payload = {"clientcode": self.client_id, "datetimestamp": today}
        return await self._post("/rest/book/v1/getorderbook", payload, auth_token, access_token)

    async def get_trade_book(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        payload = {"clientcode": self.client_id}
        return await self._post("/rest/book/v1/gettradebook", payload, auth_token, access_token)

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------

    async def get_positions(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        payload = {"clientcode": self.client_id}
        return await self._post("/rest/book/v1/getposition", payload, auth_token, access_token)

    async def get_holdings(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        payload = {"clientcode": self.client_id}
        return await self._post("/rest/report/v1/getdpholding", payload, auth_token, access_token)

    async def get_margin_summary(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        payload = {"clientcode": self.client_id}
        return await self._post("/rest/report/v1/getreportmarginsummary", payload, auth_token, access_token)

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_ltp(self, auth_token: str, access_token: str, exchange: str, scripcode: int) -> Dict[str, Any]:
        payload = {
            "clientcode": self.client_id,
            "exchange": exchange,
            "scripcode": scripcode,
        }
        return await self._post("/rest/report/v1/getltpdata", payload, auth_token, access_token)

    async def get_scrips(self, auth_token: str, access_token: str, exchangename: str, searchscrip: str) -> Dict[str, Any]:
        payload = {
            "clientcode": self.client_id,
            "exchangename": exchangename,
            "searchscrip": searchscrip,
        }
        return await self._post("/rest/report/v1/getscripsbyexchangename", payload, auth_token, access_token)

    # ------------------------------------------------------------------
    # Profile / logout
    # ------------------------------------------------------------------

    async def get_profile(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        payload = {"clientcode": self.client_id}
        return await self._post("/rest/login/v1/getprofile", payload, auth_token, access_token)

    async def logout(self, auth_token: str, access_token: str) -> Dict[str, Any]:
        payload = {"userid": self.client_id}
        return await self._post("/rest/login/v1/logout", payload, auth_token, access_token)
