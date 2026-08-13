# Phase 2: AI-Assisted Rule Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "AI Assist" mode to 4THealth+'s Rule Validation tab, powered by a ported copy of 4tAnalyst's deterministic `planner` engine plus a single-shot LLM narration step (Claude default, Codex/Ollama optional).

**Architecture:** Port 4tAnalyst's `planner/` package into `app/planner/`, adapted to call 4THealth+'s own `app/fmg_client.py`/`app/zone_db.py` directly in-process instead of over HTTP with separate credentials. Add a small multi-provider LLM abstraction (`app/llm/`) that turns the planner's structured output into narrative text via one completion call — no tool-calling loop, no MCP. Wire both together behind a new `POST /api/rule-review/ai-assist` route and a new UI panel in the existing Rule Validation tab. The existing bulk CSV/XLSX table view is untouched.

**Tech Stack:** Python 3.11+, Flask, pytest, PyYAML (new dependency), `anthropic`/`openai` SDKs (new, optional at runtime) — see spec for full rationale.

## Global Constraints

- Source spec: `docs/superpowers/specs/2026-08-10-phase2-ai-rule-validation-design.md` — read it for the "why" behind every decision below; this plan implements it verbatim.
- Ported planner code lives under `app/planner/`. Every ported file's only allowed changes from its 4tAnalyst source are import-path fixes and the specific adaptations named in each task below — do not otherwise "improve" the ported logic.
- No MCP server, no tool-calling loop. The LLM only narrates an already-computed `ChangePlan`; it never recomputes or edits any value in it.
- Provider selection is server-wide via `.env`'s `AI_PROVIDER` (default `claude`), never per-request. AI Assist is additionally gated by an `ai_assist_enabled` flag in `app_settings.json` (default `False`), following the existing `external_api_enabled` pattern in `app/app_settings.py`.
- The existing bulk CSV/XLSX table workflow in Rule Validation (`analyze_flows` in `app/rule_review.py`, its route, its UI) is not modified by this plan.
- Every new/ported Python file needs `pytest` coverage; the full suite (`uv run pytest -q`) must stay green after every task.
- Track provenance: `app/planner/VENDORED_FROM.md` records the exact 4tAnalyst commit SHA the port is based on (Task 7), per the sync workflow in memory `4tanalyst-sync-workflow`.
- Interface-resolution scope trim (decided in this plan, not the spec): the ported `fetch.py` drops 4tAnalyst's third interface-resolution tier (`device_zone_map.yaml` lookup) — connected-subnet match and routing-table longest-prefix match are kept. This avoids porting a whole second config-file subsystem for a fallback tier; document this trim in code comments where it was removed.
- Catalog building (`app/planner/catalogs.py`) drops 4tAnalyst's TTL cache/thread-lock layer — 4THealth+ calls it once per in-process AI Assist request, not from a shared multi-engineer server, so the caching complexity has no payoff here.

---

### Task 1: Port planner's dependency-free modules (models, CLI generation, matching)

**Files:**
- Create: `app/planner/__init__.py`
- Create: `app/planner/models.py`
- Create: `app/planner/cli_gen.py`
- Create: `app/planner/matching.py`
- Test: `tests/test_planner_models.py`
- Test: `tests/test_planner_cli_gen.py`
- Test: `tests/test_planner_matching.py`

**Interfaces:**
- Produces: `PlannerDataError(source, detail)`, `NormalizedFlow(src, dst, service, srcs=[], dsts=[], services=[], service_ranges=[], justification="")` with `.pairs` property, `TargetFirewall(device, adom)`, `ObjectPlan(role, action, name, obj_type, value, cli="")`, `InsertionPlan(package, insert_before_policy_id, rationale, shadowed_by=[], would_shadow=[])`, `GroupAppendAlternative(package, policy_id, policy_name, side, group, members, group_cli="", direct_cli="", affected_policies=[], warnings=[])`, `FirewallPlan(firewall, adom, status, covering_rules=[], partial_matches=[], objects=[], policy_name="", policy_cli="", srcintf="", dstintf="", insertion=None, alternative=None, warnings=[])`, `ChangePlan(ticket_id, flow, zone_verdict, risk_level, firewalls, cli_status, recommendation, warnings=[], naming={}, logging={}, approval={})` with `.to_dict()` — all from `app.planner.models`.
- Produces: `PortRange(protocol, start, end)` (frozen dataclass, `.contains()`, `.overlaps()`), `WILDCARD_RANGE`, `parse_service_request(service, protocol_hint="")`, `ServiceCatalog(custom_objects, groups)`, `AddressCatalog(objects, groups, global_objects=(), global_groups=())`, `MatchResult`, `PolicyMatcher(addr_catalog, svc_catalog)`, `_names(field)` — all from `app.planner.matching`.
- Produces: `address_object_cli`, `service_object_cli`, `policy_cli`, `exception_comment`, `addrgrp_append_cli`, `policy_addr_append_cli`, `addrgrp_create_cli` — all from `app.planner.cli_gen`.
- Consumes: nothing from other tasks — this is the base layer everything else imports.

- [ ] **Step 1: Create `app/planner/__init__.py`**

```python
"""Deterministic firewall change planner (ported from 4tAnalyst).

Takes a normalized request (src, dst, service, target firewalls) and computes
the full change plan — zone verdict, existing-rule coverage, object
reuse/create, rule insertion point, and FortiGate CLI — entirely in tested
code. No LLM involvement: app.llm only relays this module's output as
prose; it must never recompute or edit any part of the plan.

Ported from ~/code/github/ai/4tanalyst/planner/ — see VENDORED_FROM.md for
the source commit this is based on.
"""
```

- [ ] **Step 2: Create `app/planner/matching.py` verbatim**

