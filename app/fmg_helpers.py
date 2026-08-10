"""Shared FortiManager client factory.

Import this instead of duplicating _make_client() in every blueprint::

    from app.fmg_helpers import make_client

    with make_client() as client:
        devices = client.get_devices("root")
"""

from app.fmg_client import FMGClient
from app.config import Config


def make_client() -> FMGClient:
    """Return a configured FMGClient for the primary FortiManager host.

    Uses bearer token auth when FMG_API_TOKEN is set; falls back to
    username/password otherwise.
    """
    return FMGClient(
        host=Config.FMG_PRIMARY_HOST,
        username=Config.FMG_USERNAME,
        password=Config.FMG_PASSWORD,
        token=Config.FMG_API_TOKEN,
        verify_ssl=Config.FMG_VERIFY_SSL,
        timeout=Config.FMG_TIMEOUT,
    )
