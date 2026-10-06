"""Sales, sale items, payments, returns, held sales and shifts.

DATA INTEGRITY RULES ENFORCED HERE:
  * Completed sales are never physically deleted (only VOIDED / REFUNDED).
  * SaleItem snapshots name / sku / category / unit price / cost price so a
    later price or product rename can never rewrite history.
  * All money columns are integer kobo (see database/types.Money).
"""
from __future__ import annotations

from sqlalchemy import Index

from app.database.models.users import Terminal, User, utcnow
from app.database.types import Money, UTCDateTime
from app.extensions import db

# --- Sale status ---------------------------------------------------------
STATUS_COMPLETED = "COMPLETED"
STATUS_VOIDED = "VOIDED"
STATUS_REFUNDED = "REFUNDED"
STATUS_HELD = "HELD"
STATUS_CANCELLED = "CANCELLED"
ALL_STATUSES = (STATUS_COMPLETED, STATUS_VOIDED, STATUS_REFUNDED, STATUS_HELD, STATUS_CANCELLED)

# --- Payment methods -----------------------------------------------------
PAY_CASH = "CASH"
PAY_TRANSFER = "TRANSFER"
PAY_CARD = "CARD"
PAY_OTHER = "OTHER"
PAYMENT_METHODS = (PAY_CASH, PAY_TRANSFER, PAY_CARD, PAY_OTHER)


class Shift(db.Model):
    """Cashier working session with cash reconciliation."""

    __tablename__ = "shifts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    user = db.relationship("User", back_populates="shifts", foreign_keys=[user_id])
    terminal_id = db.Column(db.Integer, db.ForeignKey("terminals.id"), index=True)
    terminal = db.relationship("Terminal", back_populates="shifts")

    opening_cash = db.Column(Money, default=0, nullable=False)
    expected_cash = db.Column(Money)
    actual_cash = db.Column(Money)
    variance = db.Column(Money)

    cash_sales = db.Column(Money, default=0, nullable=False)
    cash_refunds = db.Column(Money, default=0, nullable=False)
    transfer_sales = db.Column(Money, default=0, nullable=False)
    card_sales = db.Column(Money, default=0, nullable=False)
    other_sales = db.Column(Money, default=0, nullable=False)
    total_sales = db.Column(Money, default=0, nullable=False)
    transaction_count = db.Column(db.Integer, default=0, nullable=False)
    discount_total = db.Column(Money, default=0, nullable=False)
    void_total = db.Column(Money, default=0, nullable=False)

    opened_at = db.Column(UTCDateTime, default=utcnow, nullable=False, index=True)
    closed_at = db.Column(UTCDateTime, index=True)
    closing_note = db.Column(db.String(255))

    is_open = db.Column(db.Boolean, default=True, nullable=False, index=True)
    closing_cashier_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    sales = db.relationship("Sale", back_populates="shift")

    def __repr__(self) -> str:
        return f"<Shift u{self.user_id} open={self.is_open}>"


class Sale(db.Model):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)
    receipt_number = db.Column(db.String(40), unique=True, nullable=False, index=True)

    sale_date = db.Column(db.Date, nullable=False, index=True)
    sale_time = db.Column(db.String(10), nullable=False)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False, index=True)

    cashier_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    cashier = db.relationship("User", back_populates="sales", foreign_keys=[cashier_id])
    cashier_name = db.Column(db.String(120), nullable=False)

    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), index=True)
    shift = db.relationship("Shift", back_populates="sales")
    terminal_id = db.Column(db.Integer, db.ForeignKey("terminals.id"), index=True)
    terminal = db.relationship("Terminal", back_populates="sales")

    subtotal = db.Column(Money, default=0, nullable=False)
    discount_amount = db.Column(Money, default=0, nullable=False)
    tax_amount = db.Column(Money, default=0, nullable=False)
    total_amount = db.Column(Money, default=0, nullable=False)
    cost_total = db.Column(Money, default=0, nullable=False)

    payment_method = db.Column(db.String(20), nullable=False, index=True)
    amount_received = db.Column(Money, default=0, nullable=False)
    change_given = db.Column(Money, default=0, nullable=False)

    status = db.Column(db.String(20), default=STATUS_COMPLETED, nullable=False, index=True)
    note = db.Column(db.String(255))

    discount_type = db.Column(db.String(20))
    discount_value = db.Column(Money)
    discount_authorised_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    voided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    voided_by = db.relationship("User", foreign_keys=[voided_by_id])
    voided_at = db.Column(UTCDateTime)
    void_reason = db.Column(db.String(255))

    receipt_printed_at = db.Column(UTCDateTime)
    drawer_opened_at = db.Column(UTCDateTime)

    items = db.relationship(
        "SaleItem", back_populates="sale", cascade="all, delete-orphan", order_by="SaleItem.id"
    )
    payments = db.relationship("Payment", back_populates="sale", cascade="all, delete-orphan")
    returns = db.relationship("ReturnRecord", back_populates="sale")

    @property
    def item_count(self) -> int:
        return sum(i.quantity for i in self.items)

    @property
    def line_count(self) -> int:
        return len(self.items)

    @property
    def is_void(self) -> bool:
        return self.status == STATUS_VOIDED

    @property
    def net_total(self):
        return self.total_amount

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "receipt_number": self.receipt_number,
            "date": self.sale_date.isoformat(),
            "time": self.sale_time,
            "cashier": self.cashier_name,
            "subtotal": self.subtotal,
            "discount": self.discount_amount,
            "tax": self.tax_amount,
            "total": self.total_amount,
            "payment_method": self.payment_method,
            "amount_received": self.amount_received,
            "change": self.change_given,
            "status": self.status,
            "items": [
                {
                    "product": i.product_name,
                    "quantity": i.quantity,
                    "unit_price": i.unit_price,
                    "line_total": i.line_total,
                }
                for i in self.items
            ],
        }

    def __repr__(self) -> str:
        return f"<Sale {self.receipt_number} {self.status}>"


