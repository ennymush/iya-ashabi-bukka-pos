"""Audit logging service.

Every sensitive action writes one row. Passwords and recovery codes are
explicitly stripped so they can never leak into the audit table, the Excel
export, or the log files.
"""
from __future__ import annotations

import json

from app.database.models import AuditLog
from app.extensions import db
from app.utils.logging_config import get_logger

log = get_logger("audit")

# Any key matching these is replaced with '***' before being stored.
SENSITIVE_KEYS = {
    "password",
    "new_password",
    "old_password",
    "confirm_password",
    "password_hash",
    "recovery_code",
    "code",
    "secret",
    "token",
}

REDACTED = "***"


def _scrub(value):
    """Recursively remove sensitive keys from a dict/list payload."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                cleaned[key] = REDACTED
            else:
                cleaned[key] = _scrub(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def _dump(value) -> str | None:
    if value is None:
        return None
    scrubbed = _scrub(value)
    if isinstance(scrubbed, str):
        return scrubbed
    try:
        return json.dumps(scrubbed, default=str)
    except (TypeError, ValueError):
        return str(scrubbed)


def record(
    action: str,
    user=None,
    entity_type: str = "",
    entity_id=None,
    description: str = "",
    old_value=None,
    new_value=None,
    ip_address: str | None = None,
    commit: bool = False,
):
    """Write an audit row. Never raises - auditing must not break a sale."""
    try:
        entry = AuditLog(
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", None),
            user_role=getattr(user, "role_name", None),
            action=action,
            entity_type=entity_type or "",
            entity_id=str(entity_id) if entity_id is not None else None,
            description=(description or "")[:500],
            old_value=_dump(old_value),
            new_value=_dump(new_value),
            ip_address=ip_address,
        )
        db.session.add(entry)
        if commit:
            db.session.commit()
        return entry
    except Exception as exc:  # noqa: BLE001
        log.error("Audit write failed for action=%s: %s", action, exc)
        if commit:
            db.session.rollback()
        return None


# --- Canonical action names ------------------------------------------------
LOGIN = "LOGIN"
LOGOUT = "LOGOUT"
LOGIN_FAILED = "LOGIN_FAILED"
SESSION_TIMEOUT = "SESSION_TIMEOUT"
USER_CREATE = "USER_CREATE"
USER_UPDATE = "USER_UPDATE"
USER_DEACTIVATE = "USER_DEACTIVATE"
USER_ACTIVATE = "USER_ACTIVATE"
PASSWORD_RESET = "PASSWORD_RESET"
PERMISSIONS_UPDATE = "PERMISSIONS_UPDATE"
PRODUCT_CREATE = "PRODUCT_CREATE"
PRODUCT_UPDATE = "PRODUCT_UPDATE"
PRODUCT_DEACTIVATE = "PRODUCT_DEACTIVATE"
PRODUCT_ACTIVATE = "PRODUCT_ACTIVATE"
PRICE_CHANGE = "PRICE_CHANGE"
CATEGORY_CREATE = "CATEGORY_CREATE"
CATEGORY_UPDATE = "CATEGORY_UPDATE"
CATEGORY_DEACTIVATE = "CATEGORY_DEACTIVATE"
INVENTORY_ADJUST = "INVENTORY_ADJUST"
SALE_CREATE = "SALE_CREATE"
SALE_VOID = "SALE_VOID"
SALE_REFUND = "SALE_REFUND"
DISCOUNT_APPLIED = "DISCOUNT_APPLIED"
SHIFT_OPEN = "SHIFT_OPEN"
SHIFT_CLOSE = "SHIFT_CLOSE"
SALE_HOLD = "SALE_HOLD"
SALE_HOLD_RETRIEVE = "SALE_HOLD_RETRIEVE"
SETTINGS_UPDATE = "SETTINGS_UPDATE"
BACKUP_CREATE = "BACKUP_CREATE"
RESTORE_DATABASE = "RESTORE_DATABASE"
HARDWARE_TEST = "HARDWARE_TEST"


def query_audit(
    user_id: int | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    date_from=None,
    date_to=None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 50,
):
    """Filtered, paginated audit query used by the admin screen."""
    query = AuditLog.query
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    if action:
        query = query.filter(AuditLog.action == action)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if date_from is not None:
        query = query.filter(AuditLog.created_at >= date_from)
    if date_to is not None:
        from datetime import datetime, time

        query = query.filter(AuditLog.created_at <= datetime.combine(date_to, time.max))
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                AuditLog.description.ilike(like),
                AuditLog.username.ilike(like),
                AuditLog.entity_id.ilike(like),
                AuditLog.action.ilike(like),
            )
        )
    return query.order_by(AuditLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )


def distinct_actions() -> list[str]:
    rows = db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    return [r[0] for r in rows]
