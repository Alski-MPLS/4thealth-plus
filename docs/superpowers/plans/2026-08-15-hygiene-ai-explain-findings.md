# Rule Hygiene AI "Explain" Finding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-demand "Explain" action to each finding in the Hygiene Analysis results table (Rule Review tab) that generates a plain-English explanation of why the finding matters and a suggested FortiOS CLI remediation snippet.

**Architecture:** A new module `app/hygiene_ai.py` takes one already-computed finding dict (as produced by `app.hygiene.run_checks()` and enriched with `rule_detail`/`shadow_rule`/`shadowing_rule` by `POST /api/hygiene/run`) and calls `app.llm.get_provider().narrate()` to produce an explanation + suggested remediation. It is deliberately per-finding and on-demand (an "Explain" button per row), not a bulk operation — this bounds LLM call volume to what a user actually asks for, unlike Device Review/Config-Delta's report-level summaries. A new endpoint accepts the finding JSON the frontend already has in memory (no re-fetch from FortiManager) and returns the explanation.

**Tech Stack:** Flask, existing `app/llm` provider abstraction, pytest + `unittest.mock`.

**Spec:** Self-contained; context comes from `app/hygiene.py` (check functions, finding shape) and `app/routes/hygiene_routes.py::hygiene_run()` (where `rule_detail`/`shadow_rule`/`shadowing_rule` are attached to each finding before it reaches the frontend), both already in the codebase.

## Global Constraints

- Reuse the existing `ai_assist_enabled` flag from `app/app_settings.py` — no new admin toggle.
- One LLM call per "Explain" click, on a single finding — never bulk-explain all findings in one request (that belongs to a future feature, not this one, and would multiply LLM cost by finding count with no user intent behind each call).
- The explanation is advisory only — it must never be treated as a verdict or auto-applied; any suggested CLI snippet is for the reviewer to inspect, not to execute automatically. This is the same "explain, never compute" boundary already enforced in `app/llm/` for AI Assist.
- Any narration failure must degrade to an inline error message in the UI — it must never break the findings table or any other part of the Hygiene Analysis page.

---

### Task 1: Finding explainer module

**Files:**
- Create: `app/hygiene_ai.py`
- Test: `tests/test_hygiene_ai.py`

**Interfaces:**
- Produces: `explain_finding(finding: dict) -> str`. `finding` is one entry from the `findings` array returned by `POST /api/hygiene/run` — always has `policy_id`, `policy_name`, `seq`, `check`, `detail`; additionally either `rule_detail` (most checks) or both `shadow_rule` and `shadowing_rule` (the `shadow` check only). Raises whatever `provider.narrate()` raises; callers catch it.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for app.hygiene_ai.explain_finding."""
import json
from unittest.mock import patch


def test_explain_finding_with_rule_detail():
    from app.hygiene_ai import explain_finding

    finding = {
        "policy_id": "42", "policy_name": "Allow-Web", "seq": 5,
        "check": "unlogged", "detail": "logtraffic = 'disable' — no traffic logging.",
        "rule_detail": {
            "id": "42", "name": "Allow-Web", "status": "enable", "action": "accept",
            "srcaddr": ["CORP-NET"], "dstaddr": ["all"], "service": ["HTTPS"],
            "srcintf": ["port1"], "dstintf": ["port2"], "fsso_groups": [], "comment": "",
        },
    }

    with patch("app.llm.get_provider") as mock_get_provider:
        mock_get_provider.return_value.narrate.return_value = (
            "This rule allows outbound HTTPS without logging, so matching "
            "traffic leaves no audit trail. Enable logging:\n"
            "config firewall policy\n  edit 42\n    set logtraffic all\n  next\nend"
        )
        result = explain_finding(finding)

    assert "logtraffic all" in result
    mock_get_provider.return_value.narrate.assert_called_once()
    user_prompt = mock_get_provider.return_value.narrate.call_args.kwargs["user_prompt"]
    sent = json.loads(user_prompt)
    assert sent["check"] == "unlogged"
    assert sent["rule_detail"]["name"] == "Allow-Web"


def test_explain_finding_with_shadow_rules():
    from app.hygiene_ai import explain_finding

    finding = {
        "policy_id": "10", "policy_name": "Old-Any-Any", "seq": 2,
        "check": "shadow",
        "detail": "Fully shadowed by rule 'Allow-Any-Outbound' (id 3) which appears earlier.",
        "shadow_rule": {"id": "10", "name": "Old-Any-Any", "status": "enable", "action": "accept",
                         "srcaddr": ["all"], "dstaddr": ["all"], "service": ["ALL"],
                         "fsso_groups": [], "comment": ""},
        "shadowing_rule": {"id": "3", "name": "Allow-Any-Outbound", "status": "enable", "action": "accept",
                            "srcaddr": ["all"], "dstaddr": ["all"], "service": ["ALL"],
                            "fsso_groups": [], "comment": ""},
    }

    with patch("app.llm.get_provider") as mock_get_provider:
        mock_get_provider.return_value.narrate.return_value = "Rule 10 will never match traffic; consider removing it."
        result = explain_finding(finding)

    assert "never match" in result
    user_prompt = mock_get_provider.return_value.narrate.call_args.kwargs["user_prompt"]
    sent = json.loads(user_prompt)
    assert sent["shadow_rule"]["id"] == "10"
    assert sent["shadowing_rule"]["id"] == "3"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hygiene_ai.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.hygiene_ai'`

- [ ] **Step 3: Write the implementation**

```python
"""AI explanations for individual Rule Hygiene findings.

