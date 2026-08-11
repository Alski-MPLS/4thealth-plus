# Phase 2: AI-Assisted Rule Validation — Design

## Context

Phase 1 (see [2026-08-10-phase1-repo-scaffold-design.md](2026-08-10-phase1-repo-scaffold-design.md)) produced 4THealth+ as a clean, working rebrand of 4THealth with no AI functionality — the Rule Validation tab works exactly as it did in 4THealth (deterministic, `policy_db.json`-based, no LLM).

Phase 2 adds AI-assisted analysis to that tab, drawing on 4tAnalyst's deterministic change-planning engine. The two systems were investigated in detail before this design was written (see the architecture report referenced in conversation); the key findings that shape this design:

- 4THealth+'s current Rule Validation tab (`app/rule_review.py`) already does its own flow analysis, insertion-point calculation, and CLI generation against local `policy_db.json` — independently of 4tAnalyst's planner.
- 4tAnalyst's `planner/` package (`~/code/github/ai/4tanalyst/planner/`) is a more capable, purely deterministic engine (`plan_change`) with **no LLM dependency and no MCP dependency** — it's a plain Python library. It handles multi-firewall consolidation, object reuse/creation, first-match shadowing insertion analysis, and a "Option B" group-append alternative.
- 4tAnalyst's production `fwanalyst_server` actually imports a different, external package (`fgplanner`, published separately) rather than the local `planner/` — an internal inconsistency in 4tAnalyst, with `fgplanner` not even properly declared as a dependency. The local `planner/` is self-contained and not entangled in that issue.
- 4tAnalyst today has **zero LLM SDK code anywhere** — the "LLM" in its architecture is entirely external (the engineer's own Claude Code, talking to `fwanalyst_server` over MCP). All multi-provider LLM-calling logic for phase 2 is new work, not a port.
- 4THealth+ already exposes a bearer-token external API (`/external/api/zone/query` etc., `app/routes/external_api_routes.py`) that 4tAnalyst's `zone_mcp`/`planner` consume today, over HTTP, as if 4THealth were a separate remote service.

## Decisions

These were worked through one at a time during brainstorming; each is stated with its rationale so implementation doesn't have to re-derive it.

**1. Engine: add AI Assist as a new mode; port the planner for it; leave the existing bulk table alone.**
The existing bulk CSV/XLSX table view in Rule Validation keeps its current engine (`app/rule_review.py`) completely unchanged in phase 2 — it works today and 4tAnalyst's planner isn't built for batch analysis of many flows at once (it consolidates *within* one request across firewalls, not *across* many independent requests). A new **AI Assist** single-request mode is added alongside it, powered by 4tAnalyst's local `planner/` package (not `fgplanner`) ported into 4THealth+ as a direct Python dependency. Reasoning for porting `planner/` specifically: it's self-contained, already in the 4tAnalyst monorepo, and not tangled in the `fgplanner` version-drift problem found there.

**2. Data access: planner calls 4THealth+'s own clients directly, in-process.**
The ported planner's `zone_client`/`fmg_client` abstraction (currently HTTP calls to a remote 4THealth + separate `credentials.yaml`) is adapted to call `app/fmg_client.py` and `app/zone_db.py` directly as Python functions. One FortiManager connection, one `policy_db.json`, no redundant network hop, no second credentials file to maintain.

**3. Scope: `plan_change` + standards validation only. Defer the rest.**
Phase 2 ports `planner/` plus `standards_mcp`'s naming/logging/approval-chain checks (since `plan_change` already depends on those). Explicitly deferred to a later phase: FortiManager read-only query tools (`fortimanager_mcp`), feedback/audit history (`feedback_mcp`, SQLite-backed), and `.xlsx` intake parsing (`intake_mcp`). Zone tooling is already covered by decision 2 — no separate `zone_mcp` port needed.

**4. No MCP server. Direct in-process tool calls, single-shot LLM completion.**
MCP exists to expose tools to an *external* LLM client (the engineer's own Claude Code, in 4tAnalyst's current model). Since 4THealth+ itself will originate the LLM API call server-side, there's no external client to serve — MCP would be pure overhead. Further, because of decision 5 below (structured form, not chat), the LLM doesn't even need a tool-calling loop: it receives one already-computed, structured result and produces narrative text in a single completion call. No MCP, no agentic tool-use loop.

**5. Interaction: structured form input, deterministic-first, LLM narrates only.**
The engineer fills the same kind of structured form Rule Validation already uses today (src/dst/service/firewalls/ticket/justification) — no natural-language parsing, no chat-based request intake. On submit: `plan_change` runs deterministically and its structured result (verdict, insertion point, CLI, object plan) renders immediately and unconditionally. That result is then sent to the configured LLM in one completion call to produce a human-readable narrative report and peer-review package text. The LLM never computes or edits the verdict — same invariant 4tAnalyst's current system prompt already enforces, just moved from a prompt instruction to a structural guarantee (the LLM literally isn't given tools to recompute anything). If the LLM call fails (bad key, rate limit, network), the deterministic result still renders in full; the narrative section shows "AI summary unavailable" rather than blocking the page.

