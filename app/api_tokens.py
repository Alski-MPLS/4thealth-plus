"""API token management — create, list, revoke, and validate bearer tokens.

Tokens are stored as SHA-256 hashes in api_tokens.json (gitignored).
The plaintext token is returned exactly once at creation time and never stored.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import uuid
from pathlib import Path
from typing import Optional

from app.atomic_io import atomic_write_json

_TOKENS_PATH = Path(__file__).parent.parent / "api_tokens.json"
_lock = threading.Lock()

TOKEN_PREFIX = "4th_"


def _load() -> list[dict]:
    if not _TOKENS_PATH.exists():
        return []
    try:
        with open(_TOKENS_PATH) as f:
            data = json.load(f)
        return data.get("tokens", [])
    except Exception:
        return []


def _save(tokens: list[dict]) -> None:
    atomic_write_json(_TOKENS_PATH, {"tokens": tokens})


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_token(name: str, created_by: str) -> tuple[str, dict]:
    """Create a new token. Returns (plaintext, record) — plaintext is shown once."""
    raw = TOKEN_PREFIX + secrets.token_hex(32)
    record = {
        "id": str(uuid.uuid4()),
        "name": name.strip(),
        "token_hash": _hash(raw),
        "created_by": created_by,
        "enabled": True,
    }
    with _lock:
        tokens = _load()
        tokens.append(record)
        _save(tokens)
    # Return a copy without the hash for the API response
    safe = {k: v for k, v in record.items() if k != "token_hash"}
    return raw, safe


def list_tokens() -> list[dict]:
    """Return all tokens, never including the hash."""
    with _lock:
        tokens = _load()
    return [{k: v for k, v in t.items() if k != "token_hash"} for t in tokens]


def revoke_token(token_id: str) -> bool:
    """Delete a token by ID. Returns True if found and removed."""
    with _lock:
        tokens = _load()
        original_len = len(tokens)
        tokens = [t for t in tokens if t.get("id") != token_id]
        if len(tokens) == original_len:
            return False
        _save(tokens)
    return True


def validate_token(raw: str) -> Optional[dict]:
    """Validate a bearer token. Returns the token record (without hash) or None."""
    if not raw:
        return None
    h = _hash(raw)
    with _lock:
        tokens = _load()
    for t in tokens:
        if t.get("token_hash") == h and t.get("enabled", True):
            return {k: v for k, v in t.items() if k != "token_hash"}
    return None
