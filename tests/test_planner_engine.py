"""Tests for app.planner.engine.plan_change — the deterministic core."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.planner import standards
from app.planner.engine import plan_change
from app.planner.models import PlannerDataError, TargetFirewall
from app.planner.zone_adapter import ZoneDBAdapter

_REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _use_example_standards_files(monkeypatch):
    """plan_change() reads naming.yaml/review_requirements.yaml via
    standards.load_naming()/review_requirements() with no path override, so
    it always hits the real (gitignored, team-maintained) files. Point it at
    the committed .example.yaml templates instead so these tests are
    self-contained and don't depend on runtime config existing on disk."""
    monkeypatch.setattr(standards, "_NAMING_FILE", _REPO_ROOT / "naming.example.yaml")
    monkeypatch.setattr(
        standards, "_REVIEW_FILE", _REPO_ROOT / "review_requirements.example.yaml"
    )


def _zone_client(verdict="ALLOWED", src_zones=("DMZ",), dst_zones=("Internet",)):
    zc = MagicMock(spec=ZoneDBAdapter)
    zc.query.return_value = [{
        "src": "x", "dst": "y", "service": "z", "verdict": verdict,
        "src_zones": list(src_zones), "dst_zones": list(dst_zones),
        "governing": [{"policy_set": "Corp", "access_type": "allow all"}],
        "all_policies": [],
    }]
    zc.zones.return_value = {"zones": [
        {"name": "DMZ", "domain": "Default"},
        {"name": "Internet", "domain": "Default"},
    ]}
    zc.policies.return_value = []
    return zc


def _fmg_client_with_no_devices():
    client = MagicMock()
    client.get_devices.return_value = []
    return client


def test_plan_change_unknown_verdict_skips_firewall_analysis():
    zc = _zone_client(verdict="UNKNOWN", src_zones=(), dst_zones=())
    zc.zones.return_value = {"zones": []}  # no Internet zone → stays UNKNOWN
    plan = plan_change(
        src="1.2.3.4", dst="5.6.7.8", service="tcp/443",
        firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        zone_client=zc, fmg_client=MagicMock(),
    )
    assert plan.cli_status == "unknown_no_action"
    assert plan.firewalls[0].status == "no_action"


def test_plan_change_mixed_verdicts_raises():
    zc = MagicMock(spec=ZoneDBAdapter)

    def query_side_effect(src, dst, service, verbose=True):
        verdict = "ALLOWED" if dst == "5.6.7.8" else "BLOCKED"
        return [{
            "src": src, "dst": dst, "service": service, "verdict": verdict,
            "src_zones": ["DMZ"], "dst_zones": ["Internet"],
            "governing": [{"policy_set": "Corp", "access_type": "block all"}],
            "all_policies": [],
        }]
    zc.query.side_effect = query_side_effect
    zc.zones.return_value = {"zones": [{"name": "DMZ", "domain": "Default"},
                                        {"name": "Internet", "domain": "Default"}]}

    with pytest.raises(PlannerDataError) as exc_info:
        plan_change(
            src="1.2.3.4", dst="5.6.7.8, 9.9.9.9", service="tcp/443",
            firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
            zone_client=zc, fmg_client=MagicMock(),
        )
    assert exc_info.value.source == "request"


def test_plan_change_device_not_found_reports_error_status():
    zc = _zone_client()
    client = _fmg_client_with_no_devices()
    plan = plan_change(
        src="1.2.3.4", dst="5.6.7.8", service="tcp/443",
        firewalls=[TargetFirewall(device="FW-MISSING", adom="OT-ADOM")],
        zone_client=zc, fmg_client=client,
    )
    assert plan.firewalls[0].status == "not_found"
    assert plan.cli_status == "new_rule"  # not "already_covered" — device errored, not covered


def test_plan_change_already_covered_all_firewalls():
    zc = _zone_client()
    client = MagicMock()
    client.get_devices.return_value = [{"name": "FW-A"}]
    client.get_policy_packages.return_value = [{"name": "Pkg1", "scope member": []}]
    client.get_address_objects.return_value = []
    client.get_address_groups.return_value = []
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []
    client.get_device_interfaces.return_value = [{"name": "port1", "ip": "10.0.0.1 255.255.255.0"}]
    client.get_device_routes.return_value = []
    client.get_policies.return_value = [{
        "policyid": 5, "name": "EXISTING", "status": "enable", "action": 1,
        "srcaddr": ["all"], "dstaddr": ["all"], "service": ["ALL"],
        "srcintf": ["any"], "dstintf": ["any"], "schedule": ["always"],
    }]
    plan = plan_change(
        src="10.0.0.5", dst="10.0.0.6", service="tcp/443",
        firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        zone_client=zc, fmg_client=client,
    )
    assert plan.firewalls[0].status == "already_covered"
    assert plan.cli_status == "already_covered"


