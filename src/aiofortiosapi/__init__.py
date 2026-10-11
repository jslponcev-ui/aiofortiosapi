"""aiofortiosapi — async FortiOS REST API client for Home Assistant."""

from .client import FortiOSClient
from .const import DEFAULT_ONLINE_THRESHOLD
from .exceptions import (
    FortiOSAuthenticationError,
    FortiOSConnectionError,
    FortiOSError,
    FortiOSNotFoundError,
    FortiOSResponseError,
)
from .models import DetectedDevice, MasterMacDevice, ResourceUsage, SystemStatus

__version__ = "0.1.0"

__all__ = [
    "FortiOSClient",
    # exceptions
    "FortiOSError",
    "FortiOSConnectionError",
    "FortiOSAuthenticationError",
    "FortiOSNotFoundError",
    "FortiOSResponseError",
    # models
    "SystemStatus",
    "ResourceUsage",
    "DetectedDevice",
    "MasterMacDevice",
    # constants
    "DEFAULT_ONLINE_THRESHOLD",
    "__version__",
]
