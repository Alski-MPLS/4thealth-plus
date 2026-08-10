"""Background cache for the known-ADOM list.

Queries FortiManager at startup and every 30 minutes to keep a fresh list of
ADOM names.  The list is used by the admin UI (ADOM access control per group)
and by API endpoints to validate/filter ADOM access for non-admin users.

Thread-safety: a single RLock guards the in-memory state.  Callers get a
snapshot copy so the lock is held only briefly.
"""

from __future__ import annotations

import threading
import datetime

from flask import Flask

_lock = threading.RLock()

_state: dict = {
    "adoms": [],  # list[str] — sorted ADOM names (forti* filtered out)
    "last_updated": None,  # ISO-8601 string or None
    "status": "pending",  # "pending" | "ok" | "error"
    "error": None,  # last error message, or None
}

_REFRESH_MINUTES = 30


# ── Public API ────────────────────────────────────────────────────────────────


def get_cached() -> dict:
    """Return a shallow copy of the current cache state."""
    with _lock:
        return dict(_state)


def get_adom_names() -> list[str]:
    """Return the current list of known ADOM names (snapshot)."""
    with _lock:
        return list(_state["adoms"])


# ── Refresh logic ─────────────────────────────────────────────────────────────


def _run_refresh(app: Flask) -> None:
    with app.app_context():
        try:
            from app.fmg_helpers import make_client

            with make_client() as client:
                raw = client.get_adoms()
            names = sorted(
                a.get("name", a.get("adom", ""))
                for a in raw
                if isinstance(a, dict)
                and a.get("name", a.get("adom", ""))
                and not a.get("name", a.get("adom", "")).lower().startswith("forti")
            )
            with _lock:
                _state["adoms"] = names
                _state["last_updated"] = datetime.datetime.now().isoformat(
                    timespec="seconds"
                )
                _state["status"] = "ok"
                _state["error"] = None
        except Exception as exc:
            with _lock:
                _state["status"] = "error"
                _state["error"] = str(exc)


def refresh_now(app: Flask) -> None:
    """Kick off a non-blocking refresh in a daemon thread."""
    t = threading.Thread(
        target=_run_refresh,
        args=[app],
        name="adom_cache_refresh",
        daemon=True,
    )
    t.start()


# ── Scheduler init ────────────────────────────────────────────────────────────


def init_scheduler(app: Flask) -> None:
    """Register a recurring APScheduler job and run the first fetch immediately."""
    from apscheduler.schedulers.background import BackgroundScheduler

    refresh_now(app)  # initial load at startup

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=_run_refresh,
        args=[app],
        trigger="interval",
        minutes=_REFRESH_MINUTES,
        id="adom_cache_refresh",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
