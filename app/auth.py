"""Authentication and role-based access control.

Three roles, matching how a hiring platform is actually used:

    candidate   applies, uploads their own resume, sees their own match and skill-gap
                report, practises interviews. Never sees another candidate's data.
    recruiter   runs the hiring workflow: candidates, jobs, matching, interviews, ATS,
                dashboard.
    admin       everything a recruiter can do, plus user management, the database
                console and the audit log.

Passwords are hashed with PBKDF2-HMAC-SHA256 from Python's standard library at 260,000
iterations with a per-user random salt — the same construction Django uses by default.
A login returns a random bearer token; the database stores only its SHA-256, so a leaked
database does not leak live sessions. No third-party crypto package is required.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, FrozenSet, Optional

ROLES = ("candidate", "recruiter", "admin")
PBKDF2_ITERATIONS = 260_000
SESSION_HOURS = 12
MIN_PASSWORD = 8

# --------------------------------------------------------------------------- #
# permissions — one place that says who may do what
# --------------------------------------------------------------------------- #
PERMISSIONS: Dict[str, FrozenSet[str]] = {
    "candidate": frozenset({
        "profile.read_own", "resume.upload_own", "match.read_own", "gap.read_own",
        "jobs.read", "interview.practice", "voice.practice", "survey.submit",
    }),
    "recruiter": frozenset({
        "jobs.read", "jobs.write", "candidates.read", "resume.upload",
        "match.run", "match.read", "gap.read", "interview.run", "interview.read",
        "ats.read", "ats.write", "voice.run", "dashboard.read", "survey.submit",
        "survey.read",
    }),
}
PERMISSIONS["admin"] = PERMISSIONS["recruiter"] | frozenset({
    "users.read", "users.write", "db.read", "audit.read", "system.read",
})


class AuthError(Exception):
    """Raised for a failed login, a bad token, or a missing permission."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


def can(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, frozenset())


def require(role: str, permission: str) -> None:
    if not can(role, permission):
        raise AuthError(f"the {role} role is not allowed to do this ({permission})", 403)


# --------------------------------------------------------------------------- #
# passwords
# --------------------------------------------------------------------------- #
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_credentials(email: str, password: str) -> None:
    if not EMAIL_RE.match(email or ""):
        raise AuthError("enter a valid email address", 422)
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"password must be at least {MIN_PASSWORD} characters", 422)
    if password.lower() == password or not re.search(r"\d", password):
        raise AuthError("password needs at least one capital letter and one number", 422)


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), bytes.fromhex(salt),
                                 int(iterations))
    return hmac.compare_digest(digest.hex(), expected)   # constant time


# --------------------------------------------------------------------------- #
# session tokens
# --------------------------------------------------------------------------- #
def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def expiry(hours: int = SESSION_HOURS) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def is_expired(expires_at: str) -> bool:
    when = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= when


def bearer(header: Optional[str]) -> str:
    if not header or not header.lower().startswith("bearer "):
        raise AuthError("sign in first — no bearer token was sent")
    return header.split(" ", 1)[1].strip()