def test_plan_change_new_rule_generates_cli_and_naming():
    zc = _zone_client()
    client = MagicMock()
    client.get_devices.return_value = [{"name": "FW-A"}]
    client.get_policy_packages.return_value = [{"name": "Pkg1", "scope member": []}]
    client.get_address_objects.return_value = []
    client.get_address_groups.return_value = []
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []
    client.get_device_interfaces.return_value = [
        {"name": "port1", "ip": "10.0.0.1 255.255.255.0"},
        {"name": "port2", "ip": "192.168.1.1 255.255.255.0"},
    ]
    client.get_device_routes.return_value = []
    client.get_policies.return_value = []  # no existing rules

    plan = plan_change(
        src="10.0.0.5", dst="192.168.1.50", service="tcp/8443",
        ticket_id="CHG0001", firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        zone_client=zc, fmg_client=client,
    )
    fw = plan.firewalls[0]
    assert fw.status == "new_rule"
    assert fw.srcintf == "port1"
    assert fw.dstintf == "port2"
    assert "CHG0001" in fw.policy_cli
    assert plan.cli_status == "new_rule"
    assert plan.naming["objects"]  # at least the two address objects + one service


def test_group_blast_radius_uses_package_path_not_name():
    """_group_blast_radius must call get_policies() with the package's full
    path (folder-organized ADOMs), not just its bare name."""
    from app.planner.engine import _group_blast_radius
    from app.planner.fetch import DeviceSnapshot

    client = MagicMock()
    client.get_policy_packages.return_value = [
        {"name": "MyPackage", "path": "MyFolder/MyPackage", "scope member": []}
    ]
    client.get_policies.return_value = []

    addr_catalog = MagicMock()
    addr_catalog.groups_containing.return_value = set()
    snapshot = DeviceSnapshot(
        device="FW-A", adom="OT-ADOM", packages=[], policies_by_package={},
        addr_catalog=addr_catalog, svc_catalog=MagicMock(),
        interfaces=[], routing_table=[],
    )

    _group_blast_radius(client, snapshot, "SOME_GROUP", exclude=("other", 1))

    called_args = [c.args for c in client.get_policies.call_args_list]
    assert ("OT-ADOM", "MyFolder/MyPackage") in called_args
    assert ("OT-ADOM", "MyPackage") not in called_args


def test_plan_change_blocked_verdict_generates_exception_cli():
    """BLOCKED verdict → cli_status 'blocked_exception' and the generated
    CLI carries an EXCEPTION comment instead of the normal ticket comment —
    a high-consequence path that previously had zero test coverage."""
    zc = _zone_client(verdict="BLOCKED")
    client = MagicMock()
    client.get_devices.return_value = [{"name": "FW-A"}]
    client.get_policy_packages.return_value = [{"name": "Pkg1", "scope member": []}]
    client.get_address_objects.return_value = []
    client.get_address_groups.return_value = []
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []
    client.get_device_interfaces.return_value = [
        {"name": "port1", "ip": "10.0.0.1 255.255.255.0"},
        {"name": "port2", "ip": "192.168.1.1 255.255.255.0"},
    ]
    client.get_device_routes.return_value = []
    client.get_policies.return_value = []  # no existing rules

    plan = plan_change(
        src="10.0.0.5", dst="192.168.1.50", service="tcp/8443",
        ticket_id="CHG0001", firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        zone_client=zc, fmg_client=client,
    )
    fw = plan.firewalls[0]
    assert plan.cli_status == "blocked_exception"
    assert "EXCEPTION" in fw.policy_cli


def test_to_report_payload_has_expected_top_level_keys():
    from app.planner.engine import to_report_payload
    zc = _zone_client()
    client = MagicMock()
    client.get_devices.return_value = [{"name": "FW-A"}]
    client.get_policy_packages.return_value = []
    client.get_address_objects.return_value = []
    client.get_address_groups.return_value = []
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []
    client.get_device_interfaces.return_value = []
    client.get_device_routes.return_value = []

    plan = plan_change(
        src="10.0.0.5", dst="192.168.1.50", service="tcp/8443",
        firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        zone_client=zc, fmg_client=client,
    )
    payload = to_report_payload(plan)
    assert set(payload.keys()) == {
        "ticket_id", "request", "zone_verdict", "existing_rules",
        "naming", "logging", "approval", "recommendation", "cli",
    }


