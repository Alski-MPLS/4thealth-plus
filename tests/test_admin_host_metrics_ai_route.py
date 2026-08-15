"""Tests for GET /admin/api/host-metrics/ai-summary."""
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


def test_ai_summary_disabled_returns_503(client):
    with patch("app.app_settings.get_setting", return_value=False):
        resp = client.get("/admin/api/host-metrics/ai-summary")
    assert resp.status_code == 503


def test_ai_summary_success(client):
    fake_series = {
        "cpu": [{"ts": 0, "v": 20.0}, {"ts": 86400, "v": 22.0}],
        "mem": [{"ts": 0, "v": 60.0}, {"ts": 86400, "v": 75.0}],
        "disk": [{"ts": 0, "v": 40.0}, {"ts": 86400, "v": 40.0}],
        "range": "7d", "generated_at": 86400,
    }
    fake_usage = {"total_calls": 10, "total_cost_usd": 1.0, "total_failures": 0}

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.host_metrics.get_metrics", return_value=fake_series), \
         patch("app.ai_usage.usage_summary", return_value=fake_usage), \
         patch("app.host_metrics_ai.build_trend_narrative", return_value="Looks stable.") as mock_build:
        resp = client.get("/admin/api/host-metrics/ai-summary")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["narrative"] == "Looks stable."
    assert data["narrative_error"] is None
    assert data["trends"]["mem"]["end"] == 75.0
    mock_build.assert_called_once()


def test_ai_summary_narration_failure_returns_200_with_error(client):
    fake_series = {
        "cpu": [{"ts": 0, "v": 20.0}], "mem": [{"ts": 0, "v": 60.0}],
        "disk": [{"ts": 0, "v": 40.0}], "range": "7d", "generated_at": 0,
    }
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.host_metrics.get_metrics", return_value=fake_series), \
         patch("app.ai_usage.usage_summary", return_value={"total_calls": 0, "total_cost_usd": 0.0, "total_failures": 0}), \
         patch("app.host_metrics_ai.build_trend_narrative", side_effect=RuntimeError("API down")):
        resp = client.get("/admin/api/host-metrics/ai-summary")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["narrative"] is None
    assert data["narrative_error"] == "API down"
