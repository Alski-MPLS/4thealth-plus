"""Persistent store for map region configuration.

Region names, state assignments, and colours are all user-configurable
and written back to map_regions.json in the project root. If the file is
absent the application falls back to the built-in defaults.
"""

import copy
import json
import os
import re
import threading

from app.atomic_io import atomic_write_json

_LOCK = threading.Lock()

ALL_US_STATES: list = [
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
]

_ALL_STATES_SET = set(ALL_US_STATES)

_DEFAULT: dict = {
    "regions": [
        {
            "name": "Upper Midwest",
            "color": "#1976d2",
            "states": ["Minnesota", "Wisconsin", "North Dakota", "South Dakota"],
        },
        {
            "name": "Colorado",
            "color": "#e53935",
            "states": ["Colorado"],
        },
        {
            "name": "Southwest",
            "color": "#43a047",
            "states": ["Texas", "New Mexico"],
        },
    ],
    "other_color": "#333333",
}

_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "map_regions.json")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def load() -> dict:
    """Return current region config plus the canonical US state list."""
    with _LOCK:
        if os.path.exists(_PATH):
            try:
                with open(_PATH) as f:
                    saved = json.load(f)
                saved["all_states"] = ALL_US_STATES
                return saved
            except Exception:
                pass
        out = copy.deepcopy(_DEFAULT)
        out["all_states"] = ALL_US_STATES
        return out


def save(data: dict) -> None:
    """Write region config to disk atomically (strips the runtime all_states list)."""
    out = copy.deepcopy(data)
    out.pop("all_states", None)
    with _LOCK:
        atomic_write_json(_PATH, out)


def is_valid_color(color: str) -> bool:
    return bool(_HEX_RE.match(color or ""))


def validate_regions(regions: list) -> str:
    """Validate submitted region list. Returns an error string, or empty string if valid."""
    seen_names: set = set()
    seen_states: set = set()

    for r in regions:
        name = (r.get("name") or "").strip()
        if not name:
            return "All regions must have a non-empty name"
        if name in seen_names:
            return f"Duplicate region name: '{name}'"
        seen_names.add(name)

        color = r.get("color", "")
        if not is_valid_color(color):
            return f"Invalid hex color for '{name}': {color}"

        for state in r.get("states", []):
            if state not in _ALL_STATES_SET:
                return f"'{state}' is not a valid US state name"
            if state in seen_states:
                return f"State '{state}' is assigned to more than one region"
            seen_states.add(state)

    return ""
