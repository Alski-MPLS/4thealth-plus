"""Deterministic firewall change planner (ported from 4tAnalyst).

Takes a normalized request (src, dst, service, target firewalls) and computes
the full change plan — zone verdict, existing-rule coverage, object
reuse/create, rule insertion point, and FortiGate CLI — entirely in tested
code. No LLM involvement: app.llm only relays this module's output as
prose; it must never recompute or edit any part of the plan.

Ported from ~/code/github/ai/4tanalyst/planner/ — see VENDORED_FROM.md for
the source commit this is based on.
"""
