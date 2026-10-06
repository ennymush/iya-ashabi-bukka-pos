"""Categories and products.

Products are NEVER hardcoded in the frontend. The cashier interface reads
them from SQLite, so an admin change is live on the next screen refresh.
"""
from __future__ import annotations

from sqlalchemy import Index

from app.database.models.users import User, utcnow
from app.database.types import Money, UTCDateTime
from app.extensions import db

LOW_STOCK = "LOW STOCK"
OUT_OF_STOCK = "OUT OF STOCK"
IN_STOCK = "IN STOCK"


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255))
    color = db.Column(db.String(20))
    icon = db.Column(db.String(40))
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)
    updated_at = db.Column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    products = db.relationship("Product", back_populates="category")

    def __repr__(self) -> str:
        return f"<Category {self.name}>"


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    sku = db.Column(db.String(60), unique=True, index=True)
    barcode = db.Column(db.String(64), index=True)
    product_code = db.Column(db.String(60), index=True)

    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), index=True)
    category = db.relationship("Category", back_populates="products")

    description = db.Column(db.String(500))
    preparation_notes = db.Column(db.String(500))
    unit = db.Column(db.String(20), default="portion", nullable=False)

    # Money columns hold integer kobo (see database/types.Money)
    cost_price = db.Column(Money, default=0, nullable=False)
    selling_price = db.Column(Money, default=0, nullable=False)
    tax_rate = db.Column(db.Numeric(6, 2), default=0, nullable=False)

    stock_quantity = db.Column(db.Integer, default=0, nullable=False)
    minimum_stock = db.Column(db.Integer, default=0, nullable=False)

    image_filename = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False)
    updated_at = db.Column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    price_history = db.relationship(
        "PriceHistory", back_populates="product", cascade="all, delete-orphan"
    )
    movements = db.relationship(
        "InventoryMovement", back_populates="product", cascade="all, delete-orphan"
    )

    # ---------------- stock status ----------------
    @property
    def stock_status(self) -> str:
        if self.stock_quantity <= 0:
            return OUT_OF_STOCK
        if self.stock_quantity <= (self.minimum_stock or 0):
            return LOW_STOCK
        return IN_STOCK

    @property
    def is_low(self) -> bool:
        return self.stock_status in (LOW_STOCK, OUT_OF_STOCK)

    @property
    def margin_percent(self):
        if not self.selling_price:
            return 0
        return (self.selling_price - self.cost_price) / self.selling_price * 100

    def __repr__(self) -> str:
        return f"<Product {self.name}>"


class PriceHistory(db.Model):
    """Immutable record of every price change. Old sales are never rewritten."""

    __tablename__ = "price_history"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    product = db.relationship("Product", back_populates="price_history")

    old_price = db.Column(Money, nullable=False)
    new_price = db.Column(Money, nullable=False)
    old_cost = db.Column(Money)
    new_cost = db.Column(Money)

    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    changed_by = db.relationship("User")
    reason = db.Column(db.String(255))
    created_at = db.Column(UTCDateTime, default=utcnow, nullable=False, index=True)


Index("ix_products_name_active", Product.name, Product.is_active)
Index("ix_products_barcode_active", Product.barcode, Product.is_active)
