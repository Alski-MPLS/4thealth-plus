"""Tests for app.host_metrics — SQLite-backed CPU/mem/disk sampling."""
from __future__ import annotations

import sqlite3
import time

import pytest

from app import host_metrics


@pytest.fixture
def metrics_db(tmp_path, monkeypatch):
    db_path = tmp_path / "host_metrics_test.db"
    monkeypatch.setattr(host_metrics, "_DB_PATH", db_path)
    host_metrics._init_db()
    return db_path


def _insert(db_path, ts, cpu, mem, disk):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO host_metrics (ts, cpu, mem, disk) VALUES (?, ?, ?, ?)",
        (ts, cpu, mem, disk),
    )
    conn.commit()
    conn.close()


def test_record_sample_persists_a_row(metrics_db, monkeypatch):
    monkeypatch.setattr(host_metrics.psutil, "cpu_percent", lambda interval=None: 12.5)
    monkeypatch.setattr(
        host_metrics.psutil, "virtual_memory",
        lambda: type("M", (), {"percent": 55.0})(),
    )
    monkeypatch.setattr(
        host_metrics.psutil, "disk_usage",
        lambda path: type("D", (), {"percent": 30.0})(),
    )

    host_metrics.record_sample()

    conn = sqlite3.connect(metrics_db)
    rows = conn.execute("SELECT cpu, mem, disk FROM host_metrics").fetchall()
    conn.close()
    assert rows == [(12.5, 55.0, 30.0)]


def test_get_metrics_buckets_within_window(metrics_db):
    now = int(time.time())
    _insert(metrics_db, now - 30, 10.0, 20.0, 30.0)
    _insert(metrics_db, now - 90, 90.0, 80.0, 70.0)  # outside 1h/60s-bucket window is fine, same bucket differs

    data = host_metrics.get_metrics("1h")
    assert data["range"] == "1h"
    assert len(data["cpu"]) >= 1
    assert all(0 <= p["v"] <= 100 for p in data["cpu"])


def test_get_metrics_excludes_rows_outside_window(metrics_db):
    now = int(time.time())
    _insert(metrics_db, now - 10, 50.0, 50.0, 50.0)
    _insert(metrics_db, now - 999_999, 1.0, 1.0, 1.0)  # far outside any window

    data = host_metrics.get_metrics("1h")
    values = [p["v"] for p in data["cpu"]]
    assert 1.0 not in values


def test_get_metrics_defaults_to_1h_for_invalid_range(metrics_db):
    data = host_metrics.get_metrics("not-a-range")
    assert data["range"] == "1h"


def test_prune_old_data_removes_rows_past_retention(metrics_db):
    now = int(time.time())
    _insert(metrics_db, now - (91 * 86_400), 5.0, 5.0, 5.0)
    _insert(metrics_db, now, 6.0, 6.0, 6.0)

    host_metrics.prune_old_data()

    conn = sqlite3.connect(metrics_db)
    rows = conn.execute("SELECT cpu FROM host_metrics").fetchall()
    conn.close()
    assert rows == [(6.0,)]
