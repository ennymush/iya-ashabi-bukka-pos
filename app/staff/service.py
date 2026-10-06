"""Staff account service.

The business owner manages every employee from the app - no code changes, no
database editing. Accounts with history are DEACTIVATED, never deleted, so past
sales stay attached to a real person.
"""
from __future__ import annotations

from app.audit import service as audit
from app.database.models import ROLE_ADMIN, ROLE_CASHIER, ROLE_MANAGER, Sale, Role, User
from app.extensions import db
from app.utils.errors import ValidationError
from app.utils.logging_config import get_logger
from app.utils.money import to_decimal
from app.utils.security import hash_password, password_problem

log = get_logger("staff")


def _clean_username(value: str) -> str:
    username = (value or "").strip().lower()
    if not username:
        raise ValidationError("Username is required.")
    if len(username) < 3:
        raise ValidationError("Username must be at least 3 characters.")
    if len(username) > 60:
        raise ValidationError("Username must be 60 characters or fewer.")
    if not username.replace("_", "").replace(".", "").replace("-", "").isalnum():
        raise ValidationError("Username may only use letters, numbers, dot, dash and underscore.")
    return username


def _clean_name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise ValidationError("Full name is required.")
    if len(name) > 120:
        raise ValidationError("Full name must be 120 characters or fewer.")
    return name


def _role(name: str) -> Role:
    role_name = (name or "").strip().upper()
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        raise ValidationError(f"'{role_name}' is not a valid role.")
    return role


def create_staff(actor, data: dict) -> User:
    """ADMIN > STAFF > ADD STAFF."""
    full_name = _clean_name(data.get("full_name"))
    username = _clean_username(data.get("username"))

    if User.query.filter_by(username=username).first():
        raise ValidationError(f"The username '{username}' is already taken.")

    password = data.get("password") or ""
    problem = password_problem(password, username)
    if problem:
        raise ValidationError(problem)

    role = _role(data.get("role") or ROLE_CASHIER)

    user = User(
        full_name=full_name,
        username=username,
        role_id=role.id,
        is_active=str(data.get("is_active", "1")).lower() in {"1", "true", "yes", "on"},
        phone=(data.get("phone") or "").strip()[:40] or None,
        email=(data.get("email") or "").strip()[:160] or None,
        created_by_id=getattr(actor, "id", None),
        must_change_password=bool(data.get("must_change_password")),
    )
    user.set_password(password)

    if data.get("max_discount_percent") not in (None, ""):
        try:
            user.max_discount_percent = float(str(data["max_discount_percent"]))
        except (TypeError, ValueError):
            raise ValidationError("Maximum discount percentage must be a number.")
    if data.get("max_discount_amount") not in (None, ""):
        user.max_discount_amount = to_decimal(data["max_discount_amount"])

    db.session.add(user)
    db.session.flush()

    audit.record(
        audit.USER_CREATE, user=actor, entity_type="User", entity_id=user.id,
        description=(
            f"{getattr(actor, 'full_name', 'System')} created {role.name.lower()} "
            f"'{full_name}' (username: {username})"
        ),
        new_value={
            "full_name": full_name, "username": username, "role": role.name,
            "is_active": user.is_active,
            "max_discount_percent": str(user.max_discount_percent or "0"),
        },
        ip_address=_ip(),
    )
    db.session.commit()
    return user


def update_staff(actor, user: User, data: dict) -> User:
    """Edit profile and role. Password changes go through reset_password()."""
    old = {
        "full_name": user.full_name,
        "role": user.role_name,
        "phone": user.phone,
        "email": user.email,
        "max_discount_percent": str(user.max_discount_percent or ""),
        "max_discount_amount": str(user.max_discount_amount or ""),
    }

    if "full_name" in data:
        user.full_name = _clean_name(data.get("full_name"))
    if "phone" in data:
        user.phone = (data.get("phone") or "").strip()[:40] or None
    if "email" in data:
        user.email = (data.get("email") or "").strip()[:160] or None
    if "max_discount_percent" in data:
        raw = data.get("max_discount_percent")
        user.max_discount_percent = float(str(raw)) if raw not in (None, "") else None
    if "max_discount_amount" in data:
        raw = data.get("max_discount_amount")
        user.max_discount_amount = to_decimal(raw) if raw not in (None, "") else None

    if "role" in data and data["role"]:
        new_role = _role(data["role"])
        # Never allow the last active admin to be demoted or blocked - that
        # would lock the owner out of their own business.
        if user.role_id != new_role.id:
            if user.role_name == ROLE_ADMIN and _active_admin_count() <= 1:
                raise ValidationError(
                    "This is the only active administrator. Create another admin first."
                )
        user.role_id = new_role.id

    if "is_active" in data:
        wants_active = str(data.get("is_active")).lower() in {"1", "true", "yes", "on"}
        if not wants_active and user.is_active:
            if user.role_name == ROLE_ADMIN and _active_admin_count() <= 1:
                raise ValidationError("You cannot deactivate the only active administrator.")
            if user.id == getattr(actor, "id", None):
                raise ValidationError("You cannot deactivate your own account.")
        user.is_active = wants_active

    db.session.flush()
    new = {
        "full_name": user.full_name,
        "role": user.role_name,
        "phone": user.phone,
        "email": user.email,
        "max_discount_percent": str(user.max_discount_percent or ""),
        "max_discount_amount": str(user.max_discount_amount or ""),
    }
    audit.record(
        audit.USER_UPDATE, user=actor, entity_type="User", entity_id=user.id,
        description=f"{getattr(actor, 'full_name', 'System')} updated staff '{user.full_name}'",
        old_value=old, new_value=new, ip_address=_ip(),
    )
    db.session.commit()
    return user


