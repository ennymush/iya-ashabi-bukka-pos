"""Product and category management service (admin operations)."""
from __future__ import annotations

from app.audit import service as audit

from app.database.types import minor_of, naira_of
from app.database.models import (
    REASON_CORRECTION,
    REASON_DELIVERY,
    REASON_OPENING,
    Category,
    InventoryMovement,
    PriceHistory,
    Product,
)
from app.extensions import db
from app.utils.errors import ValidationError
from app.utils.logging_config import get_logger
from app.utils.money import from_minor, to_minor

log = get_logger("products")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _clean_name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise ValidationError("Product name is required.")
    if len(name) > 160:
        raise ValidationError("Product name is too long (160 characters maximum).")
    return name


def _clean_sku(value: str, product_id: int | None = None) -> str | None:
    sku = (value or "").strip() or None
    if sku:
        query = Product.query.filter(Product.sku == sku)
        if product_id:
            query = query.filter(Product.id != product_id)
        if query.first():
            raise ValidationError(f"The SKU '{sku}' is already used by another product.")
    return sku


def _clean_barcode(value: str, product_id: int | None = None) -> str | None:
    code = (value or "").strip() or None
    if code:
        if not code.isalnum() and "-" not in code:
            raise ValidationError("Barcode may only contain letters, numbers and dashes.")
        query = Product.query.filter(Product.barcode == code)
        if product_id:
            query = query.filter(Product.id != product_id)
        if query.first():
            raise ValidationError(f"Barcode '{code}' is already used by another product.")
    return code


def _parse_int(value, field: str, minimum: int = 0) -> int:
    try:
        number = int(str(value).strip() or "0")
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a whole number.")
    if number < minimum:
        raise ValidationError(f"{field} cannot be less than {minimum}.")
    return number


def _parse_money(value, field: str) -> int:
    try:
        return to_minor(value if value not in (None, "") else 0)
    except Exception:  # noqa: BLE001
        raise ValidationError(f"{field} must be a valid amount, for example 2500.00")


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
def create_product(user, data: dict, *, opening_stock: int | None = None) -> Product:
    name = _clean_name(data.get("name"))
    selling = _parse_money(data.get("selling_price"), "Selling price")
    cost = _parse_money(data.get("cost_price"), "Cost price")
    if selling <= 0:
        raise ValidationError("Selling price must be greater than zero.")
    if cost > selling:
        raise ValidationError("Cost price cannot be higher than the selling price.")
    if selling < cost:
        log.info("Product '%s' created with selling price below cost", name)

    stock = _parse_int(data.get("stock_quantity", 0), "Stock", 0)
    minimum = _parse_int(data.get("minimum_stock", 0), "Minimum stock", 0)

    product = Product(
        name=name,
        sku=_clean_sku(data.get("sku")),
        barcode=_clean_barcode(data.get("barcode")),
        product_code=(data.get("product_code") or "").strip() or None,
        category_id=data.get("category_id") or None,
        description=(data.get("description") or "").strip()[:500] or None,
        preparation_notes=(data.get("preparation_notes") or "").strip()[:500] or None,
        unit=(data.get("unit") or "portion").strip()[:20],
        cost_price=naira_of(cost),
        selling_price=naira_of(selling),
        tax_rate=data.get("tax_rate") or 0,
        stock_quantity=stock,
        minimum_stock=minimum,
        is_active=str(data.get("is_active", "1")).lower() in {"1", "true", "yes", "on"},
    )
    db.session.add(product)
    db.session.flush()

    if stock > 0:
        db.session.add(
            InventoryMovement(
                product_id=product.id,
                user_id=user.id,
                old_quantity=0,
                new_quantity=stock,
                difference=stock,
                reason=REASON_OPENING,
                note="Opening stock when the product was created",
            )
        )

    audit.record(
        audit.PRODUCT_CREATE, user=user, entity_type="Product", entity_id=product.id,
        description=f"{user.full_name} added product '{product.name}' at {from_minor(selling)}",
        new_value={
            "name": product.name, "sku": product.sku, "selling_price": str(from_minor(selling)),
            "cost_price": str(from_minor(cost)), "stock": stock, "minimum_stock": minimum,
        },
        ip_address=_ip(),
    )
    db.session.commit()
    return product


