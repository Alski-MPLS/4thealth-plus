# Changelog

All notable changes to 4THealth+ are documented in this file.

## [Unreleased]

### Added
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
