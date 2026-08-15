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
    def query(
        self, src: str, dst: str, service: str = "", verbose: bool = True
    ) -> list[dict]:
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