def update_product(user, product: Product, data: dict) -> Product:
    old = {
        "name": product.name,
        "selling_price": str(product.selling_price),
        "cost_price": str(product.cost_price),
        "category_id": product.category_id,
        "barcode": product.barcode,
        "sku": product.sku,
        "unit": product.unit,
        "tax_rate": float(product.tax_rate or 0),
        "is_active": product.is_active,
    }

    if "name" in data:
        product.name = _clean_name(data.get("name"))
    if "sku" in data:
        product.sku = _clean_sku(data.get("sku"), product.id)
    if "barcode" in data:
        product.barcode = _clean_barcode(data.get("barcode"), product.id)
    if "product_code" in data:
        product.product_code = (data.get("product_code") or "").strip() or None
    if "category_id" in data:
        product.category_id = data.get("category_id") or None
    if "description" in data:
        product.description = (data.get("description") or "").strip()[:500] or None
    if "preparation_notes" in data:
        product.preparation_notes = (data.get("preparation_notes") or "").strip()[:500] or None
    if "unit" in data:
        product.unit = (data.get("unit") or "portion").strip()[:20]
    if "tax_rate" in data:
        try:
            product.tax_rate = float(str(data.get("tax_rate") or "0"))
        except (TypeError, ValueError):
            raise ValidationError("Tax rate must be a number.")
    if "is_active" in data:
        product.is_active = str(data.get("is_active")).lower() in {"1", "true", "yes", "on"}

    # Price changes are recorded in price_history AND audited.
    if "selling_price" in data:
        new_selling = _parse_money(data.get("selling_price"), "Selling price")
        if new_selling <= 0:
            raise ValidationError("Selling price must be greater than zero.")
        if new_selling != minor_of(product.selling_price):
            db.session.add(
                PriceHistory(
                    product_id=product.id,
                    old_price=product.selling_price,
                    new_price=naira_of(new_selling),
                    old_cost=product.cost_price,
                    new_cost=product.cost_price,
                    changed_by_id=user.id,
                    reason=(data.get("reason") or "Price updated").strip()[:255],
                )
            )
            audit.record(
                audit.PRICE_CHANGE, user=user, entity_type="Product", entity_id=product.id,
                description=(
                    f"{user.full_name} changed {product.name} from "
                    f"{product.selling_price} to {new_selling}"
                ),
                old_value={"selling_price": str(product.selling_price)},
                new_value={"selling_price": str(from_minor(new_selling))},
                ip_address=_ip(),
            )
            product.selling_price = naira_of(new_selling)

    if "cost_price" in data:
        new_cost = _parse_money(data.get("cost_price"), "Cost price")
        product.cost_price = naira_of(new_cost)

    new = {
        "name": product.name,
        "selling_price": str(product.selling_price),
        "cost_price": str(product.cost_price),
        "category_id": product.category_id,
        "barcode": product.barcode,
        "sku": product.sku,
        "unit": product.unit,
        "tax_rate": float(product.tax_rate or 0),
        "is_active": product.is_active,
    }

    audit.record(
        audit.PRODUCT_UPDATE, user=user, entity_type="Product", entity_id=product.id,
        description=f"{user.full_name} updated product '{product.name}'",
        old_value=old, new_value=new, ip_address=_ip(),
    )
    db.session.commit()
    return product


def set_active(user, product: Product, active: bool) -> Product:
    """Soft delete. Historical sale items keep their own snapshot, so a
    deactivated product never damages past reporting."""
    product.is_active = active
    if active:
        audit.record(audit.PRODUCT_ACTIVATE, user=user, entity_type="Product", entity_id=product.id,
                     description=f"{user.full_name} reactivated product '{product.name}'",
                     ip_address=_ip())
    else:
        audit.record(audit.PRODUCT_DEACTIVATE, user=user, entity_type="Product", entity_id=product.id,
                     description=f"{user.full_name} deactivated product '{product.name}'",
                     old_value={"is_active": True}, new_value={"is_active": False}, ip_address=_ip())
    db.session.commit()
    return product


