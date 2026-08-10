# Phase 1: 4THealth+ Repo Scaffold — Design

## Context

4THealth+ is planned as a merge of two existing projects:

- **4THealth** (`~/code/github/web/4thealth`) — a Flask network-operations dashboard for FortiManager/FortiGate environments. Multiple tabs (Dashboard, Firewalls, Rule Review, Device Review, Rule Validation, Zone Policy, Map, Config-Delta, Backup, Admin), background schedulers, RBAC with local/RADIUS/AD auth.
- **4tAnalyst** (`~/code/github/ai/4tanalyst`) — a separate AI-assisted firewall change-planning system: a deterministic Python planner (no LLM in the decision path) fronted by Claude Code slash commands talking to a central FastMCP server.

The end goal is for 4THealth+'s existing **Rule Validation** tab to gain AI-assisted analysis (multi-provider: Claude default, Codex, Ollama local/cloud), replacing 4tAnalyst's "engineer runs Claude Code against a central MCP server" interaction model with AI functionality embedded directly in the web app.

This merge is too large for one pass. It decomposes into at least two independent sub-projects:

1. **Phase 1 (this doc):** stand up 4THealth+ as a clean, working base repo — a rebuild of 4THealth under the new name, with refreshed docs. No AI/4tAnalyst code yet.
2. **Phase 2 (future, separate spec):** design and implement the AI-assisted Rule Validation feature — multi-LLM provider abstraction, what (if anything) is ported from 4tAnalyst's planner/MCP code, and how the FortiManager connection and LLM API calls fit together.

This spec covers **Phase 1 only**.

## Goal

Produce a working copy of the 4THealth application, renamed 4THealth+, living at `~/code/github/ai/4thealth-plus`, with a fresh git history, refreshed top-level docs, and verified to run standalone — ready to serve as the base for Phase 2.

## Source & Copy Method

- Use `git -C ~/code/github/web/4thealth ls-files` as the copy manifest (191 tracked files as of 2026-08-10). This is the safe source of truth: it already excludes `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.worktrees/`, `.git/`, `graphify-out/`, and every gitignored runtime/secret file (`.env`, `users.json`, `groups.json`, `infra_targets.json`, `policy_db.json`, `summary_history.json`, `smtp_config.json`, `config_diff_jobs.json`, `device_review_jobs.json`, `backup_config.json`, `protocol_severity.json`, `api_tokens.json`, `app_settings.json`) — while still including their `*.example.json` templates, which **are** tracked.
- Two additional exclusions from the manifest, decided during design:
  - `.claude/settings.json` — workspace-local Claude Code preference, not project content.
  - `docs/superpowers/**` (39 files) — historical feature-planning docs tied to 4THealth's own development history; not relevant to a fresh repo.
- Copy the remaining files into `~/code/github/ai/4thealth-plus`, preserving directory structure.
- `git init` fresh in the target — no history carried over from 4THealth. No secrets-in-history risk, matches "not uploading to GitHub until ready."
- Do not push to any remote as part of this phase.

## Rebrand Scope

Docs/UI-visible only — no code identifier changes.

**In scope:** README.md, page `<title>`/nav branding strings in templates, logo alt text, CLAUDE.md, container.md, all `docs/*.md`, CONTRIBUTING.md, SECURITY.md — rename "4THealth" → "4THealth+" where it refers to the product name.

**Out of scope:** Python package/module names, internal variable/function names, directory layout (`app/`, `app/routes/`), environment variable prefixes, systemd service names, Docker image/container names. These stay as-is to avoid churn with no functional benefit.

## Docs to Write/Update

- **README.md** — rewritten intro identifying this as 4THealth+, a fork/successor of 4THealth. Keep the existing feature table, architecture diagram, and quickstart sections (still accurate to the copied code). Add a **Roadmap** section noting that Rule Validation will gain AI-assisted analysis (multi-LLM: Claude default, Codex, Ollama local/cloud) in a future phase, explicitly marked as not yet implemented.
- **CHANGELOG.md** — new file (does not exist yet in the target). First entry: `Unreleased` / `0.1.0`, "Initial fork from 4THealth as 4THealth+ base."
- **.gitignore** — carried over from 4THealth's (env, certs, runtime JSON files, venv, caches, editor/OS files) — already fits this repo's needs; add 4THealth+-specific entries only if something new requires it.
- **CLAUDE.md** — copied from 4THealth and rebranded, so future Claude Code sessions in this repo have accurate project instructions.

## Explicitly Out of Scope for Phase 1

- No 4tAnalyst files (planner, MCP servers, credentials.yaml, etc.) — those stay in `~/code/github/ai/4tanalyst` until Phase 2 deliberately designs what to port.
- No AI provider abstraction, no MCP server, no LLM API wiring.
- Rule Validation tab is copied exactly as it works today in 4THealth (zone-policy-based pre-change analysis against `policy_db.json`, no LLM).

## Verification

Before Phase 1 is considered done:

1. `uv sync` succeeds in the new repo.
2. `uv run pytest` passes (same test suite, copied as-is).
3. App starts standalone: `uv run python wsgi.py`, reachable at `http://localhost:5000`, after completing the same first-run setup steps as 4THealth's README (copy `.env.example` → `.env`, generate `SECRET_KEY`, create first admin user, copy `groups.example.json`/`infra_targets.example.json`).

## Open Items for Phase 2 (not decided here)

- Whether to port 4tAnalyst's `planner/` package into 4THealth+ as a direct dependency, or keep it as a separate service reached over MCP.
- Multi-LLM provider abstraction design (Claude default, Codex, Ollama local/cloud) and where API keys/config live on the server.
- Whether a local MCP server is needed on the same host, and how it relates to 4tAnalyst's existing `fwanalyst_server`.
