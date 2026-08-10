import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-ci")
os.environ.setdefault("FMG_PRIMARY_HOST", "127.0.0.1")

from unittest.mock import patch, MagicMock
import pytest


def test_get_cached_devices_returns_none_when_empty():
    import importlib
    import app.pending_status_cache as mod
    importlib.reload(mod)  # reset module state
    assert mod.get_cached_devices("MyADOM") is None


def test_get_cached_devices_returns_snapshot_after_refresh():
    import importlib
    import app.pending_status_cache as mod
    importlib.reload(mod)

    fake_devices = [{"name": "FW1", "ip": "10.0.0.1", "pkg_status": "modified"}]

    with mod._lock:
        mod._cache["MyADOM"] = {
            "devices": fake_devices,
            "last_updated": "2026-07-17T01:00:00",
        }

    result = mod.get_cached_devices("MyADOM")
    assert result == fake_devices


def test_get_cached_devices_returns_copy_not_reference():
    import importlib
    import app.pending_status_cache as mod
    importlib.reload(mod)

    devices = [{"name": "FW1"}]
    with mod._lock:
        mod._cache["ADOM1"] = {"devices": devices, "last_updated": "2026-07-17T01:00:00"}

    result = mod.get_cached_devices("ADOM1")
    result.append({"name": "INJECTED"})
    assert len(mod._cache["ADOM1"]["devices"]) == 1


def test_get_cache_status_initial():
    import importlib
    import app.pending_status_cache as mod
    importlib.reload(mod)
    status = mod.get_cache_status()
    assert status["status"] == "pending"
    assert status["adoms_cached"] == 0
