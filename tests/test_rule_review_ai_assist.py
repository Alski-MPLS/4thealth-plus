"""Tests for POST /api/rule-review/ai-assist."""
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-ci")
os.environ.setdefault("FMG_PRIMARY_HOST", "127.0.0.1")


@pytest.fixture
def app():
    from app import create_app
    return create_app()


_TEST_USERS = {"admin": {"password_hash": "$2b$12$placeholder", "role": "admin"}}


@pytest.fixture
def client(app):
    with app.test_client() as c, \
         patch("app.auth._load_users", return_value=_TEST_USERS):
        with c.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["_csrf_token"] = "test-csrf"
            sess["login_at"] = int(time.time())
        yield c


def _post(client, url, payload):
    """POST with JSON body and CSRF header to bypass CSRF validation."""
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        headers={"X-CSRF-Token": "test-csrf"},
    )


def test_ai_assist_disabled_by_default_returns_503(client):
    with patch("app.app_settings.get_setting", return_value=False):
        resp = _post(client, "/api/rule-review/ai-assist", {
            "src": "10.0.0.5", "dst": "10.0.0.6", "service": "tcp/443",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
        })
    assert resp.status_code == 503


def test_ai_assist_missing_fields_returns_400(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = _post(client, "/api/rule-review/ai-assist", {"src": "10.0.0.5"})
    assert resp.status_code == 400


def test_ai_assist_firewall_missing_adom_returns_400_not_500(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = _post(client, "/api/rule-review/ai-assist", {
            "src": "10.0.0.5", "dst": "10.0.0.6", "service": "tcp/443",
            "firewalls": [{"device": "FortiWiFi-71G"}],
        })
    assert resp.status_code == 400
    assert "device" in resp.get_json()["error"] or "ADOM" in resp.get_json()["error"]


def test_ai_assist_success_returns_plan_and_narrative(client):
    fake_plan = MagicMock()
    fake_plan.to_dict.return_value = {"ticket_id": "CHG1", "cli_status": "new_rule"}

    fake_fmg = MagicMock()
    fake_fmg.get_device_interfaces_all_vdoms.return_value = [
        {"name": "port1", "ip": "10.0.0.1 255.255.255.0"},
    ]
    fake_fmg.get_device_routes_all_vdoms.return_value = []

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_change", return_value=fake_plan) as mock_plan_change, \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = fake_fmg
        mock_get_provider.return_value.narrate.return_value = "Here is the narrative report."

        resp = _post(client, "/api/rule-review/ai-assist", {
            "src": "10.0.0.5", "dst": "10.0.0.6", "service": "tcp/443",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
            "ticket_id": "CHG1",
        })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["plan"]["ticket_id"] == "CHG1"
    assert data["narrative"] == "Here is the narrative report."
    assert data["narrative_error"] is None
    assert "FW-A" in data["path_relevance"]
    mock_plan_change.assert_called_once()


def test_ai_assist_narration_failure_still_returns_plan(client):
    fake_plan = MagicMock()
    fake_plan.to_dict.return_value = {"ticket_id": "CHG1", "cli_status": "new_rule"}

    fake_fmg = MagicMock()
    fake_fmg.get_device_interfaces_all_vdoms.return_value = []
    fake_fmg.get_device_routes_all_vdoms.return_value = []

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_change", return_value=fake_plan), \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = fake_fmg
        mock_get_provider.return_value.narrate.side_effect = RuntimeError("API down")

        resp = _post(client, "/api/rule-review/ai-assist", {
            "src": "10.0.0.5", "dst": "10.0.0.6", "service": "tcp/443",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
        })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["plan"]["ticket_id"] == "CHG1"
    assert data["narrative"] is None
    assert data["narrative_error"] == "API down"


def test_ai_assist_planner_data_error_returns_502(client):
    from app.planner.models import PlannerDataError
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_change",
               side_effect=PlannerDataError("request", "mixed verdicts")):
        mock_make_client.return_value.__enter__.return_value = MagicMock()
        resp = _post(client, "/api/rule-review/ai-assist", {
            "src": "10.0.0.5", "dst": "10.0.0.6, 10.0.0.7", "service": "tcp/443",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
        })
    assert resp.status_code == 502
    assert "mixed verdicts" in resp.get_json()["error"]
