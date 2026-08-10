"""SMTP email client — wraps stdlib smtplib for 4THealth scheduled exports."""

from __future__ import annotations

import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

from app.atomic_io import atomic_write_json

_CONFIG_PATH = Path(__file__).parent.parent / "smtp_config.json"
_lock = threading.Lock()

_DEFAULTS: dict = {
    "host": "",
    "port": 25,
    "tls_mode": "none",
    "username": "",
    "password": "",
    "from_address": "",
    "run_history_days": 30,
    "enabled": False,
}


def load_smtp_config() -> dict:
    import json

    with _lock:
        if not _CONFIG_PATH.exists():
            return dict(_DEFAULTS)
        try:
            with open(_CONFIG_PATH) as f:
                data = json.load(f)
            return {**_DEFAULTS, **data}
        except Exception:
            return dict(_DEFAULTS)


def save_smtp_config(cfg: dict) -> None:
    with _lock:
        atomic_write_json(_CONFIG_PATH, {**_DEFAULTS, **cfg})


def _parse_recipients(to: str) -> list[str]:
    return [addr.strip() for addr in to.split(",") if addr.strip()]


def _build_message(
    cfg: dict, to: str, subject: str, body_html: str, attachments: list[dict]
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_address") or cfg.get("host", "4thealth")
    msg["To"] = ", ".join(_parse_recipients(to))
    msg.attach(MIMEText(body_html, "html"))
    for att in attachments:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(att["data"])
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=att["filename"])
        part.add_header("Content-Type", att["mimetype"])
        msg.attach(part)
    return msg


def _connect(cfg: dict) -> smtplib.SMTP:
    tls = cfg.get("tls_mode", "none")
    host = cfg["host"]
    port = int(cfg.get("port", 25))
    if tls == "ssl":
        conn = smtplib.SMTP_SSL(host, port, timeout=10)
    else:
        conn = smtplib.SMTP(host, port, timeout=10)
        if tls == "starttls":
            conn.starttls()
    username = cfg.get("username", "")
    password = cfg.get("password", "")
    if username:
        conn.login(username, password)
    return conn


def send_email(
    to: str, subject: str, body_html: str, attachments: list[dict] | None = None
) -> None:
    cfg = load_smtp_config()
    if not cfg.get("enabled"):
        raise RuntimeError("SMTP not enabled — configure SMTP in Admin → Config-Diff")
    if not cfg.get("host"):
        raise RuntimeError("SMTP host not configured")
    msg = _build_message(cfg, to, subject, body_html, attachments or [])
    conn = _connect(cfg)
    try:
        conn.sendmail(msg["From"], _parse_recipients(to), msg.as_string())
    finally:
        try:
            conn.quit()
        except Exception:
            pass


def test_connection(to_address: str) -> dict:
    try:
        send_email(
            to_address,
            "4THealth SMTP Test",
            "<p>SMTP connection test from 4THealth — if you received this, SMTP is working.</p>",
        )
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
