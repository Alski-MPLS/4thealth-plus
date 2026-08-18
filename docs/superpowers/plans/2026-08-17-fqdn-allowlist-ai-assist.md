# FQDN/Wildcard Allowlist AI Assist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second "FQDN Allowlist" mode to the existing Rule Validation AI Assist panel, ported from 4tAnalyst's new vendor-FQDN allowlist planner.

**Architecture:** Extend the existing `app/planner/` fork (matching, catalogs, models, cli_gen, engine) with the FQDN-specific classes/functions from 4tAnalyst, add a plain-Python intake parser (`app/planner/fqdn_intake.py`) for manual rows and `.xlsx` uploads, wire a new `POST /api/rule-review/ai-assist-fqdn` endpoint that mirrors the existing `ai-assist` route's structure, and add a mode-toggle UI to the existing AI Assist form/results.

**Tech Stack:** Flask, plain-dataclass Python (no new deps — `openpyxl` already present), Jinja2 templates, vanilla JS, pytest.

**Spec:** [docs/superpowers/specs/2026-08-17-fqdn-allowlist-ai-assist-design.md](../specs/2026-08-17-fqdn-allowlist-ai-assist-design.md)

## Global Constraints

- Port code **verbatim** from `~/code/github/ai/4tanalyst` wherever upstream logic doesn't depend on `fortimanager_mcp`/`intake_mcp`-specific plumbing — only import paths and data-access calls change (matches the existing Phase 2 adaptation pattern).
- Two `wildcard-fqdn` JSON field-name assumptions in `FQDNCatalog` are unverified against real FortiManager hardware — port with the upstream `# VERIFY` comments intact, do not silently "fix" them.
- `openpyxl` is already a dependency (`pyproject.toml`) — do not add a new package for `.xlsx` parsing.
- Every new backend function needs a test before being wired into the next layer (TDD) — follow the existing `tests/test_planner_*.py` style (plain pytest, no fixtures beyond what's shown below, `unittest.mock.MagicMock` for `FMGClient`).
- `naming.yaml` is gitignored — only `naming.example.yaml` is committed; both the tracked example and the local dev copy need the new patterns (tests only ever read `naming.example.yaml` via a `tmp_path` copy, per `tests/test_planner_standards.py`'s `naming_path` fixture).
- All new route code follows the existing `ai-assist` route's error-handling contract: `PlannerDataError` → 502, `FMGError` → 502 (`upstream_api_error`), anything else → 500 (`internal_api_error`); the deterministic plan always returns even if narration fails.
- Run `pytest tests/ -q` after each task that touches Python and confirm no existing test regresses.

---

### Task 1: `FQDNEntry`/`FQDNAllowlistRequest` + FQDN plan models

**Files:**
- Modify: `app/planner/models.py`
- Test: `tests/test_planner_models.py`

**Interfaces:**
- Produces: `FQDNEntry(fqdn, is_wildcard, ports, protocol, required, comment)`, `FQDNAllowlistRequest(vendor, category, src_ip, ticket_id, firewalls: list[str], entries: list[FQDNEntry], warnings=[], missing_fields=[])`, `FQDNAddressObject(name, obj_type, value, comment, cli="")`, `FQDNAddrGroup(name, members, comment, cli="")`, `FQDNFirewallPlan(firewall, adom, verdict, src_zone, coverage, covered_entries, uncovered_entries, proposed_objects, proposed_group, proposed_policy, group_append_alternative, degraded, warnings)`, `FQDNChangePlan(request: FQDNAllowlistRequest, per_firewall: list[FQDNFirewallPlan])` — all in `app/planner/models.py`, consumed by Tasks 3, 5, 8, 9.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_planner_models.py`:

```python
from app.planner.models import (
    FQDNAddressObject,
    FQDNAddrGroup,
    FQDNAllowlistRequest,
    FQDNChangePlan,
    FQDNEntry,
    FQDNFirewallPlan,
)


def test_fqdn_models_roundtrip():
    entry = FQDNEntry(
        fqdn="push.apple.com", is_wildcard=False, ports=[443, 5223],
        protocol="TCP", required=True, comment="APNs",
    )
    req = FQDNAllowlistRequest(
        vendor="Apple", category="APNs", src_ip="10.1.1.1", ticket_id="CHG1",
        firewalls=["FW-A:OT-ADOM"], entries=[entry],
    )
    obj = FQDNAddressObject(
        name="FQDN-push.apple.com", obj_type="fqdn", value="push.apple.com",
        comment="APNs - CHG1", cli="config firewall address\n...",
    )
    group = FQDNAddrGroup(name="GRP-Apple-APNs-DST", members=[obj.name],
                           comment="Apple APNs - CHG1", cli="config firewall addrgrp\n...")
    fw_plan = FQDNFirewallPlan(
        firewall="FW-A", adom="OT-ADOM", verdict="new_rule", src_zone="OT-LAN",
        coverage="new_rule", covered_entries=[], uncovered_entries=[entry],
        proposed_objects=[obj], proposed_group=group, proposed_policy={"name": "p"},
        group_append_alternative=None, degraded=False, warnings=[],
    )
    plan = FQDNChangePlan(request=req, per_firewall=[fw_plan])

    assert plan.per_firewall[0].proposed_group.name == "GRP-Apple-APNs-DST"
    assert plan.request.entries[0].fqdn == "push.apple.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_planner_models.py::test_fqdn_models_roundtrip -v`
Expected: FAIL with `ImportError: cannot import name 'FQDNEntry'`

- [ ] **Step 3: Add the models**

Append to `app/planner/models.py` (after the existing `ChangePlan` class):

```python
@dataclass
class FQDNEntry:
    """One vendor-supplied FQDN/wildcard-FQDN allowlist row."""

    fqdn: str
    is_wildcard: bool
    ports: list[int]
    protocol: str  # "TCP" | "UDP"
    required: bool
    comment: str


@dataclass
class FQDNAllowlistRequest:
    """A normalized vendor FQDN allowlist request — the FQDN-path
    equivalent of NormalizedFlow. firewalls are "DEVICE:ADOM" strings,
    matching plan_fqdn_change()'s parsing."""

    vendor: str
    category: str
    src_ip: str
    ticket_id: str
    firewalls: list[str]
    entries: list[FQDNEntry]
    warnings: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class FQDNAddressObject:
    name: str  # e.g. "FQDN-axm-adm-scep.apple.com" / "WFQDN-push.apple.com"
    obj_type: str  # "fqdn" | "wildcard-fqdn"
    value: str  # e.g. "*.push.apple.com"
    comment: str
    cli: str = ""


@dataclass
class FQDNAddrGroup:
    name: str  # "GRP-Apple-APNs-DST"
    members: list[str]  # object names
    comment: str
    cli: str = ""


@dataclass
class FQDNFirewallPlan:
    firewall: str
    adom: str
    verdict: str  # "blocked_exception" | "already_covered" | "new_rule" | "partial_coverage" | "unknown_no_action" | "error"
    src_zone: str
    coverage: str  # "already_covered" | "partial_coverage" | "new_rule" | "n/a"
    covered_entries: list[FQDNEntry]
    uncovered_entries: list[FQDNEntry]
    proposed_objects: list[FQDNAddressObject]
    proposed_group: FQDNAddrGroup | None
    proposed_policy: dict | None
    group_append_alternative: GroupAppendAlternative | None
    degraded: bool
    warnings: list[str]


@dataclass
class FQDNChangePlan:
    request: FQDNAllowlistRequest
    per_firewall: list[FQDNFirewallPlan]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_planner_models.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Commit**

```bash
git add app/planner/models.py tests/test_planner_models.py
git commit -m "feat: add FQDN allowlist planner data models"
```

---

### Task 2: `FQDNCatalog` in `matching.py`

**Files:**
- Modify: `app/planner/matching.py`
- Test: `tests/test_planner_matching.py`

**Interfaces:**
- Consumes: `_names(field) -> list[str]` (already in `matching.py`, module-level).
- Produces: `FQDNCatalog(objects: list[dict], groups: list[dict])` with `.fqdns_for_ref(name) -> set[str] | None`, `.exact_match_name(fqdn_str) -> str | None`, `.groups_containing_fqdn(fqdn_str) -> set[str]` — consumed by Task 3.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_planner_matching.py`:

```python
from app.planner.matching import FQDNCatalog


def test_fqdn_catalog_resolves_fqdn_and_wildcard_objects():
    objects = [
        {"name": "FQDN-a", "type": "fqdn", "fqdn": "api.vendor.com"},
        {"name": "WFQDN-a", "type": "wildcard-fqdn", "wildcard-fqdn": "*.push.apple.com"},
        {"name": "H_10.1.1.1", "type": "ipmask", "subnet": "10.1.1.1/32"},
    ]
    cat = FQDNCatalog(objects, groups=[])
    assert cat.fqdns_for_ref("FQDN-a") == {"api.vendor.com"}
    assert cat.fqdns_for_ref("WFQDN-a") == {"*.push.apple.com"}
    assert cat.fqdns_for_ref("H_10.1.1.1") == set()  # known, no FQDNs
    assert cat.fqdns_for_ref("unknown") is None


def test_fqdn_catalog_resolves_group_members():
    objects = [{"name": "FQDN-a", "type": "fqdn", "fqdn": "api.vendor.com"}]
    groups = [{"name": "GRP-DST", "member": ["FQDN-a"]}]
    cat = FQDNCatalog(objects, groups)
    assert cat.fqdns_for_ref("GRP-DST") == {"api.vendor.com"}
    assert cat.exact_match_name("api.vendor.com") == "FQDN-a"
    assert cat.groups_containing_fqdn("api.vendor.com") == {"GRP-DST"}
    assert cat.groups_containing_fqdn("nope.com") == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_planner_matching.py -k fqdn_catalog -v`
Expected: FAIL with `ImportError: cannot import name 'FQDNCatalog'`

- [ ] **Step 3: Add `FQDNCatalog`**

Append to `app/planner/matching.py` (after the `PolicyMatcher` class, end of file):

```python
class FQDNCatalog:
    """Resolves FortiManager address object/group refs to sets of FQDN strings.

    Parallel to AddressCatalog. IP-only objects return an empty set (known,
    but contribute no FQDNs). Unknown refs return None — same contract as
    AddressCatalog: callers must treat None as "cannot prove coverage".
    """

    def __init__(self, objects: list[dict], groups: list[dict]) -> None:
        self._objects: dict[str, dict] = {}
        self._groups: dict[str, dict] = {}
        for o in objects:
            if isinstance(o, dict) and "name" in o:
                self._objects[o["name"]] = o
        for g in groups:
            if isinstance(g, dict) and "name" in g:
                self._groups[g["name"]] = g

    def fqdns_for_ref(self, name: str) -> set[str] | None:
        """FQDN strings reachable from the named object or group, or None if unknown."""
        return self._resolve(name, seen=set())

    def exact_match_name(self, fqdn_str: str) -> str | None:
        """Name of an existing address object exactly matching this FQDN string."""
        for name, obj in self._objects.items():
            obj_type = str(obj.get("type", "")).lower()
            if obj_type == "fqdn" and obj.get("fqdn", "") == fqdn_str:
                return name
            # VERIFY: FortiManager JSON field for wildcard-fqdn type — expected "wildcard-fqdn"
            if obj_type == "wildcard-fqdn" and obj.get("wildcard-fqdn", "") == fqdn_str:
                return name
        return None

    def groups_containing_fqdn(self, fqdn_str: str) -> set[str]:
        """All groups that (transitively) contain an object matching fqdn_str."""
        obj_name = self.exact_match_name(fqdn_str)
        if obj_name is None:
            return set()
        parents: dict[str, set[str]] = {}
        for gname, g in self._groups.items():
            for m in _names(g.get("member", [])):
                parents.setdefault(m, set()).add(gname)
        result: set[str] = set()
        queue = [obj_name]
        while queue:
            for p in parents.get(queue.pop(), ()):
                if p not in result:
                    result.add(p)
                    queue.append(p)
        return result

    def _resolve(self, name: str, seen: set[str]) -> set[str] | None:
        if name in seen:
            return set()
        seen.add(name)

        obj = self._objects.get(name)
        if obj is not None:
            return self._fqdns_for_object(obj)

        group = self._groups.get(name)
        if group is not None:
            result: set[str] = set()
            any_known = False
            for m in group.get("member", []):
                member_name = m if isinstance(m, str) else m.get("name", "")
                sub = self._resolve(member_name, seen)
                if sub is not None:
                    any_known = True
                    result.update(sub)
            return result if any_known else None

        return None

    @staticmethod
    def _fqdns_for_object(obj: dict) -> set[str]:
        obj_type = str(obj.get("type", "")).lower()
        if obj_type == "fqdn":
            v = obj.get("fqdn", "")
            return {v} if v else set()
        # VERIFY: FortiManager JSON field name for wildcard-fqdn — expected "wildcard-fqdn"
        if obj_type == "wildcard-fqdn":
            v = obj.get("wildcard-fqdn", "")
            return {v} if v else set()
        # IP-type, geo, dynamic, mac — known but no FQDNs
        return set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_planner_matching.py -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 5: Commit**

```bash
git add app/planner/matching.py tests/test_planner_matching.py
git commit -m "feat: add FQDNCatalog for resolving fqdn/wildcard-fqdn address objects"
```

---

### Task 3: `build_fqdn_catalog` / `search_fqdn_rules` in `catalogs.py`

**Files:**
- Modify: `app/planner/catalogs.py`
- Test: `tests/test_planner_catalogs.py`

**Interfaces:**
- Consumes: `FQDNCatalog` (Task 2); `client.get_address_objects(adom)`, `client.get_address_groups(adom)`, `client.get_policy_packages(adom)`, `client.get_policies(adom, pkg_path)` (existing `FMGClient` methods); `package_targets_device(pkg, device)` (existing, same file); `_names` (`app.planner.matching`).
- Produces: `build_fqdn_catalog(client, adom: str) -> FQDNCatalog`; `search_fqdn_rules(client, adom: str, device: str, fqdns: list[str]) -> dict` shaped `{"results": [{"fqdn", "covered", "address_object_name", "via_group", "rule_id", "rule_name", "rule_enabled"}], "partial_group_match": {"group_name", "covered", "uncovered"} | None, "degraded": bool, "packages_searched": [...], "packages_failed": [...]}` — consumed by Task 8.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_planner_catalogs.py`:

```python
def test_build_fqdn_catalog_indexes_objects_and_groups():
    from app.planner.catalogs import build_fqdn_catalog

    client = MagicMock()
    client.get_address_objects.return_value = [
        {"name": "FQDN-a", "type": "fqdn", "fqdn": "api.vendor.com"}
    ]
    client.get_address_groups.return_value = [
        {"name": "GRP-DST", "member": ["FQDN-a"]}
    ]

    cat = build_fqdn_catalog(client, "OT-ADOM")
    assert cat.fqdns_for_ref("GRP-DST") == {"api.vendor.com"}


def test_search_fqdn_rules_reports_covered_and_uncovered():
    from app.planner.catalogs import search_fqdn_rules

    client = MagicMock()
    client.get_address_objects.return_value = [
        {"name": "FQDN-covered", "type": "fqdn", "fqdn": "covered.vendor.com"},
    ]
    client.get_address_groups.return_value = [
        {"name": "GRP-Vendor-DST", "member": ["FQDN-covered"]},
    ]
    client.get_policy_packages.return_value = [{"name": "pkg1", "path": "pkg1"}]
    client.get_policies.return_value = [
        {
            "policyid": 10, "name": "ALLOW-VENDOR", "status": "enable",
            "dstaddr": ["GRP-Vendor-DST"],
        }
    ]

    result = search_fqdn_rules(
        client, "OT-ADOM", "FW-A", ["covered.vendor.com", "uncovered.vendor.com"]
    )

    by_fqdn = {r["fqdn"]: r for r in result["results"]}
    assert by_fqdn["covered.vendor.com"]["covered"] is True
    assert by_fqdn["covered.vendor.com"]["rule_id"] == 10
    assert by_fqdn["covered.vendor.com"]["via_group"] == "GRP-Vendor-DST"
    assert by_fqdn["uncovered.vendor.com"]["covered"] is False
    assert result["degraded"] is False
    assert result["packages_searched"] == ["pkg1"]


def test_search_fqdn_rules_degrades_on_policy_fetch_failure():
    from app.planner.catalogs import search_fqdn_rules

    client = MagicMock()
    client.get_address_objects.return_value = []
    client.get_address_groups.return_value = []
    client.get_policy_packages.return_value = [{"name": "pkg1", "path": "pkg1"}]
    client.get_policies.side_effect = RuntimeError("timeout")

    result = search_fqdn_rules(client, "OT-ADOM", "FW-A", ["x.vendor.com"])
    assert result["degraded"] is True
    assert result["packages_failed"][0]["package"] == "pkg1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_planner_catalogs.py -k fqdn -v`
Expected: FAIL with `ImportError: cannot import name 'build_fqdn_catalog'`

- [ ] **Step 3: Add `build_fqdn_catalog` and `search_fqdn_rules`**

In `app/planner/catalogs.py`, change the import line at the top:

```python
from app.planner.matching import AddressCatalog, FQDNCatalog, ServiceCatalog, _names
```

Then append at the end of the file:

```python
def build_fqdn_catalog(client, adom: str) -> FQDNCatalog:
    """Fetch and index FQDN/wildcard-FQDN address objects and groups for an ADOM.

    Re-fetches the same objects as build_catalogs — no separate caching,
    since this runs once per AI Assist FQDN request.
    """
    objects = [o for o in client.get_address_objects(adom) if isinstance(o, dict)]
    groups = [g for g in client.get_address_groups(adom) if isinstance(g, dict)]
    return FQDNCatalog(objects, groups)


def search_fqdn_rules(
    client, adom: str, device: str, fqdns: list[str]
) -> dict[str, Any]:
    """Find FortiManager policies that cover any of the requested FQDNs.

    Resolves destination address objects/groups using FQDNCatalog (exact
    string match). Returns per-FQDN coverage status and a
    partial_group_match hint when some FQDNs are covered and others are
    not (candidate for group-append).
    """
    result: dict[str, Any] = {
        "results": [],
        "partial_group_match": None,
        "degraded": False,
        "packages_searched": [],
        "packages_failed": [],
    }

    try:
        fqdn_catalog = build_fqdn_catalog(client, adom)
        packages = client.get_policy_packages(adom)
    except Exception as exc:
        result["degraded"] = True
        result["error"] = str(exc)
        return result

    device_pkgs = [
        p for p in packages if isinstance(p, dict) and package_targets_device(p, device)
    ]

    coverage: dict[str, dict] = {
        f: {
            "fqdn": f, "covered": False, "address_object_name": None,
            "via_group": None, "rule_id": None, "rule_name": None,
            "rule_enabled": False,
        }
        for f in fqdns
    }

    for pkg in device_pkgs:
        pkg_name = pkg.get("path", pkg.get("name", ""))
        try:
            policies = [
                p for p in client.get_policies(adom, pkg_name) if isinstance(p, dict)
            ]
        except Exception as exc:
            result["packages_failed"].append({"package": pkg_name, "error": str(exc)})
            result["degraded"] = True
            continue

        result["packages_searched"].append(pkg_name)

        for pol in policies:
            pol_enabled = pol.get("status", "enable") != "disable"
            dst_names = _names(pol.get("dstaddr", []))

            pol_fqdns: set[str] = set()
            dst_group_for: dict[str, str] = {}  # fqdn_str → first containing group name

            for dst_name in dst_names:
                fqdn_set = fqdn_catalog.fqdns_for_ref(dst_name)
                if fqdn_set:
                    for f in fqdn_set:
                        pol_fqdns.add(f)
                        if fqdn_catalog._groups.get(dst_name) and f not in dst_group_for:
                            dst_group_for[f] = dst_name

            for fqdn_str in fqdns:
                if fqdn_str in pol_fqdns and not coverage[fqdn_str]["covered"]:
                    coverage[fqdn_str].update({
                        "covered": True,
                        "address_object_name": fqdn_catalog.exact_match_name(fqdn_str),
                        "via_group": dst_group_for.get(fqdn_str),
                        "rule_id": pol.get("policyid"),
                        "rule_name": pol.get("name", ""),
                        "rule_enabled": pol_enabled,
                    })

    result["results"] = list(coverage.values())

    covered_fqdns = [f for f in fqdns if coverage[f]["covered"]]
    uncovered_fqdns = [f for f in fqdns if not coverage[f]["covered"]]

    if covered_fqdns and uncovered_fqdns:
        candidate_groups: set[str] | None = None
        for cf in covered_fqdns:
            g = fqdn_catalog.groups_containing_fqdn(cf)
            candidate_groups = g if candidate_groups is None else candidate_groups & g
        if candidate_groups:
            result["partial_group_match"] = {
                "group_name": next(iter(sorted(candidate_groups))),
                "covered": covered_fqdns,
                "uncovered": uncovered_fqdns,
            }

    result["results"].sort(key=lambda r: r["fqdn"])
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_planner_catalogs.py -v`
Expected: PASS (all tests, including the three new ones)

- [ ] **Step 5: Commit**

```bash
git add app/planner/catalogs.py tests/test_planner_catalogs.py
git commit -m "feat: add FQDN coverage search against FortiManager policies"
```

---

### Task 4: FQDN CLI generation in `cli_gen.py`

**Files:**
- Modify: `app/planner/cli_gen.py`
- Test: `tests/test_planner_cli_gen.py`

**Interfaces:**
- Produces: `_safe_cli_str(s: str) -> str`; `fqdn_address_object_cli(name, fqdn_str, comment="") -> str`; `wildcard_fqdn_address_object_cli(name, wildcard_str, comment="") -> str`; `addrgrp_create_cli(name, members, warn_replace=False) -> str` (existing function, new optional param, default preserves current behavior) — consumed by Task 8.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_planner_cli_gen.py`:

```python
from app.planner.cli_gen import (
    addrgrp_create_cli,
    fqdn_address_object_cli,
    wildcard_fqdn_address_object_cli,
)


def test_fqdn_address_object_cli():
    cli = fqdn_address_object_cli("FQDN-api.vendor.com", "api.vendor.com", "Vendor API - CHG1")
    assert cli == (
        'config firewall address\n'
        '    edit "FQDN-api.vendor.com"\n'
        '        set type fqdn\n'
        '        set fqdn "api.vendor.com"\n'
        '        set comment "Vendor API - CHG1"\n'
        '    next\n'
        'end'
    )


def test_wildcard_fqdn_address_object_cli():
    cli = wildcard_fqdn_address_object_cli("WFQDN-push.apple.com", "*.push.apple.com")
    assert 'set type wildcard-fqdn' in cli
    assert 'set wildcard-fqdn "*.push.apple.com"' in cli
    assert 'set comment' not in cli  # no comment passed


def test_fqdn_address_object_cli_escapes_quotes_and_strips_newlines():
    cli = fqdn_address_object_cli("FQDN-x", 'evil"fqdn\ninjected', "")
    assert '"' not in cli.split('set fqdn "')[1].split('"')[0].replace("''", "")
    assert "\n" not in cli.split('set fqdn "')[1].split('"')[0]


def test_addrgrp_create_cli_warn_replace_prepends_warning():
    cli = addrgrp_create_cli("GRP-DST", ["A", "B"], warn_replace=True)
    assert cli.startswith("# WARNING: 'set member' replaces all existing members.")
    assert 'set member "A" "B"' in cli


def test_addrgrp_create_cli_default_no_warning():
    cli = addrgrp_create_cli("GRP-DST", ["A"])
    assert not cli.startswith("#")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_planner_cli_gen.py -k "fqdn or warn_replace" -v`
Expected: FAIL with `ImportError: cannot import name 'fqdn_address_object_cli'`

- [ ] **Step 3: Add the CLI generators**

In `app/planner/cli_gen.py`, insert after `_quote_list` (before `address_object_cli`):

```python
def _safe_cli_str(s: str) -> str:
    """Escape a string for embedding in a double-quoted FortiGate CLI field.

    Replaces `"` with `''` (the FortiGate-CLI escape for a literal quote
    inside a quoted string) and strips newlines/carriage-returns so an
    attacker-controlled value cannot inject additional CLI commands.
    """
    return s.replace('"', "''").replace("\n", "").replace("\r", "")
```

Replace the existing `addrgrp_create_cli` function with:

```python
def addrgrp_create_cli(name: str, members: list[str], warn_replace: bool = False) -> str:
    """CLI to create a new address group with the given members.

    When ``warn_replace`` is True a comment is prepended reminding the engineer
    that ``set member`` replaces all existing group members — use this whenever
    a group may already exist in FortiManager.
    """
    quoted = " ".join(f'"{m}"' for m in members)
    body = (
        'config firewall addrgrp\n'
        f'    edit "{name}"\n'
        f'        set member {quoted}\n'
        '        set comment "<TICKET_ID>"\n'
        '    next\n'
        'end'
    )
    if warn_replace:
        warning = (
            "# WARNING: 'set member' replaces all existing members. "
            "Verify existing members first.\n"
        )
        return warning + body
    return body
```

Then append at the end of the file:

```python
def fqdn_address_object_cli(name: str, fqdn_str: str, comment: str = "") -> str:
    """CLI block to create an address object of type fqdn."""
    safe_fqdn = _safe_cli_str(fqdn_str)
    lines = [
        "config firewall address",
        f'    edit "{name}"',
        "        set type fqdn",
        f'        set fqdn "{safe_fqdn}"',
    ]
    if comment:
        lines.append(f'        set comment "{_safe_cli_str(comment)}"')
    lines += ["    next", "end"]
    return "\n".join(lines)


def wildcard_fqdn_address_object_cli(name: str, wildcard_str: str, comment: str = "") -> str:
    """CLI block to create an address object of type wildcard-fqdn."""
    safe_wildcard = _safe_cli_str(wildcard_str)
    lines = [
        "config firewall address",
        f'    edit "{name}"',
        "        set type wildcard-fqdn",
        f'        set wildcard-fqdn "{safe_wildcard}"',
    ]
    if comment:
        lines.append(f'        set comment "{_safe_cli_str(comment)}"')
    lines += ["    next", "end"]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_planner_cli_gen.py -v`
Expected: PASS (all tests, including the five new ones)

- [ ] **Step 5: Commit**

```bash
git add app/planner/cli_gen.py tests/test_planner_cli_gen.py
git commit -m "feat: add FortiOS CLI generation for FQDN/wildcard-FQDN address objects"
```

---

### Task 5: FQDN intake parsing (`fqdn_intake.py`)

**Files:**
- Create: `app/planner/fqdn_intake.py`
- Test: `tests/test_planner_fqdn_intake.py`

**Interfaces:**
- Consumes: `FQDNEntry`, `FQDNAllowlistRequest` (Task 1, `app.planner.models`).
- Produces: `parse_fqdn_rows(rows: list[dict], src_ip="", ticket_id="", firewalls=None) -> FQDNAllowlistRequest`; `parse_fqdn_xlsx(file, src_ip="", ticket_id="", firewalls=None) -> FQDNAllowlistRequest` (`file` is a path string OR a file-like object — whatever `openpyxl.load_workbook()` accepts, matching `rr_parse_import`'s existing pattern of passing the Flask `FileStorage` directly) — consumed by Task 9.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planner_fqdn_intake.py`:

```python
"""Tests for app.planner.fqdn_intake — vendor FQDN allowlist row/xlsx parsing."""

import io

import openpyxl

from app.planner.fqdn_intake import parse_fqdn_rows, parse_fqdn_xlsx


def test_parse_fqdn_rows_basic():
    rows = [
        {
            "Hostname/Domain": "api.vendor.com", "Ports": "443, 5223",
            "Protocol": "TCP", "Vendor": "Vendor Co", "Category": "API",
            "Required?": "Yes", "Purpose/Notes": "Core API",
        },
        {
            "Hostname/Domain": "*.push.apple.com", "Ports": "5223",
            "Protocol": "TCP",
        },
    ]
    req = parse_fqdn_rows(rows, src_ip="10.1.1.1", ticket_id="CHG1", firewalls=["FW-A:OT-ADOM"])

    assert req.vendor == "Vendor Co"
    assert req.category == "API"
    assert req.src_ip == "10.1.1.1"
    assert req.firewalls == ["FW-A:OT-ADOM"]
    assert len(req.entries) == 2
    assert req.entries[0].fqdn == "api.vendor.com"
    assert req.entries[0].ports == [443, 5223]
    assert req.entries[0].required is True
    assert req.entries[1].is_wildcard is True
    assert not req.warnings


def test_parse_fqdn_rows_flags_illegal_characters_and_bad_ports():
    rows = [
        {"Hostname/Domain": 'evil"fqdn', "Ports": "443"},
        {"Hostname/Domain": "ok.vendor.com", "Ports": "not-a-port"},
    ]
    req = parse_fqdn_rows(rows)
    assert not req.entries
    assert any("illegal characters" in w for w in req.warnings)
    assert any("no valid ports" in w for w in req.warnings)


def test_parse_fqdn_rows_missing_src_ip_flagged():
    req = parse_fqdn_rows([{"Hostname/Domain": "x.vendor.com", "Ports": "443"}])
    assert "src_ip" in req.missing_fields


def test_parse_fqdn_xlsx_roundtrip():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Hostname/Domain", "Ports", "Protocol", "Vendor", "Category"])
    ws.append(["api.vendor.com", "443", "TCP", "Vendor Co", "API"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    req = parse_fqdn_xlsx(buf, src_ip="10.1.1.1", ticket_id="CHG1", firewalls=["FW-A:OT-ADOM"])
    assert req.vendor == "Vendor Co"
    assert req.entries[0].fqdn == "api.vendor.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_planner_fqdn_intake.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.planner.fqdn_intake'`

- [ ] **Step 3: Create the intake parser**

Create `app/planner/fqdn_intake.py`:

```python
"""
Vendor FQDN allowlist intake parsing.

Normalizes manually-entered rows or an uploaded .xlsx sheet into an
FQDNAllowlistRequest for app.planner.engine.plan_fqdn_change().

Adapted from ~/code/github/ai/4tanalyst's intake_mcp/fqdn_parser.py — see
VENDORED_FROM.md for the source commit. Adaptation: FQDNEntry/
FQDNAllowlistRequest live in app.planner.models (no separate intake_mcp
package here); parse_fqdn_xlsx accepts a file-like object (matching
app/routes/rule_review_routes.py's existing .xlsx upload pattern) rather
than a filesystem path.
"""

from __future__ import annotations

import openpyxl

from app.planner.models import FQDNAllowlistRequest, FQDNEntry

# Column name aliases: normalised → canonical
_COL_ALIASES: dict[str, str] = {
    "hostname / domain": "fqdn",
    "hostname/domain": "fqdn",
    "domain": "fqdn",
    "fqdn": "fqdn",
    "port(s)": "ports",
    "ports": "ports",
    "port": "ports",
    "protocol": "protocol",
    "direction": "direction",
    "vendor": "vendor",
    "category": "category",
    "required?": "required",
    "required": "required",
    "purpose / notes": "comment",
    "purpose/notes": "comment",
    "notes": "comment",
    "purpose": "comment",
}


def _normalise_col(name: str) -> str:
    return _COL_ALIASES.get(name.strip().lower(), "")


def _parse_ports(raw: str) -> tuple[list[int], list[str]]:
    """Parse "80, 443, 5223" → ([80, 443, 5223], warnings)."""
    ports = []
    warnings = []
    for tok in raw.replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            ports.append(int(tok))
        except ValueError:
            warnings.append(f"Non-numeric port {tok!r} skipped")
    return ports, warnings


def _parse_bool(raw: str) -> bool:
    return str(raw).strip().lower() in ("yes", "true", "required", "1")


def parse_fqdn_rows(
    rows: list[dict],
    src_ip: str = "",
    ticket_id: str = "",
    firewalls: list[str] | None = None,
) -> FQDNAllowlistRequest:
    """Normalise a list of row dicts into a FQDNAllowlistRequest."""
    firewalls = firewalls or []
    warnings: list[str] = []
    missing: list[str] = []
    entries: list[FQDNEntry] = []
    vendor = ""
    category = ""

    for i, row in enumerate(rows):
        norm = {_normalise_col(k): v for k, v in row.items() if _normalise_col(k)}

        if not vendor and norm.get("vendor"):
            vendor = str(norm["vendor"]).strip()
        if not category and norm.get("category"):
            category = str(norm["category"]).strip()

        fqdn_val = str(norm.get("fqdn", "")).strip()
        if not fqdn_val:
            warnings.append(f"Row {i + 1}: empty Hostname/Domain — skipped")
            continue
        if any(c in fqdn_val for c in ('"', "\n", "\r")):
            warnings.append(
                f"Row {i + 1}: FQDN {fqdn_val!r} contains illegal characters "
                '(", newline, or carriage-return) — skipped'
            )
            continue

        direction = str(norm.get("direction", "Outbound")).strip()
        if direction.lower() not in ("outbound", ""):
            warnings.append(
                f"Row {i + 1}: Direction={direction!r} — FQDNs are destination-only on FortiGate;"
                " only Outbound is supported. Review this entry before proceeding."
            )

        ports, port_warnings = _parse_ports(str(norm.get("ports", "443")))
        warnings.extend(f"Row {i + 1}: {w}" for w in port_warnings)
        if not ports:
            warnings.append(f"Row {i + 1}: no valid ports — skipped")
            continue

        protocol = str(norm.get("protocol", "TCP")).strip().upper()
        if protocol not in ("TCP", "UDP"):
            warnings.append(f"Row {i + 1}: unknown protocol {protocol!r}, defaulting to TCP")
            protocol = "TCP"

        entries.append(FQDNEntry(
            fqdn=fqdn_val,
            is_wildcard=fqdn_val.startswith("*."),
            ports=ports,
            protocol=protocol,
            required=_parse_bool(str(norm.get("required", "yes"))),
            comment=str(norm.get("comment", "")).strip(),
        ))

    if not src_ip:
        missing.append("src_ip")
    elif src_ip.strip().lower() in ("any", "all"):
        warnings.append(
            f"src_ip {src_ip!r} will be treated as FortiGate built-in 'all' address object — "
            "policy will allow traffic from any source zone. Consider restricting to a specific subnet."
        )
    else:
        import ipaddress

        try:
            ipaddress.ip_network(src_ip.strip(), strict=False)
        except ValueError:
            warnings.append(
                f"src_ip {src_ip!r} is not a valid IP/CIDR — treating as a FortiGate address "
                "object/group name. Zone verdict will be skipped; verify the object exists in FortiManager."
            )

    return FQDNAllowlistRequest(
        vendor=vendor,
        category=category,
        src_ip=src_ip,
        ticket_id=ticket_id,
        firewalls=firewalls,
        entries=entries,
        warnings=warnings,
        missing_fields=missing,
    )


def parse_fqdn_xlsx(
    file,
    src_ip: str = "",
    ticket_id: str = "",
    firewalls: list[str] | None = None,
) -> FQDNAllowlistRequest:
    """Parse a vendor URL allowlist from an .xlsx file (path or file-like object)."""
    wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = [str(c or "").strip() for c in next(rows_iter, [])]
    rows = [
        {header[i]: str(v or "").strip() for i, v in enumerate(row) if i < len(header)}
        for row in rows_iter
        if any(v is not None for v in row)
    ]
    wb.close()
    return parse_fqdn_rows(rows, src_ip=src_ip, ticket_id=ticket_id, firewalls=firewalls)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_planner_fqdn_intake.py -v`
Expected: PASS (all four tests)

- [ ] **Step 5: Commit**

```bash
git add app/planner/fqdn_intake.py tests/test_planner_fqdn_intake.py
git commit -m "feat: add vendor FQDN allowlist intake parsing (rows + xlsx)"
```

---

### Task 6: Naming conventions for FQDN objects

**Files:**
- Modify: `naming.example.yaml`
- Modify: `naming.yaml` (gitignored local copy — keep in sync so local dev/manual testing works; this edit will not appear in `git status`/the commit diff)

**Interfaces:** None (data file, read by `app.planner.standards.load_naming()` — already wired).

- [ ] **Step 1: Add the three new patterns**

In `naming.example.yaml`, inside `platforms.fortigate.conventions`, insert after the existing `address_group` entry and before `service`:

```yaml
      fqdn_address:
        pattern: "FQDN-<hostname>"
        examples:
          - "FQDN-axm-adm-scep.apple.com"
          - "FQDN-api.vendor.com"
        notes: >
          Used for address objects of type=fqdn (exact hostname match).
          Only valid in the destination field of a FortiGate policy.
          Maximum 79 characters — truncate if necessary.

      wildcard_fqdn_address:
        pattern: "WFQDN-<domain-without-asterisk-dot>"
        examples:
          - "WFQDN-push.apple.com"
          - "WFQDN-cdn.vendor.com"
        notes: >
          Used for address objects of type=wildcard-fqdn (*.domain match).
          Strip the leading "*." before applying the pattern.
          Only valid in the destination field of a FortiGate policy.
          Maximum 79 characters — truncate if necessary.

      fqdn_destination_group:
        pattern: "GRP-<Vendor>-<Category>-DST"
        examples:
          - "GRP-Apple-APNs-DST"
          - "GRP-Microsoft-O365-DST"
        notes: >
          Destination address group for vendor FQDN allowlists.
          Spaces in vendor/category names are replaced with hyphens.
          Maximum 79 characters — truncate if necessary.
          Verify this pattern against your team's actual standards before use.
```

Apply the identical insertion to `naming.yaml` (the local gitignored copy) so `plan_fqdn_change()` renders naming guidance correctly in local manual testing.

- [ ] **Step 2: Verify the YAML parses**

Run: `python3 -c "import yaml; d = yaml.safe_load(open('naming.example.yaml')); print('fqdn_address' in d['platforms']['fortigate']['conventions'])"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add naming.example.yaml
git commit -m "docs: add FQDN/wildcard-FQDN address and group naming conventions"
```

(`naming.yaml` is gitignored and will not be staged — that's expected.)

---

### Task 7: Reject non-IP src/dst in `plan_change()`

**Files:**
- Modify: `app/planner/engine.py`
- Test: `tests/test_planner_engine.py`

**Interfaces:** No new interface — tightens an existing one. `plan_change()` now raises `PlannerDataError("request", ...)` for any non-IP/CIDR token in `src`/`dst` before doing any FortiManager or zone work.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_planner_engine.py`:

```python
def test_plan_change_rejects_non_ip_src():
    from app.planner.engine import plan_change
    from app.planner.models import PlannerDataError, TargetFirewall

    with pytest.raises(PlannerDataError, match="plan_fqdn_change"):
        plan_change(
            src="not-an-ip.example.com", dst="10.0.0.6", service="tcp/443",
            firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        )


def test_plan_change_rejects_non_ip_dst():
    from app.planner.engine import plan_change
    from app.planner.models import PlannerDataError, TargetFirewall

    with pytest.raises(PlannerDataError, match="plan_fqdn_change"):
        plan_change(
            src="10.0.0.5", dst="*.vendor.com", service="tcp/443",
            firewalls=[TargetFirewall(device="FW-A", adom="OT-ADOM")],
        )
```

Confirm `pytest` is already imported at the top of `tests/test_planner_engine.py` (it is, per the existing `pytest.fixture`/`pytest.raises` usage in that file); if not, add `import pytest`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_planner_engine.py -k rejects_non_ip -v`
Expected: FAIL — no exception is raised (or a different one is), since the guard doesn't exist yet.

- [ ] **Step 3: Add the guard**

In `app/planner/engine.py`, in `plan_change()`, immediately after:

```python
    srcs = _norm_list(src, "src")
    dsts = _norm_list(dst, "dst")
    services = _norm_list(service, "service")
```

insert:

```python
    for _v in srcs + dsts:
        try:
            ipaddress.ip_network(_v, strict=False)
        except ValueError:
            raise PlannerDataError(
                "request",
                f"{_v!r} is not a valid IP/CIDR. Use plan_fqdn_change() for FQDN-based requests.",
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_planner_engine.py -v`
Expected: PASS (all tests in the file, including the two new ones — the existing tests all use valid IPs so none regress)

- [ ] **Step 5: Commit**

```bash
git add app/planner/engine.py tests/test_planner_engine.py
git commit -m "feat: reject non-IP src/dst in plan_change, point callers at plan_fqdn_change"
```

---

### Task 8: `plan_fqdn_change()` engine entry point

**Files:**
- Modify: `app/planner/engine.py`
- Test: `tests/test_planner_engine.py`

**Interfaces:**
- Consumes: `search_fqdn_rules` (Task 3, `app.planner.catalogs`); `FQDNAddressObject`, `FQDNAddrGroup`, `FQDNAllowlistRequest`, `FQDNChangePlan`, `FQDNEntry`, `FQDNFirewallPlan` (Task 1, `app.planner.models`); `fqdn_address_object_cli`, `wildcard_fqdn_address_object_cli`, `addrgrp_create_cli(..., warn_replace=True)` (Task 4, `app.planner.cli_gen`); `resolve_interfaces` (existing, `app.planner.fetch`).
- Produces: `plan_fqdn_change(request: FQDNAllowlistRequest, fmg_client=None, zone_client=None) -> FQDNChangePlan`; `to_fqdn_report_payload(plan: FQDNChangePlan) -> dict` — consumed by Task 9.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_planner_engine.py`:

```python
def _fqdn_entry(fqdn="new.vendor.com", ports=(443,), protocol="TCP"):
    from app.planner.models import FQDNEntry
    return FQDNEntry(
        fqdn=fqdn, is_wildcard=fqdn.startswith("*."), ports=list(ports),
        protocol=protocol, required=True, comment="",
    )


def test_plan_fqdn_change_proposes_objects_for_uncovered_entries():
    from app.planner.engine import plan_fqdn_change
    from app.planner.models import FQDNAllowlistRequest

    req = FQDNAllowlistRequest(
        vendor="Vendor Co", category="API", src_ip="10.0.0.5",
        ticket_id="CHG1", firewalls=["FW-A:OT-ADOM"],
        entries=[_fqdn_entry()],
    )

    fake_fmg = MagicMock()
    fake_fmg.get_devices.return_value = [{"name": "FW-A"}]
    fake_fmg.get_policy_packages.return_value = [{"name": "pkg1", "path": "pkg1"}]
    fake_fmg.get_policies.return_value = []
    fake_fmg.get_address_objects.return_value = []
    fake_fmg.get_address_groups.return_value = []
    fake_fmg.get_service_objects.return_value = []
    fake_fmg.get_service_groups.return_value = []
    fake_fmg.get_device_interfaces.return_value = []
    fake_fmg.get_device_routes.return_value = []

    fake_zc = MagicMock()
    fake_zc.query.return_value = [{
        "verdict": "ALLOWED", "src_zones": ["OT-LAN"], "dst_zones": ["Internet"],
        "governing": [], "all_policies": [],
    }]
    fake_zc.zones.return_value = {"zones": [], "total_subnets": 0}

    plan = plan_fqdn_change(req, fmg_client=fake_fmg, zone_client=fake_zc)

    assert len(plan.per_firewall) == 1
    fw = plan.per_firewall[0]
    assert fw.firewall == "FW-A"
    assert fw.adom == "OT-ADOM"
    assert fw.coverage == "new_rule"
    assert len(fw.proposed_objects) == 1
    assert fw.proposed_objects[0].name == "FQDN-new.vendor.com"
    assert fw.proposed_group.name == "GRP-Vendor-Co-API-DST"
    assert "SVC_TCP_443" in fw.proposed_policy["service"]


def test_plan_fqdn_change_invalid_firewall_spec_yields_error_plan():
    from app.planner.engine import plan_fqdn_change
    from app.planner.models import FQDNAllowlistRequest

    req = FQDNAllowlistRequest(
        vendor="V", category="C", src_ip="any", ticket_id="CHG1",
        firewalls=["not-a-valid-spec"], entries=[_fqdn_entry()],
    )
    plan = plan_fqdn_change(req, fmg_client=MagicMock(), zone_client=MagicMock())
    assert plan.per_firewall[0].verdict == "error"
    assert plan.per_firewall[0].degraded is True


def test_to_fqdn_report_payload_shape():
    from app.planner.engine import to_fqdn_report_payload
    from app.planner.models import FQDNChangePlan, FQDNFirewallPlan, FQDNAllowlistRequest

    req = FQDNAllowlistRequest(
        vendor="V", category="C", src_ip="10.0.0.5", ticket_id="CHG1",
        firewalls=["FW-A:OT-ADOM"], entries=[_fqdn_entry()],
    )
    fw_plan = FQDNFirewallPlan(
        firewall="FW-A", adom="OT-ADOM", verdict="new_rule", src_zone="OT-LAN",
        coverage="new_rule", covered_entries=[], uncovered_entries=[_fqdn_entry()],
        proposed_objects=[], proposed_group=None, proposed_policy=None,
        group_append_alternative=None, degraded=False, warnings=["w1"],
    )
    plan = FQDNChangePlan(request=req, per_firewall=[fw_plan])

    payload = to_fqdn_report_payload(plan)
    assert payload["plan_type"] == "fqdn_allowlist"
    assert payload["vendor"] == "V"
    assert payload["per_firewall"][0]["firewall"] == "FW-A"
    assert payload["warnings"] == ["w1"]
```

Add `from unittest.mock import MagicMock` at the top of `tests/test_planner_engine.py` if not already imported (check first — the existing file may already import it for other tests).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_planner_engine.py -k fqdn -v`
Expected: FAIL with `ImportError: cannot import name 'plan_fqdn_change'`

- [ ] **Step 3: Add the FQDN planner functions**

In `app/planner/engine.py`, update the `app.planner.fetch` import to include `resolve_interfaces`:

```python
from app.planner.fetch import (
    DeviceSnapshot,
    fetch_device_snapshot,
    fetch_zone_domains,
    fetch_zone_verdict,
    resolve_interfaces,
)
```

Update the `app.planner.models` import block to add the FQDN models:

```python
from app.planner.models import (
    ChangePlan,
    FirewallPlan,
    FQDNAddressObject,
    FQDNAddrGroup,
    FQDNAllowlistRequest,
    FQDNChangePlan,
    FQDNFirewallPlan,
    GroupAppendAlternative,
    InsertionPlan,
    NormalizedFlow,
    ObjectPlan,
    PlannerDataError,
    TargetFirewall,
)
```

After the `GROUP_THRESHOLD = 3` line (and before `def _side_plan`), insert:

```python
# ---------------------------------------------------------------------------
# FQDN allowlist planner constants and name helpers
# ---------------------------------------------------------------------------

_FQDN_NAME_MAX = 79
_INTERNET_SENTINEL = "8.8.8.8"  # well-known public IP that resolves to Internet zone


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_network(value.strip(), strict=False)
        return True
    except ValueError:
        return False


def _fqdn_object_name(fqdn_str: str) -> tuple[str, str]:
    """Return (obj_type, truncated_name) for an FQDN or wildcard-FQDN string."""
    if fqdn_str.startswith("*."):
        name = f"WFQDN-{fqdn_str[2:]}"
        obj_type = "wildcard-fqdn"
    else:
        name = f"FQDN-{fqdn_str}"
        obj_type = "fqdn"
    if len(name) > _FQDN_NAME_MAX:
        name = name[:_FQDN_NAME_MAX - 3] + "..."
    return obj_type, name


def _fqdn_group_name(vendor: str, category: str) -> str:
    """Return GRP-<Vendor>-<Category>-DST with spaces as hyphens, ≤79 chars."""
    v = vendor.replace(" ", "-")
    c = category.replace(" ", "-")
    name = f"GRP-{v}-{c}-DST"
    if len(name) > _FQDN_NAME_MAX:
        name = name[:_FQDN_NAME_MAX - 3] + "..."
    return name
```

At the very end of `app/planner/engine.py`, append:

```python
# ---------------------------------------------------------------------------
# FQDN allowlist planner
# ---------------------------------------------------------------------------


def _plan_fqdn_firewall(
    target: TargetFirewall,
    request: FQDNAllowlistRequest,
    zone_result: dict,
    fmg_client,
) -> FQDNFirewallPlan:
    from app.planner.catalogs import search_fqdn_rules

    src_zones = zone_result.get("src_zones", [])
    src_zone = src_zones[0] if src_zones else "Unknown"
    raw_verdict = zone_result.get("verdict", "UNKNOWN")

    if raw_verdict == "UNKNOWN":
        fw_verdict = "unknown_no_action"
    elif raw_verdict == "BLOCKED":
        fw_verdict = "blocked_exception"
    else:
        fw_verdict = "new_rule"

    fw = FQDNFirewallPlan(
        firewall=target.device, adom=target.adom,
        verdict=fw_verdict, src_zone=src_zone,
        coverage="n/a",
        covered_entries=[], uncovered_entries=list(request.entries),
        proposed_objects=[], proposed_group=None,
        proposed_policy=None, group_append_alternative=None,
        degraded=False, warnings=list(zone_result.get("notes", [])),
    )

    if fw_verdict == "unknown_no_action":
        fw.warnings.append("Zone verdict UNKNOWN — no FQDN rule analysis performed")
        return fw

    try:
        snapshot = fetch_device_snapshot(fmg_client, target.adom, target.device)
    except PlannerDataError as exc:
        fw.degraded = True
        fw.verdict = "error"
        fw.warnings.append(str(exc))
        return fw

    if snapshot.degraded:
        fw.degraded = True
        fw.warnings.append(
            f"FortiManager data for {target.device} is incomplete "
            f"({'; '.join(snapshot.failures)}) — 'no existing rule' is NOT conclusive."
        )

    fqdn_strings = [e.fqdn for e in request.entries]
    cov = search_fqdn_rules(fmg_client, target.adom, target.device, fqdn_strings)

    if cov.get("degraded") and not fw.degraded:
        fw.degraded = True
        fw.warnings.append("FQDN rule search degraded — results may be incomplete")

    covered_set = {
        r["fqdn"] for r in cov["results"]
        if r["covered"] and r.get("rule_enabled", True)
    }
    fw.covered_entries = [e for e in request.entries if e.fqdn in covered_set]
    fw.uncovered_entries = [e for e in request.entries if e.fqdn not in covered_set]

    if not fw.degraded and not fw.uncovered_entries:
        if fw.verdict != "blocked_exception":
            fw.verdict = "already_covered"
        fw.coverage = "already_covered"
        return fw

    fw.coverage = "partial_coverage" if fw.covered_entries else "new_rule"
    if fw.verdict != "blocked_exception":
        fw.verdict = fw.coverage  # "new_rule" or "partial_coverage"

    # Resolve interfaces (src IP is an IP; use internet sentinel for dst)
    srcintf, dstintf, iface_warnings = resolve_interfaces(
        snapshot, request.src_ip, _INTERNET_SENTINEL
    )
    fw.warnings.extend(iface_warnings)

    # Build proposed FQDN address objects
    obj_warnings: list[str] = []
    proposed_objects: list[FQDNAddressObject] = []
    seen_names: set[str] = set()
    for entry in fw.uncovered_entries:
        obj_type, name = _fqdn_object_name(entry.fqdn)
        if name.endswith("..."):
            obj_warnings.append(
                f"Object name for {entry.fqdn!r} truncated to 79 chars: {name!r}"
            )
        # Collision detection: two FQDNs with the same 76-char prefix generate
        # identical truncated names; disambiguate by appending -2, -3, etc.
        if name in seen_names:
            suffix_n = 2
            base = name[:-3] if name.endswith("...") else name
            while True:
                suffix = f"-{suffix_n}"
                candidate = base[: _FQDN_NAME_MAX - len(suffix)] + suffix
                if candidate not in seen_names:
                    name = candidate
                    break
                suffix_n += 1
            obj_warnings.append(
                f"Object name collision for {entry.fqdn!r}: renamed to {name!r}"
            )
        seen_names.add(name)
        comment_str = f"{entry.comment or (request.vendor + ' ' + request.category)} - <TICKET_ID>"
        cli_fn = (cli_gen.fqdn_address_object_cli if obj_type == "fqdn"
                  else cli_gen.wildcard_fqdn_address_object_cli)
        proposed_objects.append(FQDNAddressObject(
            name=name, obj_type=obj_type, value=entry.fqdn,
            comment=comment_str, cli=cli_fn(name, entry.fqdn, comment_str),
        ))
    fw.proposed_objects = proposed_objects
    fw.warnings.extend(obj_warnings)

    # Build address group
    group_name = _fqdn_group_name(request.vendor, request.category)
    existing_obj_names = [
        r["address_object_name"] for r in cov["results"]
        if r["covered"] and r["address_object_name"]
    ]
    all_member_names = existing_obj_names + [o.name for o in proposed_objects]
    group_comment = f"{request.vendor} {request.category} - <TICKET_ID>"
    fw.proposed_group = FQDNAddrGroup(
        name=group_name, members=all_member_names, comment=group_comment,
        cli=cli_gen.addrgrp_create_cli(group_name, all_member_names, warn_replace=True),
    )

    # GroupAppendAlternative (Option B) for partial coverage
    partial = cov.get("partial_group_match")
    if partial and fw.coverage == "partial_coverage":
        new_names = [o.name for o in proposed_objects]
        fw.group_append_alternative = GroupAppendAlternative(
            package=snapshot.packages[0] if snapshot.packages else "",
            policy_id=0,
            policy_name="",
            side="destination",
            group=partial["group_name"],
            members=[],
            group_cli=cli_gen.addrgrp_append_cli(partial["group_name"], new_names),
        )

    # Source address object — 'all'/'any' maps to FortiGate built-in; named objects reused as-is
    if request.src_ip.strip().lower() in ("any", "all", ""):
        src_obj = ObjectPlan(role="source", action="reuse", name="all",
                             obj_type="builtin", value="all")
    elif not _is_valid_ip(request.src_ip):
        src_obj = ObjectPlan(role="source", action="reuse", name=request.src_ip,
                             obj_type="named_object", value=request.src_ip)
    else:
        src_obj = _address_object_plan("source", request.src_ip, snapshot)

    # Service objects — one per unique (protocol, port) pair across uncovered entries
    seen_svc: set[tuple[str, int]] = set()
    svc_names: list[str] = []
    svc_cli_blocks: list[str] = []
    for entry in fw.uncovered_entries:
        for port in entry.ports:
            key = (entry.protocol.lower(), port)
            if key not in seen_svc:
                seen_svc.add(key)
                svc_name = f"SVC_{entry.protocol.upper()}_{port}"
                svc_names.append(svc_name)
                svc_cli_blocks.append(
                    cli_gen.service_object_cli(svc_name, entry.protocol.lower(), str(port))
                )
    if not svc_names:
        svc_names = ["ALL"]

    # Policy
    log_cfg = standards.log_settings("allow_internet_outbound")
    policy_name_str = standards.policy_name(
        request.ticket_id, srcintf or "any", dstintf or "any"
    )
    pol_cli = cli_gen.policy_cli(
        name=policy_name_str,
        srcintf=srcintf or "any",
        dstintf=dstintf or "any",
        srcaddr=[src_obj.name],
        dstaddr=[group_name],
        service=svc_names,
        logtraffic="all" if log_cfg.get("log_end", True) else "disable",
        logtraffic_start=bool(log_cfg.get("log_start", False)),
        comments=f"FQDN allowlist {request.vendor} {request.category} <TICKET_ID>",
        insert_before=None,
    )
    fw.proposed_policy = {
        "name": policy_name_str,
        "package": snapshot.packages[0] if snapshot.packages else "",
        "srcintf": srcintf or "any",
        "dstintf": dstintf or "any",
        "srcaddr": [src_obj.name],
        "dstaddr": [group_name],
        "service": svc_names,
        "src_object_cli": src_obj.cli,
        "service_object_cli_blocks": svc_cli_blocks,
        "cli": pol_cli,
    }
    return fw


def plan_fqdn_change(
    request: FQDNAllowlistRequest,
    fmg_client=None,
    zone_client=None,
) -> FQDNChangePlan:
    """Compute a deterministic FQDN allowlist change plan.

    Resolves zone verdict from src_ip only (destination is Internet by design).
    Searches existing FortiManager rules for FQDN coverage. Proposes
    fqdn/wildcard-fqdn address objects, one destination group, and a policy
    per firewall for any uncovered entries.
    """
    fmc = fmg_client or _default_fmg_client()

    _src_lower = request.src_ip.strip().lower()
    _src_is_named = _src_lower in ("any", "all") or not _is_valid_ip(request.src_ip)

    if _src_is_named:
        _src_zone_label = "any" if _src_lower in ("any", "all") else request.src_ip
        zone_result: dict = {"verdict": "ALLOWED", "src_zones": [_src_zone_label], "notes": []}
    else:
        zc = zone_client or _default_zone_client()
        try:
            zone_result = fetch_zone_verdict(zc, request.src_ip, _INTERNET_SENTINEL, "tcp/443")
        except PlannerDataError as exc:
            zone_warn = f"Zone client unavailable: {exc}"
            fw_plans_degraded: list[FQDNFirewallPlan] = []
            for raw in request.firewalls:
                device, sep, adom = raw.partition(":")
                fw_plans_degraded.append(FQDNFirewallPlan(
                    firewall=device if sep else raw,
                    adom=adom if sep else "",
                    verdict="unknown_no_action", src_zone="Unknown",
                    coverage="n/a", covered_entries=[],
                    uncovered_entries=list(request.entries),
                    proposed_objects=[], proposed_group=None, proposed_policy=None,
                    group_append_alternative=None, degraded=True,
                    warnings=[zone_warn],
                ))
            return FQDNChangePlan(request=request, per_firewall=fw_plans_degraded)

    perm_warnings = standards.permissiveness_warnings(
        [request.src_ip],
        [e.fqdn for e in request.entries],
        [],
    )

    fw_plans: list[FQDNFirewallPlan] = []
    for raw in request.firewalls:
        device, sep, adom = raw.partition(":")
        if not sep or not device or not adom:
            fw_plans.append(FQDNFirewallPlan(
                firewall=raw, adom="", verdict="error", src_zone="Unknown",
                coverage="n/a", covered_entries=[], uncovered_entries=list(request.entries),
                proposed_objects=[], proposed_group=None, proposed_policy=None,
                group_append_alternative=None, degraded=True,
                warnings=[f"Invalid firewall spec {raw!r} — expected DEVICE:ADOM"],
            ))
            continue
        target = TargetFirewall(device=device, adom=adom)
        fw = _plan_fqdn_firewall(target, request, zone_result, fmc)
        fw.warnings = list(perm_warnings) + fw.warnings
        fw_plans.append(fw)

    return FQDNChangePlan(request=request, per_firewall=fw_plans)


def to_fqdn_report_payload(plan: FQDNChangePlan) -> dict:
    """Emit a report-ready dict from a FQDNChangePlan."""
    import dataclasses

    req = plan.request
    fw_list = []
    for fw in plan.per_firewall:
        fw_dict: dict = {
            "firewall": fw.firewall,
            "adom": fw.adom,
            "verdict": fw.verdict,
            "src_zone": fw.src_zone,
            "coverage": fw.coverage,
            "degraded": fw.degraded,
            "warnings": fw.warnings,
            "covered_entries": [dataclasses.asdict(e) for e in fw.covered_entries],
            "uncovered_entries": [dataclasses.asdict(e) for e in fw.uncovered_entries],
            "proposed_objects": [dataclasses.asdict(o) for o in fw.proposed_objects],
            "proposed_group": dataclasses.asdict(fw.proposed_group) if fw.proposed_group else None,
            "proposed_policy": fw.proposed_policy,
            "group_append_alternative": (
                dataclasses.asdict(fw.group_append_alternative)
                if fw.group_append_alternative else None
            ),
        }
        fw_list.append(fw_dict)

    return {
        "plan_type": "fqdn_allowlist",
        "vendor": req.vendor,
        "category": req.category,
        "src_ip": req.src_ip,
        "ticket_id": req.ticket_id,
        "per_firewall": fw_list,
        "warnings": [w for fw in plan.per_firewall for w in fw.warnings],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_planner_engine.py -v`
Expected: PASS (all tests in the file, including the four new FQDN ones — this requires `naming.yaml`/`naming.example.yaml` to already have `log_settings.allow_internet_outbound`, which already exists in both files today; only the `fqdn_address`/`wildcard_fqdn_address`/`fqdn_destination_group` conventions were added in Task 6)

- [ ] **Step 5: Commit**

```bash
git add app/planner/engine.py tests/test_planner_engine.py
git commit -m "feat: add plan_fqdn_change() FQDN allowlist planning engine"
```

---

### Task 9: `POST /api/rule-review/ai-assist-fqdn` route

**Files:**
- Modify: `app/routes/rule_review_routes.py`
- Test: `tests/test_rule_review_ai_assist_fqdn.py`

**Interfaces:**
- Consumes: `plan_fqdn_change`, `to_fqdn_report_payload` (Task 8, `app.planner.engine`); `parse_fqdn_xlsx` (Task 5, `app.planner.fqdn_intake`); `FQDNAllowlistRequest`, `FQDNEntry`, `PlannerDataError` (`app.planner.models`); `get_setting` (`app.app_settings`); `check_adom_access` (`app.decorators`); `get_provider` (`app.llm`); `make_client` (`app.fmg_helpers`).
- Produces: `POST /api/rule-review/ai-assist-fqdn` — JSON body `{vendor, category, src_ip, ticket_id, firewalls: [{device, adom}], entries: [{fqdn, ports: [int], protocol, required, comment}]}` OR `multipart/form-data` with the same scalar fields plus a `file` field (`.xlsx`). Returns `{plan, narrative, narrative_error}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rule_review_ai_assist_fqdn.py`:

```python
"""Tests for POST /api/rule-review/ai-assist-fqdn."""
import io
import json
import os
import time
from unittest.mock import MagicMock, patch

import openpyxl
import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-ci")
os.environ.setdefault("FMG_PRIMARY_HOST", "127.0.0.1")


@pytest.fixture
def app():
    from app import create_app
    return create_app()


_TEST_USERS = {"admin": {"password_hash": "$2b$12$placeholder", "role": "admin"}}


@pytest.fixture
def client(app):
    with app.test_client() as c, \
         patch("app.auth._load_users", return_value=_TEST_USERS):
        with c.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["_csrf_token"] = "test-csrf"
            sess["login_at"] = int(time.time())
        yield c


def _post_json(client, payload):
    return client.post(
        "/api/rule-review/ai-assist-fqdn",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"X-CSRF-Token": "test-csrf"},
    )


def test_ai_assist_fqdn_disabled_by_default_returns_503(client):
    with patch("app.app_settings.get_setting", return_value=False):
        resp = _post_json(client, {
            "vendor": "V", "category": "C", "src_ip": "10.0.0.5", "ticket_id": "CHG1",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
            "entries": [{"fqdn": "x.vendor.com", "ports": [443], "protocol": "TCP",
                         "required": True, "comment": ""}],
        })
    assert resp.status_code == 503


def test_ai_assist_fqdn_missing_entries_returns_400(client):
    with patch("app.app_settings.get_setting", return_value=True):
        resp = _post_json(client, {
            "vendor": "V", "category": "C", "src_ip": "10.0.0.5",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
            "entries": [],
        })
    assert resp.status_code == 400


def test_ai_assist_fqdn_json_success_returns_plan_and_narrative(client):
    fake_plan = MagicMock()

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_fqdn_change", return_value=fake_plan) as mock_plan, \
         patch("app.planner.engine.to_fqdn_report_payload",
               return_value={"plan_type": "fqdn_allowlist", "vendor": "V"}), \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = MagicMock()
        mock_get_provider.return_value.narrate.return_value = "Narrative text."

        resp = _post_json(client, {
            "vendor": "V", "category": "C", "src_ip": "10.0.0.5", "ticket_id": "CHG1",
            "firewalls": [{"device": "FW-A", "adom": "OT-ADOM"}],
            "entries": [{"fqdn": "x.vendor.com", "ports": [443], "protocol": "TCP",
                         "required": True, "comment": ""}],
        })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["plan"]["vendor"] == "V"
    assert data["narrative"] == "Narrative text."
    assert data["narrative_error"] is None
    mock_plan.assert_called_once()


def test_ai_assist_fqdn_xlsx_upload_success(client):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Hostname/Domain", "Ports", "Protocol", "Vendor", "Category"])
    ws.append(["x.vendor.com", "443", "TCP", "V", "C"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    fake_plan = MagicMock()

    with patch("app.app_settings.get_setting", return_value=True), \
         patch("app.decorators.check_adom_access", return_value=None), \
         patch("app.routes.rule_review_routes.make_client") as mock_make_client, \
         patch("app.planner.engine.plan_fqdn_change", return_value=fake_plan) as mock_plan, \
         patch("app.planner.engine.to_fqdn_report_payload",
               return_value={"plan_type": "fqdn_allowlist", "vendor": "V"}), \
         patch("app.llm.get_provider") as mock_get_provider:
        mock_make_client.return_value.__enter__.return_value = MagicMock()
        mock_get_provider.return_value.narrate.return_value = "Narrative text."

        resp = client.post(
            "/api/rule-review/ai-assist-fqdn",
            data={
                "src_ip": "10.0.0.5", "ticket_id": "CHG1",
                "firewalls": json.dumps([{"device": "FW-A", "adom": "OT-ADOM"}]),
                "file": (buf, "allowlist.xlsx"),
            },
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": "test-csrf"},
        )

    assert resp.status_code == 200
    mock_plan.assert_called_once()
    called_request = mock_plan.call_args.args[0]
    assert called_request.vendor == "V"
    assert called_request.entries[0].fqdn == "x.vendor.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rule_review_ai_assist_fqdn.py -v`
Expected: FAIL — `404` (route doesn't exist yet)

- [ ] **Step 3: Add the route**

In `app/routes/rule_review_routes.py`, update the module docstring's route list (after `GET  /api/rule-review/devices`) to add:

```
  POST /api/rule-review/ai-assist-fqdn      — vendor FQDN/wildcard allowlist AI Assist (planner + LLM narration)
```

Then append at the end of the file, after the existing `rr_ai_assist` function:

```python
# ── AI Assist: FQDN allowlist ───────────────────────────────────────────────


@bp.route("/api/rule-review/ai-assist-fqdn", methods=["POST"])
@tab_required("rule_review")
def rr_ai_assist_fqdn():
    """AI Assist (FQDN allowlist mode): run plan_fqdn_change deterministically,
    then narrate the result with the configured LLM. Same guarantees as
    rr_ai_assist — the deterministic plan always returns; narration is
    best-effort."""
    from app.app_settings import get_setting

    if not get_setting("ai_assist_enabled", False):
        return jsonify({"error": "AI Assist is not enabled"}), 503

    from app.planner.fqdn_intake import parse_fqdn_xlsx
    from app.planner.models import FQDNAllowlistRequest, FQDNEntry

    is_multipart = "file" in request.files

    if is_multipart:
        src_ip = request.form.get("src_ip", "")
        ticket_id = request.form.get("ticket_id", "")
        try:
            firewalls_raw = _json.loads(request.form.get("firewalls", "[]"))
        except ValueError:
            return jsonify({"error": "firewalls must be a JSON array"}), 400
        try:
            parsed = parse_fqdn_xlsx(
                request.files["file"], src_ip=src_ip, ticket_id=ticket_id,
            )
        except Exception as exc:
            return jsonify({"error": f"Could not parse uploaded .xlsx: {exc}"}), 400
        if not parsed.entries:
            return jsonify({"error": "No valid FQDN rows found in the uploaded file"}), 400
        fqdn_request = FQDNAllowlistRequest(
            vendor=parsed.vendor, category=parsed.category, src_ip=src_ip,
            ticket_id=ticket_id,
            firewalls=[f"{fw['device']}:{fw['adom']}" for fw in firewalls_raw],
            entries=parsed.entries,
        )
        intake_warnings = parsed.warnings
    else:
        data = request.get_json(silent=True) or {}
        vendor = data.get("vendor", "")
        category = data.get("category", "")
        src_ip = data.get("src_ip", "")
        ticket_id = data.get("ticket_id", "")
        firewalls_raw = data.get("firewalls", [])
        entries_raw = data.get("entries", [])

        if not src_ip or not firewalls_raw or not entries_raw:
            return jsonify(
                {"error": "src_ip, firewalls, and at least one entry are required"}
            ), 400

        entries = [
            FQDNEntry(
                fqdn=e.get("fqdn", ""),
                is_wildcard=str(e.get("fqdn", "")).startswith("*."),
                ports=[int(p) for p in e.get("ports", [])],
                protocol=str(e.get("protocol", "TCP")).upper(),
                required=bool(e.get("required", True)),
                comment=e.get("comment", ""),
            )
            for e in entries_raw
            if e.get("fqdn")
        ]
        if not entries:
            return jsonify({"error": "No valid FQDN entries provided"}), 400

        fqdn_request = FQDNAllowlistRequest(
            vendor=vendor, category=category, src_ip=src_ip, ticket_id=ticket_id,
            firewalls=[f"{fw['device']}:{fw['adom']}" for fw in firewalls_raw],
            entries=entries,
        )
        intake_warnings = []

    for fw in firewalls_raw:
        if not fw.get("device") or not fw.get("adom"):
            return jsonify(
                {
                    "error": "Each target firewall must include both a device and an ADOM "
                    "(format: DEVICE:ADOM) — got an entry missing one or the other."
                }
            ), 400
        if err := check_adom_access(fw["adom"]):
            return err

    from app.planner.engine import plan_fqdn_change, to_fqdn_report_payload
    from app.planner.models import PlannerDataError

    try:
        with make_client() as fmg:
            plan = plan_fqdn_change(fqdn_request, fmg_client=fmg)
    except PlannerDataError as exc:
        return jsonify({"error": str(exc), "source": exc.source}), 502
    except FMGError as exc:
        return upstream_api_error("rule_review", exc)
    except Exception as exc:
        return internal_api_error("rule_review", exc)

    plan_dict = to_fqdn_report_payload(plan)
    plan_dict["intake_warnings"] = intake_warnings

    narrative = None
    narrative_error = None
    try:
        from app.llm import get_provider

        provider = get_provider()
        narrative = provider.narrate(
            system_prompt=(
                "You are a firewall change analyst assistant. You are given a "
                "structured, already-computed FQDN/wildcard allowlist change plan "
                "as JSON — one entry per target firewall with coverage status and "
                "any proposed address objects/group/policy. Write a clear, concise "
                "report for a peer reviewer: summarize coverage per firewall, what "
                "needs to be created, and any warnings. Never invent or change any "
                "value in the plan — only explain it in prose."
            ),
            user_prompt=_json.dumps(plan_dict, default=str),
        )
    except Exception as exc:
        narrative_error = str(exc)

    return jsonify(
        {
            "plan": plan_dict,
            "narrative": narrative,
            "narrative_error": narrative_error,
        }
    )
```

Add `import json as _json` to the top-level imports (alongside the existing `import csv` / `import io`):

```python
import csv
import io
import json as _json
```

Then, inside the existing `rr_ai_assist` function (the single-change route), remove its now-redundant local `import json as _json` line (it previously imported this locally inside the `try:` block before building the narrative prompt) — the top-level import covers it.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_rule_review_ai_assist_fqdn.py -v`
Expected: PASS (all four tests)

Then run the full backend suite to confirm no regressions:

Run: `pytest tests/ -q`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add app/routes/rule_review_routes.py tests/test_rule_review_ai_assist_fqdn.py
git commit -m "feat: add POST /api/rule-review/ai-assist-fqdn route (manual entry + xlsx upload)"
```

---

### Task 10: Frontend — mode toggle and FQDN form markup

**Files:**
- Modify: `app/templates/rule_review.html`

**Interfaces:** Produces DOM elements consumed by Task 11's JS: `#rrAiModeSingle`/`#rrAiModeFqdn` (toggle buttons), `#rrAiSingleForm` (wraps the existing form fields), `#rrAiFqdnForm` (new form), `#rrAiFqdnVendor`, `#rrAiFqdnCategory`, `#rrAiFqdnSrc`, `#rrAiFqdnFirewalls`, `#rrAiFqdnTicket`, `#rrAiFqdnFile`, `#rrAiFqdnRows` (tbody), `#rrAiFqdnAddRowBtn`, `#rrAiFqdnSubmitBtn`, `#rrAiFqdnResult` (results container, mirrors `#rrAiResult`'s children with an `Fqdn` suffix on each id).

- [ ] **Step 1: Add the mode toggle and wrap the existing form**

In `app/templates/rule_review.html`, replace:

```html
<section class="rr-section" id="rrAiAssistSection">
  <h2>AI Assist <span class="badge badge-beta">Beta</span></h2>
  <p class="rr-hint">Single-request analysis: describe one change, get a deterministic verdict plus an AI-written report and peer-review package. Requires an admin to enable AI Assist and configure a provider in Admin settings.</p>

  <div id="rrAiDisabledNotice" class="rr-notice" style="display:none">
    AI Assist is not enabled on this server. Ask an admin to enable it under Admin &rarr; AI Assist.
  </div>

  <form id="rrAiForm" class="rr-form">
```

with:

```html
<section class="rr-section" id="rrAiAssistSection">
  <h2>AI Assist <span class="badge badge-beta">Beta</span></h2>
  <p class="rr-hint">Single-request analysis: describe one change, get a deterministic verdict plus an AI-written report and peer-review package. Requires an admin to enable AI Assist and configure a provider in Admin settings.</p>

  <div id="rrAiDisabledNotice" class="rr-notice" style="display:none">
    AI Assist is not enabled on this server. Ask an admin to enable it under Admin &rarr; AI Assist.
  </div>

  <div class="rr-ai-mode-toggle" style="margin-bottom:1rem;display:flex;gap:.5rem">
    <button type="button" class="btn btn-sm btn-primary" id="rrAiModeSingle">Single Change</button>
    <button type="button" class="btn btn-sm btn-secondary" id="rrAiModeFqdn">FQDN Allowlist</button>
  </div>

  <form id="rrAiForm" class="rr-form" style="display:">
```

- [ ] **Step 2: Close the single-change form's wrapper and add the FQDN form**

Immediately after the existing single-change form's closing `</form>` tag (the one that follows `<button type="submit" class="btn btn-primary" id="rrAiSubmitBtn">Run AI Assist</button>`), insert the new FQDN form:

```html
  <form id="rrAiFqdnForm" class="rr-form" style="display:none">
    <div class="rr-form-row">
      <label for="rrAiFqdnVendor">Vendor</label>
      <input type="text" id="rrAiFqdnVendor" placeholder="Apple">
    </div>
    <div class="rr-form-row">
      <label for="rrAiFqdnCategory">Category</label>
      <input type="text" id="rrAiFqdnCategory" placeholder="APNs">
    </div>
    <div class="rr-form-row">
      <label for="rrAiFqdnSrc">Source IP</label>
      <input type="text" id="rrAiFqdnSrc" placeholder="10.1.2.3, or 'any'">
    </div>
    <div class="rr-form-row" style="position:relative">
      <label for="rrAiFqdnFirewalls">Target firewall(s)</label>
      <input type="text" id="rrAiFqdnFirewalls" placeholder="FW01:OT-ADOM, FW02:OT-ADOM" autocomplete="off">
      <span class="rr-field-hint">Format: DEVICE:ADOM, comma-separated for multiple — start typing a device name to search</span>
      <ul id="rrAiFqdnFirewallSuggestions" class="rr-suggestions" style="display:none"></ul>
    </div>
    <div class="rr-form-row">
      <label for="rrAiFqdnTicket">Ticket ID</label>
      <input type="text" id="rrAiFqdnTicket" placeholder="CHG0012345">
    </div>

    <div class="rr-form-row">
      <label for="rrAiFqdnFile">Upload vendor allowlist (.xlsx)</label>
      <input type="file" id="rrAiFqdnFile" accept=".xlsx">
      <span class="rr-field-hint">Columns: Hostname/Domain, Ports, Protocol, Vendor, Category, Required?, Purpose/Notes. If a file is attached, manually-entered rows below are ignored.</span>
    </div>

    <div class="rr-form-row">
      <label>Or enter rows manually</label>
      <div class="table-wrapper">
        <table class="data-table">
          <thead>
            <tr>
              <th>FQDN / Wildcard</th>
              <th>Ports</th>
              <th>Protocol</th>
              <th>Required</th>
              <th>Comment</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="rrAiFqdnRows"></tbody>
        </table>
      </div>
      <button type="button" class="btn btn-sm" id="rrAiFqdnAddRowBtn">+ Add Row</button>
    </div>

    <button type="submit" class="btn btn-primary" id="rrAiFqdnSubmitBtn">Run AI Assist</button>
  </form>

  <div id="rrAiFqdnRunning" class="rr-running" style="display:none">Running planner and generating report&hellip;</div>
  <div id="rrAiFqdnError" class="rr-error" style="display:none"></div>

  <div id="rrAiFqdnResult" style="display:none">
    <div id="rrAiFqdnWarnings" class="alert alert-warning" style="display:none"></div>
    <div id="rrAiFqdnPerFirewall"></div>
    <h3>AI-Generated Report</h3>
    <div id="rrAiFqdnNarrativeError" class="rr-notice" style="display:none"></div>
    <div id="rrAiFqdnNarrative" class="rr-narrative"></div>
  </div>
```

- [ ] **Step 3: Manual verification (template syntax only — no automated test for markup)**

Run: `python3 -c "from app import create_app; app = create_app(); app.jinja_env.get_template('rule_review.html')"`
Expected: no exception (confirms the Jinja2 template still parses)

- [ ] **Step 4: Commit**

```bash
git add app/templates/rule_review.html
git commit -m "feat: add FQDN Allowlist mode toggle and form to AI Assist panel"
```

---

### Task 11: Frontend — FQDN form logic and result rendering

**Files:**
- Modify: `app/static/js/rule_review.js`

**Interfaces:**
- Consumes: DOM elements from Task 10; `esc()` (existing helper, top of file); `aiDeviceCache`/`loadAiDeviceCache()` (existing, reused for the FQDN firewall typeahead).
- Produces: no new exports — this is page-behavior code wired to DOM events.

- [ ] **Step 1: Add mode toggle logic and dynamic row management**

Append to `app/static/js/rule_review.js` (after the existing `document.getElementById('rrAiDownloadBtn')?.addEventListener(...)` line at the end of the file):

```javascript
// ── AI Assist: FQDN Allowlist mode ───────────────────────────────────────

function switchAiMode(mode) {
  const singleForm = document.getElementById('rrAiForm');
  const fqdnForm = document.getElementById('rrAiFqdnForm');
  const singleBtn = document.getElementById('rrAiModeSingle');
  const fqdnBtn = document.getElementById('rrAiModeFqdn');
  const singleResult = document.getElementById('rrAiResult');
  const fqdnResult = document.getElementById('rrAiFqdnResult');

  if (mode === 'fqdn') {
    singleForm.style.display = 'none';
    fqdnForm.style.display = '';
    singleBtn.classList.replace('btn-primary', 'btn-secondary');
    fqdnBtn.classList.replace('btn-secondary', 'btn-primary');
    singleResult.style.display = 'none';
  } else {
    singleForm.style.display = '';
    fqdnForm.style.display = 'none';
    fqdnBtn.classList.replace('btn-primary', 'btn-secondary');
    singleBtn.classList.replace('btn-secondary', 'btn-primary');
    fqdnResult.style.display = 'none';
  }
}

document.getElementById('rrAiModeSingle')?.addEventListener('click', () => switchAiMode('single'));
document.getElementById('rrAiModeFqdn')?.addEventListener('click', () => switchAiMode('fqdn'));

function addFqdnRow() {
  const tbody = document.getElementById('rrAiFqdnRows');
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input type="text" class="fqdn-row-fqdn" placeholder="*.push.apple.com"></td>
    <td><input type="text" class="fqdn-row-ports" placeholder="443, 5223" style="width:6rem"></td>
    <td>
      <select class="fqdn-row-protocol">
        <option value="TCP">TCP</option>
        <option value="UDP">UDP</option>
      </select>
    </td>
    <td><input type="checkbox" class="fqdn-row-required" checked></td>
    <td><input type="text" class="fqdn-row-comment" placeholder="Optional"></td>
    <td><button type="button" class="btn btn-sm fqdn-row-remove">&times;</button></td>
  `;
  tr.querySelector('.fqdn-row-remove').addEventListener('click', () => tr.remove());
  tbody.appendChild(tr);
}

document.getElementById('rrAiFqdnAddRowBtn')?.addEventListener('click', addFqdnRow);

function collectFqdnRows() {
  return Array.from(document.querySelectorAll('#rrAiFqdnRows tr')).map(tr => ({
    fqdn: tr.querySelector('.fqdn-row-fqdn').value.trim(),
    ports: tr.querySelector('.fqdn-row-ports').value.split(',').map(p => p.trim()).filter(Boolean).map(Number),
    protocol: tr.querySelector('.fqdn-row-protocol').value,
    required: tr.querySelector('.fqdn-row-required').checked,
    comment: tr.querySelector('.fqdn-row-comment').value.trim(),
  })).filter(e => e.fqdn);
}

// Firewall typeahead — reuses the same aiDeviceCache/loadAiDeviceCache as the single-change form.
function renderFqdnFirewallSuggestions(matches) {
  const list = document.getElementById('rrAiFqdnFirewallSuggestions');
  if (!matches.length) {
    list.style.display = 'none';
    list.innerHTML = '';
    return;
  }
  list.innerHTML = matches.slice(0, 8).map((m, i) =>
    `<li data-idx="${i}">${esc(m.device)} <span class="rr-suggestion-adom">${esc(m.adom)}</span></li>`
  ).join('');
  list.style.display = '';
}

async function onFqdnFirewallInput(evt) {
  const input = evt.target;
  const token = activeFirewallToken(input);
  if (!token.text || token.text.includes(':')) {
    renderFqdnFirewallSuggestions([]);
    return;
  }
  const devices = await loadAiDeviceCache();
  const q = token.text.toLowerCase();
  const matches = devices.filter(d =>
    d.device.toLowerCase().includes(q) || d.adom.toLowerCase().includes(q)
  );
  if (activeFirewallToken(input).text.toLowerCase() !== q) return;
  renderFqdnFirewallSuggestions(matches);
  document.getElementById('rrAiFqdnFirewallSuggestions').querySelectorAll('li').forEach(li => {
    li.addEventListener('mousedown', (e) => {
      e.preventDefault();
      applyFirewallSuggestion(input, matches[Number(li.dataset.idx)]);
    });
  });
}

document.getElementById('rrAiFqdnFirewalls')?.addEventListener('input', onFqdnFirewallInput);
document.getElementById('rrAiFqdnFirewalls')?.addEventListener('focus', loadAiDeviceCache);
document.getElementById('rrAiFqdnFirewalls')?.addEventListener('blur', () => {
  setTimeout(() => renderFqdnFirewallSuggestions([]), 150);
});

async function runFqdnAiAssist(evt) {
  evt.preventDefault();
  const errEl = document.getElementById('rrAiFqdnError');
  const resultEl = document.getElementById('rrAiFqdnResult');
  const runningEl = document.getElementById('rrAiFqdnRunning');
  errEl.style.display = 'none';
  resultEl.style.display = 'none';
  runningEl.style.display = '';

  const srcIp = document.getElementById('rrAiFqdnSrc').value.trim();
  const ticketId = document.getElementById('rrAiFqdnTicket').value.trim();
  const firewalls = parseAiFirewalls(document.getElementById('rrAiFqdnFirewalls').value);
  const fileInput = document.getElementById('rrAiFqdnFile');
  const file = fileInput.files[0];

  try {
    let resp;
    if (file) {
      const fd = new FormData();
      fd.append('src_ip', srcIp);
      fd.append('ticket_id', ticketId);
      fd.append('firewalls', JSON.stringify(firewalls));
      fd.append('file', file);
      resp = await fetch('/api/rule-review/ai-assist-fqdn', { method: 'POST', body: fd });
    } else {
      const payload = {
        vendor: document.getElementById('rrAiFqdnVendor').value.trim(),
        category: document.getElementById('rrAiFqdnCategory').value.trim(),
        src_ip: srcIp,
        ticket_id: ticketId,
        firewalls,
        entries: collectFqdnRows(),
      };
      resp = await fetch('/api/rule-review/ai-assist-fqdn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    }
    const data = await resp.json();
    runningEl.style.display = 'none';
    if (!resp.ok) {
      errEl.textContent = data.error || `Request failed (${resp.status})`;
      errEl.style.display = '';
      return;
    }
    renderFqdnAiResult(data);
  } catch (e) {
    runningEl.style.display = 'none';
    errEl.textContent = 'Request failed: ' + e.message;
    errEl.style.display = '';
  }
}

function renderFqdnAiResult(data) {
  const plan = data.plan;

  const warningsEl = document.getElementById('rrAiFqdnWarnings');
  const warnings = plan.warnings || [];
  if (warnings.length) {
    warningsEl.innerHTML = '<strong>Warnings:</strong><ul>' +
      warnings.map(w => `<li>${esc(w)}</li>`).join('') + '</ul>';
    warningsEl.style.display = '';
  } else {
    warningsEl.innerHTML = '';
    warningsEl.style.display = 'none';
  }

  const perFwEl = document.getElementById('rrAiFqdnPerFirewall');
  perFwEl.innerHTML = (plan.per_firewall || []).map(fw => {
    const objCli = (fw.proposed_objects || []).map(o => o.cli).join('\n\n');
    const groupCli = fw.proposed_group ? fw.proposed_group.cli : '';
    const policyCli = fw.proposed_policy ? fw.proposed_policy.cli : '';
    const cliBlock = [objCli, groupCli, policyCli].filter(Boolean).join('\n\n');
    return `
      <div class="rr-section" style="margin-top:1rem">
        <h3>${esc(fw.firewall)} <span class="rr-zone-badge">${esc(fw.verdict)}</span></h3>
        <div>Coverage: ${esc(fw.coverage)}</div>
        ${fw.warnings && fw.warnings.length ? '<ul>' + fw.warnings.map(w => `<li>${esc(w)}</li>`).join('') + '</ul>' : ''}
        ${cliBlock ? `<pre class="rr-cli-block">${esc(cliBlock)}</pre>` : '<div class="text-muted">No new configuration required.</div>'}
      </div>
    `;
  }).join('');

  const narrEl = document.getElementById('rrAiFqdnNarrative');
  const narrErrEl = document.getElementById('rrAiFqdnNarrativeError');
  if (data.narrative) {
    narrEl.textContent = data.narrative;
    narrErrEl.style.display = 'none';
  } else {
    narrEl.textContent = '';
    narrErrEl.textContent = 'AI summary unavailable: ' + (data.narrative_error || 'unknown error');
    narrErrEl.style.display = '';
  }

  document.getElementById('rrAiFqdnResult').style.display = '';
}

document.getElementById('rrAiFqdnForm')?.addEventListener('submit', runFqdnAiAssist);
```

- [ ] **Step 2: Wire the FQDN disabled-notice check into the existing availability check**

Locate the existing `checkAiAssistAvailable` function and replace its body to also disable the FQDN submit button:

```javascript
async function checkAiAssistAvailable() {
  try {
    const resp = await fetch('/api/rule-review/ai-assist-status');
    const data = await resp.json();
    if (!data.available) {
      document.getElementById('rrAiDisabledNotice').style.display = '';
      document.getElementById('rrAiSubmitBtn').disabled = true;
      const fqdnSubmit = document.getElementById('rrAiFqdnSubmitBtn');
      if (fqdnSubmit) fqdnSubmit.disabled = true;
    }
  } catch (e) {
    // Non-fatal — the form's own submit handler will surface any real error.
  }
}
```

- [ ] **Step 3: Manual verification in browser**

Start the dev server (`python wsgi.py`), log in, open the Rule Validation tab, and confirm:
1. The "Single Change" / "FQDN Allowlist" toggle switches the visible form.
2. "+ Add Row" adds an editable row to the FQDN table and the row's "×" button removes it.
3. Typing in the FQDN firewall field shows the same device:ADOM typeahead as the single-change form.
4. Submitting the FQDN form with AI Assist disabled shows the disabled notice (submit button disabled), matching the single-change form's behavior.

If AI Assist is enabled and FortiManager/zone data is reachable in the test environment, submit a manual-row request and confirm the per-firewall coverage/CLI blocks render; then attach a small `.xlsx` file (matching the columns documented in the form) and confirm the same result shape renders from the upload path.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/rule_review.js
git commit -m "feat: wire FQDN Allowlist mode form submission and result rendering"
```

---

### Task 12: Provenance bookkeeping

**Files:**
- Modify: `app/planner/VENDORED_FROM.md`

**Interfaces:** None (documentation only).

- [ ] **Step 1: Update the provenance record**

In `app/planner/VENDORED_FROM.md`, update the header block:

```markdown
# Provenance

The `app/planner/` package (`models.py`, `matching.py`, `cli_gen.py`,
`standards.py`, `insertion.py`, `fetch.py`, `engine.py`, `catalogs.py`,
`zone_adapter.py`, `fqdn_intake.py`) is ported from `~/code/github/ai/4tanalyst`'s
`planner/` package (plus `fortimanager_mcp/matching.py` and
`fortimanager_mcp/query.py`'s catalog functions, and `intake_mcp/fqdn_parser.py`),
adapted to call 4THealth+'s own `app/fmg_client.py`/`app/zone_db.py` directly
in-process instead of over HTTP with separate credentials.

**Ported from commit:** `2ab05fc52d445a91f75a6696b20f5526bf86a5dd`
**Source commit date:** `2026-08-17 12:31:00 -0500`
**Ported on:** `2026-08-17`
```

Add a new row to the "Files and their adaptation" table:

```markdown
| `fqdn_intake.py` | `intake_mcp/fqdn_parser.py` | `FQDNEntry`/`FQDNAllowlistRequest` moved to `models.py` (no separate intake package here); `parse_fqdn_xlsx` takes a file-like object instead of a filesystem path, matching `rule_review_routes.py`'s existing `.xlsx` upload pattern |
```

Update the sync command at the bottom of the file to reflect the new base SHA:

```bash
git -C ~/code/github/ai/4tanalyst log 2ab05fc52d445a91f75a6696b20f5526bf86a5dd..HEAD --oneline -- planner/ standards_mcp/ fortimanager_mcp/matching.py fortimanager_mcp/query.py zone_mcp/client.py intake_mcp/fqdn_parser.py
```

- [ ] **Step 2: Run the full test suite one more time**

Run: `pytest tests/ -q`
Expected: all tests pass, no regressions from the whole feature

- [ ] **Step 3: Commit**

```bash
git add app/planner/VENDORED_FROM.md
git commit -m "docs: update planner provenance to the FQDN allowlist sync point"
```
