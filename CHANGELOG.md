# Changelog

All notable changes to 4THealth+ are documented in this file.

## [Unreleased]

### Added
- 8 new Device Review CIS checks, ported from the sibling
  [4THealth](https://github.com/Alski-MPLS/4thealth) repo (18 -> 26 total
  checks): `admin_mfa`, `hostname_changed`, `admin_port_nondefault`,
  `prelogin_banner`, `timezone_set`, `vpn_weak_crypto`, `vpn_pfs`, and
  `vpn_ike_version`. The 3 VPN checks add `FMGClient.get_device_ipsec_phase1()`/
  `get_device_ipsec_phase2()` and new `ipsec_phase1`/`ipsec_phase2` data keys.
  Ported with upstream's same-day field-name fixes already applied
  (`admin-sport`/`adminsport` and `pre-login-banner`/`preloginbanner`
  fallback lookups, `None`-sentinel for timezone so a valid `0` isn't
  misread as unset).
- FQDN Allowlist mode in Rule Validation's AI Assist panel: submit a vendor's
  FQDN/wildcard-FQDN allowlist request — manual entry rows or an uploaded
  `.xlsx` sheet — and get a deterministic per-firewall coverage analysis plus
  proposed FortiGate CLI (address objects, destination group, policy) for
  anything not already covered, with the same best-effort LLM narration
  guarantee as the existing single-change mode
  (`app/planner/fqdn_intake.py`, `app/planner/engine.py::plan_fqdn_change`,
  `POST /api/rule-review/ai-assist-fqdn`). Existing `plan_change()` now
  rejects non-IP src/dst up front, pointing callers at `plan_fqdn_change()`.
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

### Fixed
- CLI injection in generated FortiGate config: FQDN/vendor/category-derived
  object, group, and policy names/comments were interpolated into generated
  CLI without escaping. Closed with two defense layers — input sanitization
  in `app/planner/engine.py` plus `_safe_cli_str()` escaping at every CLI
  generation sink in `app/planner/cli_gen.py`, including `policy_cli()`
  (shared with the pre-existing IP-based planning path).
- FQDN Allowlist mode's rendered CLI showed the literal `<TICKET_ID>`
  placeholder in every object/group/policy comment even when a real ticket
  ID was supplied — the substitution was never wired through. Fixed for
  both the FQDN and IP-based planning paths (`app/planner/cli_gen.py`,
  `app/planner/engine.py`).
- `app/planner/fetch.py`'s routing-table parsing read the *static route
  config* field names (`dst`/`device`) instead of the field names FortiOS's
  live monitor API actually returns (`ip_mask`/`interface`), so the
  default-route/interface-resolution fallback silently found nothing
  against real FortiManager data even when a default route was plainly
  visible in the Firewalls tab.
- FQDN Allowlist mode's destination-interface resolution now looks up the
  device's default route (`0.0.0.0/0`) directly instead of inferring it by
  longest-prefix-matching a hardcoded `8.8.8.8` sentinel IP against the
  routing table — avoids a false match against an unrelated, more-specific
  static route that happens to cover that one sentinel IP.
- FQDN Allowlist mode no longer emits a spurious "'ANY' is not a valid
  IP/CIDR" warning alongside the correct built-in-`all`-object warning when
  the source IP is `any`/`all`/a named object.
- FQDN Allowlist mode's result panel now shows a labeled summary (policy
  name, target package, interfaces, service) above the generated CLI,
  split into "New Address/Service Objects", "New Destination Group", and
  "New Policy" sections, instead of one unlabeled block of concatenated
  CLI (`app/static/js/rule_review.js`).