Copy the exact content below (this is 4tAnalyst's `fortimanager_mcp/matching.py` unmodified — it has zero internal-package imports, only stdlib `ipaddress`/`dataclasses`, so it needs no adaptation):

```python
"""
Set-semantics matching for FortiManager policy analysis.

Replaces the substring-based service/address matching previously in query.py:
service references are resolved to numeric (protocol, port-range) sets and
address references to ipaddress networks, so "80" can never match TCP_8080.

Resolution rules:
  - Unknown object names resolve to None — callers must treat that as
    "cannot prove a match", never as a silent non-match or match.
  - Group references recurse with a cycle guard.
  - "all"/"any" (case-insensitive) are wildcards.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

_WILDCARD_PROTOCOLS = ("ip",)


@dataclass(frozen=True)
class PortRange:
    """A contiguous destination-port range on one protocol.

    protocol "ip" (with 0-65535) is the wildcard: it contains/overlaps
    everything. "icmp" has no ports; it is stored as a full range and only
    matches other icmp/ip entries.
    """

    protocol: str  # "tcp" | "udp" | "sctp" | "icmp" | "ip"
    start: int
    end: int

    def _proto_compatible(self, other: "PortRange") -> bool:
        return (
            self.protocol in _WILDCARD_PROTOCOLS
            or other.protocol in _WILDCARD_PROTOCOLS
            or self.protocol == other.protocol
        )

    def contains(self, other: "PortRange") -> bool:
        if not self._proto_compatible(other):
            return False
        if self.protocol in _WILDCARD_PROTOCOLS:
            return True
        if other.protocol in _WILDCARD_PROTOCOLS:
            return False
        return self.start <= other.start and other.end <= self.end

    def overlaps(self, other: "PortRange") -> bool:
        if not self._proto_compatible(other):
            return False
        if self.protocol in _WILDCARD_PROTOCOLS or other.protocol in _WILDCARD_PROTOCOLS:
            return True
        return self.start <= other.end and other.start <= self.end


WILDCARD_RANGE = PortRange("ip", 0, 65535)

# Fallback for conversationally-entered service names. FortiGate's predefined
# services live in the same custom-service table and take precedence when a
# ServiceCatalog is consulted; this table only backs parse_service_request.
_WELL_KNOWN: dict[str, list[PortRange]] = {
    "ssh": [PortRange("tcp", 22, 22)],
    "https": [PortRange("tcp", 443, 443)],
    "http": [PortRange("tcp", 80, 80)],
    "rdp": [PortRange("tcp", 3389, 3389)],
    "dns": [PortRange("tcp", 53, 53), PortRange("udp", 53, 53)],
    "ntp": [PortRange("udp", 123, 123)],
    "snmp": [PortRange("udp", 161, 161)],
    "syslog": [PortRange("udp", 514, 514)],
    "smtp": [PortRange("tcp", 25, 25)],
    "ftp": [PortRange("tcp", 21, 21)],
    "telnet": [PortRange("tcp", 23, 23)],
    "smb": [PortRange("tcp", 445, 445)],
    "ldap": [PortRange("tcp", 389, 389)],
    "ldaps": [PortRange("tcp", 636, 636)],
    "mssql": [PortRange("tcp", 1433, 1433)],
    "mysql": [PortRange("tcp", 3306, 3306)],
    "postgres": [PortRange("tcp", 5432, 5432)],
    "icmp": [PortRange("icmp", 0, 65535)],
    "ping": [PortRange("icmp", 0, 65535)],
}


def _parse_port_expr(expr: str, protocol: str) -> PortRange:
    """Parse "8443" or "8000-8100" into a PortRange (raises ValueError)."""
    expr = expr.strip()
    if "-" in expr:
        lo, hi = expr.split("-", 1)
        return PortRange(protocol, int(lo), int(hi))
    port = int(expr)
    return PortRange(protocol, port, port)


def parse_service_request(service: str, protocol_hint: str = "") -> list[PortRange]:
    """
    Parse an engineer-entered service string into port ranges.

    Accepts: "443", "tcp/8443", "udp/514", "tcp/8000-8100", well-known names
    ("ssh", "dns", ...), and "any"/"all"/"" (wildcard).

    Raises ValueError for anything unrecognisable — callers must surface
    that to the engineer rather than guessing.
    """
    raw = service.strip().lower()
    if raw in ("", "any", "all"):
        return [WILDCARD_RANGE]

    if "/" in raw:
        proto, _, port_part = raw.partition("/")
        proto = proto.strip()
        if proto not in ("tcp", "udp", "sctp", "icmp", "ip"):
            raise ValueError(f"Unknown protocol in service {service!r}")
        if proto in ("icmp", "ip"):
            return [PortRange(proto, 0, 65535)]
        return [_parse_port_expr(port_part, proto)]

    if raw in _WELL_KNOWN:
        return list(_WELL_KNOWN[raw])

    try:
        proto = protocol_hint.strip().lower() or "tcp"
        if proto in ("any", "n/a", "tcp/udp", ""):
            proto = "tcp"
        return [_parse_port_expr(raw, proto)]
    except ValueError:
        raise ValueError(
            f"Cannot interpret service {service!r} — use a port number, "
            "proto/port (e.g. tcp/8443), or a well-known name"
        ) from None


def _is_wildcard_name(name: str) -> bool:
    return name.strip().lower() in ("all", "any")


class ServiceCatalog:
    """Resolves FortiManager service object/group references to PortRanges."""

    def __init__(self, custom_objects: list[dict], groups: list[dict]) -> None:
        self._objects = {
            o["name"]: o for o in custom_objects if isinstance(o, dict) and "name" in o
        }
        self._groups = {
            g["name"]: g for g in groups if isinstance(g, dict) and "name" in g
        }

    def ranges_for_ref(self, name: str) -> list[PortRange] | None:
        """Resolve a reference by name. None means unresolvable (unknown)."""
        return self._resolve(name, seen=set())

    def exact_match_name(self, ranges: list[PortRange]) -> str | None:
        """Name of an existing service object whose ranges equal `ranges`."""
        want = set(ranges)
        for name, obj in self._objects.items():
            resolved = self._ranges_for_object(obj)
            if resolved is not None and set(resolved) == want:
                return name
        return None

    def _resolve(self, name: str, seen: set[str]) -> list[PortRange] | None:
        if _is_wildcard_name(name):
            return [WILDCARD_RANGE]
        if name in seen:
            return []  # cycle — contributes nothing further
        seen.add(name)

        obj = self._objects.get(name)
        if obj is not None:
            return self._ranges_for_object(obj)

        group = self._groups.get(name)
        if group is not None:
            members = group.get("member", [])
            resolved: list[PortRange] = []
            any_known = False
            for m in members:
                member_name = m if isinstance(m, str) else m.get("name", "")
                sub = self._resolve(member_name, seen)
                if sub is not None:
                    any_known = True
                    resolved.extend(sub)
            return resolved if any_known else None

        return None

    @staticmethod
    def _ranges_for_object(obj: dict) -> list[PortRange]:
        protocol = str(obj.get("protocol", "TCP/UDP/SCTP")).upper()
        if "ICMP" in protocol:
            return [PortRange("icmp", 0, 65535)]
        if protocol == "IP":
            # Objects with protocol=IP and a protocol-number are IP-protocol
            # typed (e.g. icmp-proto has protocol-number=1). Map known protocol
            # numbers to their PortRange types so they can be correctly compared
            # against the requested service. Unknown protocol numbers return None
            # (unresolvable) so callers treat coverage as uncertain.
            proto_num = obj.get("protocol-number")
            if proto_num is not None:
                _PROTO_NUM_MAP = {1: PortRange("icmp", 0, 65535)}
                pr = _PROTO_NUM_MAP.get(int(proto_num))
                return [pr] if pr is not None else None
            return [WILDCARD_RANGE]

        ranges: list[PortRange] = []
        for proto, key in (("tcp", "tcp-portrange"), ("udp", "udp-portrange"),
                           ("sctp", "sctp-portrange")):
            raw = obj.get(key, "")
            if isinstance(raw, list):
                raw = " ".join(str(r) for r in raw)
            for token in str(raw).split():
                # "443:1024-65535" — part after ':' is the source-port range
                dst_part = token.split(":", 1)[0]
                try:
                    ranges.append(_parse_port_expr(dst_part, proto))
                except ValueError:
                    continue
        return ranges


class AddressCatalog:
    """
    Resolves FortiManager address object/group references to ip networks.

    Per-ADOM names shadow global-ADOM names (same precedence query.py always
    used). None means the reference is unresolvable — fqdn/geo/dynamic types
    or an unknown name — and callers must not treat it as a non-match.
    """

    def __init__(
        self,
        objects: list[dict],
        groups: list[dict],
        global_objects: list[dict] = (),
        global_groups: list[dict] = (),
    ) -> None:
        self._objects: dict[str, dict] = {}
        self._groups: dict[str, dict] = {}
        for o in global_objects or ():
            if isinstance(o, dict) and "name" in o:
                self._objects[o["name"]] = o
        for g in global_groups or ():
            if isinstance(g, dict) and "name" in g:
                self._groups[g["name"]] = g
        for o in objects:
            if isinstance(o, dict) and "name" in o:
                self._objects[o["name"]] = o
        for g in groups:
            if isinstance(g, dict) and "name" in g:
                self._groups[g["name"]] = g

    def networks_for_ref(self, name: str):
        return self._resolve(name, seen=set())

    def is_group(self, name: str) -> bool:
        return name in self._groups

    def groups_containing(self, name: str) -> set[str]:
        """All groups that (transitively) include `name` as a member.
        Used for blast-radius analysis before appending to a group."""
        parents: dict[str, set[str]] = {}
        for gname, g in self._groups.items():
            for m in _names(g.get("member", [])):
                parents.setdefault(m, set()).add(gname)
        result: set[str] = set()
        queue = [name]
        while queue:
            for p in parents.get(queue.pop(), ()):
                if p not in result:
                    result.add(p)
                    queue.append(p)
        return result

    def exact_match_name(self, cidr: str) -> str | None:
        """Name of an existing address object exactly equal to `cidr`."""
        try:
            target = [ipaddress.ip_network(cidr, strict=False)]
        except ValueError:
            return None
        for name, obj in self._objects.items():
            nets = self._networks_for_object(obj)
            if nets is not None and list(nets) == target:
                return name
        return None

    def _resolve(self, name: str, seen: set[str]):
        if _is_wildcard_name(name):
            return [ipaddress.ip_network("0.0.0.0/0")]
        if name in seen:
            return []
        seen.add(name)

        obj = self._objects.get(name)
        if obj is not None:
            return self._networks_for_object(obj)

        group = self._groups.get(name)
        if group is not None:
            nets = []
            any_known = False
            for m in group.get("member", []):
                member_name = m if isinstance(m, str) else m.get("name", "")
                sub = self._resolve(member_name, seen)
                if sub is not None:
                    any_known = True
                    nets.extend(sub)
            return nets if any_known else None

        return None

    @staticmethod
    def _networks_for_object(obj: dict):
        obj_type = str(obj.get("type", "ipmask")).lower()

        if obj_type in ("ipmask", "0", "subnet"):
            subnet = obj.get("subnet", obj.get("ip", ""))
            if isinstance(subnet, list) and len(subnet) == 2:
                subnet = f"{subnet[0]}/{subnet[1]}"
            elif isinstance(subnet, str) and " " in subnet:
                addr, mask = subnet.split(None, 1)
                subnet = f"{addr}/{mask.strip()}"
            try:
                return [ipaddress.ip_network(str(subnet), strict=False)]
            except ValueError:
                return None

        if obj_type in ("iprange", "1", "range"):
            try:
                start = ipaddress.ip_address(obj.get("start-ip", ""))
                end = ipaddress.ip_address(obj.get("end-ip", ""))
                return list(ipaddress.summarize_address_range(start, end))
            except ValueError:
                return None

        # fqdn, geography, dynamic, mac — not resolvable to static networks
        return None


@dataclass
class MatchResult:
    """Outcome of evaluating one policy against a requested flow.

    matched=True with full_cover=False means partial overlap — the policy
    would catch some of the requested traffic but does not prove coverage.
    Unknown refs make a dimension conservatively matched but never full.
    """

    matched: bool
    full_cover: bool
    action: str
    disabled: bool
    conditional_schedule: bool
    unknown_refs: list[str]
    notes: list[str]


_ACTION_MAP = {0: "deny", 1: "accept", 2: "ipsec", 3: "ssl-vpn"}


def _names(field) -> list[str]:
    if isinstance(field, list):
        return [x if isinstance(x, str) else x.get("name", str(x)) for x in field]
    if isinstance(field, str):
        return [field]
    return []


class PolicyMatcher:
    """Evaluates raw FortiManager policy dicts against a requested flow
    using resolved set semantics (no substring matching)."""

    def __init__(self, addr_catalog: AddressCatalog, svc_catalog: ServiceCatalog) -> None:
        self._addr = addr_catalog
        self._svc = svc_catalog

    def evaluate(
        self,
        pol: dict,
        src: str,
        dst: str,
        service_ranges: list[PortRange],
    ) -> MatchResult:
        """src/dst are IP or CIDR strings; "" means unconstrained (wildcard).

        Containment is per-item: a requested range/network must fit inside a
        single resolved ref entry to count as covered (unions of fragmented
        refs are approximated via collapse for addresses; port ranges are not
        merged — a request spanning two adjacent objects reports partial).
        """
        unknown: list[str] = []
        notes: list[str] = []

        src_m, src_f = self._addr_dim(pol, "srcaddr", src, unknown)
        dst_m, dst_f = self._addr_dim(pol, "dstaddr", dst, unknown)
        svc_m, svc_f = self._svc_dim(pol, service_ranges, unknown)

        matched = src_m and dst_m and svc_m
        full_cover = matched and src_f and dst_f and svc_f

        raw_status = pol.get("status", "enable")
        disabled = raw_status in ("disable", 0)

        schedule = _names(pol.get("schedule", ["always"]))
        conditional_schedule = bool(schedule) and schedule != ["always"]
        if conditional_schedule:
            notes.append(f"schedule is {'/'.join(schedule)!r}, not 'always'")

        raw_action = pol.get("action", 0)
        action = _ACTION_MAP.get(raw_action, str(raw_action))

        return MatchResult(
            matched=matched,
            full_cover=full_cover,
            action=action,
            disabled=disabled,
            conditional_schedule=conditional_schedule,
            unknown_refs=unknown,
            notes=notes,
        )

    def addr_side(self, pol: dict, key: str, target: str) -> tuple[bool, bool]:
        """Public (matched, full_cover) for one address side of a policy
        (key is "srcaddr" or "dstaddr")."""
        return self._addr_dim(pol, key, target, [])

    def svc_side(self, pol: dict, requested: list[PortRange]) -> tuple[bool, bool]:
        """Public (matched, full_cover) for the service dimension."""
        return self._svc_dim(pol, requested, [])

    def addr_ip_overlap(self, pol: dict, key: str, target: str) -> bool:
        """True if any resolvable IP range in pol[key] overlaps target.

        Ignores FQDN, geo, and other unresolvable refs — only concrete IP
        networks count. Returns False when target is not a valid IP/CIDR.
        """
        try:
            target_net = ipaddress.ip_network(target, strict=False)
        except ValueError:
            return False
        for name in _names(pol.get(key, [])):
            resolved = self._addr.networks_for_ref(name)
            if resolved is None:
                continue
            if any(target_net.overlaps(n) for n in resolved):
                return True
        return False

    def uncovered_services(self, pol: dict, requested: list[PortRange]) -> list[PortRange]:
        """Return requested PortRanges not fully contained by this policy's services."""
        refs = _names(pol.get("service", []))
        ranges: list[PortRange] = []
        for name in refs:
            resolved = self._svc.ranges_for_ref(name)
            if resolved is not None:
                ranges.extend(resolved)
        return [req for req in requested if not any(r.contains(req) for r in ranges)]

    # ------------------------------------------------------------------

    def _addr_dim(self, pol: dict, key: str, target: str, unknown: list[str]):
        """Return (matched, full) for one address dimension."""
        if not target:
            target_net = ipaddress.ip_network("0.0.0.0/0")
        else:
            try:
                target_net = ipaddress.ip_network(target, strict=False)
            except ValueError:
                unknown.append(f"{key}:{target}")
                return True, False

        refs = _names(pol.get(key, []))
        negate = pol.get(f"{key}-negate", "disable") in ("enable", 1, True)

        nets = []
        has_unknown = False
        for name in refs:
            resolved = self._addr.networks_for_ref(name)
            if resolved is None:
                has_unknown = True
                unknown.append(name)
            else:
                nets.extend(resolved)

        overlap = any(target_net.overlaps(n) for n in nets)
        collapsed = list(ipaddress.collapse_addresses(nets)) if nets else []
        contained = any(
            target_net.subnet_of(n) for n in collapsed
            if n.version == target_net.version
        )

        if negate:
            # Policy matches traffic NOT in refs. Unknown refs make the
            # complement uncertain in both directions.
            if has_unknown:
                return True, False
            matched = not contained if target_net.num_addresses > 1 else not overlap
            full = not overlap  # fully covered only if target entirely outside refs
            return matched, full

        if contained:
            return True, True
        if overlap:
            return True, False
        if has_unknown:
            return True, False  # cannot prove non-match
        return False, False

    def _svc_dim(self, pol: dict, requested: list[PortRange], unknown: list[str]):
        refs = _names(pol.get("service", []))
        ranges: list[PortRange] = []
        has_unknown = False
        for name in refs:
            resolved = self._svc.ranges_for_ref(name)
            if resolved is None:
                has_unknown = True
                unknown.append(name)
            else:
                ranges.extend(resolved)

        overlap = any(r.overlaps(req) for r in ranges for req in requested)
        contained = bool(requested) and all(
            any(r.contains(req) for r in ranges) for req in requested
        )

        if contained:
            return True, True
        if overlap:
            return True, False
        if has_unknown:
            return True, False
        return False, False
```

- [ ] **Step 3: Run matching.py's ported tests**

Copy `~/code/github/ai/4tanalyst/tests/test_matching.py` to `tests/test_planner_matching.py`. In the copy, change `from fortimanager_mcp.matching import ...` to `from app.planner.matching import ...` (this is the only edit — the module is byte-identical, so every assertion should pass unmodified).

```bash
cp ~/code/github/ai/4tanalyst/tests/test_matching.py tests/test_planner_matching.py
sed -i '' 's/from fortimanager_mcp\.matching import/from app.planner.matching import/' tests/test_planner_matching.py
uv run pytest tests/test_planner_matching.py -v
```
Expected: all tests pass (same assertions, same module logic, only the import path changed).

- [ ] **Step 4: Create `app/planner/models.py`**

Identical to 4tAnalyst's `planner/models.py` except the import on line 8 changes from `from fortimanager_mcp.matching import PortRange` to `from app.planner.matching import PortRange`:

```python
"""Data model for the deterministic change planner."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.planner.matching import PortRange


class PlannerDataError(Exception):
    """A required data source failed — distinct from 'query ran, no results'.

    source identifies which system failed ("fortimanager" | "4thealth" |
    "credentials"), so callers can tell the engineer exactly what to check.
    """

    def __init__(self, source: str, detail: str) -> None:
        super().__init__(f"[{source}] {detail}")
        self.source = source
        self.detail = detail


@dataclass
class NormalizedFlow:
    """One consolidated request. src/dst/service are display strings
    (comma-joined); srcs/dsts/services are the member lists the engine
    plans over. service_ranges is the union of all service tokens."""
    src: str
    dst: str
    service: str
    srcs: list[str] = field(default_factory=list)
    dsts: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    service_ranges: list[PortRange] = field(default_factory=list)
    justification: str = ""

    def __post_init__(self) -> None:
        if not self.srcs:
            self.srcs = [t.strip() for t in self.src.split(",") if t.strip()]
        if not self.dsts:
            self.dsts = [t.strip() for t in self.dst.split(",") if t.strip()]
        if not self.services:
            self.services = [t.strip() for t in self.service.split(",") if t.strip()]

    @property
    def pairs(self) -> list[tuple[str, str]]:
        return [(s, d) for s in self.srcs for d in self.dsts]


@dataclass
class TargetFirewall:
    device: str
    adom: str


@dataclass
class ObjectPlan:
    role: str        # "source" | "destination" | "service"
    action: str      # "reuse" | "create"
    name: str
    obj_type: str    # "host" | "network" | "service"
    value: str       # "10.1.2.3/32" or "tcp/8443"
    cli: str = ""    # empty for reuse


@dataclass
class InsertionPlan:
    package: str
    insert_before_policy_id: int | None   # None → append at end
    rationale: str
    shadowed_by: list[int] = field(default_factory=list)
    would_shadow: list[int] = field(default_factory=list)


@dataclass
class GroupAppendAlternative:
    """Optional smaller change: extend a near-miss rule instead of creating
    a new policy.

    Two modes:
    - Group-append (group is not None): append the missing endpoint to an
      address group already referenced by the rule. group_cli carries the
      FortiGate CLI. Always carries the full blast radius (every other policy
      referencing the group directly or via group nesting).
    - Direct-append (group is None): add the missing endpoint directly to
      the rule's srcaddr/dstaddr list. direct_cli carries the CLI. Blast
      radius is trivially zero — only this rule is affected.

    The planner picks the best candidate by specificity: a rule that exactly
    matches on the non-failing sides (e.g. exact destination host + exact
    service) is preferred over a broad catch-all that merely qualifies."""
    package: str
    policy_id: int
    policy_name: str
    side: str                              # "source" | "destination"
    group: str | None                      # None for direct-append
    members: list[ObjectPlan]
    group_cli: str = ""                    # set for group-append; empty for direct
    direct_cli: str = ""                   # set for direct-append; empty for group
    affected_policies: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class FirewallPlan:
    firewall: str
    adom: str
    status: str                            # "already_covered" | "new_rule" | "not_found" | "error" | "no_action"
    covering_rules: list[dict] = field(default_factory=list)
    partial_matches: list[dict] = field(default_factory=list)
    objects: list[ObjectPlan] = field(default_factory=list)
    policy_name: str = ""
    policy_cli: str = ""
    srcintf: str = ""
    dstintf: str = ""
    insertion: InsertionPlan | None = None
    alternative: GroupAppendAlternative | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ChangePlan:
    ticket_id: str
    flow: NormalizedFlow
    zone_verdict: dict                     # check_ip_traffic-shaped
    risk_level: str
    firewalls: list[FirewallPlan]
    cli_status: str                        # "already_covered" | "new_rule" | "blocked_exception" | "unknown_no_action"
    recommendation: str
    warnings: list[str] = field(default_factory=list)
    naming: dict = field(default_factory=dict)
    logging: dict = field(default_factory=dict)
    approval: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

- [ ] **Step 5: Run models.py's ported tests**

`test_engine.py` in 4tAnalyst covers both `models.py` and `engine.py`. For this task, extract only the model-roundtrip tests: open `~/code/github/ai/4tanalyst/tests/test_engine.py`, find every test function that only constructs/serializes dataclasses from `planner.models` (no `plan_change` call, no fake clients) — copy those into `tests/test_planner_models.py`, changing `from planner.models import ...` to `from app.planner.models import ...` and `from fortimanager_mcp.matching import PortRange` to `from app.planner.matching import PortRange`.

```bash
uv run pytest tests/test_planner_models.py -v
```
Expected: all pass.

- [ ] **Step 6: Create `app/planner/cli_gen.py` verbatim**

No internal-package imports (only stdlib `ipaddress`) — copy exactly:

```python
"""
FortiGate CLI generation for the change planner.

Emits exact `config firewall ...` blocks. <TICKET_ID> placeholders are left
in place until a real ticket number is substituted at render time.
"""

from __future__ import annotations

import ipaddress


def _quote_list(names: list[str]) -> str:
    return " ".join(f'"{n}"' for n in names)


def address_object_cli(name: str, cidr: str) -> str:
    net = ipaddress.ip_network(cidr, strict=False)
    return (
        'config firewall address\n'
        f'    edit "{name}"\n'
        '        set type ipmask\n'
        f'        set subnet {net.network_address} {net.netmask}\n'
        '        set comment "<TICKET_ID>"\n'
        '    next\n'
        'end'
    )


def service_object_cli(name: str, proto: str, port_expr: str) -> str:
    proto = proto.lower()
    if proto not in ("tcp", "udp", "sctp"):
        raise ValueError(f"Cannot generate a service object for protocol {proto!r}")
    return (
        'config firewall service custom\n'
        f'    edit "{name}"\n'
        f'        set {proto}-portrange {port_expr}\n'
        '        set comment "<TICKET_ID>"\n'
        '    next\n'
        'end'
    )


def policy_cli(
    *,
    name: str,
    srcintf: str,
    dstintf: str,
    srcaddr: list[str],
    dstaddr: list[str],
    service: list[str],
    logtraffic: str,
    logtraffic_start: bool,
    comments: str,
    insert_before: int | None,
) -> str:
    lines = [
        "config firewall policy",
        "    edit 0",
        f'        set name "{name}"',
        f'        set srcintf "{srcintf}"',
        f'        set dstintf "{dstintf}"',
        f"        set srcaddr {_quote_list(srcaddr)}",
        f"        set dstaddr {_quote_list(dstaddr)}",
        f"        set service {_quote_list(service)}",
        "        set action accept",
        '        set schedule "always"',
        f"        set logtraffic {logtraffic}",
    ]
    if logtraffic_start:
        lines.append("        set logtraffic-start enable")
    if comments:
        lines.append(f'        set comments "{comments}"')
    lines += ["    next", "end"]

    if insert_before is not None:
        lines += [
            "",
            f"# Position: this policy must sit before policy ID {insert_before}",
            "# (first-match order). After the edit above, note the new policy ID",
            f"# shown by the CLI and run:  move <new-id> before {insert_before}",
        ]
    return "\n".join(lines)


def exception_comment(ticket: str) -> str:
    ticket = ticket or "<TICKET_ID>"
    return (
        f"EXCEPTION to active block policy — ticket {ticket}. "
        "Requires SecOps approval before implementation: <SecOps approver>"
    )


def addrgrp_append_cli(group: str, member: str | list[str]) -> str:
    """CLI to append member(s) to an existing address group. `append`
    preserves the group's current members (unlike `set member`)."""
    members = [member] if isinstance(member, str) else list(member)
    appends = "\n".join(f'        append member "{m}"' for m in members)
    return (
        'config firewall addrgrp\n'
        f'    edit "{group}"\n'
        f'{appends}\n'
        '    next\n'
        'end'
    )


def policy_addr_append_cli(policy_id: int, key: str, members: list[str]) -> str:
    """CLI to append address object(s) directly to a policy's srcaddr or
    dstaddr list.  `append` preserves the existing entries (unlike `set`).
    `key` must be "srcaddr" or "dstaddr"."""
    appends = "\n".join(f'        append {key} "{m}"' for m in members)
    return (
        'config firewall policy\n'
        f'    edit {policy_id}\n'
        f'{appends}\n'
        '    next\n'
        'end'
    )


def addrgrp_create_cli(name: str, members: list[str]) -> str:
    """CLI to create a new address group with the given members."""
    quoted = " ".join(f'"{m}"' for m in members)
    return (
        'config firewall addrgrp\n'
        f'    edit "{name}"\n'
        f'        set member {quoted}\n'
        '        set comment "<TICKET_ID>"\n'
        '    next\n'
        'end'
    )
```

- [ ] **Step 7: Run cli_gen.py's ported tests**

```bash
cp ~/code/github/ai/4tanalyst/tests/test_cli_gen.py tests/test_planner_cli_gen.py
sed -i '' 's/from planner\.cli_gen import/from app.planner.cli_gen import/;s/from planner import cli_gen/from app.planner import cli_gen/' tests/test_planner_cli_gen.py
uv run pytest tests/test_planner_cli_gen.py -v
```
Expected: all 11 tests pass unmodified.

- [ ] **Step 8: Full-suite check and commit**

```bash
uv run pytest -q
git add app/planner/__init__.py app/planner/models.py app/planner/cli_gen.py app/planner/matching.py \
        tests/test_planner_models.py tests/test_planner_cli_gen.py tests/test_planner_matching.py
git commit -m "Port planner models, CLI generation, and policy matching from 4tAnalyst"
```

---

### Task 2: Port standards lookups (naming, logging, risk, approval)

**Files:**
- Create: `app/planner/standards.py`
- Create: `naming.example.yaml` (project root, tracked)
- Create: `review_requirements.example.yaml` (project root, tracked)
- Modify: `.gitignore` (add `naming.yaml`, `review_requirements.yaml`)
- Modify: `pyproject.toml` (add `pyyaml` dependency)
- Test: `tests/test_planner_standards.py`

**Interfaces:**
- Consumes: `PortRange` from `app.planner.matching` (Task 1).
- Produces: `load_naming(path=None) -> dict`, `object_name(obj_type, *, ip="", proto="", port="", naming=None) -> str`, `policy_name(ticket_id, srcintf, dstintf, seq=1) -> str`, `risk_level(src_zones, dst_zones, zone_domains) -> str`, `rule_type_for(verdict, src_domains, dst_domains, service_ranges) -> str`, `log_settings(rule_type, naming=None) -> dict`, `permissiveness_warnings(srcs, dsts, service_ranges) -> list[str]`, `review_requirements(risk, path=None) -> dict` — all from `app.planner.standards`, used by Task 7's engine.

- [ ] **Step 1: Add `pyyaml` to `pyproject.toml`**

Open `pyproject.toml`, add `"pyyaml>=6.0"` to the `dependencies` list (alongside `flask`, `requests`, etc.), then:

```bash
uv sync
```
Expected: succeeds, `pyyaml` now in `uv.lock`.

- [ ] **Step 2: Create the example standards config files**

`naming.example.yaml` (copy verbatim from `~/code/github/ai/4tanalyst/standards_mcp/naming.yaml` — read that file and reproduce its exact content in the new file; it defines `platforms.fortigate.conventions`, `zone_abbrevs`, and `log_settings` for 9 rule types). Use:

```bash
cp ~/code/github/ai/4tanalyst/standards_mcp/naming.yaml naming.example.yaml
```

`review_requirements.example.yaml` (copy verbatim from `~/code/github/ai/4tanalyst/standards_mcp/review_requirements.yaml` — defines `risk_levels.{low,medium,high,critical}`, each with `approvers`, `peer_review`, `security_review`, `change_window`, `sla_hours`, `notes`):

```bash
cp ~/code/github/ai/4tanalyst/standards_mcp/review_requirements.yaml review_requirements.example.yaml
```

- [ ] **Step 3: Create the real (gitignored) config files for local dev/testing**

```bash
cp naming.example.yaml naming.yaml
cp review_requirements.example.yaml review_requirements.yaml
```

- [ ] **Step 4: Add the new files to `.gitignore`**

Add these two lines to the `# Runtime data` section of `.gitignore` (alongside `policy_db.json`, `groups.json`, etc.):

```
naming.yaml
review_requirements.yaml
```

- [ ] **Step 5: Create `app/planner/standards.py`**

Identical to 4tAnalyst's `planner/standards.py` except: the import on line 18 changes from `from fortimanager_mcp.matching import PortRange` to `from app.planner.matching import PortRange`, and the path constants (lines 20-22) change from `standards_mcp/naming.yaml` (relative to the 4tAnalyst repo root) to the project root of 4THealth+ (three parents up from `app/planner/standards.py`, since this file is now two directories deeper than the original `planner/standards.py`):

```python
"""
Deterministic standards lookups for the change planner.

Loads naming.yaml/review_requirements.yaml (team-maintained, gitignored —
copy from naming.example.yaml/review_requirements.example.yaml) and encodes
the risk/logging decision rules the planner applies to every flow.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

import yaml

from app.planner.matching import PortRange

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_NAMING_FILE = _REPO_ROOT / "naming.yaml"
_REVIEW_FILE = _REPO_ROOT / "review_requirements.yaml"

# Destination ports that make a rule "management access" per naming.yaml
# (interactive access logging — a common regulated-environment requirement,
# e.g. NERC CIP-005 in a regulated deployment).
_MANAGEMENT_PORTS = {("tcp", 22), ("tcp", 3389), ("tcp", 23)}


@lru_cache(maxsize=4)
def _load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_naming(path: Path | None = None) -> dict:
    return _load_yaml(str(path or _NAMING_FILE))


def object_name(obj_type: str, *, ip: str = "", proto: str = "",
                port: str = "", naming: dict | None = None) -> str:
    """Generate an object name per the FortiGate conventions in naming.yaml."""
    if obj_type == "host":
        return f"H_{ip.split('/')[0]}"
    if obj_type == "network":
        addr, _, prefix = ip.partition("/")
        return f"N_{addr}_{prefix or '32'}"
    if obj_type == "service":
        return f"SVC_{proto.upper()}_{port}"
    raise ValueError(f"No naming convention for object type {obj_type!r}")


def policy_name(ticket_id: str, srcintf: str, dstintf: str, seq: int = 1) -> str:
    ticket = ticket_id or "<TICKET_ID>"
    return f"{ticket}_{srcintf.upper()}_TO_{dstintf.upper()}_{seq:03d}"


def _domains_for(zones: list[str], zone_domains: dict[str, str]) -> set[str] | None:
    """Resolve zone names to domains. None if any zone is unknown/missing."""
    if not zones:
        return None
    domains = set()
    for z in zones:
        d = zone_domains.get(z)
        if d is None:
            return None
        domains.add(d)
    return domains


def risk_level(src_zones: list[str], dst_zones: list[str],
               zone_domains: dict[str, str]) -> str:
    """
    critical — any CIP-H/OT/Nuclear zone, Internet on either side, or any
               unresolvable zone (fail safe)
    high     — cross-domain flow
    medium   — same-domain flow between known zones
    """
    # The zone NAME "Internet" is the catch-all for unresolved IPs — it is
    # the internet regardless of what domain label the catalogue gives it.
    if "Internet" in src_zones or "Internet" in dst_zones:
        return "critical"

    src_domains = _domains_for(src_zones, zone_domains)
    dst_domains = _domains_for(dst_zones, zone_domains)
    if src_domains is None or dst_domains is None:
        return "critical"  # unknown zone: cannot bound the blast radius

    sensitive = {"CIP-H", "OT", "Nuclear", "Gas"}
    if (src_domains | dst_domains) & sensitive:
        return "critical"
    if "Internet" in src_domains or "Internet" in dst_domains:
        return "critical"
    if src_domains != dst_domains:
        return "high"
    return "medium"


def rule_type_for(verdict: str, src_domains: set[str], dst_domains: set[str],
                  service_ranges: list[PortRange]) -> str:
    """Map a flow onto a naming.yaml log_settings key.

    The zone pair decides the profile even for BLOCKED flows — an approved
    exception must log like any other rule between those zones.
    """
    ot_like = {"OT", "CIP-H", "Gas", "Nuclear"}
    if src_domains & ot_like and not (dst_domains & ot_like):
        return "allow_ot_to_it"
    if dst_domains & ot_like and not (src_domains & ot_like):
        return "allow_it_to_ot"
    if "Internet" in src_domains and "Internet" not in dst_domains:
        return "allow_internet_inbound"
    if "Internet" in dst_domains:
        return "allow_internet_outbound"
    for r in service_ranges:
        for proto, port in _MANAGEMENT_PORTS:
            if r.protocol == proto and r.start <= port <= r.end:
                return "management_access"
    return "allow_internal"


def log_settings(rule_type: str, naming: dict | None = None) -> dict:
    settings = (naming or load_naming())["log_settings"]
    if rule_type not in settings:
        raise KeyError(
            f"rule_type {rule_type!r} not present in naming.yaml log_settings"
        )
    return dict(settings[rule_type], rule_type=rule_type)


# Least-privilege thresholds. An IPv4 prefix shorter than /16 (IPv6 /48) is
# "very broad"; a tcp/udp/sctp request spanning more than this many ports is
# a wide-open service. Both are review flags, not hard blocks.
_BROAD_PREFIX_V4 = 16
_BROAD_PREFIX_V6 = 48
_WIDE_PORT_SPAN = 1024


def permissiveness_warnings(
    srcs: list[str], dsts: list[str], service_ranges: list[PortRange],
) -> list[str]:
    """Least-privilege review of the *request itself* (NIST SP 800-41):
    flag any-source/any-destination, very broad CIDRs, any-service, and
    wide port ranges. Non-IP tokens are skipped — other layers validate
    them. Returns warnings only; the engineer decides."""
    warnings: list[str] = []
    any_side = {"source": False, "destination": False}

    for label, values in (("source", srcs), ("destination", dsts)):
        for v in values:
            try:
                net = ipaddress.ip_network(v, strict=False)
            except ValueError:
                continue
            broad_at = _BROAD_PREFIX_V4 if net.version == 4 else _BROAD_PREFIX_V6
            if net.prefixlen == 0:
                any_side[label] = True
                warnings.append(
                    f"Request matches ANY {label} ({v}) — least-privilege "
                    "requires scoping to the actual endpoints."
                )
            elif net.prefixlen < broad_at:
                warnings.append(
                    f"{label.capitalize()} {v} is very broad "
                    f"(wider than /{broad_at}) — confirm the whole range "
                    "genuinely needs this access."
                )

    any_service = any(r.protocol == "ip" for r in service_ranges)
    if any_service:
        warnings.append(
            "Request is for ANY service (all protocols/ports) — "
            "least-privilege requires naming the specific service(s)."
        )
    else:
        for r in service_ranges:
            if r.protocol in ("tcp", "udp", "sctp") and \
                    (r.end - r.start + 1) > _WIDE_PORT_SPAN:
                warnings.append(
                    f"Service {r.protocol}/{r.start}-{r.end} spans "
                    f"{r.end - r.start + 1} ports — confirm the application "
                    "really needs the full range."
                )

    if any_side["source"] and any_side["destination"] and any_service:
        warnings.append(
            "ANY-source to ANY-destination on ANY service is a least-privilege "
            "violation — this request should be rejected or re-scoped, not "
            "implemented as written."
        )
    return warnings


def review_requirements(risk: str, path: Path | None = None) -> dict:
    levels = _load_yaml(str(path or _REVIEW_FILE))["risk_levels"]
    if risk not in levels:
        raise KeyError(f"risk level {risk!r} not present in review_requirements.yaml")
    return dict(levels[risk], risk_level=risk)
```

- [ ] **Step 6: Port and adapt the standards tests**

```bash
cp ~/code/github/ai/4tanalyst/tests/test_planner_standards.py tests/test_planner_standards.py
sed -i '' \
  -e 's/from planner\.standards import/from app.planner.standards import/' \
  -e 's/from planner import standards/from app.planner import standards/' \
  -e 's/from fortimanager_mcp\.matching import PortRange/from app.planner.matching import PortRange/' \
  tests/test_planner_standards.py
```

Open the copied file and check for any fixture that hardcodes a path like `standards_mcp/naming.yaml` or `_REPO_ROOT`-relative construction (used to build a temp/fixture YAML file for `load_naming(path=...)`-style tests) — update those to write to a temp file and pass it explicitly via the `path=` parameter (both `load_naming` and `review_requirements` accept an explicit `path` override for exactly this reason), rather than relying on the real `naming.yaml`/`review_requirements.yaml` at repo root.

```bash
uv run pytest tests/test_planner_standards.py -v
```
Expected: all 35 tests pass.

- [ ] **Step 7: Full-suite check and commit**

```bash
uv run pytest -q
git add app/planner/standards.py naming.example.yaml review_requirements.example.yaml \
        .gitignore pyproject.toml uv.lock tests/test_planner_standards.py
git commit -m "Port planner standards lookups (naming, risk, logging, approval)"
```

---

### Task 3: Build the address/service catalog module (simplified, no caching)

**Files:**
- Create: `app/planner/catalogs.py`
- Test: `tests/test_planner_catalogs.py`

**Interfaces:**
- Consumes: `AddressCatalog`, `ServiceCatalog` from `app.planner.matching` (Task 1). Consumes an `FMGClient`-shaped object with `.get_address_objects(adom)`, `.get_address_groups(adom)`, `.get_service_objects(adom)`, `.get_service_groups(adom)`, `.get_policy_packages(adom)`, `.get_policies(adom, pkg)` (all already exist on `app.fmg_client.FMGClient`).
- Produces: `build_catalogs(client, adom) -> tuple[AddressCatalog, ServiceCatalog]`, `package_targets_device(pkg, device) -> bool`, `get_device_policies(client, adom, device_pkgs) -> dict[str, list[dict] | None]`, `summarise_policy(pol, package_name) -> dict` — all used by Task 6's `fetch.py` and Task 7's `engine.py`.

This is a simplified rewrite of 4tAnalyst's `fortimanager_mcp/query.py` catalog functions — the TTL cache and thread-lock layer are dropped (see Global Constraints: this now runs once per in-process request, not from a shared server), but the fetch/index/summarize logic is preserved.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_planner_catalogs.py
"""Tests for app.planner.catalogs — address/service catalog building."""
from unittest.mock import MagicMock

from app.planner.catalogs import (
    build_catalogs,
    get_device_policies,
    package_targets_device,
    summarise_policy,
)


def test_package_targets_device_scoped_match():
    pkg = {"scope member": [{"name": "FW-A"}, {"name": "FW-B"}]}
    assert package_targets_device(pkg, "FW-A") is True
    assert package_targets_device(pkg, "FW-C") is False


def test_package_targets_device_unscoped_applies_to_all():
    assert package_targets_device({}, "FW-A") is True
    assert package_targets_device({"scope member": []}, "FW-A") is True


def test_build_catalogs_indexes_per_adom_and_global_objects():
    client = MagicMock()
    client.get_address_objects.side_effect = lambda adom: (
        [{"name": "H_10.1.1.1", "type": "ipmask", "subnet": "10.1.1.1/32"}]
        if adom == "OT-ADOM" else [{"name": "H_GLOBAL", "type": "ipmask", "subnet": "10.9.9.9/32"}]
    )
    client.get_address_groups.side_effect = lambda adom: []
    client.get_service_objects.return_value = [
        {"name": "SVC_TCP_443", "protocol": "TCP/UDP/SCTP", "tcp-portrange": "443"}
    ]
    client.get_service_groups.return_value = []

    addr_catalog, svc_catalog = build_catalogs(client, "OT-ADOM")

    assert addr_catalog.exact_match_name("10.1.1.1/32") == "H_10.1.1.1"
    assert addr_catalog.exact_match_name("10.9.9.9/32") == "H_GLOBAL"
    from app.planner.matching import PortRange
    assert svc_catalog.exact_match_name([PortRange("tcp", 443, 443)]) == "SVC_TCP_443"


def test_build_catalogs_degrades_gracefully_if_global_fetch_fails():
    client = MagicMock()
    def addr_objects(adom):
        if adom == "global":
            raise RuntimeError("global ADOM not accessible")
        return []
    client.get_address_objects.side_effect = addr_objects
    client.get_address_groups.side_effect = lambda adom: (
        (_ for _ in ()).throw(RuntimeError("boom")) if adom == "global" else []
    )
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []

    addr_catalog, svc_catalog = build_catalogs(client, "OT-ADOM")
    assert addr_catalog.exact_match_name("10.1.1.1/32") is None  # no crash, just empty


def test_get_device_policies_returns_none_on_fetch_failure():
    client = MagicMock()
    def get_policies(adom, pkg):
        if pkg == "bad-pkg":
            raise RuntimeError("fetch failed")
        return [{"policyid": 1}]
    client.get_policies.side_effect = get_policies

    result = get_device_policies(client, "OT-ADOM", ["good-pkg", "bad-pkg"])
    assert result["good-pkg"] == [{"policyid": 1}]
    assert result["bad-pkg"] is None


def test_summarise_policy_shape():
    pol = {
        "policyid": 42, "name": "TEST_RULE", "status": "enable",
        "srcaddr": ["H_A"], "srcintf": ["port1"],
        "dstaddr": ["H_B"], "dstintf": ["port2"],
        "service": ["SVC_TCP_443"], "action": 1, "logtraffic": 2,
        "nat": "disable", "schedule": ["always"],
        "srcaddr-negate": "disable", "dstaddr-negate": "disable",
        "comments": "test", "uuid": "abc-123",
    }
    summary = summarise_policy(pol, "TestPkg")
    assert summary["package"] == "TestPkg"
    assert summary["policy_id"] == 42
    assert summary["name"] == "TEST_RULE"
    assert summary["source"] == ["H_A"]
    assert summary["destination"] == ["H_B"]
    assert summary["service"] == ["SVC_TCP_443"]
    assert summary["action"] == "accept"
    assert summary["log"] == "all"
    assert summary["srcaddr_negate"] is False
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_planner_catalogs.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.planner.catalogs'`.

- [ ] **Step 3: Write `app/planner/catalogs.py`**

```python
"""
Address/service object catalogs and per-device policy fetch for the planner.

Simplified from 4tAnalyst's fortimanager_mcp/query.py: no TTL caching or
cross-thread locking, since 4THealth+ calls this in-process, once per
AI Assist request — unlike 4tAnalyst's shared multi-engineer MCP server.
"""

from __future__ import annotations

from typing import Any

from app.planner.matching import AddressCatalog, ServiceCatalog


def build_catalogs(client, adom: str) -> tuple[AddressCatalog, ServiceCatalog]:
    """Fetch and index all address/service objects for an ADOM (incl. global).

    A failure fetching the global-ADOM objects degrades to an empty global
    catalog rather than failing the whole request — per-ADOM objects are the
    primary source and are required; global objects are supplementary.
    """
    addr_objects = client.get_address_objects(adom)
    addr_groups = client.get_address_groups(adom)
    try:
        global_addr_objects = client.get_address_objects("global")
        global_addr_groups = client.get_address_groups("global")
    except Exception:
        global_addr_objects, global_addr_groups = [], []
    addr_catalog = AddressCatalog(addr_objects, addr_groups, global_addr_objects, global_addr_groups)

    svc_objects = client.get_service_objects(adom)
    svc_groups = client.get_service_groups(adom)
    svc_catalog = ServiceCatalog(svc_objects, svc_groups)

    return addr_catalog, svc_catalog


def package_targets_device(pkg: dict, device: str) -> bool:
    """Return True if the package's installation scope includes the device."""
    scope = pkg.get("scope member", pkg.get("scope_member", []))
    if not scope:
        return True  # global/unscoped packages apply to all
    return any(
        s.get("name", "") == device
        for s in scope
        if isinstance(s, dict)
    )


def get_device_policies(client, adom: str, device_pkgs: list[str]) -> dict[str, list[dict] | None]:
    """Fetch policies for exactly the given package names.

    A None value for a package means the fetch failed (caller degrades —
    'no covering rule found' is not conclusive when a fetch failed).
    """
    result: dict[str, list[dict] | None] = {}
    for pkg in device_pkgs:
        try:
            result[pkg] = [p for p in client.get_policies(adom, pkg) if isinstance(p, dict)]
        except Exception:
            result[pkg] = None
    return result


def summarise_policy(pol: dict, package_name: str) -> dict[str, Any]:
    """Human-readable summary of one raw FortiManager policy dict."""
    def _names(field) -> list[str]:
        if isinstance(field, list):
            return [x if isinstance(x, str) else x.get("name", str(x)) for x in field]
        if isinstance(field, str):
            return [field]
        return []

    action_map = {0: "deny", 1: "accept", 2: "ipsec", 3: "ssl-vpn"}
    action_raw = pol.get("action", 0)
    action = action_map.get(action_raw, str(action_raw))

    log_map = {0: "disable", 1: "utm", 2: "all"}
    log_raw = pol.get("logtraffic", 0)
    log = log_map.get(log_raw, str(log_raw))

    return {
        "package": package_name,
        "policy_id": pol.get("policyid", 0),
        "name": pol.get("name", ""),
        "status": pol.get("status", "enable"),
        "source": _names(pol.get("srcaddr", [])),
        "source_interface": _names(pol.get("srcintf", [])),
        "destination": _names(pol.get("dstaddr", [])),
        "destination_interface": _names(pol.get("dstintf", [])),
        "service": _names(pol.get("service", [])),
        "action": action,
        "log": log,
        "nat": pol.get("nat", "disable"),
        "schedule": _names(pol.get("schedule", ["always"])),
        "srcaddr_negate": pol.get("srcaddr-negate", "disable") in ("enable", 1, True),
        "dstaddr_negate": pol.get("dstaddr-negate", "disable") in ("enable", 1, True),
        "comments": pol.get("comments", ""),
        "uuid": pol.get("uuid", ""),
    }
```

- [ ] **Step 4: Run to verify it passes**

```bash
uv run pytest tests/test_planner_catalogs.py -v
```
Expected: all 6 tests pass.

- [ ] **Step 5: Full-suite check and commit**

```bash
uv run pytest -q
git add app/planner/catalogs.py tests/test_planner_catalogs.py
git commit -m "Add simplified address/service catalog builder for the planner"
```

---

### Task 4: Build the zone_db adapter

**Files:**
- Create: `app/planner/zone_adapter.py`
- Test: `tests/test_planner_zone_adapter.py`

**Interfaces:**
- Consumes: `app.zone_db.run_query(src_list, dst_list, service, verbose=False) -> list[dict]`, `app.zone_db.load_db() -> dict` (both already exist).
- Produces: `ZoneDBAdapter` with `.query(src, dst, service="", verbose=True) -> list[dict]`, `.zones() -> dict`, `.policies() -> list[dict]` — the exact interface Task 6's `fetch.py` expects from a zone client (this mirrors 4tAnalyst's `zone_mcp.client.ZonePolicyClient`, but calls `app.zone_db` directly instead of an HTTP endpoint).

