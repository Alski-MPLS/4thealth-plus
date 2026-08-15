# Admin Host Metrics AI Trend Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-demand AI-written trend summary above the Admin page's host resource graphs (CPU/Memory/Disk), turning the existing 7-day time-series data into a short readable warning/status instead of requiring an admin to eyeball three bar charts.

**Architecture:** Trend detection is computed deterministically in Python first (percent change over the window, a simple linear slope, and — when the slope is rising — a naive days-to-90%-threshold projection), using data already collected by `app/host_metrics.py::get_metrics()`. Only these already-computed numbers (never raw per-sample time series) are sent to `app.llm.get_provider().narrate()`, which turns them into a short prose summary. This mirrors the "compute deterministically, narrate only" discipline already used by `app/planner/` + `app/llm/` for Rule Validation's AI Assist — the LLM explains a trend, it never detects or invents one.

**Tech Stack:** Flask, existing `app/llm` provider abstraction, pytest + `unittest.mock`. No new dependency — trend math uses only the stdlib.

**Spec:** Self-contained; context comes from `app/host_metrics.py::get_metrics()` (bucketed CPU/mem/disk series) and `app/routes/admin_routes.py::api_host_metrics()` (existing `GET /admin/api/host-metrics?range=`), both already in the codebase.

## Global Constraints

- Reuse the existing `ai_assist_enabled` flag from `app/app_settings.py` — no new admin toggle.
- Admin-only: gated by `@admin_required` (`app.decorators.admin_required`, imported in `admin_routes.py` as `_admin_required`), same as every other `/admin/api/*` route.
- Trend detection (percent change, slope, threshold projection) must be deterministic Python, computed before any LLM call, and returned in the API response independently of whether narration succeeds — so the UI always has real numbers even if the LLM call fails.
- On-demand only (a button, not an auto-refreshing background job) — this is low-priority admin polish per the original scoping discussion, not something that should add a recurring scheduled LLM cost.

---

### Task 1: Deterministic trend computation + narrative builder module

**Files:**
- Create: `app/host_metrics_ai.py`
- Test: `tests/test_host_metrics_ai.py`

**Interfaces:**
- Produces:
  - `compute_trend(series: list[dict], threshold: float = 90.0) -> dict` — `series` is a list of `{"ts": int, "v": float | None}` points (the shape returned by `app.host_metrics.get_metrics()` for each of `cpu`/`mem`/`disk`). Returns `{"start": float|None, "end": float|None, "pct_change": float|None, "slope_per_day": float|None, "days_to_threshold": float|None}`. All fields are `None` when there are fewer than 2 non-null points (not enough data for a trend).
  - `build_trend_narrative(trends: dict, ai_usage_summary: dict) -> str` — `trends` is `{"cpu": <compute_trend output>, "mem": ..., "disk": ...}`; `ai_usage_summary` is the dict already returned by `app.ai_usage.usage_summary()` (has `total_calls`, `total_cost_usd`, `total_failures`). Raises whatever `provider.narrate()` raises; callers catch it.

- [ ] **Step 1: Write the failing tests for compute_trend**