def test_plan_change_rejects_non_ip_src():
    with pytest.raises(PlannerDataError, match="plan_fqdn_change"):
        plan_change(
            src="not-an-ip.example.com", dst="10.0.0.6", service="tcp/443",
            firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        )


def test_plan_change_rejects_non_ip_dst():
    with pytest.raises(PlannerDataError, match="plan_fqdn_change"):
        plan_change(
            src="10.0.0.5", dst="*.vendor.com", service="tcp/443",
            firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        )


def _fqdn_entry(fqdn="new.vendor.com", ports=(443,), protocol="TCP"):
    from app.planner.models import FQDNEntry
    return FQDNEntry(
        fqdn=fqdn, is_wildcard=fqdn.startswith("*."), ports=list(ports),
        protocol=protocol, required=True, comment="",
    )


def test_plan_fqdn_change_proposes_objects_for_uncovered_entries():
    from app.planner.engine import plan_fqdn_change
    from app.planner.models import FQDNAllowlistRequest

    req = FQDNAllowlistRequest(
        vendor="Vendor Co", category="API", src_ip="10.0.0.5",
        ticket_id="CHG1", firewalls=["FW-A:OT-ADOM"],
        entries=[_fqdn_entry()],
    )

    fake_fmg = MagicMock()
    fake_fmg.get_devices.return_value = [{"name": "FW-A"}]
    fake_fmg.get_policy_packages.return_value = [{"name": "pkg1", "path": "pkg1"}]
    fake_fmg.get_policies.return_value = []
    fake_fmg.get_address_objects.return_value = []
    fake_fmg.get_address_groups.return_value = []
    fake_fmg.get_service_objects.return_value = []
    fake_fmg.get_service_groups.return_value = []
    fake_fmg.get_device_interfaces.return_value = []
    fake_fmg.get_device_routes.return_value = []

    fake_zc = MagicMock()
    fake_zc.query.return_value = [{
        "verdict": "ALLOWED", "src_zones": ["OT-LAN"], "dst_zones": ["Internet"],
        "governing": [], "all_policies": [],
    }]
    fake_zc.zones.return_value = {"zones": [], "total_subnets": 0}

    plan = plan_fqdn_change(req, fmg_client=fake_fmg, zone_client=fake_zc)

    assert len(plan.per_firewall) == 1
    fw = plan.per_firewall[0]
    assert fw.firewall == "FW-A"
    assert fw.adom == "OT-ADOM"
    assert fw.coverage == "new_rule"
    assert len(fw.proposed_objects) == 1
    assert fw.proposed_objects[0].name == "FQDN-new.vendor.com"
    assert fw.proposed_group.name == "GRP-Vendor-Co-API-DST"
    assert "SVC_TCP_443" in fw.proposed_policy["service"]


def test_plan_fqdn_change_invalid_firewall_spec_yields_error_plan():
    from app.planner.engine import plan_fqdn_change
    from app.planner.models import FQDNAllowlistRequest

    req = FQDNAllowlistRequest(
        vendor="V", category="C", src_ip="any", ticket_id="CHG1",
        firewalls=["not-a-valid-spec"], entries=[_fqdn_entry()],
    )
    plan = plan_fqdn_change(req, fmg_client=MagicMock(), zone_client=MagicMock())
    assert plan.per_firewall[0].verdict == "error"
    assert plan.per_firewall[0].degraded is True


def test_to_fqdn_report_payload_shape():
    from app.planner.engine import to_fqdn_report_payload
    from app.planner.models import FQDNChangePlan, FQDNFirewallPlan, FQDNAllowlistRequest

    req = FQDNAllowlistRequest(
        vendor="V", category="C", src_ip="10.0.0.5", ticket_id="CHG1",
        firewalls=["FW-A:OT-ADOM"], entries=[_fqdn_entry()],
    )
    fw_plan = FQDNFirewallPlan(
        firewall="FW-A", adom="OT-ADOM", verdict="new_rule", src_zone="OT-LAN",
        coverage="new_rule", covered_entries=[], uncovered_entries=[_fqdn_entry()],
        proposed_objects=[], proposed_group=None, proposed_policy=None,
        group_append_alternative=None, degraded=False, warnings=["w1"],
    )
    plan = FQDNChangePlan(request=req, per_firewall=[fw_plan])

    payload = to_fqdn_report_payload(plan)
    assert payload["plan_type"] == "fqdn_allowlist"
    assert payload["vendor"] == "V"
    assert payload["per_firewall"][0]["firewall"] == "FW-A"
    assert payload["warnings"] == ["w1"]