Per-finding and on-demand only (one "Explain" click = one LLM call) — this
is deliberately not a bulk operation. The LLM never re-runs or overrides a
check from app.hygiene — it only explains an already-computed finding and
suggests (never applies) a FortiOS CLI remediation snippet, mirroring the
"explain, never compute" boundary used throughout app/llm/.
"""

from __future__ import annotations

import json


def explain_finding(finding: dict) -> str:
    """Return an AI-written explanation + suggested remediation for one
    Rule Hygiene finding.

    Raises whatever the configured provider's narrate() raises — callers
    must catch this and show an inline error instead of an explanation.
    """
    from app.llm import get_provider

    payload = {
        "check": finding.get("check", ""),
        "policy_name": finding.get("policy_name", ""),
        "policy_id": finding.get("policy_id", ""),
        "detail": finding.get("detail", ""),
    }
    if "shadow_rule" in finding and "shadowing_rule" in finding:
        payload["shadow_rule"] = finding["shadow_rule"]
        payload["shadowing_rule"] = finding["shadowing_rule"]
    elif "rule_detail" in finding:
        payload["rule_detail"] = finding["rule_detail"]

    provider = get_provider()
    return provider.narrate(
        system_prompt=(
            "You are a firewall rule hygiene assistant. You are given one "
            "already-computed hygiene finding (from a fixed set of checks: "
            "unnamed, unlogged, shadow, disabled, expired, unhit) as JSON, "
            "along with the affected rule's fields. Explain in 2-4 "
            "sentences why this finding matters from a security/operations "
            "standpoint for the specific rule shown, then suggest a FortiOS "
            "CLI snippet that would remediate it. Never invent rule fields "
            "not present in the JSON, and never claim the change has been "
            "applied — the snippet is a suggestion for a human reviewer."
        ),
        user_prompt=json.dumps(payload, default=str),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hygiene_ai.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/hygiene_ai.py tests/test_hygiene_ai.py
git commit -m "feat: add Rule Hygiene AI finding explainer"
```

---

### Task 2: On-demand API endpoint

**Files:**
- Modify: `app/routes/hygiene_routes.py`
- Test: `tests/test_hygiene_ai_route.py`

**Interfaces:**
- Consumes: `explain_finding(finding)` from Task 1.
- Produces: `GET /api/hygiene/ai-explain-status` → `{"available": bool}`; `POST /api/hygiene/explain-finding` — body is the finding dict itself (as the frontend already holds it from the `/api/hygiene/run` response) → `{"narrative": str|None, "narrative_error": str|None}`, `503` if disabled, `400` if `check` is missing.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for POST /api/hygiene/explain-finding."""
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


def test_explain_disabled_returns_503(client):
    with patch("app.app_settings.get_setting", return_value=False):
        resp = _post(client, "/api/hygiene/explain-finding", {
            "check": "unlogged", "policy_name": "Allow-Web", "policy_id": "42",
            "detail": "logtraffic disabled", "rule_detail": {},
        })
    assert resp.status_code == 503