Verified field-name compatibility: `zone_db.run_query()`'s per-pair result dict already has keys `src`, `dst`, `service`, `verdict`, `src_zones`, `dst_zones`, `governing`, `all_policies` — exactly what `fetch_zone_verdict` (Task 6) reads (`r.get("verdict")`, `r.get("src_zones")`, `r.get("dst_zones")`, `r.get("governing")`, `r.get("all_policies")`). No key renaming needed in `.query()`. `.zones()` needs a shape conversion since `zone_db` stores zones as a `dict` keyed by name, not the list-of-dict shape the planner expects.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_planner_zone_adapter.py
"""Tests for app.planner.zone_adapter.ZoneDBAdapter."""
from unittest.mock import patch

from app.planner.zone_adapter import ZoneDBAdapter

_FAKE_DB = {
    "zones": {
        "DMZ": {"domain": "Default", "is_shared": False,
                "subnets": [{"subnet": "10.0.0.0/24", "description": ""}],
                "children": [], "parents": []},
        "Internet": {"domain": "Default", "is_shared": True,
                     "subnets": [], "children": [], "parents": []},
    },
    "policies": [
        {"policy_set": "Corp", "from_zone": "Internet", "to_zone": "DMZ",
         "access_type": "allow all", "severity": "high", "services": [], "description": ""},
    ],
}


def test_query_delegates_to_run_query_with_single_item_lists():
    adapter = ZoneDBAdapter()
    with patch("app.zone_db.run_query") as mock_run_query:
        mock_run_query.return_value = [{"src": "10.0.0.5", "dst": "8.8.8.8", "verdict": "ALLOWED"}]
        result = adapter.query(src="10.0.0.5", dst="8.8.8.8", service="tcp/443", verbose=True)
    mock_run_query.assert_called_once_with(["10.0.0.5"], ["8.8.8.8"], "tcp/443", verbose=True)
    assert result == [{"src": "10.0.0.5", "dst": "8.8.8.8", "verdict": "ALLOWED"}]


def test_zones_converts_dict_to_list_shape():
    adapter = ZoneDBAdapter()
    with patch("app.zone_db.load_db", return_value=_FAKE_DB):
        result = adapter.zones()
    names = {z["name"] for z in result["zones"]}
    assert names == {"DMZ", "Internet"}
    dmz = next(z for z in result["zones"] if z["name"] == "DMZ")
    assert dmz["domain"] == "Default"
    assert dmz["subnets"] == [{"subnet": "10.0.0.0/24", "description": ""}]
    assert result["total_subnets"] == 1


def test_policies_returns_raw_list():
    adapter = ZoneDBAdapter()
    with patch("app.zone_db.load_db", return_value=_FAKE_DB):
        result = adapter.policies()
    assert result == _FAKE_DB["policies"]
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_planner_zone_adapter.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.planner.zone_adapter'`.

- [ ] **Step 3: Write `app/planner/zone_adapter.py`**

```python
"""
Adapts app.zone_db's module-level query engine to the query()/zones()/
policies() interface the ported planner expects from a zone_client.

4tAnalyst's planner was originally written against zone_mcp.client.
ZonePolicyClient, which called 4THealth's /external/api/zone/* HTTP
endpoints. Since the planner now runs inside 4THealth+ itself, this adapter
calls app.zone_db's functions directly in-process instead — same verdict
logic, no network hop, no separate credentials file.
"""

from __future__ import annotations

from app import zone_db


class ZoneDBAdapter:
    def query(self, src: str, dst: str, service: str = "", verbose: bool = True) -> list[dict]:
        """One src->dst verdict, shaped like zone_db.run_query's per-pair
        result: {"src", "dst", "service", "verdict", "src_zones",
        "dst_zones", "governing", "all_policies"}."""
        return zone_db.run_query([src], [dst], service or None, verbose=verbose)

    def zones(self) -> dict:
        """{"zones": [{"name", "domain", "is_shared", "subnets", "children",
        "parents"}, ...], "total_subnets": int}."""
        db = zone_db.load_db()
        zones_dict = db.get("zones", {})
        zones_list = [
            {
                "name": name,
                "domain": z.get("domain", "Default"),
                "is_shared": z.get("is_shared", False),
                "subnets": z.get("subnets", []),
                "children": z.get("children", []),
                "parents": z.get("parents", []),
            }
            for name, z in zones_dict.items()
        ]
        total_subnets = sum(len(z.get("subnets", [])) for z in zones_dict.values())
        return {"zones": zones_list, "total_subnets": total_subnets}

    def policies(self) -> list[dict]:
        db = zone_db.load_db()
        return db.get("policies", [])
```

- [ ] **Step 4: Run to verify it passes**

```bash
uv run pytest tests/test_planner_zone_adapter.py -v
```
Expected: all 3 tests pass.

- [ ] **Step 5: Full-suite check and commit**

```bash
uv run pytest -q
git add app/planner/zone_adapter.py tests/test_planner_zone_adapter.py
git commit -m "Add ZoneDBAdapter wrapping app.zone_db for the planner"
```

---

### Task 5: Port first-match insertion analysis

**Files:**
- Create: `app/planner/insertion.py`
- Test: `tests/test_planner_insertion.py`

**Interfaces:**
- Consumes: `PolicyMatcher`, `PortRange` from `app.planner.matching` (Task 1); `InsertionPlan` from `app.planner.models` (Task 1).
- Produces: `plan_insertion(package, ordered_policies, matcher, src, dst, service_ranges, srcintf, dstintf) -> InsertionPlan`, `_intf_scoped(pol, srcintf, dstintf) -> bool` (also imported directly by Task 7's `engine.py`) — from `app.planner.insertion`.

- [ ] **Step 1: Create `app/planner/insertion.py`**

Identical to 4tAnalyst's `planner/insertion.py` except the imports on lines 15-16 change from `from fortimanager_mcp.matching import PolicyMatcher, PortRange` / `from planner.models import InsertionPlan` to the `app.planner.*` equivalents:

```python
"""
First-match insertion analysis.

Given the ordered policies of a package and a candidate new rule, compute
where the rule must sit so it actually takes effect: before the first
existing policy that would otherwise match the same traffic. Also reports
which earlier policies would fully shadow the candidate and which later
policies the candidate would fully shadow.
"""

from __future__ import annotations

import ipaddress

from app.planner.matching import PolicyMatcher, PortRange
from app.planner.models import InsertionPlan


def _names(field) -> list[str]:
    if isinstance(field, list):
        return [x if isinstance(x, str) else x.get("name", str(x)) for x in field]
    if isinstance(field, str):
        return [field]
    return []


def _intf_scoped(pol: dict, srcintf: str, dstintf: str) -> bool:
    """True if the policy applies to the candidate's interface pair.
    'any'/empty on either side matches; unknown candidate intf ("") matches all."""

    def _side(refs: list[str], want: str) -> bool:
        if not want:
            return True
        return any(r in ("any", "") or r == want for r in refs)

    return _side(_names(pol.get("srcintf", [])), srcintf) and \
        _side(_names(pol.get("dstintf", [])), dstintf)


def _is_catchall_deny(pol: dict) -> bool:
    return (
        pol.get("action", 0) in (0, "deny")
        and set(_names(pol.get("srcaddr", []))) <= {"all", "any"}
        and set(_names(pol.get("dstaddr", []))) <= {"all", "any"}
        and {s.lower() for s in _names(pol.get("service", []))} <= {"all", "any"}
    )


def _candidate_nets(values: list[str]):
    nets = []
    for v in values:
        try:
            nets.append(ipaddress.ip_network(v or "0.0.0.0/0", strict=False))
        except ValueError:
            return None
    return nets


def _policy_within_candidate(
    pol: dict,
    matcher: PolicyMatcher,
    srcs: list[str],
    dsts: list[str],
    service_ranges: list[PortRange],
) -> bool:
    """True if the policy's resolved match set is fully inside the candidate's
    (the candidate placed earlier would shadow it). Unknown refs → False."""
    src_nets = _candidate_nets(srcs)
    dst_nets = _candidate_nets(dsts)
    if src_nets is None or dst_nets is None:
        return False

    for key, targets in (("srcaddr", src_nets), ("dstaddr", dst_nets)):
        if pol.get(f"{key}-negate", "disable") in ("enable", 1, True):
            return False
        nets = []
        for name in _names(pol.get(key, [])):
            resolved = matcher._addr.networks_for_ref(name)
            if resolved is None:
                return False
            nets.extend(resolved)
        if not nets:
            return False
        for n in nets:
            if not any(n.version == t.version and n.subnet_of(t) for t in targets):
                return False

    pol_ranges: list[PortRange] = []
    for name in _names(pol.get("service", [])):
        resolved = matcher._svc.ranges_for_ref(name)
        if resolved is None:
            return False
        pol_ranges.extend(resolved)
    if not pol_ranges:
        return False
    return all(
        any(req.contains(r) for req in service_ranges) for r in pol_ranges
    )


def plan_insertion(
    package: str,
    ordered_policies: list[dict],
    matcher: PolicyMatcher,
    src: str | list[str],
    dst: str | list[str],
    service_ranges: list[PortRange],
    srcintf: str,
    dstintf: str,
) -> InsertionPlan:
    srcs = [src] if isinstance(src, str) else list(src)
    dsts = [dst] if isinstance(dst, str) else list(dst)
    pairs = [(s, d) for s in srcs for d in dsts]
    notes: list[str] = []
    if not srcintf or not dstintf:
        notes.append(
            "candidate interfaces not fully resolved — analysis considered all policies"
        )

    scoped = [
        pol for pol in ordered_policies
        if isinstance(pol, dict) and _intf_scoped(pol, srcintf, dstintf)
    ]

    shadowed_by: list[int] = []
    would_shadow: list[int] = []
    frontier_id: int | None = None
    frontier_pol: dict | None = None

    for pol in scoped:
        results = [matcher.evaluate(pol, s, d, service_ranges) for s, d in pairs]
        if results[0].disabled:
            continue  # disabled rules never match traffic
        pid = pol.get("policyid", 0)

        if frontier_id is None:
            if not any(r.matched for r in results):
                continue
            frontier_id = pid
            frontier_pol = pol
            # only a rule covering EVERY pair truly shadows the candidate
            if all(r.full_cover for r in results):
                shadowed_by.append(pid)

        # every policy at or after the frontier that sits fully inside the
        # candidate's match set would be shadowed by the inserted rule
        if _policy_within_candidate(pol, matcher, srcs, dsts, service_ranges):
            would_shadow.append(pid)

    if frontier_id is not None:
        action = "deny" if frontier_pol.get("action", 0) in (0, "deny") else "accept"
        rationale = (
            f"Must precede policy {frontier_id} ({action}, "
            f"'{frontier_pol.get('name', '')}') in package '{package}' — it is the "
            "first enabled rule that would otherwise match this traffic."
        )
        if shadowed_by:
            rationale += (
                " NOTE: that rule fully covers the requested flow; placing the new"
                " rule after it would make the new rule dead."
            )
        insert_before = frontier_id
    else:
        # nothing overlaps — append, but stay above a final catch-all deny
        catchall = next((p for p in reversed(scoped) if _is_catchall_deny(p)), None)
        if catchall is not None:
            insert_before = catchall.get("policyid", 0)
            rationale = (
                f"No existing policy overlaps this flow; place before the final "
                f"catch-all deny (policy {insert_before}) in package '{package}'."
            )
        else:
            insert_before = None
            rationale = (
                f"No existing policy overlaps this flow and no catch-all deny found —"
                f" append at the end of package '{package}'."
            )

    if notes:
        rationale += " (" + "; ".join(notes) + ")"

    return InsertionPlan(
        package=package,
        insert_before_policy_id=insert_before,
        rationale=rationale,
        shadowed_by=shadowed_by,
        would_shadow=would_shadow,
    )
```

- [ ] **Step 2: Port and run the insertion tests**

```bash
cp ~/code/github/ai/4tanalyst/tests/test_insertion.py tests/test_planner_insertion.py
sed -i '' \
  -e 's/from planner\.insertion import/from app.planner.insertion import/' \
  -e 's/from planner\.models import/from app.planner.models import/' \
  -e 's/from fortimanager_mcp\.matching import/from app.planner.matching import/' \
  tests/test_planner_insertion.py
uv run pytest tests/test_planner_insertion.py -v
```
Expected: all 8 tests pass unmodified.

- [ ] **Step 3: Full-suite check and commit**

```bash
uv run pytest -q
git add app/planner/insertion.py tests/test_planner_insertion.py
git commit -m "Port planner first-match insertion analysis"
```

---

### Task 6: Port and adapt device/zone data fetching

**Files:**
- Create: `app/planner/fetch.py`
- Test: `tests/test_planner_fetch.py`

**Interfaces:**
- Consumes: `app.fmg_client.FMGClient`, `app.fmg_client.FMGError` (existing); `build_catalogs`, `get_device_policies`, `package_targets_device` from `app.planner.catalogs` (Task 3); `AddressCatalog`, `ServiceCatalog` from `app.planner.matching` (Task 1); `PlannerDataError` from `app.planner.models` (Task 1); `ZoneDBAdapter` from `app.planner.zone_adapter` (Task 4); `app.zone_db.evaluate`, `app.zone_db.find_matching_policies` (existing).
- Produces: `DeviceSnapshot` dataclass (`device, adom, packages, policies_by_package, addr_catalog, svc_catalog, interfaces, routing_table=[], degraded=False, failures=[]`), `fetch_device_snapshot(client, adom, device) -> DeviceSnapshot`, `fetch_zone_verdict(zc, src, dst, service) -> dict`, `fetch_zone_domains(zc) -> dict[str, str]`, `resolve_interface(snapshot, ip, zones, label) -> tuple[str, list[str]]`, `resolve_interfaces(snapshot, src, dst, src_zones=(), dst_zones=()) -> tuple[str, str, list[str]]` — all used by Task 7's `engine.py`.

Adaptation from 4tAnalyst's `planner/fetch.py` (beyond import paths): `FortiManagerClient`/`FortiManagerAPIError` → `FMGClient`/`FMGError`; `ZonePolicyClient`/`ZonePolicyError` → `ZoneDBAdapter` (which raises nothing special, so failures are caught as generic `Exception` and re-wrapped as `PlannerDataError`); `get_routing_table(client, adom, device)` (a 4tAnalyst helper) → `client.get_device_routes(adom, device)` (already a method on 4THealth+'s `FMGClient`); the `device_zone_map.yaml` interface-resolution tier is dropped entirely (see Global Constraints) — `DeviceSnapshot` drops its `zone_map_warnings` field and interfaces are used as fetched, with no `policy_zone` annotation.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_planner_fetch.py
"""Tests for app.planner.fetch — device snapshot and zone verdict fetching."""
from unittest.mock import MagicMock, patch

