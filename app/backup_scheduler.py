"""Scheduled backup engine — APScheduler CronTrigger, FTP/SFTP transfer, job CRUD.

Jobs are stored inside backup_config.json (cfg["jobs"]) — the same file that
backup_engine uses for its configuration.  All persistence goes through
atomic_write_json so partial writes never corrupt the file.
"""

from __future__ import annotations

import datetime
import fcntl
import ftplib
import json
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

import paramiko

from app import backup_engine
from app.atomic_io import atomic_write_json
from app.app_logger import app_log

_CONFIG_PATH = Path(__file__).parent.parent / "backup_config.json"
_lock = threading.Lock()
_scheduler = None  # BackgroundScheduler instance, set by init_scheduler
_running_jobs: set[str] = set()  # job IDs currently executing

_VALID_DAYS = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"}


# ── Validation ────────────────────────────────────────────────────────────────


def _validate_job_fields(data: dict) -> None:
    days = data.get("days_of_week")
    if not isinstance(days, list) or not days:
        raise ValueError("days_of_week must be a non-empty list")
    invalid = [d for d in days if d not in _VALID_DAYS]
    if invalid:
        raise ValueError(
            f"Invalid day codes: {invalid}. Must be from {sorted(_VALID_DAYS)}"
        )
    time_str = data.get("time", "")
    parts = time_str.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError("time must be HH:MM format")
    if not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
        raise ValueError("time HH must be 0-23, MM must be 0-59")


# ── Config persistence ────────────────────────────────────────────────────────


def _load_cfg() -> dict:
    """Load the full backup_config.json, respecting _CONFIG_PATH (monkeypatchable)."""
    if not _CONFIG_PATH.exists():
        return backup_engine.load_config()
    try:
        with open(_CONFIG_PATH) as f:
            data = json.load(f)
        # Merge defaults so new keys appear on old configs
        from app.backup_engine import _DEFAULT_CONFIG

        merged = dict(_DEFAULT_CONFIG)
        merged.update(data)
        if "ftp" in data and isinstance(data.get("ftp"), dict):
            merged["ftp"] = {**_DEFAULT_CONFIG["ftp"], **data["ftp"]}
        return merged
    except Exception:
        return backup_engine.load_config()


def _save_cfg(cfg: dict) -> None:
    """Atomically write the full config dict back to _CONFIG_PATH."""
    atomic_write_json(_CONFIG_PATH, cfg)


# ── Public CRUD ───────────────────────────────────────────────────────────────


def get_all_jobs() -> list[dict]:
    with _lock:
        return _load_cfg().get("jobs", [])


def create_job(data: dict) -> dict:
    _validate_job_fields(data)
    job: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "name": data.get("name", "Backup Job"),
        "days_of_week": data["days_of_week"],
        "time": data["time"],
        "enabled": bool(data.get("enabled", True)),
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "runs": [],
    }
    with _lock:
        cfg = _load_cfg()
        cfg.setdefault("jobs", []).append(job)
        _save_cfg(cfg)
    if job["enabled"] and _scheduler is not None:
        _register(job)
    return job


def update_job(job_id: str, data: dict) -> dict:
    _validate_job_fields(data)
    updated = None
    with _lock:
        cfg = _load_cfg()
        jobs = cfg.get("jobs", [])
        for i, j in enumerate(jobs):
            if j["id"] == job_id:
                jobs[i] = {
                    **j,
                    "name": data.get("name", j.get("name", "")),
                    "days_of_week": data["days_of_week"],
                    "time": data["time"],
                    "enabled": bool(data.get("enabled", True)),
                }
                updated = jobs[i]
                break
        if updated is None:
            raise KeyError(f"Job {job_id} not found")
        cfg["jobs"] = jobs
        _save_cfg(cfg)
    if _scheduler is not None:
        _unregister(job_id)
        if updated["enabled"]:
            _register(updated)
    return updated


def delete_job(job_id: str) -> None:
    with _lock:
        cfg = _load_cfg()
        cfg["jobs"] = [j for j in cfg.get("jobs", []) if j["id"] != job_id]
        _save_cfg(cfg)
    if _scheduler is not None:
        _unregister(job_id)


