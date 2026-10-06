"""Cart engine.

The cart lives in the Flask session so it survives a page refresh and needs no
database churn while the cashier shops. IMPORTANT: nothing sent by the browser
is trusted. On completion every line is re-priced from the Product row and all
totals are recomputed on the server.
"""
from __future__ import annotations

import json
from decimal import Decimal

from app.database.models import Product

from app.database.types import minor_of
from app.extensions import db
from app.utils.money import apply_tax, from_minor
from app.utils.errors import StockError, ValidationError

CART_KEY = "cart_lines"
DISCOUNT_KEY = "cart_discount"
CUSTOMER_KEY = "cart_customer"


class CartLine:
    __slots__ = ("product_id", "quantity", "note")

    def __init__(self, product_id: int, quantity: int = 1, note: str = ""):
        self.product_id = int(product_id)
        self.quantity = max(1, int(quantity))
        self.note = note or ""

    def to_dict(self) -> dict:
        return {"product_id": self.product_id, "quantity": self.quantity, "note": self.note}

    @staticmethod
    def from_dict(data: dict) -> "CartLine":
        return CartLine(data["product_id"], data.get("quantity", 1), data.get("note", ""))

    def as_json(self) -> str:
        return json.dumps(self.to_dict())


def _session_cart(session) -> list[CartLine]:
    raw = session.get(CART_KEY, [])
    lines = []
    for item in raw:
        try:
            lines.append(CartLine.from_dict(item))
        except (KeyError, TypeError, ValueError):
            continue
    return lines


def save(session, lines: list[CartLine]) -> None:
    session[CART_KEY] = [line.to_dict() for line in lines]
    session.modified = True


def clear(session) -> None:
    for key in (CART_KEY, DISCOUNT_KEY, CUSTOMER_KEY):
        session.pop(key, None)
    session.modified = True


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
def add(session, product_id: int, quantity: int = 1, allow_negative: bool = False) -> list[CartLine]:
    product = db.session.get(Product, int(product_id))
    if product is None or not product.is_active:
        raise ValidationError("Product not found.")

    quantity = max(1, int(quantity))
    lines = _session_cart(session)

    existing = next((ln for ln in lines if ln.product_id == product.id), None)
    new_total = (existing.quantity if existing else 0) + quantity

    _check_stock(product, new_total, allow_negative)

    if existing:
        existing.quantity = new_total
    else:
        lines.append(CartLine(product.id, quantity))

    save(session, lines)
    return lines


def _check_stock(product: Product, quantity: int, allow_negative: bool) -> None:
    if allow_negative:
        return
    if product.stock_quantity <= 0:
        raise StockError(f"{product.name} is out of stock.")
    if quantity > product.stock_quantity:
        raise StockError(
            f"Only {product.stock_quantity} of {product.name} left in stock."
        )


def set_quantity(session, product_id: int, quantity: int, allow_negative: bool = False) -> list[CartLine]:
    lines = _session_cart(session)
    product = db.session.get(Product, int(product_id))
    if product is None:
        raise ValidationError("Product not found.")

    quantity = int(quantity)
    if quantity <= 0:
        return remove(session, product_id)

    _check_stock(product, quantity, allow_negative)
    for line in lines:
        if line.product_id == product.id:
            line.quantity = quantity
            break
    else:
        lines.append(CartLine(product.id, quantity))
    save(session, lines)
    return lines


def increment(session, product_id: int, step: int = 1, allow_negative: bool = False):
    lines = _session_cart(session)
    line = next((ln for ln in lines if ln.product_id == int(product_id)), None)
    current = line.quantity if line else 0
    return set_quantity(session, product_id, current + step, allow_negative)


def remove(session, product_id: int) -> list[CartLine]:
    lines = [ln for ln in _session_cart(session) if ln.product_id != int(product_id)]
    save(session, lines)
    return lines


def clear_cart(session) -> None:
    clear(session)


def set_note(session, product_id: int, note: str):
    lines = _session_cart(session)
    for line in lines:
        if line.product_id == int(product_id):
            line.note = note[:200]
    save(session, lines)
    return lines


# ---------------------------------------------------------------------------
# Discounts
# ---------------------------------------------------------------------------
def set_discount(session, discount_type: str | None, value) -> None:
    """discount_type is 'PERCENT', 'FIXED' or None to remove the discount."""
    if not discount_type:
        session.pop(DISCOUNT_KEY, None)
    else:
        session[DISCOUNT_KEY] = {"type": discount_type, "value": str(value or "0")}
    session.modified = True


