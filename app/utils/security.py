"""Security helpers: CSRF tokens, password hashing policy, login throttling."""
from __future__ import annotations

import hmac
import re
import secrets
import time
from functools import wraps

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


# --------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------
def hash_password(raw: str) -> str:
    """Salted Werkzeug hash (scrypt by default on Werkzeug 3.x)."""
    return generate_password_hash(raw, method="scrypt:32768:8:1")


def verify_password(stored_hash: str, raw: str) -> bool:
    if not stored_hash or not raw:
        return False
    try:
        return check_password_hash(stored_hash, raw)
    except (ValueError, TypeError):
        return False


def password_problem(password: str, username: str = "") -> str | None:
    """Return a human-readable problem, or None when the password is acceptable."""
    if not password or len(password) < 6:
        return "Password must be at least 6 characters long."
    if len(password) > 128:
        return "Password must be 128 characters or fewer."
    if password.lower() in {"password", "123456", "admin", "pos", "changeme"}:
        return "That password is too easy to guess. Please choose another."
    if username and username.lower() in password.lower() and len(username) >= 4:
        return "Password must not contain your username."
    return None


def generate_recovery_code() -> str:
    """Human friendly recovery code, e.g. ``IAB-4F7K-92QX-M3TD``.

    Only a salted hash of this value is ever stored.
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "IAB-" + "-".join(groups)


def normalise_recovery_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


# --------------------------------------------------------------------------
# CSRF
# --------------------------------------------------------------------------
def generate_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token(token: str | None) -> bool:
    expected = session.get(CSRF_SESSION_KEY)
    if not expected or not token:
        return False
    return hmac.compare_digest(str(expected), str(token))


def csrf_protect() -> None:
    """``before_request`` hook rejecting cross-site state changing requests."""
    if request.method in SAFE_METHODS:
        return
    if current_app.config.get("TESTING"):
        return
    if request.endpoint in {"static", "customer.display_stream"}:
        return
    sent = request.form.get(CSRF_FORM_FIELD) or request.headers.get(CSRF_HEADER)
    # The login form is the only anonymous form; a session token is issued on GET.
    if not validate_csrf_token(sent):
        abort(400, description="Your session expired. Please refresh the page and try again.")


# --------------------------------------------------------------------------
# Login rate limiting (in-memory, per process - adequate for one local POS)
# --------------------------------------------------------------------------
class LoginThrottle:
    def __init__(self) -> None:
        self._attempts: dict[str, list[float]] = {}

    def _prune(self, key: str, window: int) -> list[float]:
        now = time.time()
        hits = [t for t in self._attempts.get(key, []) if now - t < window]
        self._attempts[key] = hits
        return hits

    def is_locked(self, key: str) -> bool:
        window = 60 * current_app.config["LOGIN_LOCKOUT_MINUTES"]
        return len(self._prune(key, window)) >= current_app.config["LOGIN_MAX_ATTEMPTS"]

    def register_failure(self, key: str) -> None:
        window = 60 * current_app.config["LOGIN_LOCKOUT_MINUTES"]
        self._prune(key, window).append(time.time())
        self._attempts[key] = self._attempts.get(key, [])

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)


login_throttle = LoginThrottle()


# --------------------------------------------------------------------------
# Authorisation helpers
# --------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def has_permission(*keys: str) -> bool:
    user = getattr(g, "user", None)
    if user is None:
        return False
    return user.has_any_permission(*keys)


def permission_required(*keys: str):
    """Server-side gate. Hiding a button is never the security control."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if has_permission(*keys):
                return view(*args, **kwargs)
            return abort(403)

        return wrapped

    return decorator


def role_required(*roles: str):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if getattr(g, "user", None) and g.user.role in roles:
                return view(*args, **kwargs)
            return abort(403)

        return wrapped

    return decorator
