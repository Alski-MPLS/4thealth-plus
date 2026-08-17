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


def test_ai_assist_fqdn_malformed_firewall_entry_returns_400_not_500(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = _post_json(client, {
            "vendor": "V", "category": "C", "src_ip": "10.0.0.5", "ticket_id": "CHG1",
            "firewalls": [{}],
            "entries": [{"fqdn": "x.vendor.com", "ports": [443], "protocol": "TCP",
                         "required": True, "comment": ""}],
        })
    assert resp.status_code == 400
    data = resp.get_json()
    assert "device" in data["error"].lower()
    assert "adom" in data["error"].lower()


def test_ai_assist_fqdn_adom_denial_short_circuits_before_fmg_work(client):
    forbidden = (json.dumps({"error": "forbidden"}), 403, {"Content-Type": "application/json"})

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.routes.rule_review_routes.check_adom_access", return_value=forbidden), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client:
        resp = _post_json(client, {
            "vendor": "V", "category": "C", "src_ip": "10.0.0.5", "ticket_id": "CHG1",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
            "entries": [{"fqdn": "x.vendor.com", "ports": [443], "protocol": "TCP",
                         "required": True, "comment": ""}],
        })

    assert resp.status_code == 403
    mock_make_client.assert_not_called()


# ── Finding 2: JSON branch routes through the shared parser ────────────────


def _entry(**over):
    e = {"fqdn": "x.vendor.com", "ports": [443], "protocol": "TCP",
         "required": True, "comment": ""}
    e.update(over)
    return e


def _base_payload(**over):
    p = {
        "vendor": "V", "category": "C", "src_ip": "10.0.0.5", "ticket_id": "CHG1",
        "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
        "entries": [_entry()],
    }
    p.update(over)
    return p


@pytest.mark.parametrize("bad_entry", [
    {"fqdn": 'evil"\nend\nconfig system admin'},   # illegal characters
    {"ports": ["not-a-port"]},                      # non-numeric port
    {"ports": []},                                  # no ports at all
    {"fqdn": ""},                                   # empty hostname
])
def test_ai_assist_fqdn_json_invalid_entry_returns_400_not_500(client, bad_entry):
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client:
        resp = _post_json(client, _base_payload(entries=[_entry(**bad_entry)]))

    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert resp.get_json().get("warnings")
    mock_make_client.assert_not_called()


def test_ai_assist_fqdn_json_unknown_protocol_defaults_to_tcp_with_warning(client):
    """The parser downgrades an unknown protocol to TCP with a warning rather
    than letting it reach cli_gen.service_object_cli and raise a 500."""
    fake_plan = MagicMock()
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_fqdn_change", return_value=fake_plan) as mock_plan, \
         patch("app.planner.engine.to_fqdn_report_payload", return_value={"vendor": "V"}), \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = MagicMock()
        mock_get_provider.return_value.narrate.return_value = "N"
        resp = _post_json(client, _base_payload(entries=[_entry(protocol="ICMP")]))

    assert resp.status_code == 200
    called_request = mock_plan.call_args.args[0]
    assert called_request.entries[0].protocol == "TCP"
    assert any("ICMP" in w for w in resp.get_json()["plan"]["intake_warnings"])


def test_ai_assist_fqdn_json_non_list_firewalls_returns_400(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = _post_json(client, _base_payload(firewalls="FW-A:OT-ADOM"))
    assert resp.status_code == 400


def test_ai_assist_fqdn_json_vendor_and_category_reach_the_request(client):
    fake_plan = MagicMock()
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_fqdn_change", return_value=fake_plan) as mock_plan, \
         patch("app.planner.engine.to_fqdn_report_payload", return_value={"vendor": "V"}), \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = MagicMock()
        mock_get_provider.return_value.narrate.return_value = "N"
        resp = _post_json(client, _base_payload(vendor="Apple", category="APNs"))

    assert resp.status_code == 200
    called_request = mock_plan.call_args.args[0]
    assert called_request.vendor == "Apple"
    assert called_request.category == "APNs"
    assert called_request.firewalls == ["FW-A:OT-ADOM"]
    assert called_request.entries[0].ports == [443]


# ── Finding 3: intake warnings / missing fields reach the payload ──────────


def test_ai_assist_fqdn_response_carries_intake_warnings_and_missing_fields(client):
    fake_plan = MagicMock()
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_fqdn_change", return_value=fake_plan), \
         patch("app.planner.engine.to_fqdn_report_payload", return_value={"vendor": "V"}), \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = MagicMock()
        mock_get_provider.return_value.narrate.return_value = "N"
        # One good entry plus one bad port token → parser emits a warning but
        # still yields entries, so the request succeeds and must surface it.
        resp = _post_json(client, _base_payload(
            src_ip="any",
            entries=[_entry(ports=[443, "bogus"])],
        ))

    assert resp.status_code == 200
    plan = resp.get_json()["plan"]
    assert "intake_warnings" in plan
    assert "intake_missing_fields" in plan
    assert any("bogus" in w for w in plan["intake_warnings"])
    # src_ip="any" also produces the parser's built-in 'all' warning
    assert any("any source" in w or "'all'" in w for w in plan["intake_warnings"])
    assert plan["intake_missing_fields"] == []


# ── Finding 4a / 9: multipart branch validation ────────────────────────────


def _xlsx_bytes():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Hostname/Domain", "Ports", "Protocol", "Vendor", "Category"])
    ws.append(["x.vendor.com", "443", "TCP", "V", "C"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _post_multipart(client, **form):
    data = {
        "src_ip": "10.0.0.5",
        "ticket_id": "CHG1",
        "firewalls": json.dumps([{"device": "FW-A", "adom": "OT-ADOM"}]),
        "file": (_xlsx_bytes(), "allowlist.xlsx"),
    }
    data.update(form)
    return client.post(
        "/api/rule-review/ai-assist-fqdn",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": "test-csrf"},
    )


@pytest.mark.parametrize("form", [
    {"src_ip": ""},
    {"firewalls": "[]"},
])
def test_ai_assist_fqdn_multipart_requires_src_ip_and_firewalls(client, form):
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client:
        resp = _post_multipart(client, **form)
    assert resp.status_code == 400
    assert "required" in resp.get_json()["error"].lower()
    mock_make_client.assert_not_called()


def test_ai_assist_fqdn_multipart_rejects_non_xlsx_filename(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = _post_multipart(client, file=(_xlsx_bytes(), "allowlist.csv"))
    assert resp.status_code == 400
    assert ".xlsx" in resp.get_json()["error"]


def test_ai_assist_fqdn_multipart_rejects_bad_content_type(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = _post_multipart(
            client, file=(_xlsx_bytes(), "allowlist.xlsx", "text/html")
        )
    assert resp.status_code == 400
    assert "content type" in resp.get_json()["error"].lower()
