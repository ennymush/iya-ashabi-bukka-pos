"""System tables: settings, audit log, backup log, customer-display state."""
from __future__ import annotations

import json

from app.database.models.users import User, utcnow
from app.database.types import UTCDateTime
from app.extensions import db


class Setting(db.Model):
    """Key/value configuration owned by the admin.

    Business name, tagline, address, logo path and every hardware option
    live here, so nothing is hardcoded in the source.
    """

    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(120), unique=True, nullable=False, index=True)
    value = db.Column(db.Text)
    category = db.Column(db.String(60), default="general", nullable=False, index=True)
    description = db.Column(db.String(255))
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    @staticmethod
    def get(key: str, default=None):
        row = Setting.query.filter_by(key=key).first()
        return row.value if row and row.value is not None else default

    @staticmethod
    def get_bool(key: str, default: bool = False) -> bool:
        raw = Setting.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def get_int(key: str, default: int = 0) -> int:
        raw = Setting.get(key)
        try:
            return int(raw) if raw is not None else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def set(key: str, value, user_id: int | None = None, category: str = "general",
            description: str | None = None) -> "Setting":
        row = Setting.query.filter_by(key=key).first()
        if row is None:
            row = Setting(key=key, category=category, description=description)
            db.session.add(row)
        row.value = None if value is None else str(value)
        if category:
            row.category = category
        if description:
            row.description = description
        if user_id is not None:
            row.updated_by_id = user_id
        return row

    @staticmethod
    def all_by_category(category: str) -> dict:
        return {
            r.key: r.value
            for r in Setting.query.filter_by(category=category).all()
        }


class AuditLog(db.Model):
    """Append-only trail of every sensitive action.

    Passwords are NEVER written here - see services/audit.py which strips them.
    """

    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    user = db.relationship("User", back_populates="audit_logs")
    username = db.Column(db.String(60), index=True)
    user_role = db.Column(db.String(20))

    action = db.Column(db.String(60), nullable=False, index=True)
    entity_type = db.Column(db.String(60), index=True)
    entity_id = db.Column(db.String(60))
    description = db.Column(db.String(500))
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    ip_address = db.Column(db.String(64))
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False, index=True)

    def old_json(self):
        try:
            return json.loads(self.old_value) if self.old_value else None
        except (TypeError, ValueError):
            return None

    def new_json(self):
        try:
            return json.loads(self.new_value) if self.new_value else None
        except (TypeError, ValueError):
            return None


class BackupLog(db.Model):
    __tablename__ = "backup_logs"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(500))
    size_bytes = db.Column(db.Integer, default=0, nullable=False)
    kind = db.Column(db.String(20), default="manual", nullable=False, index=True)  # manual|auto|pre_restore
    status = db.Column(db.String(20), default="SUCCESS", nullable=False, index=True)
    message = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    user = db.relationship("User")
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False, index=True)


class DisplayState(db.Model):
    """Shared customer-display payload.

    The customer screen polls this (or the SSE stream) so it stays in step
    with the cashier screen. Only customer-safe data is ever written here.
    """

    __tablename__ = "display_states"

    id = db.Column(db.Integer, primary_key=True)
    terminal_id = db.Column(db.Integer, db.ForeignKey("terminals.id"), index=True)

    mode = db.Column(db.String(20), default="WELCOME", nullable=False)
    payload = db.Column(db.Text, default="{}")
    transaction_total = db.Column(db.String(20))
    version = db.Column(db.Integer, default=0, nullable=False)
    updated_at = db.Column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def set_payload(self, data: dict) -> None:
        self.payload = json.dumps(data)
        self.version = (self.version or 0) + 1
        self.updated_at = utcnow()

    def get_payload(self) -> dict:
        try:
            return json.loads(self.payload) if self.payload else {}
        except (TypeError, ValueError):
            return {}