```python
"""Tests for app.host_metrics_ai.compute_trend and build_trend_narrative."""
import json
from unittest.mock import patch


def test_compute_trend_rising_series():
    from app.host_metrics_ai import compute_trend

    # 7 days of data, one point per day, roughly +2%/day
    series = [{"ts": i * 86400, "v": 50.0 + i * 2} for i in range(7)]
    trend = compute_trend(series, threshold=90.0)

    assert trend["start"] == 50.0
    assert trend["end"] == 62.0
    assert trend["pct_change"] == 24.0  # (62-50)/50 * 100
    assert trend["slope_per_day"] == 2.0
    # From 62% rising at 2%/day, threshold 90% is (90-62)/2 = 14 days away
    assert trend["days_to_threshold"] == 14.0


def test_compute_trend_flat_series_no_projection():
    from app.host_metrics_ai import compute_trend

    series = [{"ts": i * 86400, "v": 40.0} for i in range(5)]
    trend = compute_trend(series, threshold=90.0)

    assert trend["slope_per_day"] == 0.0
    assert trend["days_to_threshold"] is None


def test_compute_trend_insufficient_data_returns_none_fields():
    from app.host_metrics_ai import compute_trend

    trend = compute_trend([{"ts": 0, "v": 50.0}], threshold=90.0)
    assert trend == {
        "start": None, "end": None, "pct_change": None,
        "slope_per_day": None, "days_to_threshold": None,
    }


def test_compute_trend_ignores_null_values():
    from app.host_metrics_ai import compute_trend

    series = [
        {"ts": 0, "v": None},
        {"ts": 86400, "v": 50.0},
        {"ts": 172800, "v": None},
        {"ts": 259200, "v": 60.0},
    ]
    trend = compute_trend(series, threshold=90.0)
    assert trend["start"] == 50.0
    assert trend["end"] == 60.0


def test_build_trend_narrative_calls_provider():
    from app.host_metrics_ai import build_trend_narrative

    trends = {
        "cpu": {"start": 20.0, "end": 22.0, "pct_change": 10.0, "slope_per_day": 0.28, "days_to_threshold": None},
        "mem": {"start": 60.0, "end": 75.0, "pct_change": 25.0, "slope_per_day": 2.1, "days_to_threshold": 7.1},
        "disk": {"start": 40.0, "end": 40.0, "pct_change": 0.0, "slope_per_day": 0.0, "days_to_threshold": None},
    }
    ai_usage_summary = {"total_calls": 42, "total_cost_usd": 3.15, "total_failures": 1}

    with patch("app.llm.get_provider") as mock_get_provider:
        mock_get_provider.return_value.narrate.return_value = "Memory usage is climbing and will hit 90% in about a week."
        narrative = build_trend_narrative(trends, ai_usage_summary)

    assert "90%" in narrative
    user_prompt = mock_get_provider.return_value.narrate.call_args.kwargs["user_prompt"]
    sent = json.loads(user_prompt)
    assert sent["trends"]["mem"]["days_to_threshold"] == 7.1
    assert sent["ai_usage"]["total_cost_usd"] == 3.15
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_host_metrics_ai.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.host_metrics_ai'`

- [ ] **Step 3: Write the implementation**

