"""Backup engine — AES-256 ZIP creation, file collection, retention pruning."""

from __future__ import annotations

import datetime
import json
import socket
import threading
from pathlib import Path

import pyzipper

from app.atomic_io import atomic_write_json

_BASE_DIR = Path(__file__).parent.parent
_CONFIG_PATH = _BASE_DIR / "backup_config.json"
_backup_lock = threading.Lock()

_BACKUP_FILES = [
    ".env",
    "users.json",
    "groups.json",
    "certs/cert.pem",
    "certs/key.pem",
    "infra_targets.json",
    "policy_db.json",
    "app_settings.json",
    "api_tokens.json",
    "smtp_config.json",
    "config_diff_jobs.json",
    "device_review_jobs.json",
    "backup_config.json",
    "summary_history.json",
]

_DEFAULT_CONFIG: dict = {
    "password": "",
    "backup_dir": "/var/backups/4thealth",
    "max_files": 20,
    "exclude_tls_key": False,
    "ftp": {
        "enabled": False,
        "protocol": "sftp",
        "host": "",
        "port": 22,
        "username": "",
        "password": "",
        "remote_dir": "/backups/4thealth",
    },
    "jobs": [],
}


# ── Config persistence ────────────────────────────────────────────────────────


def load_config() -> dict:
    if not _CONFIG_PATH.exists():
        return dict(_DEFAULT_CONFIG)
    try:
        with open(_CONFIG_PATH) as f:
            data = json.load(f)
        # Merge defaults so new keys appear on old configs
        merged = dict(_DEFAULT_CONFIG)
        merged.update(data)
        # Deep-merge ftp sub-dict so partial on-disk values don't drop defaults
        if "ftp" in data and isinstance(data.get("ftp"), dict):
            merged["ftp"] = {**_DEFAULT_CONFIG["ftp"], **data["ftp"]}
        return merged
    except Exception:
        return dict(_DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    if not cfg.get("password"):
        raise ValueError("Backup password must not be empty")
    atomic_write_json(_CONFIG_PATH, cfg)


# ── Filename ──────────────────────────────────────────────────────────────────


def _make_filename() -> str:
    hostname = socket.gethostname().upper()
    now = datetime.datetime.now().astimezone()
    return f"{hostname}-BACKUP_{now.strftime('%Y-%m-%d_%H%M')}.zip"


# ── Retention ─────────────────────────────────────────────────────────────────


def _prune_backups(backup_dir: Path, max_files: int) -> None:
    archives = sorted(
        backup_dir.glob("*-BACKUP_*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in archives[max_files:]:
        try:
            old.unlink()
        except Exception:
            pass


# ── Core backup ───────────────────────────────────────────────────────────────


def create_backup(config: dict) -> tuple[Path, str]:
    """Write AES-256 ZIP to backup_dir, prune to max_files, return (path, filename).

    Thread-safe — acquires _backup_lock for the duration.
    """
    with _backup_lock:
        password: str = config.get("password", "")
        backup_dir = Path(config.get("backup_dir", "/var/backups/4thealth"))
        max_files = int(config.get("max_files", 20))
        exclude_tls_key = bool(config.get("exclude_tls_key", False))

        backup_dir.mkdir(parents=True, exist_ok=True)
        filename = _make_filename()
        dest = backup_dir / filename

        files = list(_BACKUP_FILES)
        if exclude_tls_key:
            files = [f for f in files if f != "certs/key.pem"]

        with pyzipper.AESZipFile(
            dest,
            "w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as zf:
            zf.setpassword(password.encode())
            for rel in files:
                src = _BASE_DIR / rel
                if src.exists():
                    zf.write(src, rel)

        _prune_backups(backup_dir, max_files)
        return dest, filename
