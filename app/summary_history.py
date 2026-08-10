"""Persistent 30-day history for the managed-firewall / policy-rule counts.

The history file lives at summary_history.json in the project root (same
directory as policy_db.json) and is gitignored-by-convention (runtime data).
One record per calendar date (server local date).  At most 30 records are kept.

Public API
----------
record_today(firewalls, rules)  — write today's entry (idempotent for same day)
get_history()                   — return list of {date, firewalls, rules} dicts,
                                  sorted oldest-first, last 30 days only
"""

import json
import logging
import threading
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_HISTORY_PATH = Path(__file__).parent.parent / "summary_history.json"
_MAX_DAYS = 30
_lock = threading.Lock()


def _load() -> list[dict]:
    """Return the raw list from disk, or [] on any error."""
    try:
        if _HISTORY_PATH.exists():
            return json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("summary_history: load failed: %s", exc)
    return []


def _save(records: list[dict]) -> None:
    # Write directly — atomic rename fails when the destination is a Docker
    # bind-mounted file (cross-device link error).  History data is low-risk
    # and fully regeneratable, so a direct write is acceptable here.
    _HISTORY_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _prune(records: list[dict]) -> list[dict]:
    """Keep only the most recent _MAX_DAYS unique dates."""
    seen: dict[str, dict] = {}
    for r in records:
        seen[r["date"]] = r
    cutoff = (date.today() - timedelta(days=_MAX_DAYS - 1)).isoformat()
    kept = [r for d_str, r in seen.items() if d_str >= cutoff]
    return sorted(kept, key=lambda r: r["date"])


def record_today(firewalls: int, rules: int) -> None:
    """Write today's entry, overwriting any existing entry for today."""
    today = date.today().isoformat()
    with _lock:
        records = _load()
        records = [r for r in records if r["date"] != today]
        records.append({"date": today, "firewalls": firewalls, "rules": rules})
        records = _prune(records)
        _save(records)
        logger.info(
            "summary_history: recorded %s — fw=%d rules=%d", today, firewalls, rules
        )


def get_history() -> list[dict]:
    """Return sorted list of {date, firewalls, rules}, oldest first, max 30 days."""
    with _lock:
        return _prune(_load())