def run_job_now(job_id: str) -> None:
    """Fire the job in a daemon thread; returns immediately.

    Raises KeyError if no job with ``job_id`` exists.
    """
    jobs = get_all_jobs()
    if not any(j["id"] == job_id for j in jobs):
        raise KeyError(f"Job {job_id} not found")
    t = threading.Thread(
        target=_execute_job,
        args=(job_id,),
        name=f"backup_{job_id[:8]}",
        daemon=True,
    )
    t.start()


def is_job_running(job_id: str) -> bool:
    return job_id in _running_jobs


def get_job_status(job_id: str) -> dict:
    """Return {id, running, runs[:10]}. Raises KeyError if job not found."""
    jobs = get_all_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        raise KeyError(f"Job {job_id} not found")
    return {
        "id": job["id"],
        "running": is_job_running(job_id),
        "runs": job.get("runs", [])[:10],
    }


# ── FTP/SFTP transfer ─────────────────────────────────────────────────────────


def transfer_file(ftp_cfg: dict, local_path: Path, filename: str) -> None:
    """Transfer local_path to the remote server.  Raises on any failure."""
    protocol = ftp_cfg.get("protocol", "sftp")
    host = ftp_cfg["host"]
    port = int(ftp_cfg.get("port", 22))
    username = ftp_cfg.get("username", "")
    password = ftp_cfg.get("password", "")
    remote_dir = ftp_cfg.get("remote_dir", "/").rstrip("/")

    if protocol == "sftp":
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            host, port=port, username=username, password=password, timeout=30
        )
        try:
            sftp = client.open_sftp()
            sftp.put(str(local_path), f"{remote_dir}/{filename}")
            sftp.close()
        finally:
            client.close()
    else:
        # Plain FTP
        with ftplib.FTP() as ftp:
            ftp.connect(host, port, timeout=30)
            ftp.login(username, password)
            ftp.cwd(remote_dir)
            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {filename}", f)


def test_connection(ftp_cfg: dict) -> dict:
    """Probe the FTP/SFTP server.  Returns {success: bool, message: str}; never raises."""
    try:
        protocol = ftp_cfg.get("protocol", "sftp")
        host = ftp_cfg.get("host", "")
        port = int(ftp_cfg.get("port", 22))
        username = ftp_cfg.get("username", "")
        password = ftp_cfg.get("password", "")
        if not host:
            return {"success": False, "message": "Host is required"}
        if protocol == "sftp":
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                host, port=port, username=username, password=password, timeout=10
            )
            client.close()
        else:
            with ftplib.FTP() as ftp:
                ftp.connect(host, port, timeout=10)
                ftp.login(username, password)
        return {
            "success": True,
            "message": f"Connected to {host}:{port} via {protocol.upper()}",
        }
    except Exception as exc:
        return {"success": False, "message": str(exc)}


# ── Run history ───────────────────────────────────────────────────────────────


