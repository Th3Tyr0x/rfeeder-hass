"""Async REST client for the Robotail RFeeder UCSP cloud.

Wraps the reverse-engineered HTTP API (see docs/PROTOCOL.md). All HTTP calls
use aiohttp and run in the HA event loop.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from typing import Any

import aiohttp

from .const import (
    CONF_AREA,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_PW_SALT,
    CONF_TIMESTAMP_URL,
    DEFAULT_AREA,
)

_LOGGER = logging.getLogger(__name__)

NONCE_ALPHABET = "0123456789abcdef"
LOGIN_RETRIES = 8  # backend DNS round-robin inconsistency workaround (code 20009)
USER_AGENT = "RFeederOverseas/1.1.4 (com.robotail.feeder.overseas)"


class UcspApiError(RuntimeError):
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
        code = payload.get("code") if isinstance(payload, dict) else None
        message = payload.get("message") if isinstance(payload, dict) else None
        super().__init__(f"UCSP API error status={status} code={code} message={message}")


class UcspAuthError(UcspApiError):
    """Authentication failed (bad credentials or expired session)."""


class UcspClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        account: str,
        password: str,
        config: dict[str, str],
    ) -> None:
        self.session = session
        self.account = account
        self.password = password
        self.area = config.get(CONF_AREA) or DEFAULT_AREA
        self.app_id = config[CONF_APP_ID]
        self.app_secret = config[CONF_APP_SECRET]
        self.client_id = config[CONF_CLIENT_ID]
        self.pw_salt = config[CONF_PW_SALT]
        self.base_url = config[CONF_BASE_URL].rstrip("/")
        self.timestamp_url = config[CONF_TIMESTAMP_URL].rstrip("/")
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.access_expire_at: int = 0  # epoch seconds
        self.refresh_expire_at: int = 0
        self.user_id: int | None = None
        self._server_time_offset_ms = 0
        self._server_time_fetched_at = 0.0

    # -- signing -------------------------------------------------------------

    def _now_ms(self) -> int:
        return int(time.time() * 1000) + self._server_time_offset_ms

    @staticmethod
    def _nonce() -> str:
        return (
            "".join(random.choice(NONCE_ALPHABET) for _ in range(8))
            + "-"
            + random.choice(NONCE_ALPHABET)
        )

    def _sign(self, timestamp_ms: int, nonce: str) -> str:
        digest = hashlib.md5(
            (str(timestamp_ms) + self.app_secret + nonce + self.client_id).encode()
        ).hexdigest()
        return f"{digest} {timestamp_ms} {nonce} v2"

    def _headers(self, *, auth: bool) -> dict[str, str]:
        ts = self._now_ms()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-UBT-AppId": self.app_id,
            "X-UBT-ClientId": self.client_id,
            "X-UBT-Language": "en",
            "X-UBT-Sign": self._sign(ts, self._nonce()),
            # force a fresh TCP connection per request so DNS round-robin
            # re-rolls between the inconsistent vendor backends
            "Connection": "close",
        }
        if auth:
            if not self.access_token:
                raise UcspAuthError(401, {"code": "no_token", "message": "not logged in"})
            headers["Authorization"] = self.access_token
        return headers

    async def sync_server_time(self) -> None:
        """Fetch the server timestamp once and remember the offset to local time."""
        try:
            async with self.session.get(
                f"{self.timestamp_url}/v1/client-auth-service/api/timestamp",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
            server_ms = int(data["timestamp"]) * 1000
            self._server_time_offset_ms = server_ms - int(time.time() * 1000)
            self._server_time_fetched_at = time.monotonic()
        except (aiohttp.ClientError, KeyError, ValueError, OSError) as err:
            _LOGGER.debug("Could not sync UCSP server time, using local time: %s", err)

    # -- auth ----------------------------------------------------------------

    async def _async_login_once(self) -> None:
        pw_md5 = hashlib.md5((self.password + self.pw_salt).encode()).hexdigest()
        payload = {
            "account": self.account,
            "accountType": 2 if "@" in self.account else 1,
            "areaCode": None,
            "password": pw_md5,
            "area": self.area,
        }
        status, data = await self._request(
            "POST", "/usercenter/v1/users/login/password", payload=payload, auth=False
        )
        if not isinstance(data, dict) or data.get("code") != 0:
            raise UcspAuthError(status, data)
        body = data.get("data") or {}
        token_dto = body.get("tokenDTO") or {}
        access = token_dto.get("accessToken") or {}
        refresh = token_dto.get("refreshToken") or {}
        if not access.get("token"):
            raise UcspAuthError(status, data)
        self.access_token = access["token"]
        self.access_expire_at = int(access.get("expireAt") or 0)
        self.refresh_token = refresh.get("token")
        self.refresh_expire_at = int(refresh.get("expireAt") or 0)
        user = body.get("userDTO") or {}
        self.user_id = user.get("userId")
        _LOGGER.info("UCSP login succeeded (userId=%s)", self.user_id)

    async def _drop_connections(self) -> None:
        """No-op kept for API compatibility.

        ucsp-oversea.ubtrobot.com is DNS round-robin between an EU backend
        (knows the account) and a non-EU backend (code 20009). Fresh TCP
        connections re-roll the round-robin dice; we force them per request
        via the ``Connection: close`` header instead of dropping the session
        connector (which would close the whole aiohttp session).
        """

    async def async_resolve_area_domain(self) -> None:
        """Resolve the regional API host for the configured area.

        The app learns e.g. DE -> https://ucsp-eu.ubtrobot.com via
        /usercenter/v1/area-domains on the global host. Using the regional
        host avoids the inconsistent global DNS round-robin entirely.
        """
        try:
            async with self.session.get(
                f"{self.base_url}/usercenter/v1/area-domains",
                headers=self._headers(auth=False),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json(content_type=None)
            body = data.get("data") if isinstance(data, dict) else None
            if not isinstance(body, dict):
                return
            fallback = None
            default = body.get("defaultAreaDomain") or {}
            if default.get("domainUrl"):
                fallback = default["domainUrl"]
            for group in body.get("groupList") or []:
                for entry in (group or {}).get("areaDomainList") or []:
                    if (entry or {}).get("area") == self.area and entry.get("domainUrl"):
                        self.base_url = entry["domainUrl"].rstrip("/")
                        _LOGGER.info("UCSP regional host for %s: %s", self.area, self.base_url)
                        return
            if fallback:
                self.base_url = fallback.rstrip("/")
        except (aiohttp.ClientError, KeyError, ValueError, OSError) as err:
            _LOGGER.debug("Could not resolve area domain, keeping %s: %s", self.base_url, err)

    async def async_login(self) -> None:
        """Login with retry across the inconsistent DNS round-robin backends."""
        await self.sync_server_time()
        await self.async_resolve_area_domain()
        last_error: Exception | None = None
        for attempt in range(1, LOGIN_RETRIES + 1):
            try:
                await self._async_login_once()
                return
            except UcspApiError as err:
                last_error = err
                code = err.payload.get("code") if isinstance(err.payload, dict) else None
                # 20009 = "user account does not exist" on the wrong backend shard
                if code == 20009 and attempt < LOGIN_RETRIES:
                    _LOGGER.debug("UCSP login hit inconsistent backend (20009), retrying")
                    await self._drop_connections()
                    continue
                raise
            except (aiohttp.ClientError, OSError) as err:
                last_error = err
                if attempt < LOGIN_RETRIES:
                    await self._drop_connections()
                    continue
                raise UcspApiError(0, {"message": str(err)}) from err
        if last_error is not None:
            raise last_error

    async def _async_refresh(self) -> bool:
        if not self.refresh_token or time.time() > self.refresh_expire_at - 300:
            return False
        status, data = await self._request(
            "POST",
            "/usercenter/v1/users/refresh-token",
            payload={"refreshToken": self.refresh_token},
            auth=False,
        )
        if not isinstance(data, dict) or data.get("code") != 0:
            return False
        body = data.get("data") or {}
        token_dto = body.get("tokenDTO") or body
        access = token_dto.get("accessToken") or {}
        refresh = token_dto.get("refreshToken") or {}
        if not access.get("token"):
            return False
        self.access_token = access["token"]
        self.access_expire_at = int(access.get("expireAt") or 0)
        if refresh.get("token"):
            self.refresh_token = refresh["token"]
            self.refresh_expire_at = int(refresh.get("expireAt") or 0)
        _LOGGER.debug("UCSP token refreshed")
        return True

    async def async_ensure_token(self) -> None:
        if self.access_token and time.time() < self.access_expire_at - 300:
            return
        if self.refresh_token and await self._async_refresh():
            return
        await self.async_login()

    # -- HTTP core -----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        auth: bool = True,
        _retried: bool = False,
    ) -> tuple[int, Any]:
        if auth:
            await self.async_ensure_token()
        url = self.base_url + path
        _LOGGER.debug("UCSP %s %s auth=%s", method, path, auth)
        try:
            async with self.session.request(
                method,
                url,
                json=payload,
                headers=self._headers(auth=auth),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except (aiohttp.ClientError, ValueError):
                    data = {"code": resp.status, "message": await resp.text()}
        except (aiohttp.ClientError, OSError) as err:
            raise UcspApiError(0, {"message": str(err)}) from err

        code = data.get("code") if isinstance(data, dict) else None
        # Sporadic 401/10001 or expired token -> re-login once and retry
        if auth and not _retried and (resp.status == 401 or code in (10001, 401)):
            _LOGGER.debug("UCSP %s %s returned auth error, re-login and retry", method, path)
            self.access_token = None
            await self.async_login()
            return await self._request(method, path, payload=payload, auth=auth, _retried=True)
        # Wrong backend shard -> fresh connection (new DNS round-robin) and retry
        if auth and not _retried and code == 20009:
            await self._drop_connections()
            return await self._request(method, path, payload=payload, auth=auth, _retried=True)
        if resp.status >= 400 or (code not in (None, 0)):
            raise UcspApiError(resp.status, data)
        return resp.status, data

    # -- API endpoints -------------------------------------------------------

    async def get_devices(self, product_key: str) -> list[dict[str, Any]]:
        _, data = await self._request(
            "GET", f"/platform/v1/device-relation/user/devices?productKey={product_key}"
        )
        return _require_list(data)

    async def get_shadow(self, product_key: str, device_id: str) -> dict[str, Any]:
        _, data = await self._request(
            "GET", f"/device-shadow/v1/device/shadow?deviceId={device_id}&productKey={product_key}"
        )
        return _require_dict(data)

    async def get_onoffline(self, product_key: str, device_id: str) -> list[dict[str, Any]]:
        _, data = await self._request(
            "GET",
            f"/device-manager/v1/device/onoffline?deviceIds={device_id}&productKey={product_key}",
        )
        return _require_list(data)

    async def get_feeding_records_today(
        self, product_key: str, device_id: str, start_ms: int, end_ms: int
    ) -> dict[str, Any]:
        _, data = await self._request(
            "GET",
            f"/feeder/v1/feeding-records/today?productKey={product_key}&deviceId={device_id}"
            f"&startTime={start_ms}&endTime={end_ms}",
        )
        return _require_dict(data)

    async def get_feeding_records(
        self, product_key: str, device_id: str, start_ms: int, end_ms: int, size: int = 20
    ) -> list[dict[str, Any]]:
        _, data = await self._request(
            "GET",
            f"/feeder/v1/feeding-records?productKey={product_key}&deviceId={device_id}"
            f"&startTime={start_ms}&endTime={end_ms}&pageNum=1&pageSize={size}",
        )
        body = data.get("data") if isinstance(data, dict) else None
        records = body.get("records") if isinstance(body, dict) else None
        return [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []

    async def get_plate_statistics(
        self, product_key: str, device_id: str, start_ms: int, end_ms: int
    ) -> dict[str, Any]:
        _, data = await self._request(
            "GET",
            f"/feeder/v1/feeding-records/plate/statistics?productKey={product_key}"
            f"&deviceId={device_id}&startTime={start_ms}&endTime={end_ms}",
        )
        return _require_dict(data)

    async def get_mqtt_credentials(self) -> dict[str, Any]:
        _, data = await self._request("GET", "/device-transport/mqtt/v1/login")
        body = _require_dict(data)
        if not body.get("mqttUrl") or not body.get("username") or not body.get("password"):
            raise UcspApiError(200, data)
        return body


def _require_dict(payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise UcspApiError(200, payload)
    return data


def _require_list(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise UcspApiError(200, payload)
    return data