**6. Multi-provider: server-wide default, admin-configured, no per-request choice.**
A provider-abstraction module (`app/llm/`) supports Claude (Anthropic SDK, default), Codex (OpenAI SDK), and Ollama (local or cloud). Configured via `.env`: `AI_PROVIDER=claude` by default, with other providers' key variables present but commented out until an admin fills them in and switches `AI_PROVIDER`. This matches the original project brief ("server will have the details present for API calls but will need the details (commented out)"). No per-request or per-user provider picker — one active provider for the whole server at a time. AI assistance is additionally gated by a feature flag using the existing `app_settings.py` pattern, independent of whether a provider key is configured, so an admin can disable AI Assist entirely without removing credentials.

**7. Access control: reuse the existing `rule_review` tab permission.**
No new RBAC. Any user with access to the Rule Validation tab today gets access to the new AI Assist mode within it.

**8. Path-relevance checking is preserved as a 4THealth+-specific layer.**
4tAnalyst's planner has no equivalent of 4THealth+'s existing interface/route-based "is this firewall actually in the traffic path" check (`check_path_relevance` in `app/rule_review.py`). That logic has no source in `planner/` to port from — it stays as-is and wraps around the planner's output on the new AI Assist path too, the same way it already wraps the existing engine's output today.

## What Phase 2 Delivers

An **AI Assist** mode within the existing Rule Validation tab:
1. Engineer fills a structured request form (src/dst/service, target firewalls, ticket ID, justification).
2. 4THealth+ runs the ported `plan_change` engine in-process against its own FortiManager connection and zone-policy data — deterministic, no LLM involved, verdict renders immediately.
3. The structured plan result is sent to the configured LLM (Claude by default) in a single completion call, which produces a narrative report and peer-review package text.
4. Standards validation (naming, logging, approval chain) runs as part of `plan_change` and is reflected in both the structured result and the narrative.
5. If the LLM call fails, the deterministic result still displays in full.

The existing bulk CSV/XLSX table workflow in Rule Validation is untouched.

## Explicitly Out of Scope for Phase 2

- FortiManager read-only query tools (`fortimanager_mcp`'s 17 tools)
- Feedback/audit history (`feedback_mcp`)
- `.xlsx`/manual intake parsing (`intake_mcp`)
- Migrating the bulk CSV/XLSX table view onto the planner engine
- Per-request or per-user LLM provider selection
- Any MCP server, local or otherwise
- New RBAC/permissions beyond the existing `rule_review` tab gate

## Open Items for Implementation Planning (not decided here)

- Exact module layout for the ported planner and `app/llm/` package (file-by-file structure belongs in the implementation plan, not this design).
- Exact `.env` variable names for each provider's credentials and the `AI_PROVIDER`/feature-flag settings.
- Prompt template content for the LLM narration step.
- Whether the peer-review package is downloadable (HTML/CLI file) following the same export pattern as other tabs (Config-Delta, Device Review), or presented inline only — needs a quick look at `rule_review.html`'s current export UI before the plan is written.
- Test strategy for the ported planner (reuse 4tAnalyst's existing planner test suite as a starting point) and for the new LLM-calling code (must not require live API calls in CI — needs a mocking/fixture approach per provider).
