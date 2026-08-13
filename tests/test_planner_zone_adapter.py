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
