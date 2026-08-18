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
from app.planner.catalogs import (
    build_catalogs,
    get_device_policies,
    package_targets_device,
)
from app.planner.matching import AddressCatalog, ServiceCatalog
from app.planner.models import PlannerDataError
from app.planner.zone_adapter import ZoneDBAdapter


@dataclass
class DeviceSnapshot:
    device: str
    adom: str
    packages: list[str]
    policies_by_package: dict[str, list[dict]]  # raw dicts, package order preserved
    addr_catalog: AddressCatalog
    svc_catalog: ServiceCatalog
    interfaces: list[dict]
    routing_table: list[dict] = field(default_factory=list)
    degraded: bool = False
    failures: list[str] = field(default_factory=list)


def fetch_device_snapshot(client: FMGClient, adom: str, device: str) -> DeviceSnapshot:
    """Fetch everything the planner needs about one device.

    Raises PlannerDataError if the device is unknown or the object catalogs
    cannot be fetched at all. Per-package policy failures degrade the
    snapshot instead (callers must then refuse to claim "already covered").
    """
    try:
        devices = client.get_devices(adom)
    except FMGError as exc:
        raise PlannerDataError(
            "fortimanager", f"cannot list devices in ADOM {adom!r}: {exc}"
        ) from exc

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
        raise PlannerDataError(
            "fortimanager", f"cannot fetch object catalogs: {exc}"
        ) from exc

    device_pkgs = [
        p.get("path", p.get("name", ""))
        for p in packages
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
        interfaces = [
            i for i in client.get_device_interfaces(adom, device) if isinstance(i, dict)
        ]
    except FMGError as exc:
        failures.append(f"interfaces: {exc}")

    routing_table: list[dict] = []
    try:
        routing_table = [
            r for r in client.get_device_routes(adom, device) if isinstance(r, dict)
        ]
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
    zc: ZoneDBAdapter,
    service: str,
    verdict: str,
    src_zones: list,
    dst_zones: list,
    governing: list,
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
        z.get("name", ""): z for z in catalogue.get("zones", []) if isinstance(z, dict)
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
    """Parse a route dict's destination into an ip_network.

    Route dicts come from FMGClient.get_device_routes()'s live routing
    table (FortiOS's /api/v2/monitor/router/ipv4), which reports the
    destination as "ip_mask" — already a single CIDR string like
    "0.0.0.0/0" — not the "dst" field used by the separate static-route
    *config* schema. "network"/"prefix" are accepted as aliases, matching
    app.rule_review's own route parsing (see best_route() there) — the
    two engines must agree on what a route dict looks like, since they
    read the same FMGClient.get_device_routes() data.

    Unlike _iface_network, 0.0.0.0/0 (the default route) is valid here.
    """
    raw = route.get("ip_mask", route.get("network", route.get("prefix", "")))
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


def _route_interface(route: dict) -> str:
    """Extract the egress interface name from a route dict.

    Same live-monitor schema as _route_network(): the field is
    "interface" (aliases "dev"/"ifname"), not the "device" field used by
    the static-route config schema.
    """
    raw = route.get("interface", route.get("dev", route.get("ifname", "")))
    if isinstance(raw, list) and raw:
        return str(raw[0])
    if isinstance(raw, str):
        return raw
    return str(raw) if raw else ""


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


def resolve_default_route_interface(
    snapshot: DeviceSnapshot,
    label: str = "Destination",
) -> tuple[str, list[str]]:
    """Find the interface carrying the device's enabled default route
    (0.0.0.0/0). Returns (name, warnings); unresolvable → ("", warnings).

    Used for FQDN-based destinations, where there is no real IP to resolve
    against and "whichever interface leads to the internet" is exactly
    what the default route answers directly — asking for it explicitly
    avoids the false-match risk of inferring it by longest-prefix-matching
    an arbitrary sentinel IP against the routing table (a more specific
    static route that happens to cover that one sentinel IP would win
    instead of the true default route).
    """
    warnings: list[str] = []
    for route in snapshot.routing_table:
        if route.get("status", "enable") != "enable":
            continue
        net = _route_network(route)
        if net is None or net.prefixlen != 0:
            continue
        iface_name = _route_interface(route)
        if iface_name:
            return iface_name, warnings
    warnings.append(
        f"No enabled default route (0.0.0.0/0) found on {snapshot.device} — "
        f"{label.lower()} interface must be set manually"
    )
    return "", warnings


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
        iface_name = _route_interface(route)
        if (
            net is not None
            and iface_name
            and net.overlaps(target)
            and net.prefixlen > best_route[1]
        ):
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