import pytest

from app.fmg_client import FMGError
from app.planner.fetch import (
    DeviceSnapshot,
    fetch_device_snapshot,
    fetch_zone_domains,
    fetch_zone_verdict,
    resolve_interfaces,
)
from app.planner.models import PlannerDataError
from app.planner.zone_adapter import ZoneDBAdapter


def _client_stub(**overrides):
    client = MagicMock()
    client.get_devices.return_value = [{"name": "FW-A"}]
    client.get_policy_packages.return_value = [{"name": "OT-Pkg", "scope member": []}]
    client.get_address_objects.return_value = []
    client.get_address_groups.return_value = []
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []
    client.get_policies.return_value = []
    client.get_device_interfaces.return_value = [
        {"name": "port1", "ip": "10.1.1.1 255.255.255.0"},
    ]
    client.get_device_routes.return_value = [
        {"dst": "0.0.0.0 0.0.0.0", "device": "port2", "status": "enable"},
    ]
    for k, v in overrides.items():
        getattr(client, k).return_value = v
    return client


def test_fetch_device_snapshot_happy_path():
    client = _client_stub()
    snapshot = fetch_device_snapshot(client, "OT-ADOM", "FW-A")
    assert isinstance(snapshot, DeviceSnapshot)
    assert snapshot.device == "FW-A"
    assert snapshot.degraded is False
    assert snapshot.packages == ["OT-Pkg"]
    assert len(snapshot.interfaces) == 1
    assert len(snapshot.routing_table) == 1


def test_fetch_device_snapshot_unknown_device_raises():
    client = _client_stub()
    with pytest.raises(PlannerDataError) as exc_info:
        fetch_device_snapshot(client, "OT-ADOM", "NOT-A-DEVICE")
    assert exc_info.value.source == "fortimanager"


def test_fetch_device_snapshot_degrades_on_package_fetch_failure():
    client = _client_stub()
    client.get_policies.side_effect = RuntimeError("boom")
    snapshot = fetch_device_snapshot(client, "OT-ADOM", "FW-A")
    assert snapshot.degraded is True
    assert "OT-Pkg" in snapshot.failures[0]


def test_fetch_zone_verdict_returns_verdict_shape():
    zc = MagicMock(spec=ZoneDBAdapter)
    zc.query.return_value = [{
        "src": "10.0.0.5", "dst": "8.8.8.8", "service": "tcp/443",
        "verdict": "ALLOWED", "src_zones": ["DMZ"], "dst_zones": ["Internet"],
        "governing": [{"policy_set": "Corp"}], "all_policies": [],
    }]
    result = fetch_zone_verdict(zc, "10.0.0.5", "8.8.8.8", "tcp/443")
    assert result["verdict"] == "ALLOWED"
    assert result["src_zones"] == ["DMZ"]
    assert result["dst_zones"] == ["Internet"]
    assert result["notes"] == []


def test_fetch_zone_verdict_applies_internet_default_when_unresolved():
    zc = MagicMock(spec=ZoneDBAdapter)
    zc.query.return_value = [{
        "src": "1.2.3.4", "dst": "10.0.0.5", "service": "tcp/443",
        "verdict": "UNKNOWN", "src_zones": [], "dst_zones": ["DMZ"],
        "governing": [], "all_policies": [],
    }]
    zc.zones.return_value = {"zones": [{"name": "Internet", "domain": "Default"}]}
    with patch("app.zone_db.find_matching_policies", return_value=[]), \
         patch("app.zone_db.evaluate", return_value=("ALLOWED", [])):
        zc.policies.return_value = []
        result = fetch_zone_verdict(zc, "1.2.3.4", "10.0.0.5", "tcp/443")
    assert result["src_zones"] == ["Internet"]
    assert any("catch-all" in n for n in result["notes"])


def test_fetch_zone_domains_maps_names_to_domains():
    zc = MagicMock(spec=ZoneDBAdapter)
    zc.zones.return_value = {"zones": [
        {"name": "DMZ", "domain": "Default"},
        {"name": "OT-Plant1", "domain": "OT"},
    ]}
    result = fetch_zone_domains(zc)
    assert result == {"DMZ": "Default", "OT-Plant1": "OT"}


def test_resolve_interfaces_connected_subnet_match():
    snapshot = DeviceSnapshot(
        device="FW-A", adom="OT-ADOM", packages=[], policies_by_package={},
        addr_catalog=MagicMock(), svc_catalog=MagicMock(),
        interfaces=[{"name": "port1", "ip": "10.1.1.1 255.255.255.0"}],
        routing_table=[],
    )
    srcintf, dstintf, warnings = resolve_interfaces(snapshot, "10.1.1.50", "10.1.1.60")
    assert srcintf == "port1"
    assert dstintf == "port1"
    assert warnings == []


def test_resolve_interfaces_routing_table_fallback():
    snapshot = DeviceSnapshot(
        device="FW-A", adom="OT-ADOM", packages=[], policies_by_package={},
        addr_catalog=MagicMock(), svc_catalog=MagicMock(),
        interfaces=[{"name": "port1", "ip": "10.1.1.1 255.255.255.0"}],
        routing_table=[{"dst": "0.0.0.0 0.0.0.0", "device": "port2", "status": "enable"}],
    )
    srcintf, dstintf, warnings = resolve_interfaces(snapshot, "10.1.1.50", "8.8.8.8")
    assert srcintf == "port1"
    assert dstintf == "port2"
    assert any("routing table" in w for w in warnings)


def test_resolve_interfaces_unresolvable_warns():
    snapshot = DeviceSnapshot(
        device="FW-A", adom="OT-ADOM", packages=[], policies_by_package={},
        addr_catalog=MagicMock(), svc_catalog=MagicMock(),
        interfaces=[], routing_table=[],
    )
    srcintf, dstintf, warnings = resolve_interfaces(snapshot, "10.1.1.50", "8.8.8.8")
    assert srcintf == ""
    assert dstintf == ""
    assert len(warnings) == 2
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_planner_fetch.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.planner.fetch'`.

- [ ] **Step 3: Write `app/planner/fetch.py`**

```python
"""
Data acquisition for the ported change planner.

Adapted from 4tAnalyst's planner/fetch.py: FortiManagerClient/ZonePolicyClient
(HTTP, separate credentials) are replaced with 4THealth+'s own FMGClient and
ZoneDBAdapter (direct in-process calls, one FortiManager connection, one
policy_db.json). The device_zone_map interface-resolution tier is dropped —
connected-subnet match and routing-table longest-prefix match cover the
common cases without a second config file to maintain.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from app.fmg_client import FMGClient, FMGError
from app.planner.catalogs import build_catalogs, get_device_policies, package_targets_device
from app.planner.matching import AddressCatalog, ServiceCatalog
from app.planner.models import PlannerDataError
from app.planner.zone_adapter import ZoneDBAdapter


@dataclass
class DeviceSnapshot:
    device: str
    adom: str
    packages: list[str]
    policies_by_package: dict[str, list[dict]]   # raw dicts, package order preserved
    addr_catalog: AddressCatalog
    svc_catalog: ServiceCatalog
    interfaces: list[dict]
    routing_table: list[dict] = field(default_factory=list)
    degraded: bool = False
    failures: list[str] = field(default_factory=list)


def fetch_device_snapshot(
    client: FMGClient, adom: str, device: str
) -> DeviceSnapshot:
    """Fetch everything the planner needs about one device.

    Raises PlannerDataError if the device is unknown or the object catalogs
    cannot be fetched at all. Per-package policy failures degrade the
    snapshot instead (callers must then refuse to claim "already covered").
    """
    try:
        devices = client.get_devices(adom)
    except FMGError as exc:
        raise PlannerDataError("fortimanager", f"cannot list devices in ADOM {adom!r}: {exc}") from exc

    names = {d.get("name", "") for d in devices if isinstance(d, dict)}
    if device not in names:
        raise PlannerDataError(
            "fortimanager",
            f"device {device!r} not found in ADOM {adom!r} (known: {sorted(names)})",
        )

    try:
        packages = client.get_policy_packages(adom)
        addr_catalog, svc_catalog = build_catalogs(client, adom)
    except FMGError as exc:
        raise PlannerDataError("fortimanager", f"cannot fetch object catalogs: {exc}") from exc

    device_pkgs = [
        p.get("name", "") for p in packages
        if isinstance(p, dict) and package_targets_device(p, device)
    ]

    pkg_results = get_device_policies(client, adom, device_pkgs)

    policies_by_package: dict[str, list[dict]] = {}
    failures: list[str] = []
    for pkg in device_pkgs:
        cached = pkg_results.get(pkg)
        if cached is None:
            failures.append(f"package {pkg!r}: fetch failed")
        else:
            policies_by_package[pkg] = cached

    interfaces: list[dict] = []
    try:
        interfaces = [i for i in client.get_device_interfaces(adom, device) if isinstance(i, dict)]
    except FMGError as exc:
        failures.append(f"interfaces: {exc}")

    routing_table: list[dict] = []
    try:
        routing_table = [r for r in client.get_device_routes(adom, device) if isinstance(r, dict)]
    except Exception:
        # Routing table is used only for interface-name resolution — failure
        # here does not affect coverage analysis, so do not set degraded.
        pass

    return DeviceSnapshot(
        device=device,
        adom=adom,
        packages=device_pkgs,
        policies_by_package=policies_by_package,
        addr_catalog=addr_catalog,
        svc_catalog=svc_catalog,
        interfaces=interfaces,
        routing_table=routing_table,
        degraded=bool(failures),
        failures=failures,
    )


# Zone assigned to any IP the zone database cannot resolve. The catalogue's
# Internet zone is the deliberate catch-all ("all routable addresses not
# matched by any other zone") — enumerating the whole internet as subnets is
# not viable.
DEFAULT_UNMATCHED_ZONE = "Internet"


def _apply_internet_default(
    zc: ZoneDBAdapter, service: str, verdict: str,
    src_zones: list, dst_zones: list, governing: list,
) -> tuple[str, list, list, list, list[str]]:
    """Substitute the catch-all Internet zone for unresolved endpoints and
    re-derive the verdict from the live zone policy table."""
    from app.zone_db import evaluate, find_matching_policies

    notes: list[str] = []
    try:
        catalogue = zc.zones()
    except Exception as exc:
        raise PlannerDataError("4thealth", str(exc)) from exc

    zones_by_name = {
        z.get("name", ""): z
        for z in catalogue.get("zones", []) if isinstance(z, dict)
    }
    if DEFAULT_UNMATCHED_ZONE not in zones_by_name:
        notes.append(
            "One or more IPs did not resolve to a zone and the zone policy "
            f"catalogue has no {DEFAULT_UNMATCHED_ZONE!r} zone to default to — "
            "verdict left UNKNOWN."
        )
        return verdict, src_zones, dst_zones, governing, notes

    for label, zones in (("Source", src_zones), ("Destination", dst_zones)):
        if not zones:
            zones.append(DEFAULT_UNMATCHED_ZONE)
            notes.append(
                f"{label} did not match any zone subnet — treated as the "
                f"catch-all {DEFAULT_UNMATCHED_ZONE!r} zone."
            )

    if verdict == "UNKNOWN":
        try:
            policies = zc.policies()
        except Exception as exc:
            raise PlannerDataError("4thealth", str(exc)) from exc
        matching = find_matching_policies(src_zones, dst_zones, zones_by_name, policies)
        verdict, governing = evaluate(matching, [service] if service else [])

    return verdict, src_zones, dst_zones, governing, notes


def fetch_zone_verdict(zc: ZoneDBAdapter, src: str, dst: str, service: str) -> dict:
    """One src×dst verdict from the zone policy database, check_ip_traffic-shaped.

    Endpoints the zone database cannot resolve are treated as the catch-all
    Internet zone (with an explanatory note) and the verdict is re-derived
    from the live policy table.
    """
    try:
        results = zc.query(src=src, dst=dst, service=service, verbose=True)
    except Exception as exc:
        raise PlannerDataError("4thealth", str(exc)) from exc

    if not results:
        raise PlannerDataError(
            "4thealth", f"zone query for {src} -> {dst} returned no result objects"
        )
    r = results[0]
    verdict = r.get("verdict", "UNKNOWN")
    src_zones = list(r.get("src_zones", []))
    dst_zones = list(r.get("dst_zones", []))
    governing = r.get("governing", [])
    notes: list[str] = []
    if not src_zones or not dst_zones:
        verdict, src_zones, dst_zones, governing, notes = _apply_internet_default(
            zc, service, verdict, src_zones, dst_zones, governing
        )
    return {
        "src_ip": src,
        "dst_ip": dst,
        "service": service,
        "verdict": verdict,
        "src_zones": src_zones,
        "dst_zones": dst_zones,
        "governing": governing,
        "all_policies": r.get("all_policies", []),
        "notes": notes,
    }


def fetch_zone_domains(zc: ZoneDBAdapter) -> dict[str, str]:
    """Zone name → security domain, from the live zone catalogue."""
    try:
        catalogue = zc.zones()
    except Exception as exc:
        raise PlannerDataError("4thealth", str(exc)) from exc
    return {
        z.get("name", ""): z.get("domain", "")
        for z in catalogue.get("zones", [])
        if isinstance(z, dict)
    }


def _route_network(route: dict):
    """Parse the dst field of a static route into an ip_network.

    Unlike _iface_network, 0.0.0.0/0 (the default route) is valid here.
    """
    raw = route.get("dst", "")
    if isinstance(raw, list) and len(raw) == 2:
        raw = f"{raw[0]}/{raw[1]}"
    elif isinstance(raw, str) and " " in raw:
        addr, mask = raw.split(None, 1)
        raw = f"{addr}/{mask.strip()}"
    if not raw:
        return None
    try:
        return ipaddress.ip_network(str(raw), strict=False)
    except ValueError:
        return None


def _iface_network(iface: dict):
    raw = iface.get("ip", "")
    if isinstance(raw, list) and len(raw) == 2:
        raw = f"{raw[0]}/{raw[1]}"
    elif isinstance(raw, str) and " " in raw:
        addr, mask = raw.split(None, 1)
        raw = f"{addr}/{mask.strip()}"
    if not raw or str(raw).startswith("0.0.0.0"):
        return None
    try:
        return ipaddress.ip_network(str(raw), strict=False)
    except ValueError:
        return None


def resolve_interface(
    snapshot: DeviceSnapshot,
    ip: str,
    zones: list[str],
    label: str,
) -> tuple[str, list[str]]:
    """Resolve one IP to a device interface. Returns (name, warnings);
    unresolvable → ("", warnings) — never a silent guess. `zones` is
    accepted for interface compatibility with callers but unused (the
    device_zone_map resolution tier that consumed it was dropped)."""
    warnings: list[str] = []
    name = _resolve_one(snapshot, ip, label, warnings)
    return name, warnings


def resolve_interfaces(
    snapshot: DeviceSnapshot,
    src: str,
    dst: str,
    src_zones: list[str] = (),
    dst_zones: list[str] = (),
) -> tuple[str, str, list[str]]:
    """Resolve src/dst IPs to device interfaces by connected-subnet match,
    falling back to routing-table longest-prefix match. Unresolvable → ""
    plus a warning; never a silent guess. `src_zones`/`dst_zones` accepted
    for interface compatibility with callers but unused."""
    warnings: list[str] = []
    srcintf = _resolve_one(snapshot, src, "Source", warnings)
    dstintf = _resolve_one(snapshot, dst, "Destination", warnings)
    return srcintf, dstintf, warnings


def _resolve_one(
    snapshot: DeviceSnapshot,
    ip: str,
    label: str,
    warnings: list[str],
) -> str:
    try:
        target = ipaddress.ip_network(ip, strict=False)
    except ValueError:
        warnings.append(f"{label} {ip!r} is not a valid IP/CIDR")
        return ""
    # most-specific connected subnet wins
    best = ("", -1)
    for iface in snapshot.interfaces:
        net = _iface_network(iface)
        if net is not None and net.overlaps(target) and net.prefixlen > best[1]:
            best = (iface.get("name", ""), net.prefixlen)
    if best[0]:
        return best[0]
    # Fallback: longest-prefix match on the static routing table. Catches
    # internet-bound destinations (default route) and routed internal
    # subnets that are not directly connected on this firewall.
    best_route = ("", -1)
    for route in snapshot.routing_table:
        if route.get("status", "enable") != "enable":
            continue
        net = _route_network(route)
        raw_dev = route.get("device", "")
        iface_name = (
            raw_dev[0] if isinstance(raw_dev, list) and raw_dev
            else raw_dev if isinstance(raw_dev, str)
            else str(raw_dev)
        )
        if net is not None and iface_name and net.overlaps(target) and net.prefixlen > best_route[1]:
            best_route = (iface_name, net.prefixlen)
    if best_route[0]:
        warnings.append(
            f"{label} {ip} resolved interface {best_route[0]!r} via routing table "
            "longest-prefix match — verify before implementation"
        )
        return best_route[0]
    warnings.append(
        f"Could not resolve {label.lower()} {ip} to an interface on "
        f"{snapshot.device} — engineer must set the interface manually"
    )
    return ""
```

- [ ] **Step 4: Run to verify it passes**

```bash
uv run pytest tests/test_planner_fetch.py -v
```
Expected: all 9 tests pass. If `FMGClient`'s real exception type or `get_device_routes`'s exact `"dst"` field format differs from what the stubs assume, adjust the stub in the failing test (not the implementation) to match — `app/fmg_client.py` is the source of truth.

- [ ] **Step 5: Full-suite check and commit**

```bash
uv run pytest -q
git add app/planner/fetch.py tests/test_planner_fetch.py
git commit -m "Port and adapt planner device/zone data fetching to FMGClient/ZoneDBAdapter"
```

---

### Task 7: Port and adapt the plan_change orchestration engine

