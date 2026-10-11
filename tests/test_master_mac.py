"""Regression tests for the legacy master-MAC monitor query."""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest
from aresponses import ResponsesMockServer

from aiofortiosapi import FortiOSAuthenticationError, FortiOSResponseError, MasterMacDevice
from aiofortiosapi.const import EP_DETECTED_DEVICES, EP_SYSTEM_STATUS

from .conftest import FAKE_HOST, FAKE_TOKEN, json_response, make_client


@pytest.mark.asyncio
async def test_master_mac_query(aresponses: ResponsesMockServer) -> None:
    """Preserve master identity and online flags across one authenticated query."""
    received: list[tuple[str, str, str]] = []

    async def capture(request: aiohttp.web.Request) -> aiohttp.web.Response:
        received.append(
            (request.query["filter"], request.query["vdom"], request.headers["Authorization"])
        )
        return json_response(
            {
                "results": [
                    {
                        "master_mac": "aa:bb:cc:dd:ee:ff",
                        "mac": "11:22:33:44:55:66",
                        "hostname": "phone",
                        "is_online": True,
                    },
                    {"master_mac": "22:33:44:55:66:77", "hostname": 1, "is_online": False},
                    {"master_mac": "33:44:55:66:77:88", "last_seen": 9999999999},
                    {"mac": "44:55:66:77:88:99", "is_online": True},
                    {"master_mac": ""},
                    {"master_mac": 123},
                ]
            }
        )

    aresponses.add(FAKE_HOST, f"/{EP_DETECTED_DEVICES}", "GET", capture)
    async with aiohttp.ClientSession() as session:
        devices = await make_client(session, vdom="root").get_master_mac_devices()
        assert not session.closed
    assert received == [("format=master_mac|hostname|is_online", "root", f"Bearer {FAKE_TOKEN}")]
    assert devices == [
        MasterMacDevice("AA:BB:CC:DD:EE:FF", "phone", True),
        MasterMacDevice("22:33:44:55:66:77", None, False),
        MasterMacDevice("33:44:55:66:77:88", None, False),
    ]


@pytest.mark.asyncio
async def test_master_mac_empty_scan(aresponses: ResponsesMockServer) -> None:
    """An actual empty list is a successful scan."""
    aresponses.add(FAKE_HOST, f"/{EP_DETECTED_DEVICES}", "GET", json_response({"results": []}))
    async with aiohttp.ClientSession() as session:
        assert await make_client(session).get_master_mac_devices() == []


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"status": "error"}, {"results": None}, {"results": {}}, {"results": [None]}],
)
@pytest.mark.asyncio
async def test_master_mac_invalid_scan(aresponses: ResponsesMockServer, payload: Any) -> None:
    """Malformed responses must not turn online clients into an empty scan."""
    aresponses.add(FAKE_HOST, f"/{EP_DETECTED_DEVICES}", "GET", json_response(payload))
    async with aiohttp.ClientSession() as session:
        with pytest.raises(FortiOSResponseError):
            await make_client(session).get_master_mac_devices()


@pytest.mark.parametrize("endpoint", [EP_DETECTED_DEVICES, EP_SYSTEM_STATUS])
@pytest.mark.parametrize("http_status", [401, 403])
@pytest.mark.asyncio
async def test_embedded_authentication_failure(
    aresponses: ResponsesMockServer, endpoint: str, http_status: int
) -> None:
    """HTTP-success error envelopes retain the authentication error category."""
    aresponses.add(
        FAKE_HOST,
        f"/{endpoint}",
        "GET",
        json_response({"status": "error", "http_status": http_status}),
    )
    async with aiohttp.ClientSession() as session:
        client = make_client(session)
        methods = {
            EP_DETECTED_DEVICES: client.get_master_mac_devices,
            EP_SYSTEM_STATUS: client.get_system_status,
        }
        with pytest.raises(FortiOSAuthenticationError):
            await methods[endpoint]()


@pytest.mark.parametrize("payload", [None, [], {"status": "error"}])
@pytest.mark.asyncio
async def test_invalid_system_envelope(aresponses: ResponsesMockServer, payload: Any) -> None:
    """The typed status method rejects envelopes the Core adapter previously rejected."""
    aresponses.add(FAKE_HOST, f"/{EP_SYSTEM_STATUS}", "GET", json_response(payload))
    async with aiohttp.ClientSession() as session:
        with pytest.raises(FortiOSResponseError):
            await make_client(session).get_system_status()
