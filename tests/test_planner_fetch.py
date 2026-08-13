"""Tests for app.planner.fetch — device snapshot and zone verdict fetching."""
from unittest.mock import MagicMock, patch

import pytest

from app.fmg_client import FMGError
from app.planner.fetch import (
    DeviceSnapshot,
    fetch_device_snapshot,
    fetch_zone_domains,
    fetch_zone_verdict,
    resolve_interfaces,
)
from app.planner.models import PlannerDataError
from app.planner.zone_adapter import ZoneDBAdapter


def _client_stub(**overrides):
    client = MagicMock()
    client.get_devices.return_value = [{"name": "FW-A"}]
    client.get_policy_packages.return_value = [{"name": "OT-Pkg", "scope member": []}]
    client.get_address_objects.return_value = []
    client.get_address_groups.return_value = []
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []
    client.get_policies.return_value = []
    client.get_device_interfaces.return_value = [
        {"name": "port1", "ip": "10.1.1.1 255.255.255.0"},
    ]
    client.get_device_routes.return_value = [
        {"dst": "0.0.0.0 0.0.0.0", "device": "port2", "status": "enable"},
    ]
    for k, v in overrides.items():
        getattr(client, k).return_value = v
    return client


def test_fetch_device_snapshot_happy_path():
    client = _client_stub()
    snapshot = fetch_device_snapshot(client, "OT-ADOM", "FW-A")
    assert isinstance(snapshot, DeviceSnapshot)
    assert snapshot.device == "FW-A"
    assert snapshot.degraded is False
    assert snapshot.packages == ["OT-Pkg"]
    assert len(snapshot.interfaces) == 1
    assert len(snapshot.routing_table) == 1


def test_fetch_device_snapshot_unknown_device_raises():
    client = _client_stub()
    with pytest.raises(PlannerDataError) as exc_info:
        fetch_device_snapshot(client, "OT-ADOM", "NOT-A-DEVICE")
    assert exc_info.value.source == "fortimanager"


def test_fetch_device_snapshot_degrades_on_package_fetch_failure():
    client = _client_stub()
    client.get_policies.side_effect = RuntimeError("boom")
    snapshot = fetch_device_snapshot(client, "OT-ADOM", "FW-A")
    assert snapshot.degraded is True
    assert "OT-Pkg" in snapshot.failures[0]


def test_fetch_zone_verdict_returns_verdict_shape():
    zc = MagicMock(spec=ZoneDBAdapter)
    zc.query.return_value = [{
        "src": "10.0.0.5", "dst": "8.8.8.8", "service": "tcp/443",
        "verdict": "ALLOWED", "src_zones": ["DMZ"], "dst_zones": ["Internet"],
        "governing": [{"policy_set": "Corp"}], "all_policies": [],
    }]
    result = fetch_zone_verdict(zc, "10.0.0.5", "8.8.8.8", "tcp/443")
    assert result["verdict"] == "ALLOWED"
    assert result["src_zones"] == ["DMZ"]
    assert result["dst_zones"] == ["Internet"]
    assert result["notes"] == []


def test_fetch_zone_verdict_applies_internet_default_when_unresolved():
    zc = MagicMock(spec=ZoneDBAdapter)
    zc.query.return_value = [{
        "src": "1.2.3.4", "dst": "10.0.0.5", "service": "tcp/443",
        "verdict": "UNKNOWN", "src_zones": [], "dst_zones": ["DMZ"],
        "governing": [], "all_policies": [],
    }]
    zc.zones.return_value = {"zones": [{"name": "Internet", "domain": "Default"}]}
    with patch("app.zone_db.find_matching_policies", return_value=[]), \
         patch("app.zone_db.evaluate", return_value=("ALLOWED", [])):
        zc.policies.return_value = []
        result = fetch_zone_verdict(zc, "1.2.3.4", "10.0.0.5", "tcp/443")
    assert result["src_zones"] == ["Internet"]
    assert any("catch-all" in n for n in result["notes"])


def test_fetch_zone_domains_maps_names_to_domains():
    zc = MagicMock(spec=ZoneDBAdapter)
    zc.zones.return_value = {"zones": [
        {"name": "DMZ", "domain": "Default"},
        {"name": "OT-Plant1", "domain": "OT"},
    ]}
    result = fetch_zone_domains(zc)
    assert result == {"DMZ": "Default", "OT-Plant1": "OT"}


def test_resolve_interfaces_connected_subnet_match():
    snapshot = DeviceSnapshot(
        device="FW-A", adom="OT-ADOM", packages=[], policies_by_package={},
        addr_catalog=MagicMock(), svc_catalog=MagicMock(),
        interfaces=[{"name": "port1", "ip": "10.1.1.1 255.255.255.0"}],
        routing_table=[],
    )
    srcintf, dstintf, warnings = resolve_interfaces(snapshot, "10.1.1.50", "10.1.1.60")
    assert srcintf == "port1"
    assert dstintf == "port1"
    assert warnings == []


def test_resolve_interfaces_routing_table_fallback():
    snapshot = DeviceSnapshot(
        device="FW-A", adom="OT-ADOM", packages=[], policies_by_package={},
        addr_catalog=MagicMock(), svc_catalog=MagicMock(),
        interfaces=[{"name": "port1", "ip": "10.1.1.1 255.255.255.0"}],
        routing_table=[{"dst": "0.0.0.0 0.0.0.0", "device": "port2", "status": "enable"}],
    )
    srcintf, dstintf, warnings = resolve_interfaces(snapshot, "10.1.1.50", "8.8.8.8")
    assert srcintf == "port1"
    assert dstintf == "port2"
    assert any("routing table" in w for w in warnings)


def test_resolve_interfaces_unresolvable_warns():
    snapshot = DeviceSnapshot(
        device="FW-A", adom="OT-ADOM", packages=[], policies_by_package={},
        addr_catalog=MagicMock(), svc_catalog=MagicMock(),
        interfaces=[], routing_table=[],
    )
    srcintf, dstintf, warnings = resolve_interfaces(snapshot, "10.1.1.50", "8.8.8.8")
    assert srcintf == ""
    assert dstintf == ""
    assert len(warnings) == 2
