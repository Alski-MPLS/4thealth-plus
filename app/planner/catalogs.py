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