def _prune_runs(job_id: str, retention_days: int = 30) -> None:
    """Remove run records older than retention_days from the named job."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=retention_days)
    with _lock:
        cfg = _load_cfg()
        for j in cfg.get("jobs", []):
            if j["id"] == job_id:
                j["runs"] = [
                    r
                    for r in j.get("runs", [])
                    if datetime.datetime.fromisoformat(r["started_at"].rstrip("Z"))
                    >= cutoff
                ]
        _save_cfg(cfg)


def _append_run(job_id: str, record: dict) -> None:
    """Prepend a run record (newest-first) to the named job."""
    with _lock:
        cfg = _load_cfg()
        for j in cfg.get("jobs", []):
            if j["id"] == job_id:
                j.setdefault("runs", []).insert(0, record)
        _save_cfg(cfg)


# ── Job execution ─────────────────────────────────────────────────────────────


def _try_acquire_job_lock(job_id: str):
    """Return an open file object with an exclusive fcntl lock, or None if busy."""
    lock_path = Path(tempfile.gettempdir()) / f"4thealth_backup_{job_id}.lock"
    try:
        fh = open(lock_path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        try:
            fh.close()
        except Exception:
            pass
        return None


def _execute_job(job_id: str) -> None:
    """Run a backup job.  Called by APScheduler or run_job_now()."""
    lock_fh = _try_acquire_job_lock(job_id)
    if lock_fh is None:
        app_log(
            "INFO",
            "backup_scheduler",
            f"Job {job_id} already running in another worker — skipping",
        )
        return

    started_at = datetime.datetime.utcnow().isoformat() + "Z"
    _running_jobs.add(job_id)
    try:
        cfg = _load_cfg()
        job = next((j for j in cfg.get("jobs", []) if j["id"] == job_id), None)
        if not job:
            app_log("ERROR", "backup_scheduler", f"Job {job_id} not found")
            return

        app_log(
            "INFO",
            "backup_scheduler",
            f"Running scheduled backup job: {job.get('name')}",
        )

        path, filename = backup_engine.create_backup(cfg)

        transferred: bool | None = None
        ftp_cfg = cfg.get("ftp", {})
        if ftp_cfg.get("enabled") and ftp_cfg.get("host"):
            try:
                transfer_file(ftp_cfg, path, filename)
                transferred = True
                app_log(
                    "INFO",
                    "backup_scheduler",
                    f"Transferred {filename} via {ftp_cfg.get('protocol', 'sftp').upper()}",
                )
            except Exception as exc:
                transferred = False
                app_log(
                    "ERROR",
                    "backup_scheduler",
                    f"Transfer failed for {filename}: {exc}",
                )
                # Transfer failure does NOT delete local backup (transferred flag only)

        record: dict[str, Any] = {
            "started_at": started_at,
            "status": "success",
            "filename": filename,
            "transferred": transferred,
            "detail": "",
        }
        _append_run(job_id, record)
        _prune_runs(job_id)
        app_log(
            "INFO",
            "backup_scheduler",
            f"Backup job complete: {filename}",
        )

    except Exception as exc:
        record = {
            "started_at": started_at,
            "status": "failed",
            "filename": "",
            "transferred": None,
            "detail": str(exc),
        }
        _append_run(job_id, record)
        app_log(
            "ERROR",
            "backup_scheduler",
            f"Backup job {job_id} failed: {exc}",
        )

    finally:
        _running_jobs.discard(job_id)
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()
        except Exception:
            pass


# ── APScheduler integration ───────────────────────────────────────────────────

_DAY_MAP = {
    "SUN": "sun",
    "MON": "mon",
    "TUE": "tue",
    "WED": "wed",
    "THU": "thu",
    "FRI": "fri",
    "SAT": "sat",
}


def _apscheduler_id(job_id: str) -> str:
    return f"backup_{job_id}"


def _register(job: dict) -> None:
    if _scheduler is None:
        return
    from apscheduler.triggers.cron import CronTrigger

    h, m = job["time"].split(":")
    day_str = ",".join(_DAY_MAP[d] for d in job["days_of_week"])
    _scheduler.add_job(
        _execute_job,
        CronTrigger(day_of_week=day_str, hour=int(h), minute=int(m)),
        args=[job["id"]],
        id=_apscheduler_id(job["id"]),
        replace_existing=True,
    )


def _unregister(job_id: str) -> None:
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(_apscheduler_id(job_id))
    except Exception:
        pass


def init_scheduler(app) -> None:
    """Create the BackgroundScheduler, register all enabled jobs, and start it.

    Called from app/__init__.py during app factory setup.
    """
    global _scheduler
    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(daemon=True)
    cfg = _load_cfg()
    active = 0
    for job in cfg.get("jobs", []):
        if job.get("enabled"):
            try:
                _register(job)
                active += 1
            except Exception as exc:
                app_log(
                    "ERROR",
                    "backup_scheduler",
                    f"Failed to register job {job.get('id', '?')}: {exc}",
                )
    _scheduler.start()
    app_log(
        "INFO",
        "backup_scheduler",
        f"Backup scheduler started with {active} active jobs",
    )
