# Device Review AI Narrative Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an AI-generated plain-English narrative summary to Device Review (CIS check) results — available on-demand after a live run and automatically inserted into scheduled email/exported reports.

**Architecture:** A new module `app/device_review_ai.py` builds a compact prompt from the check-summary counts, host-summary counts, and the top FAIL/INSECURE rows (never the full findings dump), and calls the existing `app.llm.get_provider().narrate()` interface. Two integration points consume it: (1) a new on-demand API endpoint hit from a "Summarize with AI" button on the Device Review results page, and (2) `app/device_review_scheduler.py::_execute_job`, which prepends the narrative to both the email HTML body and the exported PDF-HTML attachment. In both cases narration is best-effort — the deterministic report/email always sends even if narration fails.

**Tech Stack:** Flask, existing `app/llm` provider abstraction (Claude/Codex/Ollama), pytest + `unittest.mock`.

**Spec:** This plan is self-contained; there is no separate spec document. Context comes from the existing `app/device_review.py` (check engine, `Row` shape), `app/device_review_scheduler.py` (scheduled report builder), and `app/llm/` (narration interface), all already in the codebase.

## Global Constraints

- Reuse the existing `ai_assist_enabled` flag from `app/app_settings.py` — do not add a new admin toggle or settings key.
- Narration must never block, delay past its own try/except, or break report generation or email sending — on any exception, log/record the error and continue with the deterministic report unchanged.
- Only send aggregated counts + a capped list of FAIL/INSECURE rows to the LLM, not the full per-interface findings — keep the payload small and bounded regardless of ADOM size.
- Follow the existing `narrate(system_prompt, user_prompt) -> str` contract from `app/llm/base.py`; call sites must catch `app.llm.LLMError` (and generic `Exception`, mirroring the AI Assist route's own catch-all) rather than let it propagate.

---

### Task 1: Narrative builder module

**Files:**
- Create: `app/device_review_ai.py`
- Test: `tests/test_device_review_ai.py`

**Interfaces:**
- Produces: `build_narrative(adom: str, check_summary: list[dict], results: list[dict]) -> str` — raises whatever `provider.narrate()` raises (an exception) on failure; callers are responsible for catching it. `check_summary` is the list produced by `app.device_review_scheduler._build_check_summary()` (`{key, name, description, PASS, INFO, WARN, CONFIG_MISSING, FAIL, INSECURE}` per check). `results` is the list of `{device, ip, rows, error}` dicts produced by `bulk_device_review_adom()` / `run_checks()`.

- [ ] **Step 1: Write the failing test for the happy path**

```python
"""Tests for app.device_review_ai.build_narrative."""
from unittest.mock import MagicMock, patch


def test_build_narrative_calls_provider_and_returns_text():
    from app.device_review_ai import build_narrative

    check_summary = [
        {"key": "trusted_hosts", "name": "Trusted Hosts on Admin Accounts (CIS)",
         "description": "desc", "PASS": 2, "INFO": 0, "WARN": 0,
         "CONFIG_MISSING": 0, "FAIL": 1, "INSECURE": 0},
    ]
    results = [
        {"device": "fw-01", "ip": "10.0.0.1", "error": None, "rows": [
            {"device": "fw-01", "check": "Trusted Hosts on Admin Accounts (CIS)",
             "result": "FAIL", "interface": "system", "vdom": "", "ip": "",
             "detail": "Admin account(s) with no trusted-host restriction: admin"},
        ]},
    ]

    with patch("app.llm.get_provider") as mock_get_provider:
        mock_get_provider.return_value.narrate.return_value = "Overall posture is strong except for one admin trusted-host gap."
        narrative = build_narrative("CorpADOM", check_summary, results)

    assert narrative == "Overall posture is strong except for one admin trusted-host gap."
    mock_get_provider.return_value.narrate.assert_called_once()
    call_kwargs = mock_get_provider.return_value.narrate.call_args.kwargs
    assert "system_prompt" in call_kwargs
    assert "user_prompt" in call_kwargs
    assert "CorpADOM" in call_kwargs["user_prompt"]
    assert "fw-01" in call_kwargs["user_prompt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_device_review_ai.py::test_build_narrative_calls_provider_and_returns_text -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.device_review_ai'`

- [ ] **Step 3: Write the implementation**

```python
"""AI narrative summaries for Device Review (CIS) results.

The LLM here never computes a finding — app.device_review.run_checks() and
the scheduler's check-summary aggregation already have. This module only
turns already-computed, aggregated results into a short prose summary, via
the same provider-agnostic app.llm interface used by Rule Validation's AI
Assist.
"""

from __future__ import annotations

import json

_MAX_ROWS_SENT = 40  # cap the failing/insecure rows sent to the LLM


def _fail_rows(results: list[dict]) -> list[dict]:
    """Return up to _MAX_ROWS_SENT FAIL/INSECURE rows across all devices."""
    out: list[dict] = []
    for dev in results:
        for row in dev.get("rows", []):
            if row.get("result") in ("FAIL", "INSECURE"):
                out.append({
                    "device": row.get("device", ""),
                    "check": row.get("check", ""),
                    "result": row.get("result", ""),
                    "interface": row.get("interface", ""),
                    "detail": row.get("detail", ""),
                })
                if len(out) >= _MAX_ROWS_SENT:
                    return out
    return out


def build_narrative(adom: str, check_summary: list[dict], results: list[dict]) -> str:
    """Return an AI-written narrative summary for one Device Review run.

    Raises whatever the configured provider's narrate() raises — callers
    must catch this and degrade to the deterministic report without a
    narrative, same pattern as Rule Validation's AI Assist.
    """
    from app.llm import get_provider

    errors = [d.get("device", "?") for d in results if d.get("error")]
    payload = {
        "adom": adom,
        "devices_scanned": len(results),
        "devices_with_errors": errors,
        "check_summary": check_summary,
        "failing_and_insecure_findings": _fail_rows(results),
    }

    provider = get_provider()
    return provider.narrate(
        system_prompt=(
            "You are a firewall security analyst assistant. You are given "
            "already-computed CIS hardening / interface-protocol check "
            "results as JSON for one FortiManager ADOM. Write a short "
            "executive summary (3-6 sentences) for a NOC/SOC reader: what "
            "is the overall posture, which devices or checks need "
            "attention first, and why. Never invent a finding or change "
            "any count or value — only explain what is already there."
        ),
        user_prompt=json.dumps(payload, default=str),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_device_review_ai.py -v`
Expected: PASS

- [ ] **Step 5: Add a test for the row cap and commit**

```python
def test_build_narrative_caps_rows_sent_to_llm():
    from app.device_review_ai import build_narrative

    results = [
        {"device": f"fw-{i:02d}", "ip": "", "error": None, "rows": [
            {"device": f"fw-{i:02d}", "check": "X", "result": "FAIL",
             "interface": "system", "vdom": "", "ip": "", "detail": "bad"},
        ]}
        for i in range(50)
    ]

    with patch("app.llm.get_provider") as mock_get_provider:
        mock_get_provider.return_value.narrate.return_value = "summary"
        build_narrative("CorpADOM", [], results)

    user_prompt = mock_get_provider.return_value.narrate.call_args.kwargs["user_prompt"]
    import json as _json
    sent = _json.loads(user_prompt)
    assert len(sent["failing_and_insecure_findings"]) == 40
```

Run: `pytest tests/test_device_review_ai.py -v`
Expected: PASS (2 tests)

```bash
git add app/device_review_ai.py tests/test_device_review_ai.py
git commit -m "feat: add Device Review AI narrative builder"
```

---

### Task 2: On-demand API endpoint

**Files:**
- Modify: `app/routes/device_review_routes.py`
- Test: `tests/test_device_review_ai_route.py`

**Interfaces:**
- Consumes: `build_narrative(adom, check_summary, results)` from Task 1; `app.app_settings.get_setting`; `app.device_review._build_check_summary`-equivalent — reuse `app.device_review_scheduler._build_check_summary(results, checks_ran)` (already public-enough, imported the same way `device_review_scheduler.py` imports `CHECKS_META`).
- Produces: `POST /api/device-review/ai-summary-status` → `{"available": bool}`; `POST /api/device-review/ai-summary` → `{"narrative": str|None, "narrative_error": str|None}`, `503` if disabled, `400` if `results` missing/empty.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for POST /api/device-review/ai-summary."""
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
    with patch("app.app_settings.get_setting", return_value=False):
        resp = _post(client, "/api/device-review/ai-summary", {
            "adom": "CorpADOM", "results": [{"device": "fw-01", "rows": [], "error": None}],
        })
    assert resp.status_code == 503


def test_ai_summary_missing_results_returns_400(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = _post(client, "/api/device-review/ai-summary", {"adom": "CorpADOM"})
    assert resp.status_code == 400


def test_ai_summary_success(client):
    fake_results = [
        {"device": "fw-01", "rows": [
            {"device": "fw-01", "check": "Trusted Hosts on Admin Accounts (CIS)",
             "result": "FAIL", "interface": "system", "vdom": "", "ip": "",
             "detail": "no restriction"},
        ], "error": None},
    ]
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.device_review_ai.build_narrative", return_value="Summary text") as mock_build:
        resp = _post(client, "/api/device-review/ai-summary", {
            "adom": "CorpADOM", "results": fake_results, "checks": ["trusted_hosts"],
        })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["narrative"] == "Summary text"
    assert data["narrative_error"] is None
    mock_build.assert_called_once()


def test_ai_summary_narration_failure_returns_200_with_error(client):
    fake_results = [{"device": "fw-01", "rows": [], "error": None}]
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.device_review_ai.build_narrative", side_effect=RuntimeError("API down")):
        resp = _post(client, "/api/device-review/ai-summary", {
            "adom": "CorpADOM", "results": fake_results,
        })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["narrative"] is None
    assert data["narrative_error"] == "API down"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_device_review_ai_route.py -v`
Expected: FAIL — `404 NOT FOUND` for `/api/device-review/ai-summary`

- [ ] **Step 3: Add the routes**

Add to `app/routes/device_review_routes.py` (near the other `/api/device-review/*` routes, following the existing `tab_required("device_review")` pattern already used by sibling routes in this file):

```python
# ── AI Summary ─────────────────────────────────────────────────────────────


@bp.route("/api/device-review/ai-summary-status")
@tab_required("device_review")
def dr_ai_summary_status():
    from app.app_settings import get_setting

    return jsonify({"available": get_setting("ai_assist_enabled", False)})


@bp.route("/api/device-review/ai-summary", methods=["POST"])
@tab_required("device_review")
def dr_ai_summary():
    """Narrate an already-computed Device Review run. The LLM never computes
    a finding — it only summarizes results the check engine already
    produced. Narration is best-effort: any failure degrades to
    narrative=None + narrative_error, never a 500."""
    from app.app_settings import get_setting

    if not get_setting("ai_assist_enabled", False):
        return jsonify({"error": "AI Assist is not enabled"}), 503

    data = request.get_json(silent=True) or {}
    adom = (data.get("adom") or "").strip()
    results = data.get("results")
    checks = data.get("checks") or []

    if not results:
        return jsonify({"error": "results is required"}), 400

    from app.device_review_scheduler import _build_check_summary
    from app.device_review_ai import build_narrative

    check_summary = _build_check_summary(results, checks)

    narrative = None
    narrative_error = None
    try:
        narrative = build_narrative(adom, check_summary, results)
    except Exception as exc:
        narrative_error = str(exc)

    return jsonify({"narrative": narrative, "narrative_error": narrative_error})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_device_review_ai_route.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/routes/device_review_routes.py tests/test_device_review_ai_route.py
git commit -m "feat: add on-demand Device Review AI summary endpoint"
```

---

### Task 3: Frontend — "Summarize with AI" button

**Files:**
- Modify: `app/templates/device_review.html`
- Modify: `app/static/js/device_review.js`

**Interfaces:**
- Consumes: `GET /api/device-review/ai-summary-status` → `{available}`; `POST /api/device-review/ai-summary` body `{adom, results, checks}` → `{narrative, narrative_error}` from Task 2. `results` here is the same array the page already builds after a run completes (each entry `{device, rows, error}` — check how the existing results table is populated in `device_review.js` and pass that array verbatim).

- [ ] **Step 1: Add the UI container to the template**

In `app/templates/device_review.html`, inside the results section (near where the findings table renders — same container that already shows after `Run Analysis` completes), add:

```html
<div id="drAiSummaryBox" style="display:none; margin: 12px 0; padding: 12px 16px; border: 1px solid var(--border-color); border-radius: 6px; background: var(--bg-subtle);">
  <button id="drAiSummaryBtn" class="btn btn-secondary" type="button">Summarize with AI</button>
  <div id="drAiSummaryOutput" style="margin-top: 10px; font-size: 0.9rem; line-height: 1.5; white-space: pre-wrap;"></div>
</div>
```

- [ ] **Step 2: Wire it up in device_review.js**

Add near the top of `device_review.js` (module-scope, alongside other initial fetches) and after the function that renders results once a run finishes — find the function that populates the findings table (the run completion handler) and call `showAiSummaryBoxIfAvailable()` from it, passing the same `results` array already held in memory for export:

```javascript
let _drAiAssistAvailable = false;

async function checkAiSummaryAvailability() {
  try {
    const resp = await fetch('/api/device-review/ai-summary-status');
    const data = await resp.json();
    _drAiAssistAvailable = !!data.available;
  } catch (e) {
    _drAiAssistAvailable = false;
  }
}

function showAiSummaryBoxIfAvailable(adom, results, checks) {
  const box = document.getElementById('drAiSummaryBox');
  if (!box) return;
  if (!_drAiAssistAvailable) { box.style.display = 'none'; return; }
  box.style.display = '';
  const btn = document.getElementById('drAiSummaryBtn');
  const out = document.getElementById('drAiSummaryOutput');
  out.textContent = '';
  btn.disabled = false;
  btn.textContent = 'Summarize with AI';
  btn.onclick = async () => {
    btn.disabled = true;
    btn.textContent = 'Summarizing…';
    out.textContent = '';
    try {
      const resp = await fetch('/api/device-review/ai-summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window._csrfToken || '' },
        body: JSON.stringify({ adom, results, checks }),
      });
      const data = await resp.json();
      if (data.narrative) {
        out.textContent = data.narrative;
      } else {
        out.textContent = 'AI summary unavailable: ' + (data.narrative_error || data.error || 'unknown error');
      }
    } catch (e) {
      out.textContent = 'AI summary request failed: ' + e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Summarize with AI';
    }
  };
}

document.addEventListener('DOMContentLoaded', checkAiSummaryAvailability);
```

At the point in `device_review.js` where a run finishes and the findings table is rendered (the code that currently stores the completed run's per-device results into a page-level variable for CSV/JSON/PDF export), add one call:

```javascript
showAiSummaryBoxIfAvailable(selectedAdom, allDeviceResults, selectedChecks);
```

using whatever the existing variable names are for the current ADOM, the completed results array, and the checks that were run (match the names already used by the export functions in this file — do not introduce new state that duplicates them).

- [ ] **Step 3: Manual verification**

Run: `python wsgi.py`, log in as an admin with `ai_assist_enabled: true` in `app_settings.json`, run a Device Review scan, click **Summarize with AI**, confirm narrative text appears. Set `ai_assist_enabled: false` and confirm the box stays hidden.

- [ ] **Step 4: Commit**

```bash
git add app/templates/device_review.html app/static/js/device_review.js
git commit -m "feat: add Summarize with AI button to Device Review results"
```

---

### Task 4: Scheduled report integration

**Files:**
- Modify: `app/device_review_scheduler.py`
- Modify: `tests/test_device_review_scheduler.py`

**Interfaces:**
- Consumes: `build_narrative(adom, check_summary, results)` from Task 1.
- Produces: `_execute_job` now prepends an "AI Summary" section (or omits it silently on failure/disabled) to both `_build_summary_html()`'s output and `_build_pdf_html_dr()`'s output.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_device_review_scheduler.py`:

```python
def test_execute_job_includes_ai_narrative_when_enabled(jobs_path, monkeypatch):
    import app.device_review_scheduler as sched

    fake_meta = [
        {"key": "trusted_hosts", "name": "Trusted Hosts on Admin Accounts (CIS)",
         "description": "Check trusted hosts"},
    ]
    monkeypatch.setattr("app.device_review_scheduler._CHECKS_META", fake_meta)

    job = sched.create_job({
        "name": "T", "adom": "CorpADOM", "days_of_week": ["MON"], "time": "06:00",
        "checks": ["trusted_hosts"], "check_params": {},
        "format": "pdf", "email": "test@corp.com", "enabled": True,
    })

    fake_results = [
        {"device": "fw-01", "ip": "10.0.0.1",
         "rows": [{"device": "fw-01", "check": "Trusted Hosts on Admin Accounts (CIS)",
                   "result": "FAIL", "interface": "system", "vdom": "root",
                   "ip": "", "detail": "no restriction", "protocols": [],
                   "has_insecure": False, "has_secure": False}],
         "error": None},
    ]

    sent = {}
    monkeypatch.setattr("app.device_review_scheduler._bulk_device_review_adom",
                         lambda *a, **kw: fake_results)
    monkeypatch.setattr("app.device_review_scheduler._send_email",
                         lambda to, subject, body_html, attachments: sent.update(
                             {"body": body_html, "attachments": attachments}))
    monkeypatch.setattr("app.app_settings.get_setting", lambda k, d=None: True)
    monkeypatch.setattr("app.device_review_ai.build_narrative",
                         lambda adom, cs, r: "One admin account needs a trusted-host restriction.")

    sched._execute_job(job["id"])

    assert "One admin account needs a trusted-host restriction." in sent["body"]
    pdf_bytes = sent["attachments"][0]["data"]
    assert b"One admin account needs a trusted-host restriction." in pdf_bytes


def test_execute_job_omits_narrative_when_disabled(jobs_path, monkeypatch):
    import app.device_review_scheduler as sched

    fake_meta = [{"key": "trusted_hosts", "name": "Trusted Hosts on Admin Accounts (CIS)",
                  "description": "d"}]
    monkeypatch.setattr("app.device_review_scheduler._CHECKS_META", fake_meta)
    job = sched.create_job({
        "name": "T", "adom": "CorpADOM", "days_of_week": ["MON"], "time": "06:00",
        "checks": ["trusted_hosts"], "check_params": {},
        "format": "pdf", "email": "test@corp.com", "enabled": True,
    })
    fake_results = [{"device": "fw-01", "ip": "", "rows": [], "error": None}]
    sent = {}
    monkeypatch.setattr("app.device_review_scheduler._bulk_device_review_adom",
                         lambda *a, **kw: fake_results)
    monkeypatch.setattr("app.device_review_scheduler._send_email",
                         lambda to, subject, body_html, attachments: sent.update({"body": body_html}))
    monkeypatch.setattr("app.app_settings.get_setting", lambda k, d=None: False)

    sched._execute_job(job["id"])

    assert "AI Summary" not in sent["body"]


def test_execute_job_narrative_failure_still_sends_email(jobs_path, monkeypatch):
    import app.device_review_scheduler as sched

    fake_meta = [{"key": "trusted_hosts", "name": "Trusted Hosts on Admin Accounts (CIS)",
                  "description": "d"}]
    monkeypatch.setattr("app.device_review_scheduler._CHECKS_META", fake_meta)
    job = sched.create_job({
        "name": "T", "adom": "CorpADOM", "days_of_week": ["MON"], "time": "06:00",
        "checks": ["trusted_hosts"], "check_params": {},
        "format": "pdf", "email": "test@corp.com", "enabled": True,
    })
    fake_results = [{"device": "fw-01", "ip": "", "rows": [], "error": None}]
    sent = {}
    monkeypatch.setattr("app.device_review_scheduler._bulk_device_review_adom",
                         lambda *a, **kw: fake_results)
    monkeypatch.setattr("app.device_review_scheduler._send_email",
                         lambda to, subject, body_html, attachments: sent.update({"body": body_html}))
    monkeypatch.setattr("app.app_settings.get_setting", lambda k, d=None: True)
    monkeypatch.setattr("app.device_review_ai.build_narrative",
                         side_effect=RuntimeError("API down"))

    sched._execute_job(job["id"])  # must not raise

    assert sent["body"]  # email still sent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_device_review_scheduler.py -k ai_narrative -v`
Expected: FAIL — no "AI Summary" text present / `AttributeError` for `app.device_review_ai`

- [ ] **Step 3: Implement**

In `app/device_review_scheduler.py`, add a helper near `_build_summary_html` and call it from `_execute_job`:

```python
def _build_ai_narrative_html(adom: str, check_summary: list[dict], results: list[dict]) -> str:
    """Return an HTML block with the AI narrative, or '' if disabled/unavailable.
    Never raises — narration failure must not break report generation."""
    from app.app_settings import get_setting

    if not get_setting("ai_assist_enabled", False):
        return ""
    try:
        from app.device_review_ai import build_narrative
        text = build_narrative(adom, check_summary, results)
    except Exception as exc:
        app_log("WARNING", "device_review_scheduler",
                f"AI narrative generation failed for {adom}: {exc}")
        return ""
    return (
        f"<h3 style='font-family:sans-serif;margin-top:24px'>AI Summary</h3>"
        f"<p style='font-family:sans-serif;font-size:13px;white-space:pre-wrap'>{_esc(text)}</p>"
    )
```

In `_execute_job`, after `check_summary = _build_check_summary(results, checks)` and before building `body_html`/`attachment`:

```python
        ai_narrative_html = _build_ai_narrative_html(adom, check_summary, results)
```

Modify the `_build_summary_html` call site to accept and splice in the narrative — change the signature to accept an extra parameter and insert it right after the `<p>Devices scanned...</p>` line:

```python
def _build_summary_html(
    adom: str,
    results: list[dict],
    generated_at: str,
    check_summary: list[dict],
    ai_narrative_html: str = "",
) -> str:
    ...
    return f"""
<h2 style="font-family:sans-serif">4THealth+ Device Review — {_esc(adom)}</h2>
<p style="font-family:sans-serif;color:#6b7280">Generated: {generated_at}</p>
<p style="font-family:sans-serif">Devices scanned: {len(results)}</p>
{error_note}
{ai_narrative_html}
{check_summary_html}
{host_summary_html}
<p style="font-family:sans-serif;font-size:11px;color:#9ca3af;margin-top:16px">
  See attached report for full findings detail.
</p>"""
```

Similarly extend `_build_attachment_dr(adom, fmt, results, generated_at, check_summary, ai_narrative_html="")` and `_build_pdf_html_dr(adom, results, generated_at, check_summary, ai_narrative_html="")`, inserting `{ai_narrative_html}` immediately after the `<div class="meta">...</div>` block in the returned HTML string.

Update the two call sites in `_execute_job`:

```python
        body_html = _build_summary_html(adom, results, generated_at, check_summary, ai_narrative_html)
        attachment = _build_attachment_dr(adom, fmt, results, generated_at, check_summary, ai_narrative_html)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_device_review_scheduler.py -v`
Expected: PASS — all existing tests plus the 3 new ones (existing tests must not regress; the new `ai_narrative_html=""` default keeps old call sites/tests working unchanged since `get_setting` is not mocked to `True` there).

- [ ] **Step 5: Commit**

```bash
git add app/device_review_scheduler.py tests/test_device_review_scheduler.py
git commit -m "feat: include AI narrative summary in scheduled Device Review reports"
```
