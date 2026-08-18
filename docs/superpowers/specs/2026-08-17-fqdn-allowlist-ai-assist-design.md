# FQDN/Wildcard Allowlist AI Assist — Design

## Context

Phase 2 (see [2026-08-10-phase2-ai-rule-validation-design.md](2026-08-10-phase2-ai-rule-validation-design.md))
ported 4tAnalyst's `planner/` package into `app/planner/` as an adapted fork,
powering the existing single-request **AI Assist** mode on the Rule
Validation tab (`plan_change()` — IP/CIDR src/dst/service only).

Since that port (base commit `d7687df5de6c7eace65178836543f367faf6dec9`),
Alan built a new capability in `~/code/github/ai/4tanalyst`: a **vendor FQDN
allowlist planner**. Engineers regularly receive vendor spreadsheets listing
hostnames/wildcards + ports that need outbound access (e.g. "Apple APNs
requires `*.push.apple.com` on tcp/5223, `axm-adm-scep.apple.com` on
tcp/443..."). The new code in 4tAnalyst:

- Resolves FortiManager address objects/groups of `type=fqdn` /
  `type=wildcard-fqdn` to FQDN strings (`FQDNCatalog` in `matching.py`).
- Searches existing policies for coverage of a requested FQDN list, with a
  "partial group match" hint for the group-append alternative
  (`search_fqdn_rules`/`build_fqdn_catalog` in `query.py`).
- Proposes new `fqdn`/`wildcard-fqdn` address objects, one destination group,
  and a policy per firewall for whatever's uncovered
  (`plan_fqdn_change()`/`_plan_fqdn_firewall()` in `engine.py`).
- Generates FortiOS CLI for all of the above, with a CLI-injection escaper
  (`_safe_cli_str`) and a `warn_replace` flag on `addrgrp_create_cli` (FortiOS
  `set member` replaces group membership, so appending to an existing group
  needs a different code path and an explicit warning).
- Parses vendor `.xlsx` sheets or manually-entered rows into a structured
  request (`intake_mcp/fqdn_parser.py` — not previously ported at all).
- Also changed the *existing*, already-ported `plan_change()`: it now
  rejects non-IP src/dst up front rather than mishandling them, pointing the
  caller at `plan_fqdn_change()` instead.

This design covers porting all of the above into 4THealth+ as a second mode
of the existing AI Assist panel, following the same fork-not-dependency
pattern and sync workflow established in Phase 2 (see the
`4tanalyst-sync-workflow` memory note).

## Decisions

**1. Second mode inside the existing AI Assist panel, not a new tab.**
A mode toggle ("Single Change" / "FQDN Allowlist") at the top of the
existing `rrAiAssistSection` swaps the form and result rendering. Same
section, same `ai_assist_enabled` gate, same styling and CLI/peer-review
download affordances — this is additive to an existing feature, not a new
one, and engineers already know where to find AI Assist.

**2. Intake: manual entry rows AND `.xlsx` upload, both feeding one parser.**
The FQDN form has vendor/category/src IP/ticket/target-firewalls fields
(same shape as the existing form) plus a dynamic entry-rows table (FQDN or
wildcard, ports, protocol, required, comment) that can be typed into
directly, or populated by uploading a vendor `.xlsx` sheet. Both paths
resolve to the same `FQDNAllowlistRequest`/`FQDNEntry` structures, built by
one new `app/planner/fqdn_intake.py` module ported from 4tAnalyst's
`intake_mcp/fqdn_parser.py` (`parse_fqdn_rows`, `parse_fqdn_xlsx`, the
column-alias table), stripped of MCP-specific wrapping — it's a plain
parsing library, same as the rest of `app/planner/`. `openpyxl` is already a
4THealth+ dependency, so no new package.

**3. Backend port covers every planner layer touched upstream.**
Ported into the existing files (matching upstream's own diff shape, so
future syncs stay easy to diff):
- `app/planner/matching.py` — `FQDNCatalog` (verbatim; reuses the existing
  `_names` helper already in this file).
- `app/planner/catalogs.py` — `build_fqdn_catalog(client, adom)` and
  `search_fqdn_rules(client, adom, device, fqdns)`, adapted from
  `fortimanager_mcp/query.py` the same way `catalogs.py`'s existing
  `build_catalogs` was adapted: call `app.fmg_client.FMGClient`'s existing
  `get_address_objects`/`get_address_groups`/`get_policy_packages`/
  `get_policies` methods directly, no HTTP, no separate cache layer (matches
  upstream's own "no separate caching" comment).
- `app/planner/models.py` — `FQDNAddressObject`, `FQDNAddrGroup`,
  `FQDNFirewallPlan`, `FQDNChangePlan` (verbatim), plus `FQDNEntry` and
  `FQDNAllowlistRequest` folded in here from `intake_mcp` (decision 2 — no
  separate intake package, so its two data-only models live alongside the
  rest of `app/planner/models.py`).
- `app/planner/cli_gen.py` — `_safe_cli_str`, `fqdn_address_object_cli`,
  `wildcard_fqdn_address_object_cli`, and the `warn_replace` parameter added
  to `addrgrp_create_cli` (verbatim; `addrgrp_create_cli`'s one existing
  caller in `engine.py` is unaffected since the new parameter defaults to
  `False`).
- `app/planner/engine.py` — `plan_fqdn_change()`, `_plan_fqdn_firewall()`,
  `to_fqdn_report_payload()`, and the `_fqdn_object_name`/`_fqdn_group_name`/
  `_is_valid_ip` helpers (verbatim; all their dependencies —
  `fetch_zone_verdict`, `resolve_interfaces`, `TargetFirewall`,
  `GroupAppendAlternative`, `standards.policy_name`/`log_settings`/
  `permissiveness_warnings`, `_default_fmg_client`/`_default_zone_client`,
  `PlannerDataError` — already exist in this repo from the Phase 2 port).
- `naming.yaml` / `naming.example.yaml` — add `fqdn_address`,
  `wildcard_fqdn_address`, `fqdn_destination_group` patterns under
  `platforms.fortigate.conventions`.

**4. Port the `plan_change()` IP/CIDR validation too.**
Upstream's `plan_change()` now raises `PlannerDataError` up front for
non-IP src/dst, pointing callers at `plan_fqdn_change()`. Porting this
keeps the two entry points' contracts in sync with upstream (so future
syncs don't have to special-case a divergence) and gives engineers who
mistakenly enter a hostname in the single-change form an actionable error
instead of undefined behavior further down the pipeline.

**5. Two `wildcard-fqdn` field names are unverified — port as-is, flag it.**
Upstream marks `FQDNCatalog.exact_match_name`/`_fqdns_for_object`'s
`wildcard-fqdn` JSON field access with `# VERIFY` comments — not confirmed
against real FortiManager hardware. Ported verbatim with the same comments
preserved, matching this repo's existing convention for unconfirmed
Fortinet API details (see the FortiAnalyzer/FortiAuthenticator SNMP OID
caveats in `CLAUDE.md`). `type=fqdn`'s `fqdn` field is unaffected — that
half is already relied upon elsewhere and not in question.

**6. New endpoint: `POST /api/rule-review/ai-assist-fqdn`.**
Separate from the existing `/api/rule-review/ai-assist` (different request
shape entirely — vendor/category/entries[] vs. src/dst/service — trying to
overload one endpoint would just mean branching on payload shape). Accepts
either JSON (manual-entry path: `vendor, category, src_ip, ticket_id,
firewalls[], entries[]`) or `multipart/form-data` (upload path: same
scalar fields plus an `.xlsx` file, parsed server-side via
`fqdn_intake.parse_fqdn_xlsx`). Both paths converge on one
`FQDNAllowlistRequest` before calling `plan_fqdn_change()`. Mirrors the
existing route's structure: same `ai_assist_enabled` gate, same
`check_adom_access` per target firewall, same "deterministic result always
returns, narration is best-effort" guarantee, same `PlannerDataError` → 502
/ `FMGError` → 502 / unexpected → 500 error handling via
`internal_api_error`/`upstream_api_error`.

**7. LLM narration: same pattern, FQDN-specific system prompt.**
Same `app/llm.get_provider().narrate()` single-completion call as the
existing route, with a system prompt adjusted for the FQDN plan shape
(per-firewall coverage/proposed objects/group/policy rather than a single
consolidated change) via `to_fqdn_report_payload(plan)`. Same failure mode:
narration failure sets `narrative_error` and never blocks the deterministic
result.

**8. Frontend: mode toggle, dynamic entry table, reused result chrome.**
`rule_review.html`/`rule_review.js`: a toggle switches `rrAiForm` between
the existing single-change fields and a new FQDN form (vendor, category,
src IP, ticket, firewalls — reusing the existing typeahead — plus an
`.xlsx` file input and an "Add row" entry table for FQDN/wildcard, ports,
protocol, required, comment). Submission posts JSON when only manual rows
are present, or `multipart/form-data` when a file is attached. Results
render per-firewall (verdict, coverage badge, proposed objects/group/policy
CLI blocks, warnings), reusing the existing CLI copy/download button
pattern and the peer-review package download.

**9. Provenance and sync bookkeeping.**
`app/planner/VENDORED_FROM.md` gets a new row set for this batch of files
and its synced-to SHA bumped to `2ab05fc52d445a91f75a6696b20f5526bf86a5dd`
(the latest commit touching `planner/`/`fortimanager_mcp` as of this
design). The `4tanalyst-sync-workflow` memory note's file list already
covers `fortimanager_mcp/matching.py`/`query.py`, so no memory update is
needed — it's already generic to "whatever changed in `planner/`/
`standards_mcp/`/those two files."

## What This Delivers

A second **FQDN Allowlist** mode inside the existing AI Assist panel on the
Rule Validation tab:
1. Engineer enters vendor/category/src IP/ticket/target firewalls, then
   either types FQDN/wildcard rows directly or uploads a vendor `.xlsx`
   sheet.
2. 4THealth+ runs `plan_fqdn_change()` in-process: resolves zone verdict
   from src IP, searches existing FortiManager policies for FQDN coverage,
   and — for anything uncovered — proposes `fqdn`/`wildcard-fqdn` address
   objects, one destination group (or a group-append alternative when a
   related group already exists), and a policy, per target firewall.
   Deterministic, renders immediately regardless of LLM availability.
3. The structured plan is sent to the configured LLM in one completion call
   for a narrative report, following the same never-lose-the-plan guarantee
   as the existing single-change path.
4. Generated CLI (address objects, group, policy) is copyable/downloadable
   the same way the existing AI Assist CLI output is.

The existing single-change AI Assist mode and the bulk CSV/XLSX table
workflow are both unaffected except for the one validation change in
decision 4.

## Explicitly Out of Scope

- `intake_mcp`'s other capabilities beyond FQDN parsing (if any) — only
  `fqdn_parser.py` is in scope.
- Any 4tAnalyst code not touched by the `planner/`/`fortimanager_mcp`
  matching/query diff reviewed for this design (e.g. `feedback_mcp`,
  `fwanalyst_server` itself — 4THealth+ doesn't run an MCP server, per the
  Phase 2 design's decision 4, and that decision still holds here).
- Verifying the `wildcard-fqdn` FortiManager JSON field names against real
  hardware (decision 5) — tracked as a known caveat, not blocking this
  work.
- Editing `policy_severity`/CIS-style checks — this is purely additive to
  the AI Assist change-planning path, unrelated to Device Review.