def get_discount(session) -> dict | None:
    return session.get(DISCOUNT_KEY)


def clear_discount(session) -> None:
    session.pop(DISCOUNT_KEY, None)
    session.modified = True


# ---------------------------------------------------------------------------
# Totals - always recomputed from the database, never trusted from the client
# ---------------------------------------------------------------------------
def calculate(session, tax_enabled: bool = False, default_tax_rate: Decimal = Decimal("0")) -> dict:
    lines = _session_cart(session)
    items: list[dict] = []
    subtotal_minor = 0
    cost_total_minor = 0
    tax_minor = 0

    for line in lines:
        product = db.session.get(Product, line.product_id)
        if product is None or not product.is_active:
            # A deactivated product must not silently vanish from the cart.
            items.append({
                "product_id": line.product_id,
                "name": product.name if product else "Unavailable product",
                "unavailable": True,
                "quantity": line.quantity,
                "unit_price": Decimal("0.00"),
                "line_total": Decimal("0.00"),
                "note": line.note,
                "stock": 0,
            })
            continue

        unit_minor = minor_of(product.selling_price)
        line_total = unit_minor * line.quantity
        rate = Decimal(str(product.tax_rate or 0)) if tax_enabled else Decimal("0")
        if rate == 0 and tax_enabled and default_tax_rate:
            rate = Decimal(str(default_tax_rate))
        line_tax = apply_tax(line_total, rate)

        subtotal_minor += line_total
        tax_minor += line_tax
        cost_total_minor += minor_of(product.cost_price) * line.quantity

        items.append({
            "product_id": product.id,
            "name": product.name,
            "sku": product.sku,
            "category": product.category.name if product.category else "",
            "unit": product.unit,
            "quantity": line.quantity,
            "unit_price": from_minor(unit_minor),
            "line_total": from_minor(line_total),
            "tax_rate": float(rate),
            "tax": from_minor(line_tax),
            "note": line.note,
            "stock": product.stock_quantity,
            "low_stock": product.stock_quantity - line.quantity <= (product.minimum_stock or 0),
            "unavailable": False,
        })

    discount_minor = _resolve_discount(subtotal_minor, get_discount(session))
    # Discount applies to the taxable amount proportionally.
    discount_tax_minor = 0
    if discount_minor and tax_minor:
        discount_tax_minor = int(
            (Decimal(tax_minor) * Decimal(discount_minor) / Decimal(subtotal_minor or 1)).quantize(Decimal("1"))
        )
        tax_minor -= discount_tax_minor

    net_minor = max(0, subtotal_minor - discount_minor)
    total_minor = max(0, net_minor + tax_minor)

    return {
        "items": items,
        "item_count": sum(i["quantity"] for i in items),
        "line_count": len(items),
        "has_unavailable": any(i.get("unavailable") for i in items),
        "subtotal": from_minor(subtotal_minor),
        "discount": from_minor(discount_minor),
        "discount_minor": discount_minor,
        "tax": from_minor(tax_minor),
        "total": from_minor(total_minor),
        "subtotal_minor": subtotal_minor,
        "tax_minor": tax_minor,
        "total_minor": total_minor,
        "cost_total": from_minor(cost_total_minor),
        "discount_spec": get_discount(session),
    }


def _resolve_discount(subtotal_minor: int, spec: dict | None) -> int:
    """Return the discount in kobo, never more than the subtotal."""
    if not spec or subtotal_minor <= 0:
        return 0
    try:
        value = Decimal(str(spec.get("value") or "0"))
    except Exception:  # noqa: BLE001
        return 0
    if spec.get("type") == "PERCENT":
        amount = int((Decimal(subtotal_minor) * value / Decimal(100)).quantize(Decimal("1")))
    else:
        from app.utils.money import to_minor

        amount = to_minor(value)
    return max(0, min(amount, subtotal_minor))


def to_payload(session) -> list[dict]:
    """Serialisable cart lines, used when holding a sale."""
    return [line.to_dict() for line in _session_cart(session)]


def from_payload(session, payload) -> list[CartLine]:
    lines = [CartLine.from_dict(item) for item in (payload or [])]
    save(session, lines)
    return lines
