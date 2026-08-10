"""Local user authentication — bcrypt hashed passwords stored in users.json.
Prepared for future AD integration by keeping an abstract authenticate() entry point.
"""

import json
import secrets
import string
from pathlib import Path

import bcrypt

USERS_FILE = Path(__file__).parent.parent / "users.json"


def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    with USERS_FILE.open() as f:
        return json.load(f)


def authenticate(username: str, password: str) -> "tuple[str, list] | None":
    """Return (role, ad_groups) on success, None on failure.

    role     — 'admin' or 'viewer'
    ad_groups — list of AD/RADIUS group strings from the reply (empty for local auth)

    If RADIUS_ENABLED=true, RADIUS is tried first.  Local bcrypt accounts
    always work as a fallback (break-glass admin access).
    """
    from app.config import Config  # lazy to avoid circular import at module load

    if Config.RADIUS_ENABLED:
        if not Config.RADIUS_HOST or not Config.RADIUS_SECRET:
            raise RuntimeError(
                "RADIUS_ENABLED=true but RADIUS_HOST or RADIUS_SECRET is not set in .env"
            )
        from app.radius_auth import authenticate as radius_authenticate

        result = radius_authenticate(
            username=username,
            password=password,
            host=Config.RADIUS_HOST,
            port=Config.RADIUS_PORT,
            secret=Config.RADIUS_SECRET,
            timeout=Config.RADIUS_TIMEOUT,
            group_admin=Config.RADIUS_GROUP_ADMIN,
            group_viewer=Config.RADIUS_GROUP_VIEWER,
            host2=Config.RADIUS_HOST_2,
            port2=Config.RADIUS_PORT_2,
        )
        if result is not None:
            return result["role"], result["ad_groups"]

    # Local bcrypt auth (always available; acts as fallback when RADIUS is enabled)
    users = _load_users()
    entry = users.get(username)
    if not entry:
        return None
    stored_hash = entry.get("password_hash", "")
    if bcrypt.checkpw(password.encode(), stored_hash.encode()):
        return entry.get("role", "viewer"), []
    return None


def validate_password_policy(password: str) -> None:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters long")
    if not any(c.islower() for c in password):
        raise ValueError("Password must include at least one lowercase letter")
    if not any(c.isupper() for c in password):
        raise ValueError("Password must include at least one uppercase letter")
    if not any(c.isdigit() for c in password):
        raise ValueError("Password must include at least one number")
    if not any(c in string.punctuation for c in password):
        raise ValueError("Password must include at least one special character")


def add_user(username: str, password: str, role: str = "viewer") -> None:
    validate_password_policy(password)
    users = _load_users()
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    users[username] = {"password_hash": hashed, "role": role}
    with USERS_FILE.open("w") as f:
        json.dump(users, f, indent=2)


def delete_user(username: str) -> bool:
    users = _load_users()
    if username not in users:
        return False
    del users[username]
    with USERS_FILE.open("w") as f:
        json.dump(users, f, indent=2)
    return True


def list_users() -> list:
    return [
        {"username": u, "role": v.get("role", "viewer")}
        for u, v in _load_users().items()
    ]


def get_user_role(username: str) -> str:
    users = _load_users()
    return users.get(username, {}).get("role", "viewer")


def generate_secret_key() -> str:
    return secrets.token_hex(32)
