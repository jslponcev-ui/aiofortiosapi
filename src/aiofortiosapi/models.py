"""Frozen dataclasses for parsed FortiOS API responses.

FortiOS wraps all payloads as:
  {"results": <dict|list>, "status": "success", "version": "v7.6.1",
   "serial": "FGT60F...", "vdom": "root", ...}

Most models accept the full envelope dict and parse defensively: missing keys,
null values, and unexpected shapes fall back to safe defaults. MasterMacDevice
validates client lists strictly so malformed responses do not look like empty scans.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .const import DEFAULT_ONLINE_THRESHOLD
from .exceptions import FortiOSResponseError


def _first(lst: Any, key: str, default: Any = None) -> Any:
    """Extract key from the first element of a list of dicts, or default.

    FortiOS returns cpu/memory/session stats as single-element lists of
    objects; tolerate scalar lists, empty lists, and non-list values.
    """
    if isinstance(lst, list) and lst and isinstance(lst[0], dict):
        return lst[0].get(key, default)
    return default


def _as_str(value: Any) -> str:
    """Coerce an optional API string field to str; null and non-str become ''."""
    return value if isinstance(value, str) else ""


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class SystemStatus:
    """Parsed response from monitor/system/status."""

    hostname: str
    version: str
    serial: str
    model: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> SystemStatus:
        results: Any = raw.get("results")
        if not isinstance(results, dict):
            results = {}
        serial = _as_str(raw.get("serial") or results.get("Serial-Number"))
        version = _as_str(raw.get("version") or results.get("Version"))
        hostname = _as_str(results.get("Hostname") or results.get("hostname"))
        # Model name: use FGVM/FGT prefix from serial when not explicit
        model = _as_str(results.get("Model-Name") or results.get("model") or serial[:6])
        return cls(hostname=hostname, version=version, serial=serial, model=model)


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    """Parsed response from monitor/system/resource/usage.

    Real FortiOS (verified on v8.0.1) returns each metric as a single-element
    list with a ``current`` value, and splits sessions/setup-rate into IPv4
    and IPv6 buckets:

    ``{"cpu": [{"current": 0}], "mem": [{"current": 34}],
       "session": [{"current": 279}], "session6": [{"current": 57}],
       "setuprate": [{"current": 7}], "setuprate6": [{"current": 0}], ...}``

    ``sessions`` and ``session_setup_rate`` are the sum of the v4 + v6
    buckets.
    """

    cpu_percent: float
    memory_percent: float
    sessions: int  # session + session6
    session_setup_rate: int  # setuprate + setuprate6

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> ResourceUsage:
        results: Any = raw.get("results")
        if not isinstance(results, dict):
            results = {}
        return cls(
            cpu_percent=_to_float(_first(results.get("cpu"), "current")),
            memory_percent=_to_float(_first(results.get("mem"), "current")),
            sessions=(
                _to_int(_first(results.get("session"), "current"))
                + _to_int(_first(results.get("session6"), "current"))
            ),
            session_setup_rate=(
                _to_int(_first(results.get("setuprate"), "current"))
                + _to_int(_first(results.get("setuprate6"), "current"))
            ),
        )


@dataclass(frozen=True, slots=True)
class MasterMacDevice:
    """A client identified by the master MAC from the filtered monitor query."""

    mac: str
    hostname: str | None
    is_online: bool

    @classmethod
    def list_from_api(cls, raw: dict[str, Any]) -> list[MasterMacDevice]:
        """Parse a strict client list without substituting the ``mac`` field."""
        results = raw.get("results")
        if not isinstance(results, list):
            raise FortiOSResponseError("Invalid FortiOS device list")
        devices = []
        for entry in results:
            if not isinstance(entry, dict):
                raise FortiOSResponseError("Invalid FortiOS device")
            mac = entry.get("master_mac")
            if not isinstance(mac, str) or not mac:
                continue
            hostname = entry.get("hostname")
            devices.append(
                cls(
                    mac=mac.upper(),
                    hostname=hostname if isinstance(hostname, str) else None,
                    is_online=bool(entry.get("is_online", False)),
                )
            )
        return devices


@dataclass(frozen=True, slots=True)
class DetectedDevice:
    """A single device from monitor/user/device/query.

    Consumed by the Home Assistant device_tracker platform.

    FortiOS reports an ``is_online`` flag for detected devices, which is used
    verbatim.  When a firmware omits the field, ``is_online`` is derived from
    ``last_seen`` freshness instead: True when the device was seen within
    ``online_threshold`` seconds of the reference time (default
    ``DEFAULT_ONLINE_THRESHOLD``, mirroring HA's consider_home behaviour).
    """

    mac: str
    hostname: str
    ip: str  # primary IP; may be empty string if unknown
    os_name: str
    interface: str
    last_seen: int  # unix timestamp; 0 when not available
    is_online: bool  # API-reported; derived from last_seen when absent

    @classmethod
    def _from_entry(
        cls,
        entry: dict[str, Any],
        *,
        now: float,
        online_threshold: float,
    ) -> DetectedDevice:
        # IPs may come as a list, a plain string, or null
        ips: Any = entry.get("ipv4_address", entry.get("ip"))
        if isinstance(ips, list):
            ip = _as_str(ips[0]) if ips else ""
        elif isinstance(ips, str):
            ip = ips
        else:
            ip = ""

        last_seen = _to_int(entry.get("last_seen"))

        api_flag = entry.get("is_online")
        if api_flag is None:
            # Field absent on this firmware: fall back to freshness.
            is_online = last_seen > 0 and (now - float(last_seen)) <= online_threshold
        else:
            is_online = bool(api_flag)

        return cls(
            mac=_as_str(entry.get("mac")),
            hostname=_as_str(entry.get("hostname") or entry.get("name")),
            ip=ip,
            os_name=_as_str(entry.get("os_name") or entry.get("os")),
            interface=_as_str(entry.get("interface")),
            last_seen=last_seen,
            is_online=is_online,
        )

    @classmethod
    def list_from_api(
        cls,
        raw: dict[str, Any],
        *,
        now: float | None = None,
        online_threshold: float = DEFAULT_ONLINE_THRESHOLD,
    ) -> list[DetectedDevice]:
        """Parse the device list envelope.

        ``now`` and ``online_threshold`` only affect the freshness-based
        fallback used when an entry carries no ``is_online`` field.  ``now``
        defaults to the current wall clock; pass it explicitly in tests.
        """
        results = raw.get("results", [])
        if not isinstance(results, list):
            return []
        reference_now = time.time() if now is None else now
        return [
            cls._from_entry(e, now=reference_now, online_threshold=online_threshold)
            for e in results
            if isinstance(e, dict)
        ]