def test_explain_missing_check_returns_400(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = _post(client, "/api/hygiene/explain-finding", {"detail": "x"})
    assert resp.status_code == 400


def test_explain_success(client):
    finding = {
        "check": "unlogged", "policy_name": "Allow-Web", "policy_id": "42",
        "detail": "logtraffic disabled", "rule_detail": {"name": "Allow-Web"},
    }
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.hygiene_ai.explain_finding", return_value="Explanation text.") as mock_explain:
        resp = _post(client, "/api/hygiene/explain-finding", finding)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["narrative"] == "Explanation text."
    assert data["narrative_error"] is None
    mock_explain.assert_called_once_with(finding)


def test_explain_failure_returns_200_with_error(client):
    finding = {"check": "unlogged", "policy_name": "Allow-Web", "policy_id": "42",
               "detail": "x", "rule_detail": {}}
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.hygiene_ai.explain_finding", side_effect=RuntimeError("API down")):
        resp = _post(client, "/api/hygiene/explain-finding", finding)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["narrative"] is None
    assert data["narrative_error"] == "API down"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hygiene_ai_route.py -v`
Expected: FAIL — `404 NOT FOUND`

- [ ] **Step 3: Add the routes**

Add to `app/routes/hygiene_routes.py`, near the other `/api/hygiene/*` routes:

```python
# ── AI Explain ─────────────────────────────────────────────────────────────


@bp.route("/api/hygiene/ai-explain-status")
@tab_required("rule_hygiene")
def hygiene_ai_explain_status():
    from app.app_settings import get_setting

    return jsonify({"available": get_setting("ai_assist_enabled", False)})


@bp.route("/api/hygiene/explain-finding", methods=["POST"])
@tab_required("rule_hygiene")
def hygiene_explain_finding():
    """Explain one already-computed Rule Hygiene finding. The LLM never
    re-runs a check — app.hygiene.run_checks() already produced this
    finding. Best-effort: any failure degrades to narrative=None, never
    a 500."""
    from app.app_settings import get_setting

    if not get_setting("ai_assist_enabled", False):
        return jsonify({"error": "AI Assist is not enabled"}), 503

    finding = request.get_json(silent=True) or {}
    if not finding.get("check"):
        return jsonify({"error": "check is required"}), 400

    from app.hygiene_ai import explain_finding

    narrative = None
    narrative_error = None
    try:
        narrative = explain_finding(finding)
    except Exception as exc:
        narrative_error = str(exc)

    return jsonify({"narrative": narrative, "narrative_error": narrative_error})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_hygiene_ai_route.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/routes/hygiene_routes.py tests/test_hygiene_ai_route.py
git commit -m "feat: add on-demand Rule Hygiene AI explain-finding endpoint"
```

---

### Task 3: Frontend — "Explain" button per finding row

**Files:**
- Modify: `app/static/js/hygiene.js`

**Interfaces:**
- Consumes: `GET /api/hygiene/ai-explain-status`; `POST /api/hygiene/explain-finding` body = the finding object itself, from Task 2. Reuses the existing expand/detail-row mechanism already in `hygiene.js` (`hasDetail`, `shadow-expand-btn`, `shadow-detail-row` — see `app/static/js/hygiene.js` lines ~255-286) so "Explain" appears as a second action inside that same expandable area rather than adding a new UI pattern.

- [ ] **Step 1: Add an availability check on page load**

Near the top of `hygiene.js` (module scope, alongside other init calls already run on `DOMContentLoaded`):

```javascript
let _hygieneAiExplainAvailable = false;

async function checkHygieneAiExplainAvailability() {
  try {
    const resp = await fetch('/api/hygiene/ai-explain-status');
    const data = await resp.json();
    _hygieneAiExplainAvailable = !!data.available;
  } catch (e) {
    _hygieneAiExplainAvailable = false;
  }
}

document.addEventListener('DOMContentLoaded', checkHygieneAiExplainAvailability);
```

- [ ] **Step 2: Extend the findings row renderer**

In the finding-row rendering code (`app/static/js/hygiene.js` around line 255-286, the block building `mainRow`/`detailRow`), always treat a row as expandable when AI Explain is available, even if it has no `shadow_rule`/`rule_detail`, and add an Explain button + output area inside the detail row:

```javascript
    const hasDetail    = isShadow || !!f.rule_detail || _hygieneAiExplainAvailable;
    const expandBtn = hasDetail
      ? ` <button class="shadow-expand-btn" data-target="${rowId}" title="Show rule details" aria-expanded="false">&#9660;</button>`
      : '';
    const mainRow = `<tr class="${hasDetail ? 'shadow-finding-row' : ''}" ${hasDetail ? `data-target="${rowId}"` : ''}>
      <td style="font-size:.8rem;color:var(--text-muted)">${esc(String(f.seq || '—'))}</td>
      <td><strong>${esc(f.policy_name)}</strong>${f.policy_id && f.policy_id !== f.policy_name ? `<br><span style="font-size:.75rem;color:var(--text-muted)">id: ${esc(f.policy_id)}</span>` : ''}</td>
      <td><span class="hygiene-badge" style="background:${color}20;color:${color};border-color:${color}40">${esc(label)}</span></td>
      <td style="font-size:.82rem">${esc(f.detail)}${expandBtn}</td>
    </tr>`;

    if (!hasDetail) return mainRow;

    let detailContent;
    if (isShadow) {
      detailContent = ruleCard(f.shadow_rule, 'Shadowed Rule (hidden — never hit)') +
                      ruleCard(f.shadowing_rule, 'Shadowing Rule (earlier — intercepts traffic)');
    } else if (f.rule_detail) {
      detailContent = ruleCard(f.rule_detail, 'Rule Details');
    } else {
      detailContent = '';
    }

    const explainBlock = _hygieneAiExplainAvailable ? `
      <div class="hygiene-ai-explain" style="margin-top:8px">
        <button class="btn btn-secondary hygiene-explain-btn" type="button" data-finding-idx="${i}">Explain</button>
        <div class="hygiene-explain-output" style="margin-top:6px;font-size:.85rem;line-height:1.5;white-space:pre-wrap"></div>
      </div>` : '';

    const detailRow = `<tr id="${rowId}" class="shadow-detail-row" style="display:none">
      <td colspan="4">
        <div class="shadow-detail-wrap">${detailContent}${explainBlock}</div>
      </td>
    </tr>`;
    return mainRow + detailRow;
  }).join('') || `<tr><td colspan="4" class="empty-state" style="padding:.85rem 1rem">No findings match your filter.</td></tr>`;

  tbody.innerHTML = rowsHtml;
  renderPagination(total);
  wireExplainButtons();
}
```

- [ ] **Step 3: Add the click handler**

Add a new function, called at the end of the render function (as shown above) after `tbody.innerHTML = rowsHtml;`:

```javascript
function wireExplainButtons() {
  document.querySelectorAll('.hygiene-explain-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const idx = parseInt(btn.dataset.findingIdx, 10);
      const finding = filtered()[idx];
      if (!finding) return;
      const out = btn.nextElementSibling;
      btn.disabled = true;
      btn.textContent = 'Explaining…';
      out.textContent = '';
      try {
        const resp = await fetch('/api/hygiene/explain-finding', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window._csrfToken || '' },
          body: JSON.stringify(finding),
        });
        const data = await resp.json();
        out.textContent = data.narrative || ('AI explanation unavailable: ' + (data.narrative_error || data.error || 'unknown error'));
      } catch (e) {
        out.textContent = 'AI explanation request failed: ' + e.message;
      } finally {
        btn.disabled = false;
        btn.textContent = 'Explain';
      }
    });
  });
}
```

`filtered()` must already exist in this file (it is referenced at line ~330 when building the export payload — `filtered().map(...)`); reuse it rather than re-deriving the filtered/paginated findings list, so `data-finding-idx` lines up with the same array the table was rendered from.

- [ ] **Step 4: Manual verification**

Run: `python wsgi.py`, log in as admin with `ai_assist_enabled: true`, run a Hygiene Analysis scan, expand a finding, click **Explain**, confirm explanation text (and suggested CLI) appears below the rule detail card. Confirm the Explain button/section is absent when `ai_assist_enabled` is false.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/hygiene.js
git commit -m "feat: add per-finding Explain button to Rule Hygiene findings table"
```
