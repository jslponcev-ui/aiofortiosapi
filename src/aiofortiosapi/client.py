"""FortiOSClient — async HTTP client for the FortiOS REST API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

from .const import (
    DEFAULT_ONLINE_THRESHOLD,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    EP_DETECTED_DEVICES,
    EP_RESOURCE_USAGE,
    EP_SYSTEM_STATUS,
)
from .exceptions import (
    FortiOSAuthenticationError,
    FortiOSConnectionError,
    FortiOSNotFoundError,
    FortiOSResponseError,
)
from .models import DetectedDevice, MasterMacDevice, ResourceUsage, SystemStatus

_LOGGER = logging.getLogger(__name__)

# FortiOS error envelopes carry the useful string under different keys
# depending on firmware and failure mode (e.g. bad token, trusted-host
# violation, CMDB error).  Checked in priority order.
_ERROR_DETAIL_KEYS: tuple[str, ...] = (
    "cli_error",
    "message",
    "error",
    "http_message",
)
_ERROR_DETAIL_MAX_CHARS = 200


def _validate_response(raw: Any) -> dict[str, Any]:
    """Reject failed envelopes even when the HTTP response succeeds."""
    if isinstance(raw, dict) and raw.get("http_status") in (401, 403):
        raise FortiOSAuthenticationError("FortiOS authentication failed")
    if not isinstance(raw, dict) or raw.get("status") == "error":
        raise FortiOSResponseError("Invalid FortiOS response")
    return raw


def _extract_error_detail(body_text: str) -> str:
    """Return the most useful human-readable string from an error body.

    Falls back to the raw body (truncated) when the payload is not JSON or
    carries none of the known detail keys.
    """
    try:
        parsed: Any = json.loads(body_text)
    except ValueError:
        return body_text.strip()[:_ERROR_DETAIL_MAX_CHARS]
    if isinstance(parsed, dict):
        for key in _ERROR_DETAIL_KEYS:
            if value := parsed.get(key):
                return str(value)[:_ERROR_DETAIL_MAX_CHARS]
    return body_text.strip()[:_ERROR_DETAIL_MAX_CHARS]


class FortiOSClient:
    """Async client for the FortiOS REST API.

    The caller (e.g. Home Assistant) owns the aiohttp.ClientSession lifecycle.
    This class never creates or closes the session it receives.
    """

    def __init__(
        self,
        host: str,
        token: str,
        *,
        session: aiohttp.ClientSession,
        port: int = DEFAULT_PORT,
        verify_ssl: bool = True,
        vdom: str | None = None,
        request_timeout: float = DEFAULT_TIMEOUT,
        device_online_threshold: float = DEFAULT_ONLINE_THRESHOLD,
    ) -> None:
        self._host = host
        self._token = token
        self._session = session
        self._port = port
        self._verify_ssl = verify_ssl
        self._vdom = vdom
        self._request_timeout = request_timeout
        self._device_online_threshold = device_online_threshold

    # ------------------------------------------------------------------
    # Internal request plumbing
    # ------------------------------------------------------------------

    def _build_url(self, path: str) -> str:
        return f"https://{self._host}:{self._port}/{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Single choke-point for all HTTP.

        Builds URL, injects auth header, merges vdom param, applies timeout,
        and maps all error conditions to the exception hierarchy.
        """
        url = self._build_url(path)
        merged_params: dict[str, Any] = dict(params) if params else {}
        if self._vdom is not None:
            merged_params["vdom"] = self._vdom

        headers = {"Authorization": f"Bearer {self._token}"}
        ssl: bool = self._verify_ssl

        _LOGGER.debug("FortiOS %s %s params=%s", method, url, list(merged_params.keys()))

        try:
            async with (
                asyncio.timeout(self._request_timeout),
                self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=merged_params or None,
                    ssl=ssl,
                ) as response,
            ):
                if response.status in (401, 403):
                    raise FortiOSAuthenticationError(
                        f"Authentication failed (HTTP {response.status})"
                    )
                if response.status == 404:
                    raise FortiOSNotFoundError(f"Endpoint not found: {path}")

                if response.status >= 400:
                    detail = _extract_error_detail(await response.text())
                    raise FortiOSResponseError(f"HTTP {response.status} from {url}: {detail}")

                try:
                    data: dict[str, Any] = await response.json()
                except (aiohttp.ClientError, ValueError) as exc:
                    raise FortiOSResponseError(f"Invalid JSON from {url}") from exc

                return data
        except TimeoutError as exc:
            raise FortiOSConnectionError(f"Request timed out: {url}") from exc
        except aiohttp.ClientError as exc:
            raise FortiOSConnectionError(f"Connection error: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform a raw GET and return the parsed JSON envelope.

        Escape hatch for endpoints without a dedicated typed method: returns
        the full ``{"results": ..., "status": ..., ...}`` dict so the caller
        can extract whatever it needs.  Inherits Bearer auth, timeout, and
        the typed exception hierarchy from ``_request``.
        """
        return await self._request("GET", path, params=params)

    async def get_system_status(self) -> SystemStatus:
        """Return basic device identity: hostname, version, serial, model."""
        raw = _validate_response(await self._request("GET", EP_SYSTEM_STATUS))
        return SystemStatus.from_api(raw)

    async def get_resource_usage(self) -> ResourceUsage:
        """Return CPU, memory, session count, and uptime."""
        raw = await self._request("GET", EP_RESOURCE_USAGE)
        return ResourceUsage.from_api(raw)

    async def get_detected_devices(self) -> list[DetectedDevice]:
        """Return devices seen on the network (for device_tracker).

        ``is_online`` uses the API-reported flag; when a firmware omits it,
        it is derived from ``last_seen`` freshness using the client's
        ``device_online_threshold``.
        """
        raw = await self._request("GET", EP_DETECTED_DEVICES)
        return DetectedDevice.list_from_api(
            raw,
            now=time.time(),
            online_threshold=self._device_online_threshold,
        )

    async def get_master_mac_devices(self) -> list[MasterMacDevice]:
        """Read master-MAC clients using the legacy Home Assistant query.

        Preserve ``master_mac`` identity and the API's online flag. Missing
        flags mean offline, without the detected-device freshness fallback.
        Malformed lists raise instead of appearing as a successful empty scan.
        """
        raw = _validate_response(
            await self._request(
                "GET",
                EP_DETECTED_DEVICES,
                params={"filter": "format=master_mac|hostname|is_online"},
            )
        )
        return MasterMacDevice.list_from_api(raw)

    async def async_validate(self) -> SystemStatus:
        """Validate credentials cheaply by calling get_system_status.

        Raises FortiOSAuthenticationError for bad tokens, or
        FortiOSConnectionError for unreachable devices.  Used by the HA
        config_flow to confirm a token works before saving the entry.
        """
        return await self.get_system_status()