def set_active(actor, user: User, active: bool) -> User:
    """Deactivate/reactivate. Historical sales are never removed."""
    if not active:
        if user.role_name == ROLE_ADMIN and _active_admin_count() <= 1:
            raise ValidationError("You cannot deactivate the only active administrator.")
        if user.id == getattr(actor, "id", None):
            raise ValidationError("You cannot deactivate your own account.")
    was_active = user.is_active
    user.is_active = active
    db.session.flush()
    audit.record(
        audit.USER_ACTIVATE if active else audit.USER_DEACTIVATE,
        user=actor, entity_type="User", entity_id=user.id,
        description=(
            f"{getattr(actor, 'full_name', 'System')} "
            f"{'reactivated' if active else 'deactivated'} {user.full_name} "
            f"(username: {user.username})"
        ),
        old_value={"is_active": was_active}, new_value={"is_active": active}, ip_address=_ip(),
    )
    db.session.commit()
    return user


def reset_password(actor, user: User, new_password: str, must_change: bool = False) -> User:
    problem = password_problem(new_password, user.username)
    if problem:
        raise ValidationError(problem)
    user.set_password(new_password)
    user.must_change_password = must_change
    db.session.flush()
    # The audit log records THAT a reset happened, never the password itself.
    audit.record(
        audit.PASSWORD_RESET, user=actor, entity_type="User", entity_id=user.id,
        description=(
            f"{getattr(actor, 'full_name', 'System')} reset the password for "
            f"{user.full_name} (username: {user.username})"
        ),
        ip_address=_ip(),
    )
    db.session.commit()
    return user


def can_delete(user: User) -> tuple[bool, str]:
    """Delete is only safe when the account has never been used."""
    if Sale.query.filter_by(cashier_id=user.id).count():
        return False, "This account has sales history and must be deactivated, not deleted."
    return True, ""


def delete_staff(actor, user: User) -> None:
    allowed, reason = can_delete(user)
    if not allowed:
        raise ValidationError(reason)
    if user.id == getattr(actor, "id", None):
        raise ValidationError("You cannot delete your own account.")

    username = user.username
    full_name = user.full_name
    db.session.delete(user)
    audit.record(
        "USER_DELETE", user=actor, entity_type="User", entity_id=user.id,
        description=(
            f"{getattr(actor, 'full_name', 'System')} deleted unused staff account "
            f"'{full_name}' (username: {username})"
        ),
        ip_address=_ip(),
    )
    db.session.commit()


def _active_admin_count() -> int:
    admin_role = Role.query.filter_by(name=ROLE_ADMIN).first()
    if admin_role is None:
        return 0
    return User.query.filter_by(role_id=admin_role.id, is_active=True).count()


def staff_stats(user: User) -> dict:
    """Activity figures shown on the staff screen."""
    from datetime import date

    base = Sale.query.filter_by(cashier_id=user.id)

    def _sum(*extra):
        """Completed-sales total, optionally narrowed by an extra criterion.

        Accepts positional SQLAlchemy criteria (``Sale.sale_date == today``)
        rather than keyword arguments, which ``Query.filter`` does not support.
        """
        return (
            db.session.query(db.func.coalesce(db.func.sum(Sale.total_amount), 0))
            .filter(Sale.cashier_id == user.id, Sale.status == "COMPLETED", *extra)
            .scalar()
        )

    return {
        "transactions": base.count(),
        "total_sales": _sum(),
        "today_transactions": base.filter(Sale.sale_date == date.today()).count(),
        "today_sales": _sum(Sale.sale_date == date.today()),
        "last_login": user.last_login_at,
        "created_at": user.created_at,
    }


def _ip():
    """Client IP, or None outside a request (CLI, tests, background jobs)."""
    try:
        from flask import request

        return getattr(request, "remote_addr", None)
    except RuntimeError:
        return None