**Files:**
- Create: `app/planner/engine.py`
- Create: `app/planner/VENDORED_FROM.md`
- Test: `tests/test_planner_engine.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6 (`app.planner.models`, `app.planner.matching`, `app.planner.cli_gen`, `app.planner.standards`, `app.planner.catalogs.summarise_policy`, `app.planner.insertion.plan_insertion`/`_intf_scoped`, `app.planner.fetch.*`, `app.planner.zone_adapter.ZoneDBAdapter`); `app.fmg_helpers.make_client()` (existing) for default client construction.
- Produces: `plan_change(*, src, dst, service, firewalls, justification="", ticket_id="", src_group="", dst_group="", fmg_client=None, zone_client=None) -> ChangePlan`, `to_report_payload(plan: ChangePlan) -> dict` — from `app.planner.engine`, consumed by Task 9's route.

Adaptation from 4tAnalyst's `planner/engine.py` (beyond import paths): `_default_fmg_client()`/`_default_zone_client()` (which read `credentials.yaml`) are replaced — the default FortiManager client now comes from `app.fmg_helpers.make_client()` (already logged in via `.login()`, matching the original's behavior of returning a logged-in client), and the default zone client is simply `ZoneDBAdapter()` (no config needed at all — it reads `policy_db.json` directly). All `credentials.yaml`/`yaml`/`os`/`lru_cache`/`Path` machinery for credential loading is removed. `FortiManagerAPIError` → `FMGError`. `fortimanager_mcp.query._summarise_policy` → `app.planner.catalogs.summarise_policy`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_planner_engine.py
"""Tests for app.planner.engine.plan_change — the deterministic core."""
from unittest.mock import MagicMock

import pytest

from app.planner.engine import plan_change
from app.planner.models import PlannerDataError, TargetFirewall
from app.planner.zone_adapter import ZoneDBAdapter


def _zone_client(verdict="ALLOWED", src_zones=("DMZ",), dst_zones=("Internet",)):
    zc = MagicMock(spec=ZoneDBAdapter)
    zc.query.return_value = [{
        "src": "x", "dst": "y", "service": "z", "verdict": verdict,
        "src_zones": list(src_zones), "dst_zones": list(dst_zones),
        "governing": [{"policy_set": "Corp", "access_type": "allow all"}],
        "all_policies": [],
    }]
    zc.zones.return_value = {"zones": [
        {"name": "DMZ", "domain": "Default"},
        {"name": "Internet", "domain": "Default"},
    ]}
    zc.policies.return_value = []
    return zc


def _fmg_client_with_no_devices():
    client = MagicMock()
    client.get_devices.return_value = []
    return client


def test_plan_change_unknown_verdict_skips_firewall_analysis():
    zc = _zone_client(verdict="UNKNOWN", src_zones=(), dst_zones=())
    zc.zones.return_value = {"zones": []}  # no Internet zone → stays UNKNOWN
    plan = plan_change(
        src="1.2.3.4", dst="5.6.7.8", service="tcp/443",
        firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        zone_client=zc, fmg_client=MagicMock(),
    )
    assert plan.cli_status == "unknown_no_action"
    assert plan.firewalls[0].status == "no_action"


def test_plan_change_mixed_verdicts_raises():
    zc = MagicMock(spec=ZoneDBAdapter)

    def query_side_effect(src, dst, service, verbose=True):
        verdict = "ALLOWED" if dst == "5.6.7.8" else "BLOCKED"
        return [{
            "src": src, "dst": dst, "service": service, "verdict": verdict,
            "src_zones": ["DMZ"], "dst_zones": ["Internet"],
            "governing": [{"policy_set": "Corp", "access_type": "block all"}],
            "all_policies": [],
        }]
    zc.query.side_effect = query_side_effect
    zc.zones.return_value = {"zones": [{"name": "DMZ", "domain": "Default"},
                                        {"name": "Internet", "domain": "Default"}]}

    with pytest.raises(PlannerDataError) as exc_info:
        plan_change(
            src="1.2.3.4", dst="5.6.7.8, 9.9.9.9", service="tcp/443",
            firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
            zone_client=zc, fmg_client=MagicMock(),
        )
    assert exc_info.value.source == "request"


def test_plan_change_device_not_found_reports_error_status():
    zc = _zone_client()
    client = _fmg_client_with_no_devices()
    plan = plan_change(
        src="1.2.3.4", dst="5.6.7.8", service="tcp/443",
        firewalls=[TargetFirewall(device="FW-MISSING", adom="OT-ADOM")],
        zone_client=zc, fmg_client=client,
    )
    assert plan.firewalls[0].status == "not_found"
    assert plan.cli_status == "new_rule"  # not "already_covered" — device errored, not covered


def test_plan_change_already_covered_all_firewalls():
    zc = _zone_client()
    client = MagicMock()
    client.get_devices.return_value = [{"name": "FW-A"}]
    client.get_policy_packages.return_value = [{"name": "Pkg1", "scope member": []}]
    client.get_address_objects.return_value = []
    client.get_address_groups.return_value = []
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []
    client.get_device_interfaces.return_value = [{"name": "port1", "ip": "10.0.0.1 255.255.255.0"}]
    client.get_device_routes.return_value = []
    client.get_policies.return_value = [{
        "policyid": 5, "name": "EXISTING", "status": "enable", "action": 1,
        "srcaddr": ["all"], "dstaddr": ["all"], "service": ["ALL"],
        "srcintf": ["any"], "dstintf": ["any"], "schedule": ["always"],
    }]
    plan = plan_change(
        src="10.0.0.5", dst="10.0.0.6", service="tcp/443",
        firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        zone_client=zc, fmg_client=client,
    )
    assert plan.firewalls[0].status == "already_covered"
    assert plan.cli_status == "already_covered"


def test_plan_change_new_rule_generates_cli_and_naming():
    zc = _zone_client()
    client = MagicMock()
    client.get_devices.return_value = [{"name": "FW-A"}]
    client.get_policy_packages.return_value = [{"name": "Pkg1", "scope member": []}]
    client.get_address_objects.return_value = []
    client.get_address_groups.return_value = []
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []
    client.get_device_interfaces.return_value = [
        {"name": "port1", "ip": "10.0.0.1 255.255.255.0"},
        {"name": "port2", "ip": "192.168.1.1 255.255.255.0"},
    ]
    client.get_device_routes.return_value = []
    client.get_policies.return_value = []  # no existing rules

    plan = plan_change(
        src="10.0.0.5", dst="192.168.1.50", service="tcp/8443",
        ticket_id="CHG0001", firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        zone_client=zc, fmg_client=client,
    )
    fw = plan.firewalls[0]
    assert fw.status == "new_rule"
    assert fw.srcintf == "port1"
    assert fw.dstintf == "port2"
    assert "CHG0001" in fw.policy_cli
    assert plan.cli_status == "new_rule"
    assert plan.naming["objects"]  # at least the two address objects + one service


def test_to_report_payload_has_expected_top_level_keys():
    from app.planner.engine import to_report_payload
    zc = _zone_client()
    client = MagicMock()
    client.get_devices.return_value = [{"name": "FW-A"}]
    client.get_policy_packages.return_value = []
    client.get_address_objects.return_value = []
    client.get_address_groups.return_value = []
    client.get_service_objects.return_value = []
    client.get_service_groups.return_value = []
    client.get_device_interfaces.return_value = []
    client.get_device_routes.return_value = []

    plan = plan_change(
        src="10.0.0.5", dst="192.168.1.50", service="tcp/8443",
        firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        zone_client=zc, fmg_client=client,
    )
    payload = to_report_payload(plan)
    assert set(payload.keys()) == {
        "ticket_id", "request", "zone_verdict", "existing_rules",
        "naming", "logging", "approval", "recommendation", "cli",
    }
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_planner_engine.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.planner.engine'`.

- [ ] **Step 3: Write `app/planner/engine.py`**

