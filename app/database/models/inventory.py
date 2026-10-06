"""Inventory movements.

Inventory is never changed silently: every adjustment writes a movement row.
"""
from __future__ import annotations

from app.database.models.users import User, utcnow
from app.database.types import UTCDateTime
from app.extensions import db

# Controlled reason codes
REASON_OPENING = "Opening Stock"
REASON_DELIVERY = "New Delivery"
REASON_DAMAGED = "Damaged"
REASON_SPOILAGE = "Spoilage"
REASON_CORRECTION = "Correction"
REASON_RETURN = "Return"
REASON_SALE = "Sale"
REASON_VOID = "Void Reversal"
REASON_OTHER = "Other"

MOVEMENT_REASONS = (
    REASON_OPENING,
    REASON_DELIVERY,
    REASON_DAMAGED,
    REASON_SPOILAGE,
    REASON_CORRECTION,
    REASON_RETURN,
    REASON_SALE,
    REASON_VOID,
    REASON_OTHER,
)


class InventoryMovement(db.Model):
    __tablename__ = "inventory_movements"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    product = db.relationship("Product", back_populates="movements")

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    user = db.relationship("User")

    old_quantity = db.Column(db.Integer, nullable=False)
    new_quantity = db.Column(db.Integer, nullable=False)
    difference = db.Column(db.Integer, nullable=False)

    reason = db.Column(db.String(60), nullable=False, index=True)
    note = db.Column(db.String(255))
    reference = db.Column(db.String(60), index=True)

    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<InventoryMovement p{self.product_id} {self.difference:+d}>"