```python
"""Deterministic trend detection + AI narration for the Admin host-metrics
and AI-usage graphs.

Trend detection (percent change, slope, threshold projection) is plain
Python arithmetic over already-collected data from app.host_metrics — the
LLM never computes or invents a trend, it only turns already-computed
numbers into a short readable summary, via the same provider-agnostic
app.llm interface used elsewhere in the app.
"""

from __future__ import annotations

import json


def compute_trend(series: list[dict], threshold: float = 90.0) -> dict:
    """Return start/end/pct_change/slope_per_day/days_to_threshold for one
    bucketed metric series ({"ts": int, "v": float|None} points, ordered).

    All fields are None when fewer than 2 non-null points are available.
    slope_per_day is 0.0 for a flat or falling series that never reaches
    the threshold; days_to_threshold is None whenever slope_per_day <= 0
    or the series is already at/above the threshold.
    """
    points = [(p["ts"], p["v"]) for p in series if p.get("v") is not None]
    if len(points) < 2:
        return {
            "start": None, "end": None, "pct_change": None,
            "slope_per_day": None, "days_to_threshold": None,
        }

    start_ts, start_v = points[0]
    end_ts, end_v = points[-1]
    span_days = (end_ts - start_ts) / 86400.0

    pct_change = ((end_v - start_v) / start_v * 100.0) if start_v else 0.0
    slope_per_day = (end_v - start_v) / span_days if span_days > 0 else 0.0

    days_to_threshold = None
    if slope_per_day > 0 and end_v < threshold:
        days_to_threshold = round((threshold - end_v) / slope_per_day, 1)

    return {
        "start": round(start_v, 2),
        "end": round(end_v, 2),
        "pct_change": round(pct_change, 2),
        "slope_per_day": round(slope_per_day, 2),
        "days_to_threshold": days_to_threshold,
    }


def build_trend_narrative(trends: dict, ai_usage_summary: dict) -> str:
    """Return an AI-written trend summary for the Admin page's host-metrics
    and AI-usage graphs.

    Raises whatever the configured provider's narrate() raises — callers
    must catch this and continue without a narrative.
    """
    from app.llm import get_provider

    payload = {
        "trends": trends,
        "ai_usage": {
            "total_calls": ai_usage_summary.get("total_calls", 0),
            "total_cost_usd": ai_usage_summary.get("total_cost_usd", 0.0),
            "total_failures": ai_usage_summary.get("total_failures", 0),
        },
    }

    provider = get_provider()
    return provider.narrate(
        system_prompt=(
            "You are an infrastructure monitoring assistant for the admin "
            "of a small internal web application. You are given "
            "already-computed 7-day trend statistics for host CPU, "
            "memory, and disk usage (percent, percent change, slope per "
            "day, and a naive days-until-90%-threshold projection when "
            "rising), plus AI-feature usage/cost stats, as JSON. Write a "
            "short summary (2-4 sentences) highlighting anything that "
            "needs attention — a metric trending toward its threshold, an "
            "unusual cost/failure count — or state that everything looks "
            "stable if nothing stands out. Never invent a number not "
            "present in the JSON."
        ),
        user_prompt=json.dumps(payload, default=str),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_host_metrics_ai.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/host_metrics_ai.py tests/test_host_metrics_ai.py
git commit -m "feat: add deterministic trend computation + AI narrative for host metrics"
```

---

### Task 2: Admin API endpoint

**Files:**
- Modify: `app/routes/admin_routes.py`
- Test: `tests/test_admin_host_metrics_ai_route.py`

**Interfaces:**
- Consumes: `compute_trend(series, threshold)` and `build_trend_narrative(trends, ai_usage_summary)` from Task 1; `app.host_metrics.get_metrics("7d")`; `app.ai_usage.usage_summary(start, end)` (already used elsewhere in `admin_routes.py` for the AI Usage chart — reuse the same 7-day `start`/`end` construction pattern found at the existing `usage_summary(start, end)` call site around line 330).
- Produces: `GET /admin/api/host-metrics/ai-summary` → `{"trends": {"cpu": {...}, "mem": {...}, "disk": {...}}, "narrative": str|None, "narrative_error": str|None}`. `503` if `ai_assist_enabled` is false — note `trends` are still deterministic and cheap to compute, but this endpoint returns them only alongside a narrative attempt, keeping one endpoint and one gate rather than splitting deterministic/AI concerns across two routes.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_admin_host_metrics_ai_route.py -v`
Expected: FAIL — `404 NOT FOUND`

- [ ] **Step 3: Add the route**

Add to `app/routes/admin_routes.py`, immediately after `api_host_metrics()`:

```python
@bp.route("/api/host-metrics/ai-summary")
@_admin_required
def api_host_metrics_ai_summary():
    """Deterministic 7-day trend stats for CPU/mem/disk plus AI usage,
    narrated by the configured LLM provider. Trend math is plain Python —
    the LLM only explains numbers already computed here. Best-effort:
    narration failure degrades to narrative=None, never a 500."""
    from app.app_settings import get_setting

    if not get_setting("ai_assist_enabled", False):
        return jsonify({"error": "AI Assist is not enabled"}), 503

    import datetime as _dt

    from app.host_metrics import get_metrics
    from app.host_metrics_ai import compute_trend, build_trend_narrative
    from app.ai_usage import usage_summary

    series = get_metrics("7d")
    trends = {
        "cpu": compute_trend(series["cpu"]),
        "mem": compute_trend(series["mem"]),
        "disk": compute_trend(series["disk"]),
    }

    end = _dt.datetime.now(_dt.timezone.utc)
    start = end - _dt.timedelta(days=7)
    ai_usage = usage_summary(start, end)

    narrative = None
    narrative_error = None
    try:
        narrative = build_trend_narrative(trends, ai_usage)
    except Exception as exc:
        narrative_error = str(exc)

    return jsonify({"trends": trends, "narrative": narrative, "narrative_error": narrative_error})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_admin_host_metrics_ai_route.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/routes/admin_routes.py tests/test_admin_host_metrics_ai_route.py
