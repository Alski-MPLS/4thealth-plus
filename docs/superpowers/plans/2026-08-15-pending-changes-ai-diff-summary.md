# Config-Delta AI Diff Summarizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an AI-generated plain-English summary of FortiManager install-preview CLI diffs — available on-demand next to a single device's diff panel, and automatically included in scheduled Config-Delta export emails covering multiple devices.

**Architecture:** A new module `app/pending_changes_ai.py` turns the already-parsed diff structure (`parse_preview_diff()`'s `{summary, vdoms}` output, already computed by `app/fmg_client.py`) into a short prose description via `app.llm.get_provider().narrate()`. Two integration points: (1) a new on-demand endpoint hit from a "Summarize with AI" button in the diff panel of the Config-Delta tab, given the diff the page already fetched; (2) `app/config_diff_scheduler.py::_execute_job`, which prepends a per-device (or per-batch) narrative to the scheduled export email body and PDF-HTML attachment. Narration is best-effort everywhere — the raw CLI diff always remains the ground truth and is never replaced, only preceded by a summary.

**Tech Stack:** Flask, existing `app/llm` provider abstraction, pytest + `unittest.mock`.

**Spec:** Self-contained; context comes from `app/fmg_client.py::parse_preview_diff()`, `app/routes/pending_changes_routes.py` (`bulk_preview_adom()`, the single-device preview task), and `app/config_diff_scheduler.py` (scheduled export builder), all already in the codebase.

## Global Constraints

- Reuse the existing `ai_assist_enabled` flag from `app/app_settings.py` — no new admin toggle.
- Narration is advisory only: the raw CLI diff (`vdoms[].changes[].line`) is always shown/exported unmodified. The AI summary is a label above it, never a replacement.
- Diff text can be large (some previews run to hundreds of lines) — cap what is sent to the LLM per device (first N changed lines) and cap the number of devices detailed in a bulk/scheduled summary (aggregate the rest by counts only), so payload size does not scale unbounded with ADOM size.
- Any narration failure must degrade silently to "no summary" — it must never prevent a diff from rendering in the UI or an export email from sending.

---

### Task 1: Diff narrative builder module

**Files:**
- Create: `app/pending_changes_ai.py`
- Test: `tests/test_pending_changes_ai.py`

**Interfaces:**
- Produces: `build_diff_narrative(adom: str, devices: list[dict]) -> str`. `devices` is a list of `{"device": str, "summary": dict, "vdoms": list[dict]}` — the same shape as one or more `parse_preview_diff()` results merged with a `"device"` key (matches both the single-device preview result and each entry of `bulk_preview_adom()`'s output). Raises whatever `provider.narrate()` raises; callers catch it.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for app.pending_changes_ai.build_diff_narrative."""
import json
from unittest.mock import patch


def test_build_diff_narrative_single_device():
    from app.pending_changes_ai import build_diff_narrative

    devices = [{
        "device": "fw-01",
        "summary": {"firewall_policy": 2, "routing": 0, "address": 1, "service": 0, "system": 0, "other": 0},
        "vdoms": [{"name": "root", "changes": [
            {"type": "add", "line": "edit 12"},
            {"type": "add", "line": "set srcaddr \"CORP-NET\""},
            {"type": "remove", "line": "set dstaddr \"OLD-DMZ\""},
        ]}],
    }]

    with patch("app.llm.get_provider") as mock_get_provider:
        mock_get_provider.return_value.narrate.return_value = "Adds one new firewall policy allowing CORP-NET; removes a stale DMZ reference."
        narrative = build_diff_narrative("CorpADOM", devices)

    assert "CORP-NET" in narrative
    mock_get_provider.return_value.narrate.assert_called_once()
    user_prompt = mock_get_provider.return_value.narrate.call_args.kwargs["user_prompt"]
    assert "fw-01" in user_prompt
    assert "CorpADOM" in user_prompt


def test_build_diff_narrative_caps_lines_and_devices():
    from app.pending_changes_ai import build_diff_narrative

    many_changes = [{"type": "add", "line": f"set field{i} \"x\""} for i in range(200)]
    devices = [
        {"device": f"fw-{i:02d}", "summary": {}, "vdoms": [{"name": "root", "changes": many_changes}]}
        for i in range(30)
    ]

    with patch("app.llm.get_provider") as mock_get_provider:
        mock_get_provider.return_value.narrate.return_value = "summary"
        build_diff_narrative("CorpADOM", devices)

    user_prompt = mock_get_provider.return_value.narrate.call_args.kwargs["user_prompt"]
    sent = json.loads(user_prompt)
    assert len(sent["devices"]) <= 20
    for dev in sent["devices"]:
        total_lines = sum(len(v["changes"]) for v in dev["vdoms"])
        assert total_lines <= 30
    assert sent["devices_total"] == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pending_changes_ai.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pending_changes_ai'`

- [ ] **Step 3: Write the implementation**

```python
"""AI narrative summaries for Config-Delta (FortiManager install-preview) diffs.

The LLM never alters or re-derives a diff line — app.fmg_client.parse_preview_diff()
already parsed the raw CLI text. This module only turns the already-structured
diff into a short prose description, via the same provider-agnostic app.llm
interface used elsewhere in the app. The raw CLI diff is always shown/exported
alongside the summary, never replaced by it.
"""

from __future__ import annotations

import json

_MAX_LINES_PER_DEVICE = 30
_MAX_DEVICES_DETAILED = 20


def _trim_device(dev: dict) -> dict:
    """Return a copy of one device's parsed diff, capped to _MAX_LINES_PER_DEVICE
    total change lines across all its VDOMs."""
    remaining = _MAX_LINES_PER_DEVICE
    vdoms_out = []
    for vdom in dev.get("vdoms", []):
        if remaining <= 0:
            break
        changes = vdom.get("changes", [])[:remaining]
        remaining -= len(changes)
        vdoms_out.append({"name": vdom.get("name", "root"), "changes": changes})
    return {"device": dev.get("device", ""), "summary": dev.get("summary", {}), "vdoms": vdoms_out}


def build_diff_narrative(adom: str, devices: list[dict]) -> str:
    """Return an AI-written narrative summary of one or more device diffs.

    Raises whatever the configured provider's narrate() raises — callers
    must catch this and continue without a narrative.
    """
    from app.llm import get_provider

    with_changes = [d for d in devices if any(v.get("changes") for v in d.get("vdoms", []))]
    detailed = [_trim_device(d) for d in with_changes[:_MAX_DEVICES_DETAILED]]
    omitted_count = max(0, len(with_changes) - _MAX_DEVICES_DETAILED)

    payload = {
        "adom": adom,
        "devices_total": len(devices),
        "devices_with_changes": len(with_changes),
        "devices": detailed,
        "additional_devices_with_changes_not_detailed": omitted_count,
    }

    provider = get_provider()
    return provider.narrate(
        system_prompt=(
            "You are a firewall change analyst assistant. You are given "
            "already-parsed FortiManager install-preview CLI diffs (config "
            "adds/removes/modifies awaiting push to devices) as JSON. Write "
            "a short summary (2-6 sentences, or one short bullet per device "
            "if there are several) describing what is actually changing — "
            "e.g. new/removed policies, address or service object changes, "
            "routing changes — in plain English for an engineer reviewing "
            "pending changes before they are pushed. Never invent a change "
            "or omit that changes exist — only describe what is present in "
            "the JSON."
        ),
        user_prompt=json.dumps(payload, default=str),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pending_changes_ai.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pending_changes_ai.py tests/test_pending_changes_ai.py
git commit -m "feat: add Config-Delta AI diff narrative builder"
```

---

### Task 2: On-demand single-device endpoint

**Files:**
- Modify: `app/routes/pending_changes_routes.py`
- Test: `tests/test_pending_changes_ai_route.py`

**Interfaces:**
- Consumes: `build_diff_narrative(adom, devices)` from Task 1.
- Produces: `GET /api/pending-changes/ai-summary-status` → `{"available": bool}`; `POST /api/pending-changes/adoms/<adom>/device/<device>/ai-summary` — body `{"summary": dict, "vdoms": list}` (the parsed diff the page already has in memory from the existing preview task result) → `{"narrative": str|None, "narrative_error": str|None}`, `503` if disabled, `400` if `vdoms` missing.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pending_changes_ai_route.py -v`
Expected: FAIL — `404 NOT FOUND`

- [ ] **Step 3: Add the routes**

Add to `app/routes/pending_changes_routes.py`, near the other `/api/pending-changes/*` routes:

```python
# ── AI Summary ─────────────────────────────────────────────────────────────


@bp.route("/api/pending-changes/ai-summary-status")
@tab_required("pending_changes")
def pc_ai_summary_status():
    from app.app_settings import get_setting

    return jsonify({"available": get_setting("ai_assist_enabled", False)})


@bp.route(
    "/api/pending-changes/adoms/<adom>/device/<device>/ai-summary", methods=["POST"]
)
@tab_required("pending_changes")
def pc_ai_summary(adom: str, device: str):
    """Narrate an already-parsed install-preview diff for one device. The LLM
    never alters a diff line — it only summarizes what parse_preview_diff()
    already produced. Best-effort: any failure degrades to narrative=None."""
    if err := check_adom_access(adom):
        return err

    from app.app_settings import get_setting

    if not get_setting("ai_assist_enabled", False):
        return jsonify({"error": "AI Assist is not enabled"}), 503

    data = request.get_json(silent=True) or {}
    vdoms = data.get("vdoms")
    if not vdoms:
        return jsonify({"error": "vdoms is required"}), 400

    from app.pending_changes_ai import build_diff_narrative

    devices = [{"device": device, "summary": data.get("summary", {}), "vdoms": vdoms}]

    narrative = None
    narrative_error = None
    try:
        narrative = build_diff_narrative(adom, devices)
    except Exception as exc:
        narrative_error = str(exc)

    return jsonify({"narrative": narrative, "narrative_error": narrative_error})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pending_changes_ai_route.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/routes/pending_changes_routes.py tests/test_pending_changes_ai_route.py
git commit -m "feat: add on-demand Config-Delta AI diff summary endpoint"
```

---

### Task 3: Frontend — diff panel "Summarize with AI" button

**Files:**
- Modify: `app/templates/pending_changes.html`
- Modify: `app/static/js/pending_changes.js`

**Interfaces:**
- Consumes: `GET /api/pending-changes/ai-summary-status`; `POST /api/pending-changes/adoms/<adom>/device/<device>/ai-summary` from Task 2, called with the `{summary, vdoms}` already held in the page's current diff state (the same object the diff panel renders from — find the variable holding the last-fetched preview `result` in `pending_changes.js` and reuse it, do not re-fetch).

- [ ] **Step 1: Add the UI container to the template**

In `app/templates/pending_changes.html`, inside the diff panel (above the per-VDOM CLI diff rendering, below the three status badges), add:

```html
<div id="pcAiSummaryBox" style="display:none; margin: 10px 0; padding: 10px 14px; border: 1px solid var(--border-color); border-radius: 6px; background: var(--bg-subtle);">
  <button id="pcAiSummaryBtn" class="btn btn-secondary" type="button">Summarize with AI</button>
  <div id="pcAiSummaryOutput" style="margin-top: 8px; font-size: 0.88rem; line-height: 1.5; white-space: pre-wrap;"></div>
</div>
```

- [ ] **Step 2: Wire it up in pending_changes.js**

Add near the top of `pending_changes.js`:

```javascript
let _pcAiAssistAvailable = false;

async function checkPcAiSummaryAvailability() {
  try {
    const resp = await fetch('/api/pending-changes/ai-summary-status');
    const data = await resp.json();
    _pcAiAssistAvailable = !!data.available;
  } catch (e) {
    _pcAiAssistAvailable = false;
  }
}

function showPcAiSummaryBoxIfAvailable(adom, device, diffResult) {
  const box = document.getElementById('pcAiSummaryBox');
  if (!box) return;
  const hasChanges = (diffResult.vdoms || []).some(v => (v.changes || []).length);
  if (!_pcAiAssistAvailable || !hasChanges) { box.style.display = 'none'; return; }
  box.style.display = '';
  const btn = document.getElementById('pcAiSummaryBtn');
  const out = document.getElementById('pcAiSummaryOutput');
  out.textContent = '';
  btn.disabled = false;
  btn.textContent = 'Summarize with AI';
  btn.onclick = async () => {
    btn.disabled = true;
    btn.textContent = 'Summarizing…';
    out.textContent = '';
    try {
      const resp = await fetch(
        `/api/pending-changes/adoms/${encodeURIComponent(adom)}/device/${encodeURIComponent(device)}/ai-summary`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window._csrfToken || '' },
          body: JSON.stringify({ summary: diffResult.summary, vdoms: diffResult.vdoms }),
        }
      );
      const data = await resp.json();
      out.textContent = data.narrative || ('AI summary unavailable: ' + (data.narrative_error || data.error || 'unknown error'));
    } catch (e) {
      out.textContent = 'AI summary request failed: ' + e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Summarize with AI';
    }
  };
}

document.addEventListener('DOMContentLoaded', checkPcAiSummaryAvailability);
```

At the point in `pending_changes.js` where the diff panel finishes rendering a completed preview task's `result` (the polling handler that currently populates the CLI diff view from `data.result`), add:

```javascript
showPcAiSummaryBoxIfAvailable(currentAdom, result.device, result);
```

using whatever the existing variable names are for the selected ADOM and the just-received `result` object (match names already used in this file's polling/render code; do not introduce duplicate state).

- [ ] **Step 3: Manual verification**

Run: `python wsgi.py`, log in as admin with `ai_assist_enabled: true`, open Config-Delta, click a device with pending changes, click **Summarize with AI**, confirm narrative text appears above the raw diff. Confirm the box stays hidden for devices with no changes, and when `ai_assist_enabled` is false.

- [ ] **Step 4: Commit**

```bash
git add app/templates/pending_changes.html app/static/js/pending_changes.js
git commit -m "feat: add Summarize with AI button to Config-Delta diff panel"
```

---

### Task 4: Scheduled export integration

**Files:**
- Modify: `app/config_diff_scheduler.py`
- Test: `tests/test_config_diff_scheduler.py`

**Interfaces:**
- Consumes: `build_diff_narrative(adom, devices)` from Task 1, where `devices` is built from `bulk_preview_adom()`'s per-device results (each already has `device`, `summary`, `vdoms`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config_diff_scheduler.py`:

```python
def test_execute_job_includes_ai_narrative_when_enabled(jobs_path, monkeypatch):
    import app.config_diff_scheduler as sched

    job = sched.create_job({
        "name": "T", "adom": "CorpADOM", "days_of_week": ["MON"], "time": "06:00",
        "format": "pdf", "email": "test@corp.com", "enabled": True,
    })

    fake_results = [
        {"device": "fw-01", "ip": "10.0.0.1", "status": "ok", "pkg_status": "modified",
         "summary": {"firewall_policy": 1}, "vdoms": [{"name": "root", "changes": [
             {"type": "add", "line": "edit 1"}]}], "raw": "edit 1", "error": None},
    ]

    sent = {}
    monkeypatch.setattr(
        "app.routes.pending_changes_routes.bulk_preview_adom",
        lambda adom, max_workers=1: fake_results,
    )
    monkeypatch.setattr(
        "app.smtp_client.send_email",
        lambda to, subject, body_html, attachments: sent.update(
            {"body": body_html, "attachments": attachments}),
    )
    monkeypatch.setattr("app.app_settings.get_setting", lambda k, d=None: True)
    monkeypatch.setattr(
        "app.pending_changes_ai.build_diff_narrative",
        lambda adom, devices: "Adds one firewall policy on fw-01.",
    )

    sched._execute_job(job["id"])

    assert "Adds one firewall policy on fw-01." in sent["body"]


def test_execute_job_narrative_failure_still_sends_email(jobs_path, monkeypatch):
    import app.config_diff_scheduler as sched

    job = sched.create_job({
        "name": "T", "adom": "CorpADOM", "days_of_week": ["MON"], "time": "06:00",
        "format": "pdf", "email": "test@corp.com", "enabled": True,
    })
    fake_results = [{"device": "fw-01", "ip": "", "status": "no_changes", "pkg_status": "",
                      "summary": {}, "vdoms": [], "raw": "", "error": None}]
    sent = {}
    monkeypatch.setattr(
        "app.routes.pending_changes_routes.bulk_preview_adom",
        lambda adom, max_workers=1: fake_results,
    )
    monkeypatch.setattr(
        "app.smtp_client.send_email",
        lambda to, subject, body_html, attachments: sent.update({"body": body_html}),
    )
    monkeypatch.setattr("app.app_settings.get_setting", lambda k, d=None: True)
    monkeypatch.setattr(
        "app.pending_changes_ai.build_diff_narrative",
        lambda adom, devices: (_ for _ in ()).throw(RuntimeError("API down")),
    )

    sched._execute_job(job["id"])  # must not raise

    assert sent["body"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config_diff_scheduler.py -k ai_narrative -v`
Expected: FAIL — narrative text absent from `sent["body"]`

- [ ] **Step 3: Implement**

In `app/config_diff_scheduler.py`, add a helper near `_build_summary_html`:

```python
def _build_ai_narrative_html(adom: str, results: list[dict]) -> str:
    """Return an HTML block with the AI narrative, or '' if disabled/unavailable.
    Never raises — narration failure must not break export generation."""
    from app.app_settings import get_setting

    if not get_setting("ai_assist_enabled", False):
        return ""
    try:
        from app.pending_changes_ai import build_diff_narrative
        text = build_diff_narrative(adom, results)
    except Exception as exc:
        app_log("WARNING", "config_diff_scheduler",
                f"AI narrative generation failed for {adom}: {exc}")
        return ""
    return f"<h3>AI Summary</h3><p style='white-space:pre-wrap'>{text}</p>"
```

In `_execute_job`, after `results = bulk_preview_adom(adom, max_workers=1)` and before building `body_html`:

```python
        ai_narrative_html = _build_ai_narrative_html(adom, results)
```

Modify `_build_summary_html(adom, results, ai_narrative_html="")` to splice it in right after the opening `<h2>...</h2>` line, and update the call site:

```python
        body_html = _build_summary_html(adom, results, ai_narrative_html)
```

Similarly thread `ai_narrative_html=""` through `_build_attachment(...)` → `_build_pdf_html(...)`, inserting it directly under the `<h1>`/header block, and update the `attachment = _build_attachment(adom, fmt, results, generated_at, ai_narrative_html)` call site.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config_diff_scheduler.py -v`
Expected: PASS — all existing tests (unaffected by the new optional trailing parameter) plus the 2 new ones.

- [ ] **Step 5: Commit**

```bash
git add app/config_diff_scheduler.py tests/test_config_diff_scheduler.py
git commit -m "feat: include AI narrative summary in scheduled Config-Delta exports"
```
