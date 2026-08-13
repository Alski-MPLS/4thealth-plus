"""
Tests for the planner models (roundtrip serialization).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.planner.matching import PortRange
from app.planner.models import (
    ChangePlan,
    FirewallPlan,
    InsertionPlan,
    NormalizedFlow,
    ObjectPlan,
    PlannerDataError,
)


def test_models_roundtrip():
    plan = ChangePlan(
        ticket_id="CHG1",
        flow=NormalizedFlow(src="10.1.1.1", dst="10.2.2.2", service="443",
                            service_ranges=[PortRange("tcp", 443, 443)]),
        zone_verdict={"verdict": "ALLOWED"},
        risk_level="high",
        firewalls=[FirewallPlan(
            firewall="FW1", adom="root", status="new_rule",
            objects=[ObjectPlan(role="source", action="create",
                                name="H_10.1.1.1", obj_type="host",
                                value="10.1.1.1/32", cli="config ...")],
            insertion=InsertionPlan(package="pkgA", insert_before_policy_id=7,
                                    rationale="precede deny"),
        )],
        cli_status="new_rule",
        recommendation="ok",
    )
    d = plan.to_dict()
    assert d["firewalls"][0]["insertion"]["insert_before_policy_id"] == 7
    assert d["flow"]["service_ranges"][0]["protocol"] == "tcp"


def test_planner_data_error_fields():
    err = PlannerDataError("fortimanager", "all hosts unreachable")
    assert err.source == "fortimanager"
    assert "unreachable" in err.detail
    assert "[fortimanager]" in str(err)
