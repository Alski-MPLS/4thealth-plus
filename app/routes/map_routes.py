"""Map — device location map routes."""

from flask import Blueprint, render_template, jsonify, current_app
from app import registry
from app.decorators import tab_required, admin_required

bp = Blueprint("map", __name__)

registry.register("map_view", "🌐 Map", "map.map_page")


@bp.route("/map")
@tab_required("map_view")
def map_page():
    return render_template("map.html")


@bp.route("/api/map/devices")
@tab_required("map_view")
def map_devices():
    """Return cached device location data."""
    from app.map_cache import get_cached
    from app.groups import get_allowed_adoms
    from flask import session

    cached = get_cached()
    devices = cached.get("devices", [])

    allowed = get_allowed_adoms(
        session.get("user", ""), ad_groups=session.get("ad_groups", [])
    )
    if allowed is not None:
        devices = [d for d in devices if d.get("adom") in allowed]

    return jsonify(
        {
            "devices": devices,
            "last_updated": cached.get("last_updated"),
            "status": cached.get("status"),
            "error": cached.get("error"),
            "adom_progress": cached.get("adom_progress", {}),
        }
    )


@bp.route("/api/map/refresh", methods=["POST"])
@admin_required
def map_refresh():
    """Trigger an immediate cache refresh (non-blocking)."""
    from app.map_cache import refresh_now

    refresh_now(current_app._get_current_object())
    return jsonify({"queued": True})


@bp.route("/api/map/regions")
@tab_required("map_view")
def map_regions():
    """Return region definitions (name, states, color) used by the map legend."""
    from app.map_regions import load

    return jsonify(load())


@bp.route("/api/map/status")
@tab_required("map_view")
def map_status():
    """Return cache status only (lightweight poll during a refresh)."""
    from app.map_cache import get_cached

    cached = get_cached()
    return jsonify(
        {
            "status": cached.get("status"),
            "last_updated": cached.get("last_updated"),
            "error": cached.get("error"),
            "device_count": len(cached.get("devices", [])),
            "adom_progress": cached.get("adom_progress", {}),
        }
    )
