"""Admin API endpoints for the Backup feature.

All routes are admin-only. Blueprint is registered at no URL prefix — every
route declares its full path (e.g. /admin/api/backup/*).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request, send_file

from app import backup_engine, backup_scheduler
from app.decorators import admin_required as _admin_required

bp = Blueprint("backup", __name__)


# ── Config ────────────────────────────────────────────────────────────────────

_MASK = "••••••"


@bp.get("/admin/api/backup/config")
@_admin_required
def backup_get_config():
    cfg = backup_engine.load_config()
    masked = dict(cfg)
    if masked.get("password"):
        masked["password"] = _MASK
    ftp = dict(masked.get("ftp", {}))
    if ftp.get("password"):
        ftp["password"] = _MASK
    masked["ftp"] = ftp
    # jobs live separately in the scheduler — don't expose them here
    masked.pop("jobs", None)
    return jsonify(masked)


@bp.put("/admin/api/backup/config")
@_admin_required
def backup_put_config():
    data = request.get_json(force=True) or {}
    existing = backup_engine.load_config()

    # If the client echoed back the mask placeholder, keep the stored value
    new_password = data.get("password", "")
    if new_password == _MASK:
        new_password = existing.get("password", "")

    ftp_in = data.get("ftp", existing.get("ftp", {}))
    ftp = dict(ftp_in)
    if ftp.get("password") == _MASK:
        ftp["password"] = existing.get("ftp", {}).get("password", "")

    cfg = {
        **existing,
        "password": new_password,
        "backup_dir": data.get(
            "backup_dir", existing.get("backup_dir", "/var/backups/4thealth")
        ),
        "max_files": int(data.get("max_files", existing.get("max_files", 20))),
        "exclude_tls_key": bool(
            data.get("exclude_tls_key", existing.get("exclude_tls_key", False))
        ),
        "ftp": ftp,
    }

    try:
        backup_engine.save_config(cfg)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True})


# ── One-time backup ───────────────────────────────────────────────────────────


@bp.post("/admin/api/backup/run-now")
@_admin_required
def backup_run_now():
    cfg = backup_engine.load_config()
    if not cfg.get("password"):
        return jsonify(
            {
                "error": "Backup password not configured. Set it in Backup Settings first."
            }
        ), 400

    try:
        path, filename = backup_engine.create_backup(cfg)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    resp = send_file(
        path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )
    resp.headers["X-Backup-Filename"] = filename
    return resp


# ── FTP/SFTP test connection ──────────────────────────────────────────────────


@bp.post("/admin/api/backup/ftp/test")
@_admin_required
def backup_ftp_test():
    ftp_cfg = request.get_json(force=True) or {}
    result = backup_scheduler.test_connection(ftp_cfg)
    return jsonify(result)


# ── Scheduled jobs — CRUD ─────────────────────────────────────────────────────


@bp.get("/admin/api/backup/jobs")
@_admin_required
def backup_list_jobs():
    return jsonify(backup_scheduler.get_all_jobs())


@bp.post("/admin/api/backup/jobs")
@_admin_required
def backup_create_job():
    data = request.get_json(force=True) or {}
    try:
        job = backup_scheduler.create_job(data)
    except (ValueError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(job), 201


@bp.put("/admin/api/backup/jobs/<job_id>")
@_admin_required
def backup_update_job(job_id: str):
    data = request.get_json(force=True) or {}
    try:
        job = backup_scheduler.update_job(job_id, data)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(job)


@bp.delete("/admin/api/backup/jobs/<job_id>")
@_admin_required
def backup_delete_job(job_id: str):
    backup_scheduler.delete_job(job_id)
    return jsonify({"ok": True})


# ── Scheduled jobs — run + status ─────────────────────────────────────────────


@bp.post("/admin/api/backup/jobs/<job_id>/run")
@_admin_required
def backup_run_job(job_id: str):
    try:
        backup_scheduler.run_job_now(job_id)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"ok": True, "message": "Job queued"})


@bp.get("/admin/api/backup/jobs/<job_id>/status")
@_admin_required
def backup_job_status(job_id: str):
    try:
        status = backup_scheduler.get_job_status(job_id)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(status)
