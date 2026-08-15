"""Tests for POST /api/pending-changes/adoms/<adom>/device/<device>/ai-summary."""
import json
import time
from unittest.mock import patch

import pytest


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
    return client.post(
        url, data=json.dumps(payload), content_type="application/json",
        headers={"X-CSRF-Token": "test-csrf"},
    )


def test_ai_summary_disabled_returns_503(client):
    with patch("app.app_settings.get_setting", return_value=False), \
         patch("app.decorators.check_adom_access", return_value=None):
        resp = _post(client, "/api/pending-changes/adoms/CorpADOM/device/fw-01/ai-summary", {
            "summary": {}, "vdoms": [{"name": "root", "changes": [{"type": "add", "line": "edit 1"}]}],
        })
    assert resp.status_code == 503


def test_ai_summary_missing_vdoms_returns_400(client):
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None):
        resp = _post(client, "/api/pending-changes/adoms/CorpADOM/device/fw-01/ai-summary", {})
    assert resp.status_code == 400


def test_ai_summary_vdoms_wrong_type_returns_400(client):
    """vdoms present but not a list (e.g. a string) must 400, not 500 — the
    known pitfall from the two prior sibling AI-summary features."""
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None):
        resp = _post(client, "/api/pending-changes/adoms/CorpADOM/device/fw-01/ai-summary", {
            "summary": {}, "vdoms": "not-a-list",
        })
    assert resp.status_code == 400

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None):
        resp = _post(client, "/api/pending-changes/adoms/CorpADOM/device/fw-01/ai-summary", {
            "summary": {}, "vdoms": 42,
        })
    assert resp.status_code == 400


def test_ai_summary_success(client):
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.pending_changes_ai.build_diff_narrative", return_value="Adds a policy.") as mock_build:
        resp = _post(client, "/api/pending-changes/adoms/CorpADOM/device/fw-01/ai-summary", {
            "summary": {"firewall_policy": 1}, "vdoms": [{"name": "root", "changes": [{"type": "add", "line": "edit 1"}]}],
        })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["narrative"] == "Adds a policy."
    assert data["narrative_error"] is None
    mock_build.assert_called_once()
    called_devices = mock_build.call_args.args[1]
    assert called_devices[0]["device"] == "fw-01"


def test_ai_summary_narration_failure_returns_200_with_error(client):
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.pending_changes_ai.build_diff_narrative", side_effect=RuntimeError("API down")):
        resp = _post(client, "/api/pending-changes/adoms/CorpADOM/device/fw-01/ai-summary", {
            "summary": {}, "vdoms": [{"name": "root", "changes": [{"type": "add", "line": "edit 1"}]}],
        })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["narrative"] is None
    assert data["narrative_error"] == "API down"