```python
"""
The change planning engine — the deterministic core of AI Assist.

plan_change() takes a normalized flow plus named firewalls and computes the
entire change plan: zone verdict (app.zone_db), existing-rule coverage
(FortiManager, set semantics), object reuse vs. create, rule insertion point
(first-match shadowing analysis), naming/logging/approval requirements, and
the FortiGate CLI. to_report_payload() emits a report-ready dict.

The LLM layer (app.llm) must call this and relay the result; it must never
recompute or edit any part of the plan.

Ported and adapted from ~/code/github/ai/4tanalyst/planner/engine.py — see
VENDORED_FROM.md for the source commit. Adaptation: no credentials.yaml —
the default FortiManager client comes from app.fmg_helpers.make_client(),
the default zone client is ZoneDBAdapter() (reads policy_db.json directly,
no config needed).
"""

from __future__ import annotations

import ipaddress
import logging

from app.fmg_client import FMGClient, FMGError
from app.planner import cli_gen, standards
from app.planner.catalogs import summarise_policy
from app.planner.fetch import (
    DeviceSnapshot,
    fetch_device_snapshot,
    fetch_zone_domains,
    fetch_zone_verdict,
)
from app.planner.insertion import _intf_scoped, plan_insertion
from app.planner.matching import PolicyMatcher, _names as _ref_names, parse_service_request
from app.planner.models import (
    ChangePlan,
    FirewallPlan,
    GroupAppendAlternative,
    InsertionPlan,
    NormalizedFlow,
    ObjectPlan,
    PlannerDataError,
    TargetFirewall,
)
from app.planner.zone_adapter import ZoneDBAdapter

logger = logging.getLogger(__name__)


def _default_fmg_client() -> FMGClient:
    from app.fmg_helpers import make_client
    client = make_client()
    client.login()
    return client


def _default_zone_client() -> ZoneDBAdapter:
    return ZoneDBAdapter()


# ---------------------------------------------------------------------------
# Object planning
# ---------------------------------------------------------------------------

def _normalize_cidr(ip: str) -> str:
    net = ipaddress.ip_network(ip, strict=False)
    return str(net)


def _address_object_plan(role: str, ip: str, snapshot: DeviceSnapshot) -> ObjectPlan:
    cidr = _normalize_cidr(ip)
    existing = snapshot.addr_catalog.exact_match_name(cidr)
    if existing:
        return ObjectPlan(role=role, action="reuse", name=existing,
                          obj_type="host" if cidr.endswith("/32") else "network",
                          value=cidr)
    if cidr.endswith("/32"):
        name = standards.object_name("host", ip=cidr)
        obj_type = "host"
    else:
        name = standards.object_name("network", ip=cidr)
        obj_type = "network"
    return ObjectPlan(role=role, action="create", name=name, obj_type=obj_type,
                      value=cidr, cli=cli_gen.address_object_cli(name, cidr))


def _service_object_plan(token: str, snapshot: DeviceSnapshot) -> ObjectPlan:
    ranges = parse_service_request(token)
    if any(r.protocol == "ip" for r in ranges):
        # wildcard service — FortiGate's built-in ALL object, never created
        return ObjectPlan(role="service", action="reuse", name="ALL",
                          obj_type="service", value=token)
    existing = snapshot.svc_catalog.exact_match_name(ranges)
    if existing:
        return ObjectPlan(role="service", action="reuse", name=existing,
                          obj_type="service", value=token)
    r = ranges[0]
    port_expr = str(r.start) if r.start == r.end else f"{r.start}-{r.end}"
    name = standards.object_name("service", proto=r.protocol, port=port_expr)
    return ObjectPlan(role="service", action="create", name=name,
                      obj_type="service", value=f"{r.protocol}/{port_expr}",
                      cli=cli_gen.service_object_cli(name, r.protocol, port_expr))


# Sides with more members than this get a dedicated address group; smaller
# sets are inlined directly in the policy's srcaddr/dstaddr.
GROUP_THRESHOLD = 3


def _side_plan(
    objs: list[ObjectPlan], explicit_group: str, ticket_id: str, tag: str,
) -> tuple[list[str], list[ObjectPlan]]:
    """Return (policy member refs, extra group ObjectPlans) for one side."""
    names = [o.name for o in objs]
    if explicit_group or len(names) > GROUP_THRESHOLD:
        gname = explicit_group or f"GRP_{ticket_id or '<TICKET_ID>'}_{tag}"
        group = ObjectPlan(
            role=f"{'source' if tag == 'SRC' else 'destination'}-group",
            action="create", name=gname, obj_type="group",
            value=", ".join(names),
            cli=cli_gen.addrgrp_create_cli(gname, names),
        )
        return [gname], [group]
    return names, []


# ---------------------------------------------------------------------------
# Per-firewall planning
# ---------------------------------------------------------------------------

def _plan_firewall(
    target: TargetFirewall,
    flow: NormalizedFlow,
    zone_verdict: dict,
    log_cfg: dict,
    ticket_id: str,
    fmg_client,
    plan_warnings: list[str],
    src_group: str = "",
    dst_group: str = "",
) -> FirewallPlan:
    try:
        snapshot = fetch_device_snapshot(fmg_client, target.adom, target.device)
    except PlannerDataError as exc:
        status = "not_found" if "not found" in exc.detail else "error"
        return FirewallPlan(
            firewall=target.device, adom=target.adom, status=status,
            warnings=[str(exc)],
        )

    fw = FirewallPlan(firewall=target.device, adom=target.adom, status="new_rule")
    if snapshot.degraded:
        msg = (
            f"FortiManager data for {target.device} is incomplete "
            f"({'; '.join(snapshot.failures)}) — 'no existing rule' is NOT conclusive."
        )
        fw.warnings.append(msg)
        plan_warnings.append(msg)

    matcher = PolicyMatcher(snapshot.addr_catalog, snapshot.svc_catalog)

    # Interfaces are resolved up front: coverage must be judged only against
    # rules that apply to the flow's interface pair — a broad LAN->WAN accept
    # rule does not cover an east-west flow on a real FortiGate.
    fw.srcintf = _resolve_side_interface(
        snapshot, flow.srcs, zone_verdict.get("src_zones", []), "Source", fw.warnings)
    fw.dstintf = _resolve_side_interface(
        snapshot, flow.dsts, zone_verdict.get("dst_zones", []), "Destination", fw.warnings)

    # --- existing-rule coverage -------------------------------------------
    # A consolidated request is covered only if EVERY src×dst pair is fully
    # covered (possibly by different rules).
    pairs = flow.pairs
    pair_covered: dict[tuple[str, str], list[int]] = {p: [] for p in pairs}
    for pkg, policies in snapshot.policies_by_package.items():
        for pol in policies:
            results = {p: matcher.evaluate(pol, p[0], p[1], flow.service_ranges)
                       for p in pairs}
            if not any(r.matched for r in results.values()):
                continue
            summary = summarise_policy(pol, pkg)
            any_r = next(iter(results.values()))
            conditions_ok = (
                any_r.action == "accept" and not any_r.disabled
                and not any_r.conditional_schedule
                and not any(r.unknown_refs for r in results.values())
                and _intf_scoped(pol, fw.srcintf, fw.dstintf)
            )
            full_pairs = [p for p, r in results.items() if r.full_cover]
            summary["full_cover"] = conditions_ok and len(full_pairs) == len(pairs)
            if conditions_ok and full_pairs:
                for p in full_pairs:
                    pair_covered[p].append(pol.get("policyid", 0))
                if len(full_pairs) < len(pairs):
                    summary["covered_pairs"] = [f"{s} -> {d}" for s, d in full_pairs]
                fw.covering_rules.append(summary)
            else:
                # Skip disabled rules — they have no effect on traffic.
                if any_r.disabled:
                    continue
                # Skip if the service dimension has no overlap (e.g. an ICMP
                # rule when tcp/22 was requested — pure noise).
                svc_m, _ = matcher.svc_side(pol, flow.service_ranges)
                if not svc_m:
                    continue
                # Skip rules where the destination matched only via FQDN /
                # unresolvable refs with no actual IP-range overlap. These are
                # application-specific policies that have no real relationship
                # to the requested destination IP.
                if not pol.get("dstaddr-negate", "disable") in ("enable", 1, True):
                    if not any(matcher.addr_ip_overlap(pol, "dstaddr", d) for d in flow.dsts):
                        continue
                # Annotate which requested services aren't covered — engineers
                # can see at a glance what the gap is without reading the policy.
                svc_gap = matcher.uncovered_services(pol, flow.service_ranges)
                if svc_gap:
                    summary["svc_gap"] = [
                        f"{pr.protocol}/{pr.start}"
                        if pr.start == pr.end
                        else f"{pr.protocol}/{pr.start}-{pr.end}"
                        for pr in svc_gap
                    ]
                fw.partial_matches.append(summary)

    uncovered = [p for p in pairs if not pair_covered[p]]
    if not uncovered and not snapshot.degraded:
        fw.status = "already_covered"
        return fw
    if len(uncovered) < len(pairs):
        covered_ids = sorted({pid for ids in pair_covered.values() for pid in ids})
        fw.warnings.append(
            f"{len(pairs) - len(uncovered)} of {len(pairs)} flow pair(s) are "
            f"already covered by existing rule(s) {covered_ids} — the "
            "consolidated rule will overlap that coverage."
        )

    # --- new rule (or exception) -------------------------------------------

    src_objs = _dedupe_objects(
        [_address_object_plan("source", s, snapshot) for s in flow.srcs])
    dst_objs = _dedupe_objects(
        [_address_object_plan("destination", d, snapshot) for d in flow.dsts])
    svc_objs = _dedupe_objects(
        [_service_object_plan(tok, snapshot) for tok in flow.services])

    src_refs, src_groups = _side_plan(src_objs, src_group, ticket_id, "SRC")
    dst_refs, dst_groups = _side_plan(dst_objs, dst_group, ticket_id, "DST")
    fw.objects = src_objs + src_groups + dst_objs + dst_groups + svc_objs

    fw.policy_name = standards.policy_name(
        ticket_id,
        fw.srcintf or "<SET_SRC_INTERFACE>",
        fw.dstintf or "<SET_DST_INTERFACE>",
    )

    # insertion analysis on the package where the traffic would be evaluated:
    # the first package that has any overlapping policy, else the first fetched
    insertion: InsertionPlan | None = None
    pkg_for_insertion = None
    for pkg, policies in snapshot.policies_by_package.items():
        if any(matcher.evaluate(p, s, d, flow.service_ranges).matched
               for p in policies for s, d in pairs):
            pkg_for_insertion = pkg
            break
    if pkg_for_insertion is None and snapshot.policies_by_package:
        pkg_for_insertion = next(iter(snapshot.policies_by_package))
    if pkg_for_insertion is not None:
        insertion = plan_insertion(
            pkg_for_insertion,
            snapshot.policies_by_package[pkg_for_insertion],
            matcher, flow.srcs, flow.dsts, flow.service_ranges,
            fw.srcintf, fw.dstintf,
        )
        if insertion.shadowed_by:
            fw.warnings.append(
                f"Policies {insertion.shadowed_by} already fully match this flow "
                "(non-accept or conditional) — review before inserting."
            )
        if insertion.would_shadow:
            fw.warnings.append(
                f"The new rule would shadow existing policies {insertion.would_shadow} "
                "— consider consolidating instead of adding."
            )
    fw.insertion = insertion

    blocked = zone_verdict.get("verdict") == "BLOCKED"
    comments = cli_gen.exception_comment(ticket_id) if blocked else "Ticket <TICKET_ID>"

    fw.policy_cli = cli_gen.policy_cli(
        name=fw.policy_name,
        srcintf=fw.srcintf or "<SET_SRC_INTERFACE>",
        dstintf=fw.dstintf or "<SET_DST_INTERFACE>",
        srcaddr=src_refs,
        dstaddr=dst_refs,
        service=[o.name for o in svc_objs],
        logtraffic="all" if log_cfg.get("log_end", True) else "disable",
        logtraffic_start=bool(log_cfg.get("log_start", False)),
        comments=comments,
        insert_before=insertion.insert_before_policy_id if insertion else None,
    )

    fw.alternative = _group_append_alternative(fw, snapshot, matcher, flow, fmg_client)
    if fw.alternative:
        alt = fw.alternative
        # Inject the near-miss rule into partial_matches so the rule table
        # and the CLI section (Option B) tell the same story.
        for _pkg, _pols in snapshot.policies_by_package.items():
            if _pkg != alt.package:
                continue
            for _pol in _pols:
                if _pol.get("policyid") == alt.policy_id:
                    _nm = summarise_policy(_pol, _pkg)
                    _nm["full_cover"] = False
                    _nm["match_reason"] = (
                        f"{alt.side.capitalize()} missing — "
                        + ", ".join(m.name for m in alt.members)
                        + (" not yet in group " + alt.group if alt.group
                           else " not yet in address list")
                    )
                    fw.partial_matches.append(_nm)
                    break
            else:
                continue
            break
        member_names = ", ".join(m.name for m in alt.members)
        if alt.group:
            others = len(alt.affected_policies)
            fw.warnings.append(
                f"Alternative: rule #{alt.policy_id} {alt.policy_name!r} already covers "
                f"everything except the {alt.side} — appending {member_names} to "
                f"group {alt.group!r} would cover this flow without a new policy "
                f"({others} other rule(s) reference that group). Choose ONE option."
            )
        else:
            fw.warnings.append(
                f"Alternative: rule #{alt.policy_id} {alt.policy_name!r} already covers "
                f"everything except the {alt.side} — adding {member_names} directly to "
                "the rule's source address list would cover this flow without a new policy "
                "(only this rule is affected). Choose ONE option."
            )
    return fw


def _dedupe_objects(objs: list[ObjectPlan]) -> list[ObjectPlan]:
    seen: set[str] = set()
    out: list[ObjectPlan] = []
    for o in objs:
        if o.name not in seen:
            seen.add(o.name)
            out.append(o)
    return out


def _resolve_side_interface(
    snapshot, members: list[str], zones: list[str], label: str,
    warnings: list[str],
) -> str:
    """One interface for a whole side. All members must resolve to the same
    interface; a conflict yields "" plus a warning — never a silent pick."""
    from app.planner.fetch import resolve_interface

    resolved: dict[str, str] = {}
    for m in members:
        name, w = resolve_interface(snapshot, m, zones, label)
        warnings.extend(x for x in w if x not in warnings)
        resolved[m] = name
    distinct = sorted({v for v in resolved.values() if v})
    if len(distinct) > 1:
        detail = ", ".join(f"{m}→{v or '?'}" for m, v in resolved.items())
        warnings.append(
            f"{label} members resolve to different interfaces ({detail}) — a "
            "single consolidated rule cannot carry both; set the interface "
            "manually or split the request."
        )
        return ""
    return distinct[0] if distinct else ""


def _group_append_alternative(
    fw: FirewallPlan,
    snapshot,
    matcher: PolicyMatcher,
    flow: NormalizedFlow,
    client,
) -> GroupAppendAlternative | None:
    """Find the best near-miss rule where the only gap is one address side,
    and propose extending it instead of creating a new policy.

    Two extension modes are offered:
    - Group-append: the failing side references a named address group →
      append the missing endpoint to that group. Carries full blast radius.
    - Direct-append: the failing side is a concrete host/subnet list with no
      group → add the missing endpoint directly to the rule's address list.
      Only that one rule is affected (no blast radius).

    All qualifying candidates across every package are collected, then ranked
    by the specificity of the non-failing sides (count of non-"all" address
    refs). A direct-append candidate receives a +1 tiebreaker because it has
    a smaller blast radius than an equivalent group-append.

    Rules must be enabled, accept, unconditional, interface-scoped, and have
    no unknown refs. The failing side must be non-negated and non-empty (an
    unconstrained "all" source/destination is skipped for direct-append since
    the rule already matches anything).
    """
    # Score tuple: (has_specific, -non_all_count, direct_tiebreaker)
    # has_specific=1 beats "all" rules (has_specific=0).
    # Among specific rules, FEWER non-failing side refs is better — a rule
    # with exactly the destination we need (1 ref) is more targeted than one
    # that matches our destination among 10 others.
    # direct_tiebreaker=1 breaks ties in favour of direct-append (no blast radius).
    candidates: list[tuple[tuple[int, int, int], GroupAppendAlternative]] = []

    for pkg, policies in snapshot.policies_by_package.items():
        for pol in policies:
            results = [matcher.evaluate(pol, s, d, flow.service_ranges)
                       for s, d in flow.pairs]
            r = results[0]
            if (r.disabled or r.conditional_schedule or r.action != "accept"
                    or any(x.unknown_refs for x in results)
                    or all(x.full_cover for x in results)):
                continue
            if not _intf_scoped(pol, fw.srcintf, fw.dstintf):
                continue
            _, svc_full = matcher.svc_side(pol, flow.service_ranges)
            if not svc_full:
                continue
            src_fulls = {s: matcher.addr_side(pol, "srcaddr", s)[1] for s in flow.srcs}
            dst_fulls = {d: matcher.addr_side(pol, "dstaddr", d)[1] for d in flow.dsts}
            for side, key, other_key, missing, other_all_full in (
                ("destination", "dstaddr", "srcaddr",
                 [d for d, f in dst_fulls.items() if not f],
                 all(src_fulls.values())),
                ("source", "srcaddr", "dstaddr",
                 [s for s, f in src_fulls.items() if not f],
                 all(dst_fulls.values())),
            ):
                if not missing or not other_all_full:
                    continue
                if pol.get(f"{key}-negate", "disable") in ("enable", 1, True):
                    continue  # appending to a negated side REMOVES access

                other_refs = list(_ref_names(pol.get(other_key, [])))
                non_all_count = sum(1 for ref in other_refs if ref.lower() != "all")
                has_specific = 1 if non_all_count > 0 else 0

                group = next(
                    (n for n in _ref_names(pol.get(key, []))
                     if snapshot.addr_catalog.is_group(n)), None,
                )
                members = [_address_object_plan(side, t, snapshot) for t in missing]

                if group is not None:
                    score: tuple[int, int, int] = (has_specific, -non_all_count, 0)
                    candidates.append((score, GroupAppendAlternative(
                        package=pkg,
                        policy_id=pol.get("policyid", 0),
                        policy_name=pol.get("name", ""),
                        side=side,
                        group=group,
                        members=members,
                        group_cli=cli_gen.addrgrp_append_cli(
                            group, [m.name for m in members]),
                    )))
                else:
                    # Direct-append: the failing side has concrete refs, no group.
                    # Skip if unconstrained ("all") — the rule would already match.
                    failing_refs = list(_ref_names(pol.get(key, [])))
                    if not failing_refs or failing_refs == ["all"]:
                        continue
                    member_names = [m.name for m in members]
                    score = (has_specific, -non_all_count, 1)
                    candidates.append((score, GroupAppendAlternative(
                        package=pkg,
                        policy_id=pol.get("policyid", 0),
                        policy_name=pol.get("name", ""),
                        side=side,
                        group=None,
                        members=members,
                        direct_cli=cli_gen.policy_addr_append_cli(
                            pol.get("policyid", 0), key, member_names),
                        warnings=[
                            f"Adding {', '.join(member_names)} directly to rule "
                            f"#{pol.get('policyid', 0)} {side} address list — "
                            "only this rule is affected."
                        ],
                    )))

    if not candidates:
        return None

    winner = max(candidates, key=lambda c: c[0])[1]

    # Compute blast radius once, only for the winning group-append candidate.
    if winner.group is not None:
        affected, scan_warnings = _group_blast_radius(
            client, snapshot, winner.group,
            exclude=(winner.package, winner.policy_id),
        )
        winner.affected_policies = affected
        winner.warnings = list(scan_warnings)
        if affected:
            winner.warnings.append(
                f"Appending to group {winner.group!r} also changes "
                f"{len(affected)} other rule(s) — review each before "
                "choosing this option."
            )
        else:
            winner.warnings.append(
                f"No other rule references group {winner.group!r} — the append "
                "affects only the rule above."
            )

    return winner


def _group_blast_radius(
    client, snapshot, group: str, exclude: tuple[str, int],
) -> tuple[list[dict], list[str]]:
    """Every policy in the ADOM referencing `group` directly or through a
    parent group — the set of rules whose behaviour changes on append."""
    names = {group} | snapshot.addr_catalog.groups_containing(group)
    affected: list[dict] = []
    warnings: list[str] = []

    try:
        all_pkgs = [
            p.get("name", "") for p in client.get_policy_packages(snapshot.adom)
            if isinstance(p, dict)
        ]
    except FMGError as exc:
        return [], [f"Blast-radius scan incomplete — cannot list packages: {exc}"]

    for pkg in all_pkgs:
        policies = snapshot.policies_by_package.get(pkg)
        if policies is None:
            try:
                policies = [
                    p for p in client.get_policies(snapshot.adom, pkg)
                    if isinstance(p, dict)
                ]
            except FMGError as exc:
                warnings.append(
                    f"Blast-radius scan incomplete — package {pkg!r} could not "
                    f"be read: {exc}"
                )
                continue
        for pol in policies:
            pid = pol.get("policyid", 0)
            if (pkg, pid) == exclude:
                continue
            for key, label in (("srcaddr", "source"), ("dstaddr", "destination")):
                via = sorted(set(_ref_names(pol.get(key, []))) & names)
                if via:
                    affected.append({
                        "package": pkg,
                        "policy_id": pid,
                        "name": pol.get("name", ""),
                        "side": label,
                        "status": pol.get("status", "enable"),
                        "via": via,
                    })
    return affected, warnings


# ---------------------------------------------------------------------------
# Recommendation text (fixed templates — no free-form generation)
# ---------------------------------------------------------------------------

def _recommendation(plan_status: str, verdict: str, firewalls: list[FirewallPlan],
                    risk: str, warnings: list[str],
                    zone_verdict: dict | None = None) -> str:
    if plan_status == "unknown_no_action":
        return (
            "Zone verdict is UNKNOWN — at least one IP did not resolve to a known "
            "zone. Verify the IPs with the requester and/or update the zone policy "
            "catalogue. No change should be implemented until resolved."
        )
    if plan_status == "already_covered":
        return (
            "Traffic is permitted by zone policy and every named firewall already "
            "has an enabled rule covering this exact flow. No change required — "
            "close the request citing the existing rules listed above."
        )
    lines = []
    if plan_status == "blocked_exception":
        governing = (zone_verdict or {}).get("governing", [])
        blocking_policy = next(
            (g.get("policy_set", "") for g in governing
             if g.get("access_type", "").startswith("block")),
            None,
        )
        block_detail = (
            f" Blocked by: \"{blocking_policy}\"." if blocking_policy else ""
        )
        lines.append(
            f"Zone policy BLOCKS this flow.{block_detail} The generated CLI "
            "implements an EXCEPTION and must not be pushed until the approval "
            f"chain (risk level: {risk}) has signed off."
        )
    else:
        lines.append(
            "Traffic is permitted by zone policy but not yet implemented on: "
            + ", ".join(f.firewall for f in firewalls if f.status == "new_rule")
            + f". Implement the generated objects and policy (risk level: {risk})."
        )
    not_found = [f.firewall for f in firewalls if f.status in ("not_found", "error")]
    if not_found:
        lines.append(
            "Could not analyse: " + ", ".join(not_found) + " — verify the device "
            "names/ADOM with FortiManager before proceeding."
        )
    if warnings:
        lines.append("Review the warnings section before implementation.")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _norm_list(value: str | list[str], label: str) -> list[str]:
    """Accept a comma-separated string or a list; return clean tokens."""
    if isinstance(value, str):
        tokens = [t.strip() for t in value.split(",") if t.strip()]
    else:
        tokens = [str(t).strip() for t in value if str(t).strip()]
    if not tokens:
        raise PlannerDataError("request", f"{label} must have at least one value")
    return tokens


def _consolidated_zone_verdict(
    zc: ZoneDBAdapter, srcs: list[str], dsts: list[str], services: list[str],
) -> dict:
    """One aggregated verdict across every src×dst×service combination.

    Mixed ALLOWED+BLOCKED means the request cannot be one consolidated rule —
    that is a request problem, not a data problem, and the caller must split
    it. Any UNKNOWN combination makes the whole request UNKNOWN (fail safe).
    """
    verdicts: dict[str, list[str]] = {}
    src_zones: list[str] = []
    dst_zones: list[str] = []
    governing: list = []
    all_policies: list = []
    notes: list[str] = []
    seen_gov: set[str] = set()

    for s in srcs:
        for d in dsts:
            for svc in services:
                r = fetch_zone_verdict(zc, s, d, svc)
                v = r.get("verdict", "UNKNOWN")
                verdicts.setdefault(v, []).append(f"{s} -> {d} ({svc})")
                for z in r.get("src_zones", []):
                    if z not in src_zones:
                        src_zones.append(z)
                for z in r.get("dst_zones", []):
                    if z not in dst_zones:
                        dst_zones.append(z)
                for g in r.get("governing", []):
                    key = repr(g)
                    if key not in seen_gov:
                        seen_gov.add(key)
                        governing.append(g)
                all_policies.extend(r.get("all_policies", []))
                for n in r.get("notes", []):
                    if n not in notes:
                        notes.append(n)

    if "UNKNOWN" in verdicts:
        verdict = "UNKNOWN"
        notes.append(
            "Verdict UNKNOWN for: " + "; ".join(verdicts["UNKNOWN"])
            + " — no combination may be implemented until resolved."
        )
    elif "ALLOWED" in verdicts and "BLOCKED" in verdicts:
        raise PlannerDataError(
            "request",
            "Zone policy gives mixed verdicts — ALLOWED for "
            + "; ".join(verdicts["ALLOWED"]) + " but BLOCKED for "
            + "; ".join(verdicts["BLOCKED"])
            + ". A single consolidated rule cannot carry both: split the "
            "request into one per verdict and re-run.",
        )
    else:
        verdict = next(iter(verdicts))

    return {
        "src_ip": ", ".join(srcs),
        "dst_ip": ", ".join(dsts),
        "service": ", ".join(services),
        "verdict": verdict,
        "src_zones": src_zones,
        "dst_zones": dst_zones,
        "governing": governing,
        "all_policies": all_policies,
        "notes": notes,
    }


def plan_change(
    *,
    src: str | list[str],
    dst: str | list[str],
    service: str | list[str],
    firewalls: list[TargetFirewall],
    justification: str = "",
    ticket_id: str = "",
    src_group: str = "",
    dst_group: str = "",
    fmg_client: FMGClient | None = None,
    zone_client: ZoneDBAdapter | None = None,
) -> ChangePlan:
    """Compute the full deterministic change plan for one consolidated
    request. src/dst/service accept a single value, a comma-separated
    string, or a list; the plan emits ONE policy per firewall covering
    every combination.

    fmg_client should already be logged in (e.g. via `with make_client() as
    client:`) when passed explicitly — only the default (fmg_client=None)
    path calls .login() itself.
    """
    srcs = _norm_list(src, "src")
    dsts = _norm_list(dst, "dst")
    services = _norm_list(service, "service")

    service_ranges = []
    for tok in services:
        try:
            service_ranges.extend(parse_service_request(tok))
        except ValueError as exc:
            raise PlannerDataError("request", str(exc)) from exc

    flow = NormalizedFlow(src=", ".join(srcs), dst=", ".join(dsts),
                          service=", ".join(services),
                          srcs=srcs, dsts=dsts, services=services,
                          service_ranges=service_ranges,
                          justification=justification)

    zc = zone_client or _default_zone_client()
    zone_verdict = _consolidated_zone_verdict(zc, srcs, dsts, services)
    zone_domains = fetch_zone_domains(zc)

    src_zones = zone_verdict.get("src_zones", [])
    dst_zones = zone_verdict.get("dst_zones", [])
    verdict = zone_verdict.get("verdict", "UNKNOWN")

    risk = standards.risk_level(src_zones, dst_zones, zone_domains)
    src_domains = {zone_domains.get(z, "") for z in src_zones} - {""}
    dst_domains = {zone_domains.get(z, "") for z in dst_zones} - {""}
    # The catch-all zone named "Internet" is the internet whatever domain
    # label the catalogue happens to give it.
    if "Internet" in src_zones:
        src_domains.add("Internet")
    if "Internet" in dst_zones:
        dst_domains.add("Internet")
    rule_type = standards.rule_type_for(verdict, src_domains, dst_domains, service_ranges)
    log_cfg = standards.log_settings(rule_type)
    approval = standards.review_requirements(risk)

    warnings: list[str] = list(zone_verdict.get("notes", []))
    warnings.extend(standards.permissiveness_warnings(srcs, dsts, service_ranges))
    fw_plans: list[FirewallPlan] = []

    if verdict == "UNKNOWN":
        for target in firewalls:
            fw_plans.append(FirewallPlan(
                firewall=target.device, adom=target.adom, status="no_action",
                warnings=["Zone verdict UNKNOWN — no analysis performed"],
            ))
        cli_status = "unknown_no_action"
    else:
        client = fmg_client or _default_fmg_client()
        for target in firewalls:
            fw_plans.append(_plan_firewall(
                target, flow, zone_verdict, log_cfg, ticket_id, client, warnings,
                src_group=src_group, dst_group=dst_group,
            ))
        for fw in fw_plans:
            warnings.extend(w for w in fw.warnings if w not in warnings)

        if verdict == "BLOCKED":
            cli_status = "blocked_exception"
        elif fw_plans and all(f.status == "already_covered" for f in fw_plans):
            cli_status = "already_covered"
        else:
            cli_status = "new_rule"

    recommendation = _recommendation(cli_status, verdict, fw_plans, risk, warnings,
                                      zone_verdict=zone_verdict)

    return ChangePlan(
        ticket_id=ticket_id,
        flow=flow,
        zone_verdict=zone_verdict,
        risk_level=risk,
        firewalls=fw_plans,
        cli_status=cli_status,
        recommendation=recommendation,
        warnings=warnings,
        naming=_naming_section(fw_plans),
        logging=log_cfg,
        approval=approval,
    )


def _naming_section(fw_plans: list[FirewallPlan]) -> dict:
    naming_yaml = standards.load_naming()
    conventions = naming_yaml.get("platforms", {}).get("fortigate", {}).get("conventions", {})
    objects = []
    seen = set()
    for fw in fw_plans:
        for obj in fw.objects:
            if obj.name in seen:
                continue
            seen.add(obj.name)
            pattern = conventions.get(obj.obj_type, {}).get("pattern", "")
            objects.append({
                "role": obj.role,
                "type": obj.obj_type,
                "name": obj.name,
                "pattern": pattern if obj.action == "create" else "(existing object — reused)",
            })
    return {"objects": objects}


# ---------------------------------------------------------------------------
# Report payload
# ---------------------------------------------------------------------------

def to_report_payload(plan: ChangePlan) -> dict:
    """Emit a report-ready dict from a ChangePlan."""
    existing_rules = {}
    for fw in plan.firewalls:
        if fw.status == "already_covered":
            note = "Existing enabled rule(s) fully cover this flow."
        elif fw.status == "new_rule":
            note = "No covering rule found — a new rule is required."
            if fw.partial_matches:
                note += f" ({len(fw.partial_matches)} partially-overlapping rule(s) noted.)"
        elif fw.status == "no_action":
            note = "Not analysed — zone verdict UNKNOWN."
        else:
            note = "; ".join(fw.warnings) or "Device could not be analysed."
        existing_rules[fw.firewall] = {
            "status": fw.status.upper().replace("_", " "),
            "rules": fw.covering_rules + fw.partial_matches,
            "covering_rules": fw.covering_rules,
            "partial_matches": fw.partial_matches,
            "note": note,
        }

    per_firewall = []
    for fw in plan.firewalls:
        if fw.status not in ("new_rule",):
            continue
        entry = {
            "firewall": fw.firewall,
            "warnings": list(fw.warnings),
            "address_objects": [
                {"cli": o.cli} for o in fw.objects if o.action == "create" and o.cli
            ],
            "policy": {"cli": fw.policy_cli},
        }
        if fw.insertion:
            entry["warnings"].append(f"Placement: {fw.insertion.rationale}")
        if fw.alternative:
            alt = fw.alternative
            member_names = ", ".join(m.name for m in alt.members)
            if alt.group:
                summary = (
                    f"Extend existing rule #{alt.policy_id} {alt.policy_name!r} "
                    f"(package {alt.package!r}) by appending {member_names} "
                    f"to its {alt.side} group {alt.group!r} instead of creating "
                    "a new policy. Choose ONE option, not both."
                )
            else:
                summary = (
                    f"Extend existing rule #{alt.policy_id} {alt.policy_name!r} "
                    f"(package {alt.package!r}) by adding {member_names} directly "
                    f"to its {alt.side} address list instead of creating "
                    "a new policy. Choose ONE option, not both."
                )
            entry["alternative"] = {
                "summary": summary,
                "package": alt.package,
                "policy_id": alt.policy_id,
                "policy_name": alt.policy_name,
                "side": alt.side,
                "group": alt.group,
                "member_names": [m.name for m in alt.members],
                "member_cli": "\n\n".join(m.cli for m in alt.members if m.cli),
                "group_cli": alt.group_cli,
                "direct_cli": alt.direct_cli,
                "affected_rules": alt.affected_policies,
                "warnings": alt.warnings,
            }
        per_firewall.append(entry)

    return {
        "ticket_id": plan.ticket_id,
        "request": {
            "src": plan.flow.src,
            "dst": plan.flow.dst,
            "service": plan.flow.service,
            "justification": plan.flow.justification,
            "firewalls": [f.firewall for f in plan.firewalls],
        },
        "zone_verdict": {
            "verdict": plan.zone_verdict.get("verdict", "UNKNOWN"),
            "src_zones": plan.zone_verdict.get("src_zones", []),
            "dst_zones": plan.zone_verdict.get("dst_zones", []),
            "governing": plan.zone_verdict.get("governing", []),
        },
        "existing_rules": existing_rules,
        "naming": plan.naming,
        "logging": {
            "rule_type": plan.logging.get("rule_type", ""),
            "log_start": plan.logging.get("log_start", ""),
            "log_end": plan.logging.get("log_end", ""),
            "alert_on_match": plan.logging.get("alert_on_match", ""),
            "retention_days": plan.logging.get("retention_days", ""),
            "siem_forward": plan.logging.get("siem_forward", ""),
            "notes": plan.logging.get("notes", ""),
        },
        "approval": {
            "risk_level": plan.risk_level,
            "approvers": plan.approval.get("approvers", []),
            "peer_review": plan.approval.get("peer_review", ""),
            "security_review": plan.approval.get("security_review", ""),
            "change_window": str(plan.approval.get("change_window", "")).strip(),
            "sla_hours": plan.approval.get("sla_hours", ""),
        },
        "recommendation": plan.recommendation,
        "cli": {
            "status": plan.cli_status,
            "per_firewall": per_firewall,
        },
    }
```

- [ ] **Step 4: Run to verify it passes**

```bash
uv run pytest tests/test_planner_engine.py -v
```
Expected: all 6 tests pass. If `app.fmg_helpers.make_client()` requires an application context or `.env` values not present in the test environment, mock it out in the two tests that don't pass an explicit `fmg_client` — but all six tests above already pass `fmg_client=` explicitly, so `_default_fmg_client()` should never execute during this test run.

- [ ] **Step 5: Write the provenance marker**

```bash
git -C ~/code/github/ai/4tanalyst rev-parse HEAD
git -C ~/code/github/ai/4tanalyst log -1 --format='%ci'
```

Create `app/planner/VENDORED_FROM.md` using the commit hash and date printed above:

```markdown
# Provenance

The `app/planner/` package (`models.py`, `matching.py`, `cli_gen.py`,
`standards.py`, `insertion.py`, `fetch.py`, `engine.py`) is ported from
`~/code/github/ai/4tanalyst`'s `planner/` package (plus `fortimanager_mcp/
matching.py` and `fortimanager_mcp/query.py`'s catalog functions), adapted
to call 4THealth+'s own `app/fmg_client.py`/`app/zone_db.py` directly
in-process instead of over HTTP with separate credentials.

**Ported from commit:** `<PASTE THE COMMIT HASH PRINTED ABOVE>`
**Source commit date:** `<PASTE THE DATE PRINTED ABOVE>`
**Ported on:** `<TODAY'S DATE>`

## Files and their adaptation

| 4THealth+ file | 4tAnalyst source | Adaptation |
|---|---|---|
| `models.py` | `planner/models.py` | Import path only |
| `matching.py` | `fortimanager_mcp/matching.py` | Import path only (verbatim otherwise) |
| `cli_gen.py` | `planner/cli_gen.py` | None (verbatim) |
| `standards.py` | `planner/standards.py` | Import path; YAML file paths moved to project root (`naming.yaml`/`review_requirements.yaml` instead of `standards_mcp/`) |
| `catalogs.py` | `fortimanager_mcp/query.py` (catalog functions only) | Dropped TTL cache/thread-lock layer; uses `app.fmg_client.FMGClient`'s existing methods |
| `zone_adapter.py` | `zone_mcp/client.py` (`ZonePolicyClient`) | New adapter class wrapping `app.zone_db` directly instead of an HTTP client |
| `insertion.py` | `planner/insertion.py` | Import path only (verbatim otherwise) |
| `fetch.py` | `planner/fetch.py` | `FortiManagerClient`/`ZonePolicyClient` → `FMGClient`/`ZoneDBAdapter`; dropped the `device_zone_map.yaml` interface-resolution tier |
| `engine.py` | `planner/engine.py` | Dropped `credentials.yaml` loading — default clients now come from `app.fmg_helpers.make_client()` and `ZoneDBAdapter()` |

