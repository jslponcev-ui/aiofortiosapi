# aiofortiosapi

Async Python client for the FortiOS REST API, built for the [Home Assistant](https://www.home-assistant.io/) `fortios` integration.

> **Community project** — not affiliated with or supported by Fortinet TAC.

## Install

```bash
pip install aiofortiosapi
```

## Usage

The library requires an injected `aiohttp.ClientSession` — Home Assistant provides one via
`async_get_clientsession(hass)`. You own the session lifecycle; this library never creates or closes it.

```python
import asyncio
import aiohttp
from aiofortiosapi import FortiOSClient, FortiOSAuthenticationError, FortiOSConnectionError

async def main() -> None:
    session = aiohttp.ClientSession()
    try:
        client = FortiOSClient(
            host="192.168.1.1",
            token="your-rest-api-token",
            session=session,
            verify_ssl=False,   # set True in production with a valid cert
        )
        status = await client.get_system_status()
        print(status.hostname, status.version)

        usage = await client.get_resource_usage()
        print(f"CPU {usage.cpu_percent}%  MEM {usage.memory_percent}%")

        devices = await client.get_detected_devices()
        for d in devices:
            print(d.mac, d.hostname, d.ip, "online" if d.is_online else "offline")
    except FortiOSAuthenticationError:
        print("Bad token — re-enter credentials")
    except FortiOSConnectionError:
        print("Cannot reach the FortiGate — check host/port")
    finally:
        await session.close()

asyncio.run(main())
```

## Generating a FortiOS REST API token

1. In the FortiGate GUI go to **System → Administrators → Create New → REST API Admin**.
2. Set a **Trusted Host** (the IP of your Home Assistant instance) to restrict token use.
3. Assign a read-only profile (`prof_admin` or custom).
4. Copy the generated token — it is shown only once.

The library uses `Authorization: Bearer <token>` (not the legacy `?access_token=` query string).

### Device online state

`DetectedDevice.is_online` uses the flag reported by FortiOS. On firmware that
omits the field, it falls back to deriving online state from `last_seen`
freshness: a device counts as online when seen within
`DEFAULT_ONLINE_THRESHOLD` seconds (300). Tune the fallback per client:

```python
client = FortiOSClient(..., device_online_threshold=600)
```

## Scope

### Master-MAC clients

`await client.get_master_mac_devices()` uses the legacy Home Assistant
`format=master_mac|hostname|is_online` monitor query and returns typed
`MasterMacDevice` objects. Their `mac` field comes only from `master_mac` and is
uppercased; it is never replaced with the separate `mac` field. Missing online
flags mean offline, without the `DetectedDevice` freshness fallback. Invalid
response lists raise `FortiOSResponseError`, while an actual empty list returns
an empty scan. The caller continues to own the HTTP session.

`aiofortiosapi` is intentionally minimal:

- **Typed monitor methods** (`get_system_status`, `get_resource_usage`,
  `get_detected_devices`, `get_master_mac_devices`) for Home Assistant integrations.
- A generic `get(path)` for any other endpoint that returns the raw JSON envelope.
- **No** config-write, CMDB, file upload, SSH fallback, or CLI helpers.
- **No** session/cookie login flow — Bearer token only.

This keeps the dependency tree small (runtime dep: `aiohttp` only) and passes HA integration
quality review requirements.

## Exception hierarchy

| Exception | When raised | HA mapping |
|---|---|---|
| `FortiOSConnectionError` | Transport/timeout failure | `ConfigEntryNotReady` |
| `FortiOSAuthenticationError` | HTTP 401 / 403 | `ConfigEntryAuthFailed` |
| `FortiOSNotFoundError` | HTTP 404 | log / inspect |
| `FortiOSResponseError` | Bad JSON, 5xx, other 4xx | log / raise |

## License

MIT
