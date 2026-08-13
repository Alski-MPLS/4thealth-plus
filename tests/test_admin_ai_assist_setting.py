"""Tests for the ai_assist_enabled admin setting toggle."""
import time
from unittest.mock import patch

import pytest

from app import create_app


@pytest.fixture
def admin_client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["_csrf_token"] = "test-csrf"
            sess["login_at"] = int(time.time())
        yield c


def test_settings_get_includes_ai_assist_enabled(admin_client):
    resp = admin_client.get("/admin/api/settings")
    assert resp.status_code == 200
    assert "ai_assist_enabled" in resp.get_json()


def test_settings_put_toggles_ai_assist_enabled(admin_client):
    with patch("app.routes.admin_routes.set_setting") as mock_set:
        resp = admin_client.put(
            "/admin/api/settings",
            json={"ai_assist_enabled": True},
            headers={"X-CSRF-Token": "test-csrf"},
        )
    assert resp.status_code == 200
    mock_set.assert_any_call("ai_assist_enabled", True)
