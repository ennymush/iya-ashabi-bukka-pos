"""Authentication service.

ONE login page for everybody. The role is read from the authenticated
account on the SERVER - there is no hidden form field and no client-side role
selection, so a cashier can never escalate by tampering with the request.
"""
from __future__ import annotations

from datetime import timedelta

from flask import g, request, session

from app.audit import service as audit
from app.database.models import ROLE_ADMIN, LoginAttempt, User, utcnow
from app.extensions import db
from app.utils.logging_config import get_logger
from app.utils.security import login_throttle, verify_password

log = get_logger("auth")

SESSION_USER_KEY = "user_id"
SESSION_LAST_ACTIVITY = "last_activity"


class AuthResult:
    def __init__(self, ok: bool, message: str = "", user: User | None = None):
        self.ok = ok
        self.message = message
        self.user = user


def login(username: str, password: str) -> AuthResult:
    """Validate credentials and open a session. Records every attempt."""
    username = (username or "").strip()
    ip = request.remote_addr if request else None
    throttle_key = f"{ip}|{username.lower()}"

    if login_throttle.is_locked(throttle_key):
        _record_attempt(username, None, False, ip, "Locked out")
        return AuthResult(False, "Too many failed attempts. Please wait a few minutes and try again.")

    user = User.query.filter_by(username=username).first()

    # Always run a hash comparison so timing does not reveal whether the
    # username exists.
    stored_hash = user.password_hash if user else "scrypt:32768:8:1$invalid$invalid"
    password_ok = verify_password(stored_hash, password or "")

    if not user or not password_ok:
        login_throttle.register_failure(throttle_key)
        _record_attempt(username, user.id if user else None, False, ip, "Invalid credentials")
        audit.record(audit.LOGIN_FAILED, user=user, entity_type="User",
                     entity_id=user.id if user else username,
                     description=f"Failed login attempt for '{username}'", ip_address=ip)
        return AuthResult(False, "Invalid login credentials.")

    if not user.is_active:
        _record_attempt(username, user.id, False, ip, "Account inactive")
        audit.record(audit.LOGIN_FAILED, user=user, entity_type="User", entity_id=user.id,
                     description=f"Login blocked - account deactivated: '{username}'", ip_address=ip)
        return AuthResult(False, "This account has been deactivated. Please contact your manager.")

    login_throttle.reset(throttle_key)
    _record_attempt(username, user.id, True, ip, "OK")
    open_session(user)
    user.last_login_at = utcnow()
    db.session.commit()

    audit.record(audit.LOGIN, user=user, entity_type="User", entity_id=user.id,
                 description=f"{user.full_name} signed in as {user.role_name}", ip_address=ip)
    return AuthResult(True, "Welcome back.", user)


def open_session(user: User) -> None:
    session.clear()
    session[SESSION_USER_KEY] = user.id
    session[SESSION_LAST_ACTIVITY] = utcnow().isoformat()
    session.permanent = True


def logout(user: User | None = None) -> None:
    if user is not None:
        audit.record(audit.LOGOUT, user=user, entity_type="User", entity_id=user.id,
                     description=f"{user.full_name} signed out")
    session.clear()


def current_user_id() -> int | None:
    return session.get(SESSION_USER_KEY)


def touch_session() -> None:
    session[SESSION_LAST_ACTIVITY] = utcnow().isoformat()


def session_expired(timeout_minutes: int | None = None) -> bool:
    """Idle-timeout check based on the last recorded activity."""
    last = session.get(SESSION_LAST_ACTIVITY)
    if not last:
        return False
    if timeout_minutes is None:
        from app.database import service_settings as settings_service

        timeout_minutes = settings_service.get_int("system.session_timeout", 30) or 30
    try:
        from datetime import datetime

        last_dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return False
    return utcnow() - last_dt > timedelta(minutes=timeout_minutes)


def home_route_for(user: User) -> str:
    """Where to send this user after login - decided from the ROLE on record."""
    if user.role_name in (ROLE_ADMIN, "MANAGER"):
        return "/admin/dashboard"
    return "/pos"


def _record_attempt(username, user_id, success, ip, reason) -> None:
    try:
        db.session.add(
            LoginAttempt(username=username, user_id=user_id, success=success,
                         ip_address=ip, reason=reason)
        )
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not record login attempt: %s", exc)
        db.session.rollback()


def active_staff_count() -> int:
    return User.query.filter_by(is_active=True).count()