def quick_price_change(user, product: Product, new_price, reason: str = "") -> Product:
    """The one-click CHANGE PRICE action used by the owner."""
    amount = _parse_money(new_price, "Selling price")
    if amount <= 0:
        raise ValidationError("Selling price must be greater than zero.")
    old_price = minor_of(product.selling_price)
    if old_price == amount:
        raise ValidationError("That is already the current price.")

    db.session.add(
        PriceHistory(
            product_id=product.id,
            old_price=naira_of(old_price),
            new_price=naira_of(amount),
            old_cost=product.cost_price,
            new_cost=product.cost_price,
            changed_by_id=user.id,
            reason=(reason or "Price updated").strip()[:255],
        )
    )
    product.selling_price = naira_of(amount)
    audit.record(
        audit.PRICE_CHANGE, user=user, entity_type="Product", entity_id=product.id,
        description=(
            f"{user.full_name} changed {product.name} from {from_minor(old_price)} to {from_minor(amount)}"
        ),
        old_value={"selling_price": str(from_minor(old_price))},
        new_value={"selling_price": str(from_minor(amount))},
        ip_address=_ip(),
    )
    db.session.commit()
    return product


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
def create_category(user, data: dict) -> Category:
    name = _clean_name(data.get("name"))
    existing = Category.query.filter(db.func.lower(Category.name) == name.lower()).first()
    if existing:
        raise ValidationError(f"A category called '{name}' already exists.")

    category = Category(
        name=name,
        description=(data.get("description") or "").strip()[:255] or None,
        color=(data.get("color") or "#8B4513").strip()[:20],
        sort_order=int(data.get("sort_order") or 0),
        is_active=str(data.get("is_active", "1")).lower() in {"1", "true", "yes", "on"},
    )
    db.session.add(category)
    audit.record(audit.CATEGORY_CREATE, user=user, entity_type="Category", entity_id=category.id,
                 description=f"{user.full_name} added category '{name}'", ip_address=_ip())
    db.session.commit()
    return category


def update_category(user, category: Category, data: dict) -> Category:
    old_name = category.name
    if "name" in data:
        name = _clean_name(data.get("name"))
        clash = Category.query.filter(
            db.func.lower(Category.name) == name.lower(), Category.id != category.id
        ).first()
        if clash:
            raise ValidationError(f"A category called '{name}' already exists.")
        category.name = name
    if "description" in data:
        category.description = (data.get("description") or "").strip()[:255] or None
    if "color" in data:
        category.color = (data.get("color") or "").strip()[:20] or None
    if "sort_order" in data:
        try:
            category.sort_order = int(data.get("sort_order") or 0)
        except (TypeError, ValueError):
            pass
    if "is_active" in data:
        category.is_active = str(data.get("is_active")).lower() in {"1", "true", "yes", "on"}

    audit.record(audit.CATEGORY_UPDATE, user=user, entity_type="Category", entity_id=category.id,
                 description=f"{user.full_name} updated category '{old_name}'",
                 old_value={"name": old_name}, new_value={"name": category.name}, ip_address=_ip())
    db.session.commit()
    return category


def set_category_active(user, category: Category, active: bool) -> Category:
    product_count = Product.query.filter_by(category_id=category.id, is_active=True).count()
    if not active and product_count:
        # Block it: an admin should move or deactivate the products first.
        raise ValidationError(
            f"'{category.name}' still has {product_count} active product(s). "
            "Move or deactivate them first."
        )
    category.is_active = active
    audit.record(
        audit.CATEGORY_DEACTIVATE if not active else audit.CATEGORY_UPDATE,
        user=user, entity_type="Category", entity_id=category.id,
        description=f"{user.full_name} {'deactivated' if not active else 'reactivated'} category '{category.name}'",
        ip_address=_ip(),
    )
    db.session.commit()
    return category


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
def adjust_stock(user, product: Product, new_quantity, reason: str, note: str = "") -> Product:
    """Set stock to an absolute figure and record the movement."""
    if reason not in (REASON_OPENING, REASON_DELIVERY, "Damaged", "Spoilage",
                      REASON_CORRECTION, "Return", "Other"):
        reason = REASON_CORRECTION
    try:
        target = int(str(new_quantity).strip())
    except (TypeError, ValueError):
        raise ValidationError("Enter a valid stock quantity.")

    old_qty = product.stock_quantity
    difference = target - old_qty
    if difference == 0:
        raise ValidationError("That is already the current stock level.")

    product.stock_quantity = target
    db.session.add(
        InventoryMovement(
            product_id=product.id,
            user_id=user.id,
            old_quantity=old_qty,
            new_quantity=target,
            difference=difference,
            reason=reason,
            note=(note or "").strip()[:255] or None,
        )
    )
    audit.record(
        audit.INVENTORY_ADJUST, user=user, entity_type="Product", entity_id=product.id,
        description=(
            f"{user.full_name} adjusted stock for {product.name} from {old_qty} to {target} ({reason})"
        ),
        old_value={"stock": old_qty}, new_value={"stock": target, "reason": reason}, ip_address=_ip(),
    )
    db.session.commit()
    return product


def adjust_stock_by(user, product: Product, delta: int, reason: str, note: str = "") -> Product:
    return adjust_stock(user, product, product.stock_quantity + int(delta), reason, note)


def _ip():
    """Client IP, or None outside a request (CLI, tests, background jobs)."""
    try:
        from flask import request

        return getattr(request, "remote_addr", None)
    except RuntimeError:
        return None