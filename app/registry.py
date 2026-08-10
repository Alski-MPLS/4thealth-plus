"""Navigation tab registry — single source of truth for nav metadata.

Each blueprint self-registers at import time:

    from app import registry
    registry.register("my_tab", "My Tab", "myblueprint.myview")

The app factory then:
  1. Injects ``nav_registry`` into every template via context_processor
     so base.html can render the nav bar without hard-coded tab names.
  2. Syncs ``groups.KNOWN_TABS`` from this registry so the Admin UI
     tab-permission checklist stays current automatically.

Adding a new nav tab therefore requires NO changes to base.html,
groups.py, or admin.py — just a register() call in the new blueprint
and one line in __init__._BLUEPRINT_MODULES.
"""

from __future__ import annotations

_registry: dict[str, dict] = {}


def register(key: str, name: str, endpoint: str, icon: str = "") -> None:
    """Register a navigable tab.

    Args:
        key:      Unique tab identifier used in allowed_tabs and groups.json.
        name:     Display label shown in the nav bar and Admin UI checklist.
        endpoint: Flask endpoint string passed to url_for(), e.g.
                  ``"dashboard.index"`` or ``"hygiene.hygiene_page"``.
        icon:     Optional Unicode character prepended to the nav label.
    """
    _registry[key] = {"name": name, "endpoint": endpoint, "icon": icon}


def get_registry() -> dict[str, dict]:
    """Return a snapshot of the full registry (preserves insertion order)."""
    return dict(_registry)


def known_tabs() -> dict[str, str]:
    """Return ``{key: name}`` — compatible with ``groups.KNOWN_TABS``."""
    return {k: v["name"] for k, v in _registry.items()}
