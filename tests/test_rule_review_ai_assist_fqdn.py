"""Tests for POST /api/rule-review/ai-assist-fqdn."""
import io
import json
import os
import time
from unittest.mock import MagicMock, patch

import openpyxl
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


def _post_json(client, payload):
    return client.post(
        "/api/rule-review/ai-assist-fqdn",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"X-CSRF-Token": "test-csrf"},
    )


def test_ai_assist_fqdn_disabled_by_default_returns_503(client):
    with patch("app.app_settings.get_setting", return_value=False):
        resp = _post_json(client, {
            "vendor": "V", "category": "C", "src_ip": "10.0.0.5", "ticket_id": "CHG1",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
            "entries": [{"fqdn": "x.vendor.com", "ports": [443], "protocol": "TCP",
                         "required": True, "comment": ""}],
        })
    assert resp.status_code == 503


def test_ai_assist_fqdn_missing_entries_returns_400(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = _post_json(client, {
            "vendor": "V", "category": "C", "src_ip": "10.0.0.5",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
            "entries": [],
        })
    assert resp.status_code == 400


def test_ai_assist_fqdn_json_success_returns_plan_and_narrative(client):
    fake_plan = MagicMock()

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_fqdn_change", return_value=fake_plan) as mock_plan, \
         patch("app.planner.engine.to_fqdn_report_payload",
               return_value={"plan_type": "fqdn_allowlist", "vendor": "V"}), \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = MagicMock()
        mock_get_provider.return_value.narrate.return_value = "Narrative text."

        resp = _post_json(client, {
            "vendor": "V", "category": "C", "src_ip": "10.0.0.5", "ticket_id": "CHG1",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
            "entries": [{"fqdn": "x.vendor.com", "ports": [443], "protocol": "TCP",
                         "required": True, "comment": ""}],
        })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["plan"]["vendor"] == "V"
    assert data["narrative"] == "Narrative text."
    assert data["narrative_error"] is None
    mock_plan.assert_called_once()


def test_ai_assist_fqdn_xlsx_upload_success(client):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Hostname/Domain", "Ports", "Protocol", "Vendor", "Category"])
    ws.append(["x.vendor.com", "443", "TCP", "V", "C"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    fake_plan = MagicMock()

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_fqdn_change", return_value=fake_plan) as mock_plan, \
         patch("app.planner.engine.to_fqdn_report_payload",
               return_value={"plan_type": "fqdn_allowlist", "vendor": "V"}), \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = MagicMock()
        mock_get_provider.return_value.narrate.return_value = "Narrative text."

        resp = client.post(
            "/api/rule-review/ai-assist-fqdn",
            data={
                "src_ip": "10.0.0.5", "ticket_id": "CHG1",
                "firewalls": json.dumps([{"device": "FW-A", "adom": "OT-ADOM"}]),
                "file": (buf, "allowlist.xlsx"),
            },
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": "test-csrf"},
        )

    assert resp.status_code == 200
    mock_plan.assert_called_once()
    called_request = mock_plan.call_args.args[0]
    assert called_request.vendor == "V"
    assert called_request.entries[0].fqdn == "x.vendor.com"
