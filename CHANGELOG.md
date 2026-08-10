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
