# Changelog

All notable changes to 4THealth+ are documented in this file.

## [Unreleased]

### Added
- AI-generated trend summary on the Admin page's host resource graphs
  (CPU/Memory/Disk): deterministic 7-day trend statistics (percent change,
  slope, days-to-threshold projection) computed in Python, then phrased by
  the configured LLM provider on demand (`app/host_metrics_ai.py`,
  `GET /admin/api/host-metrics/ai-summary`). Reuses the existing
  `ai_assist_enabled` flag.
- AI-generated narrative summary for Config-Delta install-preview diffs, both
  on-demand (per-device "Summarize with AI" button) and in scheduled export
  emails (`app/pending_changes_ai.py`,
  `POST /api/pending-changes/adoms/<adom>/device/<device>/ai-summary`).
- Per-finding "Explain" action in Rule Hygiene Analysis: AI-written
  explanation plus a suggested FortiOS CLI remediation snippet for a single
  finding (`app/hygiene_ai.py`, `POST /api/hygiene/explain-finding`).
- AI-generated narrative summary for Device Review (CIS check) results, both
  on-demand and in scheduled email/PDF reports
  (`app/device_review_ai.py`, `POST /api/device-review/ai-summary`).
- AI Assist mode in Rule Validation: single-request change analysis powered by
  a ported deterministic planner (`app/planner/`) plus multi-provider LLM
  narration (`app/llm/` — Claude default, Codex, Ollama). Admin-gated via a
  new `ai_assist_enabled` setting; existing bulk CSV/XLSX workflow unchanged.
- Initial fork from [4THealth](https://github.com/) as the 4THealth+ base repository.
- Rebranded user-facing text (page titles, nav, CLI help, generated email/report
  content, documentation) from "4THealth" to "4THealth+". Internal identifiers
  (Python package name, systemd service name, Docker image/container names,
  file paths, RADIUS/AD literal values) intentionally left unchanged to match
  the existing deployment tooling.
