from flask import Blueprint, render_template, session
from app.decorators import tab_required
from app import registry

bp = Blueprint("dashboard", __name__)

# Register nav tabs — order here controls nav bar order.
registry.register("dashboard", "Dashboard", "dashboard.index")
registry.register("firewalls", "Firewalls", "dashboard.firewalls")
registry.register("versions", "Device Versions", "dashboard.versions")


@bp.route("/")
@tab_required("dashboard")
def index():
    return render_template("dashboard.html", user=session["user"])


@bp.route("/firewalls")
@tab_required("firewalls")
def firewalls():
    return render_template("firewalls.html", user=session["user"])


@bp.route("/versions")
@tab_required("versions")
def versions():
    return render_template("versions.html", user=session["user"])