class SaleItem(db.Model):
    """Line item with a frozen snapshot of the product at sale time."""

    __tablename__ = "sale_items"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id", ondelete="CASCADE"), nullable=False, index=True)
    sale = db.relationship("Sale", back_populates="items")

    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), index=True)
    product = db.relationship("Product")

    # Snapshots - these are what reports read, forever.
    product_name = db.Column(db.String(160), nullable=False, index=True)
    sku = db.Column(db.String(60))
    category_name = db.Column(db.String(120))
    unit = db.Column(db.String(20))

    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(Money, nullable=False)
    cost_price = db.Column(Money, default=0, nullable=False)
    discount_amount = db.Column(Money, default=0, nullable=False)
    tax_rate = db.Column(db.Numeric(6, 2), default=0, nullable=False)
    tax_amount = db.Column(Money, default=0, nullable=False)
    line_total = db.Column(Money, nullable=False)
    stock_deducted = db.Column(db.Boolean, default=False, nullable=False)

    returned_quantity = db.Column(db.Integer, default=0, nullable=False)

    @property
    def gross_total(self):
        return self.unit_price * self.quantity

    @property
    def line_profit(self):
        return (self.unit_price - self.cost_price) * self.quantity


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id", ondelete="CASCADE"), nullable=False, index=True)
    sale = db.relationship("Sale", back_populates="payments")

    method = db.Column(db.String(20), nullable=False, index=True)
    amount = db.Column(Money, nullable=False)
    amount_received = db.Column(Money)
    change_given = db.Column(Money)
    reference = db.Column(db.String(120))
    is_refund = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)


class ReturnRecord(db.Model):
    """A refund that references the original sale. The sale is never deleted."""

    __tablename__ = "returns"

    id = db.Column(db.Integer, primary_key=True)
    return_number = db.Column(db.String(40), unique=True, nullable=False, index=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False, index=True)
    sale = db.relationship("Sale", back_populates="returns")
    original_receipt = db.Column(db.String(40), nullable=False, index=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    user = db.relationship("User", foreign_keys=[user_id])
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    reason = db.Column(db.String(255), nullable=False)
    refund_method = db.Column(db.String(20), nullable=False, default=PAY_CASH)
    total_amount = db.Column(Money, default=0, nullable=False)
    restock = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False, index=True)
    sale_date = db.Column(db.Date, nullable=False, index=True)
    sale_time = db.Column(db.String(10), nullable=False)

    items = db.relationship("ReturnItem", back_populates="return_record", cascade="all, delete-orphan")


class ReturnItem(db.Model):
    __tablename__ = "return_items"

    id = db.Column(db.Integer, primary_key=True)
    return_id = db.Column(db.Integer, db.ForeignKey("returns.id", ondelete="CASCADE"), nullable=False, index=True)
    return_record = db.relationship("ReturnRecord", back_populates="items")

    sale_item_id = db.Column(db.Integer, db.ForeignKey("sale_items.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), index=True)
    product_name = db.Column(db.String(160), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(Money, nullable=False)
    amount = db.Column(Money, nullable=False)
    restocked = db.Column(db.Boolean, default=False, nullable=False)


class HeldSale(db.Model):
    """A parked transaction the cashier can retrieve later."""

    __tablename__ = "held_sales"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(40), unique=True, nullable=False, index=True)
    label = db.Column(db.String(120), nullable=False)

    cashier_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    cashier = db.relationship("User", back_populates="held_sales")
    terminal_id = db.Column(db.Integer, db.ForeignKey("terminals.id"), index=True)

    payload = db.Column(db.Text, nullable=False)  # JSON snapshot of the cart
    item_count = db.Column(db.Integer, default=0, nullable=False)
    subtotal = db.Column(Money, default=0, nullable=False)
    total_amount = db.Column(Money, default=0, nullable=False)

    status = db.Column(db.String(20), default="HELD", nullable=False, index=True)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False, index=True)
    retrieved_at = db.Column(UTCDateTime)


class CashDrawerEvent(db.Model):
    __tablename__ = "cash_drawer_events"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), index=True)
    terminal_id = db.Column(db.Integer, db.ForeignKey("terminals.id"), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    trigger = db.Column(db.String(40), nullable=False)  # SALE | TEST | MANUAL
    status = db.Column(db.String(20), nullable=False)   # OPENED | FAILED
    message = db.Column(db.String(255))
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False, index=True)


Index("ix_sales_date_status", Sale.sale_date, Sale.status)
Index("ix_sales_date_cashier", Sale.sale_date, Sale.cashier_id)
Index("ix_sales_date_payment", Sale.sale_date, Sale.payment_method)
Index("ix_sale_items_product", SaleItem.product_id, SaleItem.sale_id)
Index("ix_sale_items_name", SaleItem.product_name)