git commit -m "feat: add admin host-metrics AI trend summary endpoint"
```

---

### Task 3: Frontend — Admin page trend summary panel

**Files:**
- Modify: `app/templates/admin.html`
- Modify: `app/static/js/admin.js`

**Interfaces:**
- Consumes: `GET /admin/api/host-metrics/ai-summary` from Task 2.

- [ ] **Step 1: Add the UI container to the template**

In `app/templates/admin.html`, directly above the three host resource graph cards (CPU/Memory/Disk — the section preceding the range-pill selector described in the Admin tab docs), add:

```html
<div id="hmAiSummaryBox" style="margin: 0 0 14px 0; padding: 12px 16px; border: 1px solid var(--border-color); border-radius: 6px; background: var(--bg-subtle); display:none;">
  <button id="hmAiSummaryBtn" class="btn btn-secondary" type="button">Generate AI Trend Summary</button>
  <div id="hmAiSummaryOutput" style="margin-top: 10px; font-size: 0.9rem; line-height: 1.5; white-space: pre-wrap;"></div>
</div>
```

- [ ] **Step 2: Wire it up in admin.js**

Add near the other host-metrics chart code in `admin.js` (the code that already fetches `/admin/api/host-metrics?range=` for the graphs):

```javascript
async function checkHostMetricsAiAvailability() {
  const box = document.getElementById('hmAiSummaryBox');
  if (!box) return;
  try {
    const resp = await fetch('/admin/api/settings');
    const data = await resp.json();
    box.style.display = data.ai_assist_enabled ? '' : 'none';
  } catch (e) {
    box.style.display = 'none';
  }
}

function wireHostMetricsAiSummaryButton() {
  const btn = document.getElementById('hmAiSummaryBtn');
  const out = document.getElementById('hmAiSummaryOutput');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = 'Generating…';
    out.textContent = '';
    try {
      const resp = await fetch('/admin/api/host-metrics/ai-summary');
      const data = await resp.json();
      out.textContent = data.narrative || ('AI summary unavailable: ' + (data.narrative_error || data.error || 'unknown error'));
    } catch (e) {
      out.textContent = 'AI summary request failed: ' + e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Generate AI Trend Summary';
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  checkHostMetricsAiAvailability();
  wireHostMetricsAiSummaryButton();
});
```

`GET /admin/api/settings` already exists (documented as the endpoint backing the External API / AI Assist toggles in Admin — `admin_routes.py`'s `GET/PUT /admin/api/settings`); confirm its response includes `ai_assist_enabled` before using it here, since that is what gates the box's visibility the same way the AI Assist sub-tab's own toggle checkbox is populated (`app/static/js/admin.js:395`, `document.getElementById('aiAssistEnabled').checked = !!settings.ai_assist_enabled;`).

- [ ] **Step 3: Manual verification**

Run: `python wsgi.py`, log in as admin with `ai_assist_enabled: true`, open Admin, confirm the trend summary box appears above the host metrics graphs, click **Generate AI Trend Summary**, confirm narrative text appears. Set `ai_assist_enabled: false`, reload, confirm the box stays hidden.

- [ ] **Step 4: Commit**

```bash
git add app/templates/admin.html app/static/js/admin.js
git commit -m "feat: add AI trend summary panel to Admin host metrics"
```
