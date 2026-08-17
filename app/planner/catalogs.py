"""
Address/service object catalogs and per-device policy fetch for the planner.

Simplified from 4tAnalyst's fortimanager_mcp/query.py: no TTL caching or
cross-thread locking, since 4THealth+ calls this in-process, once per
AI Assist request — unlike 4tAnalyst's shared multi-engineer MCP server.
"""

from __future__ import annotations

from typing import Any

from app.planner.matching import AddressCatalog, FQDNCatalog, ServiceCatalog, _names


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
    addr_catalog = AddressCatalog(
        addr_objects, addr_groups, global_addr_objects, global_addr_groups
    )

    svc_objects = client.get_service_objects(adom)
    svc_groups = client.get_service_groups(adom)
    svc_catalog = ServiceCatalog(svc_objects, svc_groups)

    return addr_catalog, svc_catalog


def package_targets_device(pkg: dict, device: str) -> bool:
    """Return True if the package's installation scope includes the device."""
    scope = pkg.get("scope member", pkg.get("scope_member", []))
    if not scope:
        return True  # global/unscoped packages apply to all
    return any(s.get("name", "") == device for s in scope if isinstance(s, dict))


def get_device_policies(
    client, adom: str, device_pkgs: list[str]
) -> dict[str, list[dict] | None]:
    """Fetch policies for exactly the given package names.

    A None value for a package means the fetch failed (caller degrades —
    'no covering rule found' is not conclusive when a fetch failed).
    """
    result: dict[str, list[dict] | None] = {}
    for pkg in device_pkgs:
        try:
            result[pkg] = [
                p for p in client.get_policies(adom, pkg) if isinstance(p, dict)
            ]
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

    Coverage is destination-address-only: the policy's source addresses and
    services are NOT compared against the request (accepted design
    limitation). Only enabled policies whose action is `accept` can mark an
    FQDN covered — a deny policy matching the FQDN is not coverage.
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
            # Only an accept policy grants coverage — a deny (or ipsec/ssl-vpn)
            # policy matching the FQDN blocks it and must not be reported as
            # "already covered". FMG encodes action as an int (see _ACTION_MAP
            # in app.planner.matching) or, on some payloads, as a string.
            action_raw = pol.get("action", 0)
            pol_accepts = action_raw in (1, "1", "accept")
            if not (pol_enabled and pol_accepts):
                continue
            dst_names = _names(pol.get("dstaddr", []))

            pol_fqdns: set[str] = set()
            dst_group_for: dict[str, str] = {}  # fqdn_str -> first containing group name

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
