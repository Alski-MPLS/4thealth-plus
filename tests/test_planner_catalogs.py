"""Tests for app.planner.catalogs — address/service catalog building."""
from unittest.mock import MagicMock

from app.planner.catalogs import (
    build_catalogs,
    get_device_policies,
    package_targets_device,
    summarise_policy,
)


def test_package_targets_device_scoped_match():
    pkg = {"scope member": [{"name": "FW-A"}, {"name": "FW-B"}]}
    assert package_targets_device(pkg, "FW-A") is True
    assert package_targets_device(pkg, "FW-C") is False


def test_package_targets_device_unscoped_applies_to_all():
    assert package_targets_device({}, "FW-A") is True
    assert package_targets_device({"scope member": []}, "FW-A") is True


def test_build_catalogs_indexes_per_adom_and_global_objects():
    client = MagicMock()
    client.get_address_objects.side_effect = lambda adom: (
        [{"name": "H_10.1.1.1", "type": "ipmask", "subnet": "10.1.1.1/32"}]
        if adom == "OT-ADOM" else [{"name": "H_GLOBAL", "type": "ipmask", "subnet": "10.9.9.9/32"}]
    )
    client.get_address_groups.side_effect = lambda adom: []
    client.get_service_objects.return_value = [
        {"name": "SVC_TCP_443", "protocol": "TCP/UDP/SCTP", "tcp-portrange": "443"}
    ]
    client.get_service_groups.return_value = []

    addr_catalog, svc_catalog = build_catalogs(client, "OT-ADOM")

    assert addr_catalog.exact_match_name("10.1.1.1/32") == "H_10.1.1.1"
    assert addr_catalog.exact_match_name("10.9.9.9/32") == "H_GLOBAL"
    from app.planner.matching import PortRange
    assert svc_catalog.exact_match_name([PortRange("tcp", 443, 443)]) == "SVC_TCP_443"


def test_build_catalogs_degrades_gracefully_if_global_fetch_fails():
    client = MagicMock()
    def addr_objects(adom):
        if adom == "global":
            raise RuntimeError("global ADOM not accessible")
        return []
    client.get_address_objects.side_effect = addr_objects
    client.get_address_groups.side_effect = lambda adom: (
        (_ for _ in ()).throw(RuntimeError("boom")) if adom == "global" else []
    )
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []

    addr_catalog, svc_catalog = build_catalogs(client, "OT-ADOM")
    assert addr_catalog.exact_match_name("10.1.1.1/32") is None  # no crash, just empty


def test_get_device_policies_returns_none_on_fetch_failure():
    client = MagicMock()
    def get_policies(adom, pkg):
        if pkg == "bad-pkg":
            raise RuntimeError("fetch failed")
        return [{"policyid": 1}]
    client.get_policies.side_effect = get_policies

    result = get_device_policies(client, "OT-ADOM", ["good-pkg", "bad-pkg"])
    assert result["good-pkg"] == [{"policyid": 1}]
    assert result["bad-pkg"] is None


def test_summarise_policy_shape():
    pol = {
        "policyid": 42, "name": "TEST_RULE", "status": "enable",
        "srcaddr": ["H_A"], "srcintf": ["port1"],
        "dstaddr": ["H_B"], "dstintf": ["port2"],
        "service": ["SVC_TCP_443"], "action": 1, "logtraffic": 2,
        "nat": "disable", "schedule": ["always"],
        "srcaddr-negate": "disable", "dstaddr-negate": "disable",
        "comments": "test", "uuid": "abc-123",
    }
    summary = summarise_policy(pol, "TestPkg")
    assert summary["package"] == "TestPkg"
    assert summary["policy_id"] == 42
    assert summary["name"] == "TEST_RULE"
    assert summary["source"] == ["H_A"]
    assert summary["destination"] == ["H_B"]
    assert summary["service"] == ["SVC_TCP_443"]
    assert summary["action"] == "accept"
    assert summary["log"] == "all"
    assert summary["srcaddr_negate"] is False