## How to sync later changes from 4tAnalyst

See the `4tanalyst-sync-workflow` memory note, or:

```bash
git -C ~/code/github/ai/4tanalyst log <SHA above>..HEAD --oneline -- planner/ standards_mcp/ fortimanager_mcp/matching.py fortimanager_mcp/query.py zone_mcp/client.py
```

Review each change and manually apply the relevant parts here — this is a
fork, not a live dependency, so nothing merges automatically. Update the
commit hash above once you've synced.
```

- [ ] **Step 6: Full-suite check and commit**

```bash
uv run pytest -q
git add app/planner/engine.py app/planner/VENDORED_FROM.md tests/test_planner_engine.py
git commit -m "Port and adapt planner plan_change orchestration engine"
```

---

### Task 8: Multi-provider LLM narration abstraction

**Files:**
- Create: `app/llm/__init__.py`
- Create: `app/llm/base.py`
- Create: `app/llm/claude_provider.py`
- Create: `app/llm/codex_provider.py`
- Create: `app/llm/ollama_provider.py`
- Modify: `app/config.py` (add AI provider settings)
- Modify: `app/app_settings.py` (add `ai_assist_enabled` flag)
- Modify: `.env.example` (document new variables)
- Modify: `pyproject.toml` (add `anthropic`, `openai` dependencies)
- Test: `tests/test_llm_providers.py`

**Interfaces:**
- Produces: `LLMError(Exception)`, `LLMProvider` (ABC with `.narrate(system_prompt, user_prompt) -> str`) from `app.llm.base`; `ClaudeProvider`, `CodexProvider`, `OllamaProvider` (each implementing `LLMProvider`) from their respective modules; `get_provider() -> LLMProvider` from `app.llm` — consumed by Task 9's route.
- Consumes: `Config.AI_PROVIDER`, `Config.ANTHROPIC_API_KEY`, `Config.ANTHROPIC_MODEL`, `Config.OPENAI_API_KEY`, `Config.OPENAI_MODEL`, `Config.OLLAMA_HOST`, `Config.OLLAMA_MODEL`, `Config.OLLAMA_API_KEY` (this task adds these to `app.config.Config`).

- [ ] **Step 1: Add AI provider settings to `app/config.py`**

Add these lines to the end of the `Config` class in `app/config.py` (after the existing `SNMP_PRIV_KEY` line):

```python
    # AI Assist (Rule Validation) — LLM provider selection and credentials
    AI_PROVIDER = os.environ.get("AI_PROVIDER", "claude")
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5")
    OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")
    OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "")
```

- [ ] **Step 2: Add `ai_assist_enabled` to `app/app_settings.py`**

Change the `_DEFAULTS` dict (line 12-14) from:
```python
_DEFAULTS: dict = {
    "external_api_enabled": False,
}
```
to:
```python
_DEFAULTS: dict = {
    "external_api_enabled": False,
    "ai_assist_enabled": False,
}
```

- [ ] **Step 3: Document the new `.env` variables**

Add this block to `.env.example` (near the other feature sections, e.g. after the SNMP block):

```
# AI Assist (Rule Validation) — provider selection: claude | codex | ollama
AI_PROVIDER=claude
ANTHROPIC_API_KEY=your-anthropic-api-key-here
ANTHROPIC_MODEL=claude-sonnet-4-5
# OPENAI_API_KEY=your-openai-api-key-here
# OPENAI_MODEL=gpt-5
# OLLAMA_HOST=http://localhost:11434
# OLLAMA_MODEL=llama3.1
# OLLAMA_API_KEY=
```

- [ ] **Step 4: Add SDK dependencies to `pyproject.toml`**

Add `"anthropic>=0.40"` and `"openai>=1.0"` to the `dependencies` list. Ollama needs no extra SDK — it's called via plain HTTP using the already-present `requests` dependency.

```bash
uv sync
```
Expected: succeeds.

- [ ] **Step 5: Write the failing tests**

```python
# tests/test_llm_providers.py
"""Tests for app.llm — multi-provider LLM narration."""
from unittest.mock import MagicMock, patch

import pytest

from app.llm.base import LLMError


def test_get_provider_returns_claude_by_default(monkeypatch):
    monkeypatch.setattr("app.config.Config.AI_PROVIDER", "claude")
    monkeypatch.setattr("app.config.Config.ANTHROPIC_API_KEY", "test-key")
    from app.llm import get_provider
    from app.llm.claude_provider import ClaudeProvider
    provider = get_provider()
    assert isinstance(provider, ClaudeProvider)


def test_get_provider_unknown_raises():
    with patch("app.config.Config.AI_PROVIDER", "not-a-real-provider"):
        from app.llm import get_provider
        with pytest.raises(LLMError):
            get_provider()


def test_claude_provider_requires_api_key(monkeypatch):
    monkeypatch.setattr("app.config.Config.ANTHROPIC_API_KEY", "")
    from app.llm.claude_provider import ClaudeProvider
    with pytest.raises(LLMError):
        ClaudeProvider()


