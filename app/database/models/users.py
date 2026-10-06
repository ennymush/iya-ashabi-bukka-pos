"""Users, roles, permissions, sessions and staff lifecycle."""
from __future__ import annotations

from datetime import datetime, timezone

from flask_login import UserMixin

from app.database.types import Money, UTCDateTime
from app.extensions import db
from app.utils.security import hash_password, verify_password

# --- Role names ---------------------------------------------------------
ROLE_ADMIN = "ADMIN"
ROLE_MANAGER = "MANAGER"
ROLE_CASHIER = "CASHIER"
ALL_ROLES = (ROLE_ADMIN, ROLE_MANAGER, ROLE_CASHIER)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255))

    users = db.relationship("User", back_populates="role")
    permissions = db.relationship(
        "RolePermission", back_populates="role", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Role {self.name}>"


class Permission(db.Model):
    __tablename__ = "permissions"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255))

    def __repr__(self) -> str:
        return f"<Permission {self.key}>"


class RolePermission(db.Model):
    __tablename__ = "role_permissions"

    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permission_id = db.Column(db.Integer, db.ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False)

    role = db.relationship("Role", back_populates="permissions")
    permission = db.relationship("Permission")

    __table_args__ = (db.UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)


class Terminal(db.Model):
    """A physical POS machine. Version 1 registers one, but the schema
    supports many so a future multi-terminal rollout needs no redesign."""

    __tablename__ = "terminals"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    location = db.Column(db.String(160))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_seen_at = db.Column(UTCDateTime)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)

    sales = db.relationship("Sale", back_populates="terminal")
    shifts = db.relationship("Shift", back_populates="terminal")

    def __repr__(self) -> str:
        return f"<Terminal {self.code}>"


class User(UserMixin, db.Model):
    """Staff account.

    Staff are never hard-deleted once they have history: they are deactivated
    so past sales stay linked to a real, identifiable person.
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(60), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False, index=True)
    role = db.relationship("Role", back_populates="users")

    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    phone = db.Column(db.String(40))
    email = db.Column(db.String(160))

    # Cashier discount ceiling. NULL means "no discount permitted".
    max_discount_percent = db.Column(db.Numeric(6, 2))
    max_discount_amount = db.Column(Money)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)
    updated_at = db.Column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)
    deactivated_at = db.Column(UTCDateTime)
    last_login_at = db.Column(UTCDateTime)

    created_by = db.relationship("User", remote_side=[id])

    sales = db.relationship("Sale", back_populates="cashier", foreign_keys="Sale.cashier_id")
    shifts = db.relationship("Shift", back_populates="user", foreign_keys="Shift.user_id")
    audit_logs = db.relationship("AuditLog", back_populates="user")
    held_sales = db.relationship("HeldSale", back_populates="cashier")

    # ---------------- password ----------------
    def set_password(self, raw: str) -> None:
        self.password_hash = hash_password(raw)

    def check_password(self, raw: str) -> bool:
        return verify_password(self.password_hash, raw)

    # ---------------- roles / permissions ----------------
    @property
    def role_name(self) -> str:
        return self.role.name if self.role else ""

    def has_any_permission(self, *keys: str) -> bool:
        if self.is_admin:
            return True
        if not self.role:
            return False
        granted = {rp.permission.key for rp in self.role.permissions if rp.permission}
        return any(k in granted for k in keys)

    def has_all_permissions(self, *keys: str) -> bool:
        if self.is_admin:
            return True
        if not self.role:
            return False
        granted = {rp.permission.key for rp in self.role.permissions if rp.permission}
        return all(k in granted for k in keys)

    @property
    def is_admin(self) -> bool:
        return self.role_name == ROLE_ADMIN

    @property
    def is_manager(self) -> bool:
        return self.role_name == ROLE_MANAGER

    @property
    def is_cashier(self) -> bool:
        return self.role_name == ROLE_CASHIER

    def get_id(self) -> str:
        return str(self.id)

    @property
    def initials(self) -> str:
        parts = [p for p in (self.full_name or "").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def __repr__(self) -> str:
        return f"<User {self.username} {self.role_name}>"


class LoginAttempt(db.Model):
    """Persisted login history so the owner can audit access to the till."""

    __tablename__ = "login_attempts"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(60), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    success = db.Column(db.Boolean, default=False, nullable=False)
    ip_address = db.Column(db.String(64))
    reason = db.Column(db.String(160))
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False, index=True)
