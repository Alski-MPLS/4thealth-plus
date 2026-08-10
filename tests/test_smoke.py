"""Smoke tests — verify the app can be imported and instantiated."""
import os

import pytest


@pytest.fixture
def app():
    os.environ.setdefault("SECRET_KEY", "test-secret")
    os.environ.setdefault("FMG_PRIMARY_HOST", "127.0.0.1")
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def test_app_creates(app):
    assert app is not None


def test_login_page_reachable(client):
    response = client.get("/login")
    assert response.status_code == 200


def test_unauthenticated_root_redirects(client):
    response = client.get("/")
    assert response.status_code in (302, 200)
