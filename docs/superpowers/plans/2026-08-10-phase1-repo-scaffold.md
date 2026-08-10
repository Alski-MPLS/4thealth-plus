# Phase 1: 4THealth+ Repo Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a working copy of the 4THealth application, renamed 4THealth+, at `~/code/github/ai/4thealth-plus`, with refreshed docs, verified to run standalone.

**Architecture:** Copy 4THealth's git-tracked files verbatim into the new repo (minus two exclusions), then apply a scoped rebrand pass that only touches user-visible text (page titles, nav, CLI help text, generated email/report content, prose docs) — never code identifiers, package names, file paths, systemd/Docker/RADIUS/AD literal names. Finish with a rewritten README, a new CHANGELOG, and a runnable-app verification.

**Tech Stack:** Python 3.11+, Flask, uv, pytest — unchanged from 4THealth.

## Global Constraints

- Source of truth for "what to copy": `git -C ~/code/github/web/4thealth ls-files`, minus `.claude/settings.json`, everything under `docs/superpowers/`, and `temp_secret.txt` (tracked but empty in the source; excluded during execution because Claude Code's security classifier blocks any git operation naming a file called `temp_secret.txt` regardless of content — decided 2026-08-10).
- Rebrand rule (applies to every task below): rename "4THealth" → "4THealth+" (and "4thealth" → "4thealth+" where lowercase form is itself prose, not an identifier) **only** in text a human reads as the product name — page titles, nav bar, CLI `--help` text, generated report/email subjects and bodies, and prose in docs/comments. **Never** rename: the Python package name (`pyproject.toml`, `uv.lock`), Docker image/container names, systemd service names, GitLab CI runner tags/service-account names, Ansible inventory group names/variable names, RADIUS NAS-Identifier bytes, filesystem path defaults (`/opt/4thealth`, `/var/backups/4thealth`, `/var/log/4thealth`, temp-file prefixes), or example AD/RADIUS group names in `.env.example`/`admin.html` placeholders.
- Every task that changes a `.py` file whose tests assert on the changed string must update those tests in the same task, and the full suite must stay green.
- Target repo has no remote configured; do not push anywhere in this plan.

---

### Task 1: Bulk copy source tree

**Files:**
- Create: ~151 files under `~/code/github/ai/4thealth-plus/` (mirrors 4THealth's tracked tree)

**Interfaces:**
- Produces: the full 4THealth+ file tree that every later task edits in place.

- [ ] **Step 1: Build the copy manifest and copy files**

```bash
cd ~/code/github/web/4thealth
git ls-files | grep -v '^docs/superpowers/' | grep -v '^\.claude/settings\.json$' | grep -v '^temp_secret\.txt$' > /tmp/4thealth-manifest.txt
wc -l /tmp/4thealth-manifest.txt   # expect 151

rsync -av --files-from=/tmp/4thealth-manifest.txt \
  ~/code/github/web/4thealth/ ~/code/github/ai/4thealth-plus/
```

- [ ] **Step 2: Verify the copy**

```bash
cd ~/code/github/ai/4thealth-plus
find . -type f -not -path './.git/*' -not -path './docs/superpowers/specs/*' -not -path './plan.md' | wc -l
# expect 151 (docs/superpowers/specs/ and plan.md are pre-existing files from the brainstorming phase, excluded from this count on purpose)
diff <(sort /tmp/4thealth-manifest.txt) <(cd ~/code/github/ai/4thealth-plus && git ls-files -o --exclude-standard -- . ':!docs/superpowers/specs' ':!plan.md' | sort)
# expect no output (every manifest file landed, nothing extra)
```

- [ ] **Step 3: Commit**

```bash
cd ~/code/github/ai/4thealth-plus
git add -A -- . ':!docs/superpowers/specs' ':!plan.md'
git commit -m "Copy 4THealth base app into 4THealth+ (unmodified)"
```

---

### Task 2: Rebrand templates and their tests

**Files:**
- Modify: `app/templates/base.html:7,20`
- Modify: `app/templates/dashboard.html:2`
- Modify: `app/templates/device_review.html:2`
- Modify: `app/templates/firewalls.html:2`
- Modify: `app/templates/hygiene.html:2`
- Modify: `app/templates/login.html:6,11`
- Modify: `app/templates/map.html:2`
- Modify: `app/templates/pending_changes.html:2`
- Modify: `app/templates/rule_review.html:2`
- Modify: `app/templates/versions.html:2`
- Modify: `app/templates/zone_policy.html:2`
- Modify: `app/templates/admin.html:2` (title block only — do not touch lines 177, 379, 430, 576, which are unrelated example/default values)

**Interfaces:**
- Consumes: files produced by Task 1.
- Produces: no new interfaces; purely text changes.

- [ ] **Step 1: Apply the renames**

```bash
cd ~/code/github/ai/4thealth-plus
for f in app/templates/base.html app/templates/dashboard.html \
         app/templates/device_review.html app/templates/firewalls.html \
         app/templates/hygiene.html app/templates/login.html \
         app/templates/map.html app/templates/pending_changes.html \
         app/templates/rule_review.html app/templates/versions.html \
         app/templates/zone_policy.html; do
  sed -i '' 's/4THealth/4THealth+/g' "$f"
done
# admin.html: only the title block on line 2
sed -i '' '2s/4THealth/4THealth+/' app/templates/admin.html
```

- [ ] **Step 2: Verify only the intended lines changed**

```bash
grep -rn '4THealth+' app/templates/
# expect exactly: base.html:7, base.html:20, dashboard.html:2, device_review.html:2,
# firewalls.html:2, hygiene.html:2, login.html:6, login.html:11, map.html:2,
# pending_changes.html:2, rule_review.html:2, versions.html:2, zone_policy.html:2,
# admin.html:2

grep -n '4thealth' app/templates/admin.html
# expect lines 177, 379, 430, 576 still present with plain "4thealth" (unchanged)
```

- [ ] **Step 3: Run the app test suite to confirm no template test broke**

```bash
uv sync
uv run pytest -q
```

Expected: all tests pass (no test asserts on these exact title strings).

- [ ] **Step 4: Commit**

```bash
git add app/templates/
git commit -m "Rebrand page titles and nav to 4THealth+"
```

---

### Task 3: Rebrand app-generated user-facing strings (CLI, email, reports)

**Files:**
- Modify: `manage_users.py:55`
- Modify: `app/smtp_client.py:1,111,112`
- Modify: `app/config_diff_scheduler.py:238,347,428`
- Modify: `app/device_review_scheduler.py:256,612,676,879`
- Modify: `app/static/js/help.js:12,13,542`
- Modify: `logo.svg:1`
- Modify: `tests/test_config_diff_scheduler.py:134,154`
- Modify: `tests/test_device_review_scheduler.py:298`

**Interfaces:**
- Consumes: files produced by Task 1.
- Produces: no new interfaces; purely text/string-literal changes. Test expectations updated to match.

- [ ] **Step 1: Rebrand `manage_users.py`**

Change line 55:
```python
parser = argparse.ArgumentParser(description="4THealth user management")
```
to:
```python
parser = argparse.ArgumentParser(description="4THealth+ user management")
```

- [ ] **Step 2: Rebrand `app/smtp_client.py`**

Line 1, change:
```python
"""SMTP email client — wraps stdlib smtplib for 4THealth scheduled exports."""
```
to:
```python
"""SMTP email client — wraps stdlib smtplib for 4THealth+ scheduled exports."""
```

Lines 111-112, change:
```python
            "4THealth SMTP Test",
            "<p>SMTP connection test from 4THealth — if you received this, SMTP is working.</p>",
```
to:
```python
            "4THealth+ SMTP Test",
            "<p>SMTP connection test from 4THealth+ — if you received this, SMTP is working.</p>",
```

- [ ] **Step 3: Rebrand `app/config_diff_scheduler.py`**

Line 238, change:
```python
        subject = f"4THealth Config-Delta — {adom} — {generated_at[:10]}"
```
to:
```python
        subject = f"4THealth+ Config-Delta — {adom} — {generated_at[:10]}"
```

Line 347, change:
```python
        w.writerow(["# Generated by: 4THealth Config-Delta Scheduler"])
```
to:
```python
        w.writerow(["# Generated by: 4THealth+ Config-Delta Scheduler"])
```

Line 428, change:
```python
        f"<td>4THealth Config-Delta Scheduler</td></tr>"
```
to:
```python
        f"<td>4THealth+ Config-Delta Scheduler</td></tr>"
```

- [ ] **Step 4: Rebrand `app/device_review_scheduler.py`**

Line 256, change:
```python
        subject = f"4THealth Device Review — {adom} — {generated_at[:10]}"
```
to:
```python
        subject = f"4THealth+ Device Review — {adom} — {generated_at[:10]}"
```

Line 612, change:
```python
<h2 style="font-family:sans-serif">4THealth Device Review — {_esc(adom)}</h2>
```
to:
```python
<h2 style="font-family:sans-serif">4THealth+ Device Review — {_esc(adom)}</h2>
```

Line 676, change:
```python
        w.writerow(["# 4THealth Device Review"])
```
to:
```python
        w.writerow(["# 4THealth+ Device Review"])
```

Line 879, change:
```python
<h1>4THealth Device Review Scheduler</h1>
```
to:
```python
<h1>4THealth+ Device Review Scheduler</h1>
```

- [ ] **Step 5: Rebrand `app/static/js/help.js`**

Line 12, change:
```html
<h3>What is 4THealth?</h3>
```
to:
```html
<h3>What is 4THealth+?</h3>
```

Line 13, change:
```html
<p>4THealth is a read-only monitoring dashboard for your Fortinet infrastructure. It connects to FortiManager's API and displays live health data — no configuration changes are ever made to any device.</p>
```
to:
```html
<p>4THealth+ is a read-only monitoring dashboard for your Fortinet infrastructure. It connects to FortiManager's API and displays live health data — no configuration changes are ever made to any device.</p>
```

Line 542, change:
```html
  <div class="faq-a">No. 4THealth is strictly read-only. All API calls use <code>action: get</code> — no configuration endpoints are ever called.</div>
```
to:
```html
  <div class="faq-a">No. 4THealth+ is strictly read-only. All API calls use <code>action: get</code> — no configuration endpoints are ever called.</div>
```

- [ ] **Step 6: Rebrand `logo.svg`**

Line 1, change:
```
aria-label="4tHealth logo"
```
to:
```
aria-label="4tHealth+ logo"
```

- [ ] **Step 7: Update dependent tests**

`tests/test_config_diff_scheduler.py` line 134, change:
```python
    assert "4THealth Config-Delta Scheduler" in html
```
to:
```python
    assert "4THealth+ Config-Delta Scheduler" in html
```

Line 154, change:
```python
    assert "4THealth" in html
```
to:
```python
    assert "4THealth+" in html
```

`tests/test_device_review_scheduler.py` line 298, change:
```python
    assert "4THealth" in html
```
to:
```python
    assert "4THealth+" in html
```

- [ ] **Step 8: Run the full test suite**

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add manage_users.py app/smtp_client.py app/config_diff_scheduler.py \
        app/device_review_scheduler.py app/static/js/help.js logo.svg \
        tests/test_config_diff_scheduler.py tests/test_device_review_scheduler.py
git commit -m "Rebrand CLI help text, generated emails, and reports to 4THealth+"
```

---

### Task 4: Rebrand Ansible/CI email and report text

**Files:**
- Modify: `Ansible/4thealth_healthcheck.yml:2,4,43,60,62,81,370`
- Modify: `Ansible/group_vars/4thealth_prod.yml:33`
- Modify: `.gitlab-ci.yml:1`

**Interfaces:**
- Consumes: files produced by Task 1.
- Produces: no new interfaces; purely text changes. Systemd service names, host group names, and file paths in these files are deliberately left unchanged (see Global Constraints).

- [ ] **Step 1: Rebrand `Ansible/4thealth_healthcheck.yml`**

Line 2, change:
```yaml
# 4THealth Production Health Check
```
to:
```yaml
# 4THealth+ Production Health Check
```

Line 4, change:
```yaml
# Runs a comprehensive set of checks against the production 4THealth server
```
to:
```yaml
# Runs a comprehensive set of checks against the production 4THealth+ server
```

Line 43, change:
```yaml
#   app_host            FQDN or IP of the 4THealth server (used for curl and TLS checks)
```
to:
```yaml
#   app_host            FQDN or IP of the 4THealth+ server (used for curl and TLS checks)
```

Line 60, change:
```yaml
#   email_subject_prefix Subject prefix (default: "[4THealth]")
```
to:
```yaml
#   email_subject_prefix Subject prefix (default: "[4THealth+]")
```

Line 62, change:
```yaml
- name: 4THealth Production Health Check
```
to:
```yaml
- name: 4THealth+ Production Health Check
```

Line 81, change:
```yaml
    email_subject_prefix: "[4THealth]"
```
to:
```yaml
    email_subject_prefix: "[4THealth+]"
```

Line 370, change:
```yaml
          4THealth health check reported CRITICAL status.
```
to:
```yaml
          4THealth+ health check reported CRITICAL status.
```

- [ ] **Step 2: Rebrand `Ansible/group_vars/4thealth_prod.yml`**

Line 33, change:
```yaml
email_subject_prefix: "[4THealth]"
```
to:
```yaml
email_subject_prefix: "[4THealth+]"
```

- [ ] **Step 3: Rebrand `.gitlab-ci.yml`**

Line 1, change:
```yaml
# CI/CD pipeline for 4THealth
```
to:
```yaml
# CI/CD pipeline for 4THealth+
```

Leave line 2 and everything below unchanged — those reference the actual GitLab runner tag and service account name (`4thealth`), which stay as-is.

- [ ] **Step 4: Verify no infra identifiers were touched**

```bash
cd ~/code/github/ai/4thealth-plus
grep -n '4thealth' .gitlab-ci.yml Ansible/4thealth_healthcheck.yml Ansible/group_vars/4thealth_prod.yml
```
Expected: still shows the systemd service name, `/opt/4thealth`, `4thealth_prod` host group, runner tag `4thealth`, and sudo user `4thealth` — all unchanged (lowercase, no `+`).

- [ ] **Step 5: Commit**

```bash
git add Ansible/4thealth_healthcheck.yml Ansible/group_vars/4thealth_prod.yml .gitlab-ci.yml
git commit -m "Rebrand Ansible health-check emails and CI comment to 4THealth+"
```

---

### Task 5: Rebrand documentation prose

**Files:**
- Modify: `CLAUDE.md`
- Modify: `CONTRIBUTING.md`
- Modify: `container.md`
- Modify: `docs/authentication.md`
- Modify: `docs/backup.md`
- Modify: `docs/deployment.md`
- Modify: `docs/features.md`
- Modify: `docs/hardening.md`
- Modify: `docs/operations.md`
- Modify: `developer-guide.html`

**Interfaces:**
- Consumes: files produced by Task 1.
- Produces: no new interfaces; purely prose changes.

- [ ] **Step 1: List every occurrence for manual review**

```bash
cd ~/code/github/ai/4thealth-plus
grep -noE '.{0,40}4[Tt][Hh]ealth.{0,40}' CLAUDE.md CONTRIBUTING.md container.md \
  docs/authentication.md docs/backup.md docs/deployment.md docs/features.md \
  docs/hardening.md docs/operations.md developer-guide.html > /tmp/doc-rebrand-review.txt
wc -l /tmp/doc-rebrand-review.txt
```

- [ ] **Step 2: Apply the rename to prose occurrences only**

For each match in `/tmp/doc-rebrand-review.txt`, replace "4THealth" with "4THealth+" in running prose (headings, sentences, list items) — this is the vast majority of matches in these files, since they are documentation, not config.

**Skip** (leave unchanged) any match that sits inside inline code (single backticks) or a fenced code block naming an actual system value: `/opt/4thealth`, `4thealth.service`, `4thealth:latest`, `name = "4thealth"`, `sudo -u 4thealth`, systemd unit names, Docker image/container names, or file/directory paths. These describe the real (unrenamed) infrastructure and must keep matching the running system.

Apply with per-file review, e.g.:

```bash
# Example for a file with no code-literal occurrences of "4thealth" (verify first with grep):
grep -c '`4thealth\|4thealth\.service\|4thealth:latest\|/opt/4thealth\|/var/log/4thealth\|/var/backups/4thealth' docs/features.md
# if 0, safe to blanket-replace the product name:
sed -i '' 's/4THealth/4THealth+/g' docs/features.md
```

For files where the grep above returns a non-zero count (expect this in `CLAUDE.md`, `container.md`, `docs/deployment.md`, `docs/operations.md`, which document real deployment paths and the systemd service), edit by hand: open the file, replace "4THealth" with "4THealth+" everywhere it names the product in prose, and leave every backticked/code-block path, service name, or image name exactly as it reads today.

- [ ] **Step 3: Verify no code-literal was renamed**

```bash
grep -rn '4thealth+\.service\|/opt/4thealth+\|/var/log/4thealth+\|/var/backups/4thealth+\|4thealth+:latest\|name = "4thealth+"' \
  CLAUDE.md CONTRIBUTING.md container.md docs/ developer-guide.html
```
Expected: no output. Any hit means an infra literal was incorrectly renamed — fix it before committing.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md CONTRIBUTING.md container.md docs/ developer-guide.html
git commit -m "Rebrand documentation prose to 4THealth+"
```

---

### Task 6: Rewrite README.md

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: feature table, architecture tree, and quickstart sections already present in the copied README.md (unchanged content, just renamed product name).
- Produces: final README.md for the repo.

- [ ] **Step 1: Rename remaining product-name prose in the existing README**

```bash
cd ~/code/github/ai/4thealth-plus
grep -n '4[Tt][Hh]ealth' README.md
```
Replace "4THealth" → "4THealth+" in every line that is prose (title, alt text, table headers, section text). Do not alter code-block commands (`uv sync`, `python manage_users.py`, etc.) — none of those contain the product name.

- [ ] **Step 2: Rewrite the opening section (lines 1-14)**

Replace the existing title block and intro paragraph with:

```markdown
<img alt="4tHealth+ logo" src="logo.svg" width="240">

# 4THealth+ — Network Operations Dashboard

A read-only web dashboard for monitoring FortiManager, FortiAnalyzer, FortiAuthenticator,
and managed FortiGate firewalls. All FortiGate data flows **through FortiManager's
JSON-RPC API** — no direct device connections are made.

4THealth+ is a fork and successor of 4THealth, rebuilt as the foundation for adding
AI-assisted change analysis to the Rule Validation tab (see Roadmap below).

> **Note**: This is an independent open-source project and is not affiliated with, endorsed by, or supported by Fortinet, Inc. FortiManager is a trademark of Fortinet, Inc.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![FortiManager](https://img.shields.io/badge/FortiManager-7.4.x%20%7C%207.6.x-red)
```

- [ ] **Step 3: Add a Roadmap section**

Insert a new section directly after the existing "## Features" table (before "## Architecture"):

```markdown
---

## Roadmap

4THealth+'s **Rule Validation** tab currently performs zone-policy-based pre-change
analysis (unchanged from 4THealth). A future phase will add AI-assisted analysis to
this tab: engineers will be able to describe a requested change and get research,
standards validation, and peer-review-package generation powered by an LLM of the
engineer's choice (Claude by default, with Codex and Ollama — local or cloud — also
supported). That work is not yet implemented and will be designed and built as its
own phase.
```

- [ ] **Step 4: Verify rendering sanity**

```bash
grep -n '^#' README.md   # confirm heading structure still makes sense top to bottom
grep -c '4THealth+' README.md   # expect > 0
grep -n '4THealth[^+]' README.md   # expect no output — no un-rebranded "4THealth" left outside code
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "Rewrite README.md for 4THealth+ with roadmap section"
```

---

### Task 7: Write CHANGELOG.md

**Files:**
- Create: `CHANGELOG.md`

**Interfaces:**
- Produces: `CHANGELOG.md` at repo root, referenced by the README's Documentation table (already present, pointing to `CHANGELOG.md`).

- [ ] **Step 1: Write the file**

```markdown
# Changelog

All notable changes to 4THealth+ are documented in this file.

## [Unreleased]

### Added
- Initial fork from [4THealth](https://github.com/) as the 4THealth+ base repository.
- Rebranded user-facing text (page titles, nav, CLI help, generated email/report
  content, documentation) from "4THealth" to "4THealth+". Internal identifiers
  (Python package name, systemd service name, Docker image/container names,
  file paths, RADIUS/AD literal values) intentionally left unchanged to match
  the existing deployment tooling.

### Roadmap
- AI-assisted analysis for the Rule Validation tab (multi-LLM provider support:
  Claude default, Codex, Ollama local/cloud) — planned for a future phase, not
  yet designed or implemented.
```

- [ ] **Step 2: Commit**

```bash
cd ~/code/github/ai/4thealth-plus
git add CHANGELOG.md
git commit -m "Add CHANGELOG.md"
```

---

### Task 8: Final verification

**Files:**
- None (verification only; may produce fix commits if issues are found)

**Interfaces:**
- Consumes: the complete repo state from Tasks 1-7.

- [ ] **Step 1: Install dependencies**

```bash
cd ~/code/github/ai/4thealth-plus
uv sync
```

Expected: succeeds with no errors.

- [ ] **Step 2: Run the full test suite**

```bash
uv run pytest -q
```

Expected: all tests pass (0 failures).

- [ ] **Step 3: First-run setup**

```bash
cp .env.example .env
uv run python manage_users.py secret
# paste output into .env as SECRET_KEY=...
cp groups.example.json groups.json
cp infra_targets.example.json infra_targets.json
uv run python manage_users.py add admin --role admin
# set a password when prompted
```

- [ ] **Step 4: Start the app and smoke-test it**

```bash
uv run python wsgi.py &
sleep 2
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5000/login
# expect 200
curl -s http://localhost:5000/login | grep -o '4THealth+' | head -1
# expect "4THealth+" (confirms the rebranded login page is being served)
kill %1
```

- [ ] **Step 5: Fix any issues found, then confirm clean state**

```bash
git status
```

Expected: clean (no uncommitted changes) once any fixes from this task are committed. If Steps 1-4 uncovered a bug, commit the fix with a message describing what was wrong, then re-run Steps 1-4 to confirm.

---