def test_claude_provider_narrate_calls_anthropic_sdk(monkeypatch):
    monkeypatch.setattr("app.config.Config.ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("app.config.Config.ANTHROPIC_MODEL", "claude-sonnet-4-5")
    from app.llm.claude_provider import ClaudeProvider

    fake_block = MagicMock(type="text", text="Here is the report.")
    fake_response = MagicMock(content=[fake_block])
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch("anthropic.Anthropic", return_value=fake_client):
        provider = ClaudeProvider()
        result = provider.narrate("system prompt", "user prompt")
    assert result == "Here is the report."
    fake_client.messages.create.assert_called_once()


def test_claude_provider_narrate_wraps_sdk_errors(monkeypatch):
    monkeypatch.setattr("app.config.Config.ANTHROPIC_API_KEY", "test-key")
    from app.llm.claude_provider import ClaudeProvider
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("rate limited")
    with patch("anthropic.Anthropic", return_value=fake_client):
        provider = ClaudeProvider()
        with pytest.raises(LLMError):
            provider.narrate("system", "user")


def test_codex_provider_requires_api_key(monkeypatch):
    monkeypatch.setattr("app.config.Config.OPENAI_API_KEY", "")
    from app.llm.codex_provider import CodexProvider
    with pytest.raises(LLMError):
        CodexProvider()


def test_codex_provider_narrate_calls_openai_sdk(monkeypatch):
    monkeypatch.setattr("app.config.Config.OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("app.config.Config.OPENAI_MODEL", "gpt-5")
    from app.llm.codex_provider import CodexProvider

    fake_message = MagicMock(content="Here is the report.")
    fake_choice = MagicMock(message=fake_message)
    fake_response = MagicMock(choices=[fake_choice])
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    with patch("openai.OpenAI", return_value=fake_client):
        provider = CodexProvider()
        result = provider.narrate("system prompt", "user prompt")
    assert result == "Here is the report."


def test_ollama_provider_requires_host(monkeypatch):
    monkeypatch.setattr("app.config.Config.OLLAMA_HOST", "")
    from app.llm.ollama_provider import OllamaProvider
    with pytest.raises(LLMError):
        OllamaProvider()


def test_ollama_provider_narrate_calls_http_api(monkeypatch):
    monkeypatch.setattr("app.config.Config.OLLAMA_HOST", "http://localhost:11434")
    monkeypatch.setattr("app.config.Config.OLLAMA_MODEL", "llama3.1")
    monkeypatch.setattr("app.config.Config.OLLAMA_API_KEY", "")
    from app.llm.ollama_provider import OllamaProvider

    fake_response = MagicMock()
    fake_response.json.return_value = {"message": {"content": "Here is the report."}}
    fake_response.raise_for_status.return_value = None

    with patch("requests.post", return_value=fake_response) as mock_post:
        provider = OllamaProvider()
        result = provider.narrate("system prompt", "user prompt")
    assert result == "Here is the report."
    call_kwargs = mock_post.call_args
    assert call_kwargs.args[0] == "http://localhost:11434/api/chat"


def test_ollama_provider_narrate_wraps_http_errors(monkeypatch):
    monkeypatch.setattr("app.config.Config.OLLAMA_HOST", "http://localhost:11434")
    from app.llm.ollama_provider import OllamaProvider
    with patch("requests.post", side_effect=ConnectionError("refused")):
        provider = OllamaProvider()
        with pytest.raises(LLMError):
            provider.narrate("system", "user")
```

- [ ] **Step 6: Run to verify it fails**

```bash
uv run pytest tests/test_llm_providers.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.llm'`.

- [ ] **Step 7: Write `app/llm/base.py`**

```python
"""Provider-agnostic interface every LLM narration backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMError(Exception):
    """Raised when a provider is misconfigured or a completion call fails."""


class LLMProvider(ABC):
    @abstractmethod
    def narrate(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's text completion for one single-shot prompt.

        Raises LLMError on any failure (missing key, network error, non-2xx
        response) — callers must catch this and degrade gracefully rather
        than let it propagate to the user as a raw exception.
        """
```

- [ ] **Step 8: Write `app/llm/claude_provider.py`**

```python
"""Anthropic Claude provider — the default AI Assist backend."""

from __future__ import annotations

from app.config import Config
from app.llm.base import LLMError, LLMProvider


class ClaudeProvider(LLMProvider):
    def __init__(self) -> None:
        if not Config.ANTHROPIC_API_KEY:
            raise LLMError("ANTHROPIC_API_KEY is not set in .env")
        self._model = Config.ANTHROPIC_MODEL

    def narrate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("the 'anthropic' package is not installed") from exc
        try:
            client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            )
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Claude API call failed: {exc}") from exc
```

- [ ] **Step 9: Write `app/llm/codex_provider.py`**

```python
"""OpenAI (Codex/GPT) provider."""

from __future__ import annotations

from app.config import Config
from app.llm.base import LLMError, LLMProvider


class CodexProvider(LLMProvider):
    def __init__(self) -> None:
        if not Config.OPENAI_API_KEY:
            raise LLMError("OPENAI_API_KEY is not set in .env")
        self._model = Config.OPENAI_MODEL

    def narrate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            import openai
        except ImportError as exc:
            raise LLMError("the 'openai' package is not installed") from exc
        try:
            client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=self._model,
                max_tokens=2048,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content or ""
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"OpenAI API call failed: {exc}") from exc
```

- [ ] **Step 10: Write `app/llm/ollama_provider.py`**

```python
"""Ollama provider — local or cloud, via plain HTTP (no extra SDK dependency)."""

from __future__ import annotations

import requests

from app.config import Config
from app.llm.base import LLMError, LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self) -> None:
        if not Config.OLLAMA_HOST:
            raise LLMError("OLLAMA_HOST is not set in .env")
        self._host = Config.OLLAMA_HOST.rstrip("/")
        self._model = Config.OLLAMA_MODEL

    def narrate(self, system_prompt: str, user_prompt: str) -> str:
        headers = {}
        if Config.OLLAMA_API_KEY:
            headers["Authorization"] = f"Bearer {Config.OLLAMA_API_KEY}"
        try:
            resp = requests.post(
                f"{self._host}/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                },
                headers=headers,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "")
        except Exception as exc:
            raise LLMError(f"Ollama API call failed: {exc}") from exc
```

- [ ] **Step 11: Write `app/llm/__init__.py`**

```python
"""Multi-provider LLM narration for AI-assisted Rule Validation.

The LLM here never computes a verdict — app.planner.engine.plan_change()
already has. This package only turns that structured result into
human-readable prose (report + peer-review package text) via a single
completion call, using whichever provider is configured in AI_PROVIDER.
"""

from __future__ import annotations

from app.config import Config
from app.llm.base import LLMError, LLMProvider

__all__ = ["get_provider", "LLMError", "LLMProvider"]


def get_provider() -> LLMProvider:
    """Return the configured LLM provider instance."""
    provider = (Config.AI_PROVIDER or "claude").lower()
    if provider == "claude":
        from app.llm.claude_provider import ClaudeProvider
        return ClaudeProvider()
    if provider == "codex":
        from app.llm.codex_provider import CodexProvider
        return CodexProvider()
    if provider == "ollama":
        from app.llm.ollama_provider import OllamaProvider
        return OllamaProvider()
    raise LLMError(f"Unknown AI_PROVIDER {provider!r} — expected claude, codex, or ollama")
```

- [ ] **Step 12: Run to verify it passes**

```bash
uv run pytest tests/test_llm_providers.py -v
```
Expected: all 11 tests pass.

- [ ] **Step 13: Full-suite check and commit**

```bash
uv run pytest -q
git add app/llm/ app/config.py app/app_settings.py .env.example pyproject.toml uv.lock \
        tests/test_llm_providers.py
git commit -m "Add multi-provider LLM narration abstraction (Claude/Codex/Ollama)"
```

---

### Task 9: New `POST /api/rule-review/ai-assist` route

**Files:**
- Modify: `app/routes/rule_review_routes.py`
- Test: `tests/test_rule_review_ai_assist.py`

**Interfaces:**
- Consumes: `plan_change`, `to_report_payload` from `app.planner.engine` (Task 7); `PlannerDataError` from `app.planner.models` (Task 1); `TargetFirewall` from `app.planner.models` (Task 1); `get_provider` from `app.llm` (Task 8); `get_setting` from `app.app_settings` (already exists, flag added in Task 8); `make_client` from `app.fmg_helpers`, `FMGError` from `app.fmg_client`, `check_adom_access` from `app.decorators`, `internal_api_error`/`upstream_api_error` from `app.security` (all already imported in this file).
- Produces: `POST /api/rule-review/ai-assist` — request body `{"src", "dst", "service", "firewalls": [{"device", "adom"}, ...], "ticket_id", "justification", "src_group", "dst_group"}`, response `{"plan": <ChangePlan.to_dict()>, "narrative": str | None, "narrative_error": str | None, "path_relevance": {device: <check_path_relevance() result>}}`. Returns `503` if `ai_assist_enabled` is `False`, `400` for missing required fields, `403` via `check_adom_access` for any ADOM the user can't reach, `502` for `PlannerDataError`/`FMGError`, `500` for anything else. `path_relevance` is populated only for single-src/single-dst requests (see Step 3) and is `{}` otherwise — it's an advisory annotation from `app.rule_review.check_path_relevance`, not part of the deterministic verdict.
- Consumes (additionally): `check_path_relevance` from `app.rule_review` (existing, unmodified — see Global Constraints' decision 8 in the design spec).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rule_review_ai_assist.py
"""Tests for POST /api/rule-review/ai-assist."""
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = "tester"
            sess["role"] = "admin"
        yield c


def test_ai_assist_disabled_by_default_returns_503(client):
    with patch("app.app_settings.get_setting", return_value=False):
        resp = client.post("/api/rule-review/ai-assist", json={
            "src": "10.0.0.5", "dst": "10.0.0.6", "service": "tcp/443",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
        })
    assert resp.status_code == 503


def test_ai_assist_missing_fields_returns_400(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = client.post("/api/rule-review/ai-assist", json={"src": "10.0.0.5"})
    assert resp.status_code == 400


def test_ai_assist_success_returns_plan_and_narrative(client):
    fake_plan = MagicMock()
    fake_plan.to_dict.return_value = {"ticket_id": "CHG1", "cli_status": "new_rule"}

    fake_fmg = MagicMock()
    fake_fmg.get_device_interfaces_all_vdoms.return_value = [
        {"name": "port1", "ip": "10.0.0.1 255.255.255.0"},
    ]
    fake_fmg.get_device_routes_all_vdoms.return_value = []

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.fmg_helpers.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_change", return_value=fake_plan) as mock_plan_change, \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = fake_fmg
        mock_get_provider.return_value.narrate.return_value = "Here is the narrative report."

        resp = client.post("/api/rule-review/ai-assist", json={
            "src": "10.0.0.5", "dst": "10.0.0.6", "service": "tcp/443",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
            "ticket_id": "CHG1",
        })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["plan"]["ticket_id"] == "CHG1"
    assert data["narrative"] == "Here is the narrative report."
    assert data["narrative_error"] is None
    assert "FW-A" in data["path_relevance"]
    mock_plan_change.assert_called_once()


def test_ai_assist_narration_failure_still_returns_plan(client):
    fake_plan = MagicMock()
    fake_plan.to_dict.return_value = {"ticket_id": "CHG1", "cli_status": "new_rule"}

    fake_fmg = MagicMock()
    fake_fmg.get_device_interfaces_all_vdoms.return_value = []
    fake_fmg.get_device_routes_all_vdoms.return_value = []

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.fmg_helpers.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_change", return_value=fake_plan), \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = fake_fmg
        mock_get_provider.return_value.narrate.side_effect = RuntimeError("API down")

        resp = client.post("/api/rule-review/ai-assist", json={
            "src": "10.0.0.5", "dst": "10.0.0.6", "service": "tcp/443",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
        })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["plan"]["ticket_id"] == "CHG1"
    assert data["narrative"] is None
    assert data["narrative_error"] == "API down"


def test_ai_assist_planner_data_error_returns_502(client):
    from app.planner.models import PlannerDataError
    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.fmg_helpers.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_change",
               side_effect=PlannerDataError("request", "mixed verdicts")):
        mock_make_client.return_value.__enter__.return_value = MagicMock()
        resp = client.post("/api/rule-review/ai-assist", json={
            "src": "10.0.0.5", "dst": "10.0.0.6, 10.0.0.7", "service": "tcp/443",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
        })
    assert resp.status_code == 502
    assert "mixed verdicts" in resp.get_json()["error"]
```

Adjust the `create_app`/session-fixture pattern above to match whatever existing test file in `tests/` sets up a Flask test client with an authenticated admin session (check `tests/test_pending_changes.py` or `tests/conftest.py` for the established pattern in this repo, and copy it exactly rather than reinventing it) — the shape above is illustrative of the assertions needed, not necessarily the exact fixture boilerplate this codebase uses.

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_rule_review_ai_assist.py -v
```
Expected: FAIL with 404 (route doesn't exist yet) on every test.

- [ ] **Step 3: Add the route to `app/routes/rule_review_routes.py`**

Update the module docstring (top of file) to add the new endpoint to the list:

```python
"""Rule Validation tab.

Page:
  GET  /rule-review

API (all read-only against FortiManager; POST is for submitting work items):
  GET  /api/rule-review/adoms
  GET  /api/rule-review/adoms/<adom>/packages
  POST /api/rule-review/parse-import        — parse uploaded CSV or XLSX
  POST /api/rule-review/analyze             — run analysis
  GET  /api/rule-review/zone-status         — is zone policy DB available?
  POST /api/rule-review/ai-assist           — single-request AI Assist (planner + LLM narration)
"""
```

Append this route to the end of the file (after `rr_analyze`):

```python
# ── AI Assist ─────────────────────────────────────────────────────────────────


@bp.route("/api/rule-review/ai-assist", methods=["POST"])
@tab_required("rule_review")
def rr_ai_assist():
    """AI Assist: run plan_change deterministically, then narrate the result
    with the configured LLM. The deterministic result is always returned;
    narration is best-effort and degrades gracefully on failure — the LLM
    never computes or edits any value in the plan, it only explains it."""
    from app.app_settings import get_setting

    if not get_setting("ai_assist_enabled", False):
        return jsonify({"error": "AI Assist is not enabled"}), 503

    data = request.get_json(silent=True) or {}
    src = data.get("src", "")
    dst = data.get("dst", "")
    service = data.get("service", "")
    firewalls_raw = data.get("firewalls", [])
    ticket_id = data.get("ticket_id", "")
    justification = data.get("justification", "")
    src_group = data.get("src_group", "")
    dst_group = data.get("dst_group", "")

    if not src or not dst or not service or not firewalls_raw:
        return jsonify({"error": "src, dst, service, and firewalls are required"}), 400

    for fw in firewalls_raw:
        adom = fw.get("adom", "")
        if err := check_adom_access(adom):
            return err

    from app.planner.engine import plan_change
    from app.planner.models import PlannerDataError, TargetFirewall

    targets = [TargetFirewall(device=fw["device"], adom=fw["adom"]) for fw in firewalls_raw]

    path_relevance: dict = {}
    try:
        with make_client() as fmg:
            plan = plan_change(
                src=src, dst=dst, service=service, firewalls=targets,
                justification=justification, ticket_id=ticket_id,
                src_group=src_group, dst_group=dst_group,
                fmg_client=fmg,
            )

            # Path-relevance ("is this firewall actually in the traffic path")
            # has no equivalent in the ported planner — it's 4THealth+-specific
            # and wraps the planner's output, same as it already wraps the
            # existing bulk-analysis engine. Scoped to the single-src/single-dst
            # case (the common one); multi-value requests skip this check
            # rather than guessing which pair to report on.
            #
            # This entire block is best-effort and MUST NEVER raise: the plan
            # computed above has already succeeded, and losing it because an
            # advisory annotation failed would violate the core guarantee that
            # the deterministic result always renders. Every failure mode here
            # — a bad FMG call, a malformed interface/route shape,  anything
            # unanticipated — degrades to a per-device note, never a 500.
            srcs_list = [s.strip() for s in src.split(",") if s.strip()]
            dsts_list = [d.strip() for d in dst.split(",") if d.strip()]
            if len(srcs_list) == 1 and len(dsts_list) == 1:
                from app.rule_review import check_path_relevance
                for target in targets:
                    try:
                        interfaces = fmg.get_device_interfaces_all_vdoms(target.adom, target.device)
                        routes = fmg.get_device_routes_all_vdoms(target.adom, target.device)
                        path_relevance[target.device] = check_path_relevance(
                            srcs_list[0], dsts_list[0], interfaces, routes,
                        )
                    except Exception:
                        path_relevance[target.device] = {"in_path": None, "confidence": "low",
                                                          "notes": ["Could not determine path relevance"]}
    except PlannerDataError as exc:
        return jsonify({"error": str(exc), "source": exc.source}), 502
    except FMGError as exc:
        return upstream_api_error("rule_review", exc)
    except Exception as exc:
        return internal_api_error("rule_review", exc)

    plan_dict = plan.to_dict()

    narrative = None
    narrative_error = None
    try:
        import json as _json
        from app.llm import get_provider
        provider = get_provider()
        narrative = provider.narrate(
            system_prompt=(
                "You are a firewall change analyst assistant. You are given a "
                "structured, already-computed change plan as JSON. Write a clear, "
                "concise report for a peer reviewer: summarize the verdict, the "
                "required change per firewall, risk level, and approval "
                "requirements. Never invent or change any value in the plan — "
                "only explain it in prose."
            ),
            user_prompt=_json.dumps(plan_dict, default=str),
        )
    except Exception as exc:
        narrative_error = str(exc)

    return jsonify({
        "plan": plan_dict,
        "narrative": narrative,
        "narrative_error": narrative_error,
        "path_relevance": path_relevance,
    })
```

- [ ] **Step 4: Run to verify it passes**

```bash
uv run pytest tests/test_rule_review_ai_assist.py -v
```
Expected: all 5 tests pass. If the test client / session fixture pattern differs from what's assumed above, fix the test file to match this repo's actual pattern (found in Step 1) — do not change the route to accommodate an incorrect test fixture.

- [ ] **Step 5: Full-suite check and commit**

```bash
uv run pytest -q
git add app/routes/rule_review_routes.py tests/test_rule_review_ai_assist.py
git commit -m "Add POST /api/rule-review/ai-assist route"
```

---

### Task 10: AI Assist panel in the Rule Validation UI

**Files:**
- Modify: `app/templates/rule_review.html`
- Modify: `app/static/js/rule_review.js`

**Interfaces:**
- Consumes: `POST /api/rule-review/ai-assist` (Task 9).
- Produces: no new interfaces — this is the UI leaf node.

This UI panel has no Python test coverage (this codebase's JS isn't unit-tested — confirmed no `.js` test files anywhere in `tests/`); correctness here is verified by manual smoke-testing in Task 11.

- [ ] **Step 1: Add the AI Assist section to `app/templates/rule_review.html`**

Insert a new section between the existing "2 — Select Policy Packages" section and the "Review Results" section (i.e., as a new numbered section, or a collapsible panel below the existing Review button — place it directly after the closing tag of the package-selection section, before the results section). Use this markup:

```html
<section class="rr-section" id="rrAiAssistSection">
  <h2>AI Assist <span class="badge badge-beta">Beta</span></h2>
  <p class="rr-hint">Single-request analysis: describe one change, get a deterministic verdict plus an AI-written report and peer-review package. Requires an admin to enable AI Assist and configure a provider in Admin settings.</p>

  <div id="rrAiDisabledNotice" class="rr-notice" style="display:none">
    AI Assist is not enabled on this server. Ask an admin to enable it under Admin &rarr; AI Assist.
  </div>

  <form id="rrAiForm" class="rr-form">
    <div class="rr-form-row">
      <label for="rrAiSrc">Source IP(s)</label>
      <textarea id="rrAiSrc" placeholder="10.1.2.3 or comma/newline separated"></textarea>
    </div>
    <div class="rr-form-row">
      <label for="rrAiDst">Destination IP(s)</label>
      <textarea id="rrAiDst" placeholder="10.9.8.7"></textarea>
    </div>
    <div class="rr-form-row">
      <label for="rrAiSvc">Service(s)</label>
      <input type="text" id="rrAiSvc" placeholder="tcp/8443, tcp/22">
    </div>
    <div class="rr-form-row">
      <label for="rrAiFirewalls">Target firewall(s)</label>
      <input type="text" id="rrAiFirewalls" placeholder="FW01:OT-ADOM, FW02:OT-ADOM">
      <span class="rr-field-hint">Format: DEVICE:ADOM, comma-separated for multiple</span>
    </div>
    <div class="rr-form-row">
      <label for="rrAiTicket">Ticket ID</label>
      <input type="text" id="rrAiTicket" placeholder="CHG0012345">
    </div>
    <div class="rr-form-row">
      <label for="rrAiJustification">Justification</label>
      <input type="text" id="rrAiJustification" placeholder="Optional">
    </div>
    <div class="rr-form-row">
      <label for="rrAiSrcGroup">Source group name (optional)</label>
      <input type="text" id="rrAiSrcGroup" placeholder="Leave blank to auto-name">
    </div>
    <div class="rr-form-row">
      <label for="rrAiDstGroup">Destination group name (optional)</label>
      <input type="text" id="rrAiDstGroup" placeholder="Leave blank to auto-name">
    </div>
    <button type="submit" class="btn btn-primary" id="rrAiSubmitBtn">Run AI Assist</button>
  </form>

  <div id="rrAiRunning" class="rr-running" style="display:none">Running planner and generating report&hellip;</div>
  <div id="rrAiError" class="rr-error" style="display:none"></div>

  <div id="rrAiResult" style="display:none">
    <h3>Verdict: <span id="rrAiVerdict"></span></h3>
    <div id="rrAiPlanSummary"></div>
    <div id="rrAiPathRelevance" class="rr-hint" style="display:none"></div>
    <h3>AI-Generated Report</h3>
    <div id="rrAiNarrativeError" class="rr-notice" style="display:none"></div>
    <div id="rrAiNarrative" class="rr-narrative"></div>
    <h3>Generated CLI</h3>
    <pre id="rrAiCliOutput"></pre>
    <button type="button" class="btn btn-sm" id="rrAiCopyBtn">Copy CLI</button>
    <button type="button" class="btn btn-sm" id="rrAiDownloadBtn">Download Peer Review Package</button>
  </div>
</section>
```

- [ ] **Step 2: Add the AI Assist logic to `app/static/js/rule_review.js`**

Append this block to the end of the file:

```javascript
// ── AI Assist ─────────────────────────────────────────────────────────────

let aiAssistLastPayload = null;

function parseAiIPs(raw) {
  return raw.split(/[\n,]+/).map(s => s.trim()).filter(Boolean).join(', ');
}

function parseAiFirewalls(raw) {
  return raw.split(',').map(s => s.trim()).filter(Boolean).map(tok => {
    const [device, adom] = tok.split(':').map(s => (s || '').trim());
    return { device, adom };
  });
}

async function checkAiAssistAvailable() {
  try {
    const resp = await fetch('/api/rule-review/ai-assist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.CSRF_TOKEN || '' },
      body: JSON.stringify({}),
    });
    if (resp.status === 503) {
      document.getElementById('rrAiDisabledNotice').style.display = '';
      document.getElementById('rrAiSubmitBtn').disabled = true;
    }
  } catch (e) {
    // Non-fatal — the form's own submit handler will surface any real error.
  }
}

async function runAiAssist(evt) {
  evt.preventDefault();
  const errEl = document.getElementById('rrAiError');
  const resultEl = document.getElementById('rrAiResult');
  const runningEl = document.getElementById('rrAiRunning');
  errEl.style.display = 'none';
  resultEl.style.display = 'none';
  runningEl.style.display = '';

  const payload = {
    src: parseAiIPs(document.getElementById('rrAiSrc').value),
    dst: parseAiIPs(document.getElementById('rrAiDst').value),
    service: document.getElementById('rrAiSvc').value.trim(),
    firewalls: parseAiFirewalls(document.getElementById('rrAiFirewalls').value),
    ticket_id: document.getElementById('rrAiTicket').value.trim(),
    justification: document.getElementById('rrAiJustification').value.trim(),
    src_group: document.getElementById('rrAiSrcGroup').value.trim(),
    dst_group: document.getElementById('rrAiDstGroup').value.trim(),
  };

  try {
    const resp = await fetch('/api/rule-review/ai-assist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.CSRF_TOKEN || '' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    runningEl.style.display = 'none';
    if (!resp.ok) {
      errEl.textContent = data.error || `Request failed (${resp.status})`;
      errEl.style.display = '';
      return;
    }
    renderAiResult(data);
  } catch (e) {
    runningEl.style.display = 'none';
    errEl.textContent = 'Request failed: ' + e.message;
    errEl.style.display = '';
  }
}

function renderAiResult(data) {
  aiAssistLastPayload = data;
  const plan = data.plan;
  document.getElementById('rrAiVerdict').textContent = plan.cli_status;
  document.getElementById('rrAiPlanSummary').textContent = plan.recommendation || '';

  const pathEl = document.getElementById('rrAiPathRelevance');
  const pathEntries = Object.entries(data.path_relevance || {});
  if (pathEntries.length) {
    pathEl.innerHTML = pathEntries.map(([device, pr]) => {
      const status = pr.in_path === true ? 'In path' : pr.in_path === false ? 'Not in path' : 'Unknown';
      return `<div><strong>${device}:</strong> ${status} (${pr.confidence || 'low'} confidence)</div>`;
    }).join('');
    pathEl.style.display = '';
  } else {
    pathEl.style.display = 'none';
  }

  const narrEl = document.getElementById('rrAiNarrative');
  const narrErrEl = document.getElementById('rrAiNarrativeError');
  if (data.narrative) {
    narrEl.textContent = data.narrative;
    narrErrEl.style.display = 'none';
  } else {
    narrEl.textContent = '';
    narrErrEl.textContent = 'AI summary unavailable: ' + (data.narrative_error || 'unknown error');
    narrErrEl.style.display = '';
  }

  const cliLines = (plan.firewalls || [])
    .filter(fw => fw.policy_cli)
    .map(fw => `# ${fw.firewall}\n${fw.policy_cli}`);
  document.getElementById('rrAiCliOutput').textContent = cliLines.join('\n\n');

  document.getElementById('rrAiResult').style.display = '';
}

function copyAiCli() {
  const text = document.getElementById('rrAiCliOutput').textContent;
  navigator.clipboard.writeText(text).catch(() => {});
}

function downloadAiPackage() {
  if (!aiAssistLastPayload) return;
  const plan = aiAssistLastPayload.plan;
  const narrative = aiAssistLastPayload.narrative || '(AI summary unavailable)';
  const cli = document.getElementById('rrAiCliOutput').textContent;
  const text = [
    `Peer Review Package — ${plan.ticket_id || '(no ticket)'}`,
    '='.repeat(60),
    '',
    'AI-Generated Report:',
    narrative,
    '',
    'Generated CLI:',
    cli,
  ].join('\n');
  const a = document.createElement('a');
  const bl = new Blob([text], { type: 'text/plain' });
  a.href = URL.createObjectURL(bl);
  a.download = `peer_review_${plan.ticket_id || 'package'}.txt`;
  a.click();
  URL.revokeObjectURL(a.href);
}

document.getElementById('rrAiForm')?.addEventListener('submit', runAiAssist);
document.getElementById('rrAiCopyBtn')?.addEventListener('click', copyAiCli);
document.getElementById('rrAiDownloadBtn')?.addEventListener('click', downloadAiPackage);
checkAiAssistAvailable();
```

Note: the `checkAiAssistAvailable()` probe POSTs an empty body specifically to trigger the route's `ai_assist_enabled` check (which runs before body validation) without triggering a real planner run — the route returns `503` before reaching field validation when the flag is off, and `400` (harmlessly ignored here) when the flag is on but the probe's empty body fails validation. If a lighter-weight status-check pattern already exists elsewhere in this codebase (e.g. how `checkZoneStatus()` in this same file calls `GET /api/rule-review/zone-status`), consider adding a matching `GET /api/rule-review/ai-assist-status` endpoint instead for consistency — evaluate this against the existing pattern before implementing, and note the choice in the commit message.

- [ ] **Step 3: Manual smoke test**

```bash
uv run python wsgi.py &
sleep 2
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5000/rule-review
kill %1
```
Expected: `302` (redirects to login when unauthenticated) or `200` if a session cookie is supplied — confirms the template renders without a Jinja error (a malformed template would 500, not redirect/200).

- [ ] **Step 4: Commit**

```bash
git add app/templates/rule_review.html app/static/js/rule_review.js
git commit -m "Add AI Assist panel to the Rule Validation UI"
```

---

### Task 11: Admin toggle for AI Assist, final verification, and docs

**Files:**
- Modify: `app/routes/admin_routes.py`
- Modify: `app/templates/admin.html`
- Modify: `app/static/js/admin.js`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Test: `tests/test_admin_ai_assist_setting.py`

**Interfaces:**
- Consumes: `get_setting`/`set_setting` from `app.app_settings` (flag added in Task 8), the existing `/admin/api/settings` GET/PUT pattern.
- Produces: an admin-toggleable `ai_assist_enabled` setting, reachable from the UI — without this, Task 9's route can never be turned on except by hand-editing `app_settings.json`.

Without this task, `ai_assist_enabled` (added to `_DEFAULTS` in Task 8) has no way to be set to `True` except manually editing `app_settings.json` — this task wires it into the existing admin settings pattern (mirrors `external_api_enabled` exactly, per `app/routes/admin_routes.py:263-279`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_admin_ai_assist_setting.py
"""Tests for the ai_assist_enabled admin setting toggle."""
from unittest.mock import patch

import pytest

from app import create_app


@pytest.fixture
def admin_client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
        yield c


def test_settings_get_includes_ai_assist_enabled(admin_client):
    resp = admin_client.get("/admin/api/settings")
    assert resp.status_code == 200
    assert "ai_assist_enabled" in resp.get_json()


def test_settings_put_toggles_ai_assist_enabled(admin_client):
    with patch("app.routes.admin_routes.set_setting") as mock_set:
        resp = admin_client.put("/admin/api/settings", json={"ai_assist_enabled": True})
    assert resp.status_code == 200
    mock_set.assert_any_call("ai_assist_enabled", True)
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_admin_ai_assist_setting.py -v
```
Expected: `test_settings_get_includes_ai_assist_enabled` passes already (the flag exists in `_DEFAULTS` from Task 8, so `get_all_settings()` already includes it) — `test_settings_put_toggles_ai_assist_enabled` FAILS because `api_settings_put` doesn't check for `"ai_assist_enabled"` yet.

- [ ] **Step 3: Extend `api_settings_put` in `app/routes/admin_routes.py`**

Change:
```python
@bp.route("/api/settings", methods=["PUT"])
@_admin_required
def api_settings_put():
    data = request.get_json(silent=True) or {}
    if "external_api_enabled" in data:
        enabled = bool(data["external_api_enabled"])
        set_setting("external_api_enabled", enabled)
        app_log(
            "INFO", "admin", "External API toggled", by=session["user"], enabled=enabled
        )
    return jsonify(get_all_settings())
```
to:
```python
@bp.route("/api/settings", methods=["PUT"])
@_admin_required
def api_settings_put():
    data = request.get_json(silent=True) or {}
    if "external_api_enabled" in data:
        enabled = bool(data["external_api_enabled"])
        set_setting("external_api_enabled", enabled)
        app_log(
            "INFO", "admin", "External API toggled", by=session["user"], enabled=enabled
        )
    if "ai_assist_enabled" in data:
        enabled = bool(data["ai_assist_enabled"])
        set_setting("ai_assist_enabled", enabled)
        app_log(
            "INFO", "admin", "AI Assist toggled", by=session["user"], enabled=enabled
        )
    return jsonify(get_all_settings())
```

Also update the module docstring's endpoint summary (near line 31) from:
```
  GET    /admin/api/settings         {"external_api_enabled": bool}
  PUT    /admin/api/settings         {"external_api_enabled": bool}
```
to:
```
  GET    /admin/api/settings         {"external_api_enabled": bool, "ai_assist_enabled": bool}
  PUT    /admin/api/settings         {"external_api_enabled": bool, "ai_assist_enabled": bool}
```

- [ ] **Step 4: Run to verify it passes**

```bash
uv run pytest tests/test_admin_ai_assist_setting.py -v
```
Expected: both tests pass.

- [ ] **Step 5: Add the admin UI toggle**

In `app/templates/admin.html`, find the "External API" panel toggle block (around the `extApiEnabled` checkbox, `id="external-api"` panel per the tab list near the top of the file). Add an equivalent block for AI Assist — either as a new tab/panel (mirroring the `data-panel="external-api"` pattern) or, if a Rule Validation / AI-related admin panel doesn't exist yet, add it as a new admin tab. Use this markup for the toggle itself, adapting the surrounding panel wrapper to match whichever tab you place it under:

```html
  <div class="admin-panel-header">
    <div>
      <h3>AI Assist</h3>
      <p class="text-muted" style="font-size:.85rem;margin-top:.2rem">
        Enable AI-assisted analysis in the Rule Validation tab. Requires at least
        one LLM provider configured in <code>.env</code> (<code>AI_PROVIDER</code>
        plus that provider's API key).
      </p>
    </div>
  </div>

  <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.5rem;padding:.75rem 1rem;background:var(--surface-alt);border:1px solid var(--border);border-radius:6px">
    <label style="display:flex;align-items:center;gap:.6rem;cursor:pointer;font-weight:500">
      <input type="checkbox" id="aiAssistEnabled" style="width:1.1rem;height:1.1rem" />
      AI Assist enabled
    </label>
    <button class="btn btn-primary btn-sm" id="btnSaveAiAssistToggle">Save</button>
    <span id="aiAssistToggleMsg" style="font-size:.83rem"></span>
  </div>
```

- [ ] **Step 6: Wire the toggle in `app/static/js/admin.js`**

In the `loadExtApi()` function (or wherever `/admin/api/settings` is fetched on page load), add a line reading the new flag — right after the existing `document.getElementById('extApiEnabled').checked = !!settings.external_api_enabled;`:

```javascript
    document.getElementById('aiAssistEnabled').checked = !!settings.ai_assist_enabled;
```

Add a save handler mirroring the existing one, right after the `btnSaveExtApiToggle` click handler:

```javascript
  document.getElementById('btnSaveAiAssistToggle').addEventListener('click', async () => {
    const enabled = document.getElementById('aiAssistEnabled').checked;
    const msgEl = document.getElementById('aiAssistToggleMsg');
    const res = await fetch('/admin/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ai_assist_enabled: enabled }),
    });
    if (res.ok) {
      msgEl.textContent = enabled ? 'AI Assist enabled.' : 'AI Assist disabled.';
      msgEl.style.color = enabled ? 'var(--success)' : 'var(--warning)';
    } else {
      msgEl.textContent = 'Failed to save.';
      msgEl.style.color = 'var(--danger)';
    }
  });
```

- [ ] **Step 7: Full-suite check and commit the admin wiring**

```bash
uv run pytest -q
git add app/routes/admin_routes.py app/templates/admin.html app/static/js/admin.js \
        tests/test_admin_ai_assist_setting.py
git commit -m "Add admin toggle for AI Assist (ai_assist_enabled)"
```

- [ ] **Step 8: End-to-end manual verification with Ollama (no paid API key needed)**

If Ollama is available locally (`ollama --version`), this step verifies the whole path with zero cost. If not, skip straight to Step 9 and note in the final report that live-provider verification was not performed.

```bash
# Pull a small model if not already present
ollama pull llama3.1

# Configure 4THealth+ to use it
echo 'AI_PROVIDER=ollama' >> .env
echo 'OLLAMA_HOST=http://localhost:11434' >> .env
echo 'OLLAMA_MODEL=llama3.1' >> .env

uv run python -c "
import json
from app.app_settings import set_setting
set_setting('ai_assist_enabled', True)
print('ai_assist_enabled set to True')
"
```

Start the app, log in as admin, open Rule Validation, and in the AI Assist panel submit a request against any ADOM/device you have FortiManager access to (or, if no live FortiManager is reachable in this environment, confirm instead that the request fails with a clear `502` FortiManager error rather than a `500` crash — that still proves the route, planner wiring, and error handling are correct end to end). Confirm the deterministic verdict renders, and either the AI narrative renders or a clear "AI summary unavailable" message does.

- [ ] **Step 9: Full regression check**

```bash
uv run pytest -q
```
Expected: every test in the suite passes (the pre-phase-2 tests plus every test added across Tasks 1-11).

- [ ] **Step 10: Update README.md's roadmap section**

Open `README.md`, find the "## Roadmap" section added in Phase 1 (it currently says AI-assisted Rule Validation is "not yet implemented"). Replace it with:

```markdown
---

## Roadmap

4THealth+'s **Rule Validation** tab now includes an **AI Assist** mode: engineers
describe a single change request (source/destination/service/target firewalls)
and get a deterministic verdict — computed by a ported, tested change-planning
engine, not the LLM — plus an AI-written report and peer-review package.
Multi-provider support: Claude (default), Codex, and Ollama (local or cloud),
configured server-wide via `.env`. The existing bulk CSV/XLSX table workflow is
unchanged and does not use the LLM.

Deferred to a future phase: FortiManager read-only query tools, feedback/audit
history, `.xlsx` intake parsing, and per-request provider selection.
```

- [ ] **Step 11: Update CLAUDE.md**

Add a new subsection under the existing "### Rule Validation tab" section in `CLAUDE.md` (find it via `grep -n "### Rule Validation tab" CLAUDE.md`), documenting: the `app/planner/` package and its provenance (point to `app/planner/VENDORED_FROM.md`), the `app/llm/` provider abstraction and how `AI_PROVIDER` selects one, the `ai_assist_enabled` feature flag, and the new `POST /api/rule-review/ai-assist` endpoint — following the same documentation style/depth as the existing Device Review / Rule Validation sections in that file (read a neighboring section first to match tone and level of detail before writing).

- [ ] **Step 12: Update CHANGELOG.md**

Add a new entry above the existing `[Unreleased]` content (or start a new dated section if the phase 1 entry has already been released/tagged):

```markdown
### Added
- AI Assist mode in Rule Validation: single-request change analysis powered by
  a ported deterministic planner (`app/planner/`) plus multi-provider LLM
  narration (`app/llm/` — Claude default, Codex, Ollama). Admin-gated via a
  new `ai_assist_enabled` setting; existing bulk CSV/XLSX workflow unchanged.
```

- [ ] **Step 13: Final commit**

```bash
git status
git add README.md CLAUDE.md CHANGELOG.md
git commit -m "Update docs for phase 2: AI Assist mode in Rule Validation"
```

Expected: `git status` clean afterward, full test suite green, phase 2 complete.

---
