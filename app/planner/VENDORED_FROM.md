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
| `fqdn_intake.py` | `intake_mcp/fqdn_parser.py` | `FQDNEntry`/`FQDNAllowlistRequest` moved to `models.py` (no separate intake package here); `parse_fqdn_xlsx` takes a file-like object instead of a filesystem path, matching `rule_review_routes.py`'s existing `.xlsx` upload pattern |

## How to sync later changes from 4tAnalyst

See the `4tanalyst-sync-workflow` memory note, or:

```bash
git -C ~/code/github/ai/4tanalyst log 2ab05fc52d445a91f75a6696b20f5526bf86a5dd..HEAD --oneline -- planner/ standards_mcp/ fortimanager_mcp/matching.py fortimanager_mcp/query.py zone_mcp/client.py intake_mcp/fqdn_parser.py
```

Review each change and manually apply the relevant parts here — this is a
fork, not a live dependency, so nothing merges automatically. Update the
commit hash above once you've synced.
