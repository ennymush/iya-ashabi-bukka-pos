"""Sale processing service.

The single place a transaction is committed. Guarantees:
  1. ATOMIC - sale, items, payment, inventory movements and the audit row are
     written in ONE database transaction. Any failure rolls everything back,
     so a half-written sale can never exist.
  2. SERVER-TRUSTED TOTALS - every figure is recomputed from Product rows; the
     browser's totals are ignored.
  3. HISTORICAL SNAPSHOTS - sale items freeze name, sku, category and price so
     later price changes cannot rewrite history.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.audit import service as audit

from app.database.types import minor_of, naira_of
from app.database.models import (
    PAYMENT_METHODS,
    REASON_SALE,
    REASON_VOID,
    STATUS_COMPLETED,
    STATUS_VOIDED,
    CashDrawerEvent,
    InventoryMovement,
    Payment,
    Product,
    ReturnItem,
    ReturnRecord,
    Sale,
    SaleItem,
    Shift,
    Terminal,
    utcnow,
)
from app.extensions import db
from app.utils.errors import PaymentError, StockError, ValidationError
from app.utils.logging_config import get_logger
from app.utils.money import apply_tax, from_minor, to_minor

log = get_logger("sales")


class SaleOutcome:
    """What happened, including hardware results which are handled separately."""

    def __init__(self, sale: Sale, change_minor: int = 0, hardware: dict | None = None):
        self.sale = sale
        self.change_minor = change_minor
        self.hardware = hardware or {}

    @property
    def change(self):
        return from_minor(self.change_minor)


# ---------------------------------------------------------------------------
# Receipt numbers
# ---------------------------------------------------------------------------
def next_receipt_number(sale_date: date | None = None) -> str:
    """Readable, unique, date-scoped: IAB-YYYYMMDD-000123."""
    sale_date = sale_date or date.today()
    prefix = f"IAB-{sale_date.strftime('%Y%m%d')}-"
    last = (
        db.session.query(Sale.receipt_number)
        .filter(Sale.receipt_number.like(f"{prefix}%"))
        .order_by(Sale.receipt_number.desc())
        .first()
    )
    sequence = 1
    if last:
        try:
            sequence = int(last[0][-6:]) + 1
        except (ValueError, IndexError):
            sequence = db.session.query(db.func.count(Sale.id)).scalar() + 1
    candidate = f"{prefix}{sequence:06d}"
    # Defensive: never hand back a duplicate even under a race.
    while Sale.query.filter_by(receipt_number=candidate).first():
        sequence += 1
        candidate = f"{prefix}{sequence:06d}"
    return candidate


def next_return_number() -> str:
    stamp = datetime.now().strftime("%Y%m%d")
    prefix = f"IAB-RTN-{stamp}-"
    last = (
        db.session.query(ReturnRecord.return_number)
        .filter(ReturnRecord.return_number.like(f"{prefix}%"))
        .order_by(ReturnRecord.return_number.desc())
        .first()
    )
    sequence = 1
    if last:
        try:
            sequence = int(last[0][-4:]) + 4
        except (ValueError, IndexError):
            sequence = 1
    return f"{prefix}{sequence:04d}"


# ---------------------------------------------------------------------------
# Discount authorisation
# ---------------------------------------------------------------------------
def resolve_discount(subtotal_minor: int, spec: dict | None, user) -> tuple[int, dict]:
    """Validate a discount against the cashier's ceiling.

    Returns (discount_minor, info). Raises PermissionError-style validation
    when the amount exceeds what this user is allowed to give.
    """
    if not spec or subtotal_minor <= 0:
        return 0, {}

    try:
        value = Decimal(str(spec.get("value") or "0"))
    except Exception:  # noqa: BLE001
        raise ValidationError("That discount amount is not valid.")

    discount_type = str(spec.get("type") or "").upper()
    if discount_type not in ("PERCENT", "FIXED"):
        raise ValidationError("Choose either a percentage or a fixed-amount discount.")

    if discount_type == "PERCENT":
        if value <= 0:
            raise ValidationError("Discount must be greater than zero.")
        if value > 100:
            raise ValidationError("A percentage discount cannot be more than 100%.")
        amount = int((Decimal(subtotal_minor) * value / Decimal(100)).quantize(Decimal("1")))
    else:
        if value <= 0:
            raise ValidationError("Discount must be greater than zero.")
        amount = to_minor(value)

    amount = max(0, min(amount, subtotal_minor))

    if user is not None and not user.has_any_permission("sales.discount_override"):
        limit_percent = getattr(user, "max_discount_percent", None)
        limit_amount = getattr(user, "max_discount_amount", None)
        if discount_type == "PERCENT":
            allowed = Decimal(str(limit_percent)) if limit_percent is not None else Decimal("0")
            if value > allowed:
                raise ValidationError(
                    f"You may give up to {allowed:g}% discount. Please ask a manager."
                )
        else:
            allowed_minor = int(limit_amount) if limit_amount is not None else 0
            if amount > allowed_minor:
                raise ValidationError(
                    f"Discount of {from_minor(amount)} exceeds your limit. Please ask a manager."
                )

    return amount, {
        "type": discount_type,
        "value": str(value),
        "amount_minor": amount,
        "value_minor": amount,
    }


# ---------------------------------------------------------------------------
# Completing a sale
# ---------------------------------------------------------------------------
def complete_sale(
    *,
    user,
    cart_payload: list[dict],
    payment_method: str,
    amount_received=None,
    discount_spec: dict | None = None,
    tax_enabled: bool = False,
    default_tax_rate: Decimal = Decimal("0"),
    allow_negative_stock: bool = False,
    note: str = "",
    shift_id: int | None = None,
    terminal_id: int | None = None,
    hardware: dict | None = None,
) -> SaleOutcome:
    """Commit a sale atomically. Raises ValidationError and rolls back on error."""
    if not cart_payload:
        raise ValidationError("The cart is empty.")
    payment_method = str(payment_method or "").upper()
    if payment_method not in PAYMENT_METHODS:
        raise ValidationError("Choose a valid payment method.")

    now = utcnow()
    sale_date = now.date()

    try:
        # ---- BEGIN TRANSACTION ----
        # Rows are locked for the whole block so two terminals cannot oversell.
        products = (
            db.session.query(Product)
            .filter(Product.id.in_([int(l["product_id"]) for l in cart_payload]))
            .with_for_update()
            .all()
        )
        product_map = {p.id: p for p in products}
        if len(product_map) != len({int(l["product_id"]) for l in cart_payload}):
            raise ValidationError("One of the products in the cart no longer exists.")

        subtotal_minor = 0
        cost_minor = 0
        tax_minor = 0
        prepared: list[dict] = []

        for raw in cart_payload:
            product = product_map[int(raw["product_id"])]
            quantity = int(raw.get("quantity", 1))
            if quantity <= 0:
                raise ValidationError("Quantity must be at least 1.")
            if not product.is_active:
                raise ValidationError(f"{product.name} is no longer available.")
            if not allow_negative_stock:
                if product.stock_quantity <= 0:
                    raise StockError(f"{product.name} is out of stock.")
                if quantity > product.stock_quantity:
                    raise StockError(
                        f"Only {product.stock_quantity} of {product.name} left in stock."
                    )

            unit_minor = minor_of(product.selling_price)
            line_total = unit_minor * quantity
            rate = Decimal(str(product.tax_rate or 0)) if tax_enabled else Decimal("0")
            if rate == 0 and tax_enabled and default_tax_rate:
                rate = Decimal(str(default_tax_rate))
            line_tax = apply_tax(line_total, rate)

            subtotal_minor += line_total
            cost_minor += minor_of(product.cost_price) * quantity
            tax_minor += line_tax
            prepared.append({
                "product": product,
                "quantity": quantity,
                "unit_minor": unit_minor,
                "line_total": line_total,
                "line_tax": line_tax,
                "rate": rate,
                "note": (raw.get("note") or "")[:200],
            })

        discount_minor, discount_info = resolve_discount(subtotal_minor, discount_spec, user)
        if discount_minor and tax_minor:
            tax_minor -= int(
                (Decimal(tax_minor) * Decimal(discount_minor) / Decimal(subtotal_minor)).quantize(Decimal("1"))
            )

        net_minor = max(0, subtotal_minor - discount_minor)
        total_minor = max(0, net_minor + tax_minor)

        # ---- Payment validation (server side) ----
        received_minor = 0
        change_minor = 0
        if payment_method == "CASH":
            if amount_received in (None, ""):
                raise PaymentError("Please enter the amount received.")
            received_minor = to_minor(amount_received)
            if received_minor < total_minor:
                short = from_minor(total_minor - received_minor)
                raise PaymentError(
                    f"Amount received is less than the total. {short} still outstanding."
                )
            change_minor = received_minor - total_minor
        else:
            received_minor = total_minor

        # ---- Write the sale ----
        sale = Sale(
            receipt_number=next_receipt_number(sale_date),
            sale_date=sale_date,
            sale_time=now.strftime("%H:%M"),
            created_at=now,
            cashier_id=user.id,
            cashier_name=user.full_name,
            shift_id=shift_id,
            terminal_id=terminal_id,
            subtotal=naira_of(subtotal_minor),
            discount_amount=naira_of(discount_minor),
            tax_amount=naira_of(tax_minor),
            total_amount=naira_of(total_minor),
            cost_total=naira_of(cost_minor),
            payment_method=payment_method,
            amount_received=naira_of(received_minor),
            change_given=naira_of(change_minor),
            status=STATUS_COMPLETED,
            note=note[:255],
            discount_type=discount_info.get("type"),
            discount_value=discount_info.get("value_minor"),
            discount_authorised_by_id=user.id if discount_minor else None,
        )
        db.session.add(sale)
        db.session.flush()  # get sale.id while still inside the transaction

        for row in prepared:
            product = row["product"]
            item = SaleItem(
                sale_id=sale.id,
                product_id=product.id,
                product_name=product.name,
                sku=product.sku,
                category_name=product.category.name if product.category else "",
                unit=product.unit,
                quantity=row["quantity"],
                unit_price=naira_of(row["unit_minor"]),
                cost_price=product.cost_price,
                tax_rate=row["rate"],
                tax_amount=naira_of(row["line_tax"]),
                line_total=naira_of(row["line_total"]),
                stock_deducted=False,
            )
            db.session.add(item)

            # Inventory moves in the SAME transaction as the sale.
            old_qty = product.stock_quantity
            new_qty = old_qty - row["quantity"]
            product.stock_quantity = new_qty
            item.stock_deducted = True
            db.session.add(
                InventoryMovement(
                    product_id=product.id,
                    user_id=user.id,
                    old_quantity=old_qty,
                    new_quantity=new_qty,
                    difference=-row["quantity"],
                    reason=REASON_SALE,
                    reference=sale.receipt_number,
                    note=row["note"] or None,
                )
            )

        db.session.add(
            Payment(
                sale_id=sale.id,
                method=payment_method,
                amount=naira_of(total_minor),
                amount_received=naira_of(received_minor),
                change_given=naira_of(change_minor),
                is_refund=False,
            )
        )

        _update_shift_totals(sale, shift_id)

        if discount_minor:
            audit.record(
                audit.DISCOUNT_APPLIED, user=user, entity_type="Sale",
                entity_id=sale.receipt_number,
                description=f"Discount of {from_minor(discount_minor)} applied to {sale.receipt_number}",
                new_value=discount_info,
                ip_address=_client_ip(),
            )

        audit.record(
            audit.SALE_CREATE, user=user, entity_type="Sale", entity_id=sale.receipt_number,
            description=(
                f"{user.full_name} completed sale {sale.receipt_number} "
                f"for {from_minor(total_minor)} ({payment_method})"
            ),
            new_value={"total": str(from_minor(total_minor)), "items": len(prepared)},
            ip_address=_client_ip(),
        )

        # ---- COMMIT ----
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Sale failed and was rolled back")
        raise

    sale = db.session.get(Sale, sale.id)
    outcome = SaleOutcome(sale, change_minor=change_minor, hardware=hardware)

    if payment_method == "CASH":
        outcome.hardware["drawer"] = record_drawer_event(
            sale=sale, user=user, terminal_id=terminal_id, trigger="SALE"
        )
    return outcome


def _update_shift_totals(sale: Sale, shift_id: int | None) -> None:
    if not shift_id:
        return
    shift = db.session.get(Shift, int(shift_id))
    if shift is None:
        return
    shift.transaction_count = (shift.transaction_count or 0) + 1
    shift.total_sales = naira_of(minor_of(shift.total_sales) + minor_of(sale.total_amount))
    shift.discount_total = naira_of(minor_of(shift.discount_total) + minor_of(sale.discount_amount))
    method = sale.payment_method
    if method == "CASH":
        shift.cash_sales = naira_of(minor_of(shift.cash_sales) + minor_of(sale.total_amount))
    elif method == "TRANSFER":
        shift.transfer_sales = naira_of(minor_of(shift.transfer_sales) + minor_of(sale.total_amount))
    elif method == "CARD":
        shift.card_sales = naira_of(minor_of(shift.card_sales) + minor_of(sale.total_amount))
    else:
        shift.other_sales = naira_of(minor_of(shift.other_sales) + minor_of(sale.total_amount))


def _client_ip() -> str | None:
    try:
        from flask import request

        return request.remote_addr
    except RuntimeError:
        return None


# ---------------------------------------------------------------------------
# Void
# ---------------------------------------------------------------------------
def void_sale(sale: Sale, user, reason: str) -> Sale:
    """Void a completed sale. The original rows are kept; stock is restored."""
    if not reason or not reason.strip():
        raise ValidationError("Please give a reason for the void.")
    if sale.status == STATUS_VOIDED:
        raise ValidationError("That transaction has already been voided.")

    try:
        for item in sale.items:
            if item.stock_deducted and item.product_id:
                product = db.session.get(Product, item.product_id)
                if product is not None:
                    old_qty = product.stock_quantity
                    new_qty = old_qty + item.quantity
                    product.stock_quantity = new_qty
                    db.session.add(
                        InventoryMovement(
                            product_id=product.id,
                            user_id=user.id,
                            old_quantity=old_qty,
                            new_quantity=new_qty,
                            difference=item.quantity,
                            reason=REASON_VOID,
                            reference=sale.receipt_number,
                            note=f"Void of {sale.receipt_number}",
                        )
                    )

        sale.status = STATUS_VOIDED
        sale.voided_by_id = user.id
        sale.voided_at = utcnow()
        sale.void_reason = reason.strip()[:255]

        if sale.shift_id:
            shift = db.session.get(Shift, sale.shift_id)
            if shift:
                shift.void_total = naira_of(minor_of(shift.void_total) + minor_of(sale.total_amount))
                shift.total_sales = naira_of(max(0, minor_of(shift.total_sales) - minor_of(sale.total_amount)))
                method = sale.payment_method
                if method == "CASH":
                    shift.cash_sales = naira_of(max(0, minor_of(shift.cash_sales) - minor_of(sale.total_amount)))
                elif method == "TRANSFER":
                    shift.transfer_sales = naira_of(max(0, minor_of(shift.transfer_sales) - minor_of(sale.total_amount)))
                elif method == "CARD":
                    shift.card_sales = naira_of(max(0, minor_of(shift.card_sales) - minor_of(sale.total_amount)))
                else:
                    shift.other_sales = naira_of(max(0, minor_of(shift.other_sales) - minor_of(sale.total_amount)))
                shift.transaction_count = max(0, (shift.transaction_count or 0) - 1)

        audit.record(
            audit.SALE_VOID, user=user, entity_type="Sale", entity_id=sale.receipt_number,
            description=f"{user.full_name} voided {sale.receipt_number}: {reason.strip()[:120]}",
            new_value={"status": STATUS_VOIDED, "reason": reason.strip()[:255]},
            ip_address=_client_ip(),
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Void failed for %s", sale.receipt_number)
        raise
    return sale


# ---------------------------------------------------------------------------
# Refund / return
# ---------------------------------------------------------------------------
def refund_sale(
    sale: Sale,
    user,
    lines: list[dict],
    reason: str,
    refund_method: str = "CASH",
    restock: bool = True,
    approved_by=None,
) -> ReturnRecord:
    """Refund part or all of a sale. The original sale is never deleted."""
    if not reason or not reason.strip():
        raise ValidationError("Please give a reason for the return.")
    if sale.status == STATUS_VOIDED:
        raise ValidationError("A voided transaction cannot be refunded.")

    refund_method = str(refund_method or "CASH").upper()
    if refund_method not in PAYMENT_METHODS:
        raise ValidationError("Choose a valid refund method.")

    wanted: dict[int, int] = {}
    for entry in lines or []:
        item_id = int(entry.get("sale_item_id") or 0)
        quantity = int(entry.get("quantity") or 0)
        if item_id and quantity > 0:
            wanted[item_id] = wanted.get(item_id, 0) + quantity
    if not wanted:
        raise ValidationError("Select at least one item to return.")

    try:
        now = utcnow()
        record = ReturnRecord(
            return_number=next_return_number(),
            sale_id=sale.id,
            original_receipt=sale.receipt_number,
            user_id=user.id,
            approved_by_id=getattr(approved_by, "id", None) or (user.id if user.has_any_permission("sales.refund") else None),
            reason=reason.strip()[:255],
            refund_method=refund_method,
            restock=restock,
            created_at=now,
            sale_date=sale.sale_date,
            sale_time=sale.sale_time,
            total_amount=0,
        )
        db.session.add(record)
        db.session.flush()

        total_minor = 0
        for item in sale.items:
            if item.id not in wanted:
                continue
            quantity = min(wanted[item.id], item.quantity - item.returned_quantity)
            if quantity <= 0:
                continue
            amount = minor_of(item.unit_price) * quantity
            item.returned_quantity += quantity
            total_minor += amount

            restocked = False
            if restock and item.product_id:
                product = db.session.get(Product, item.product_id)
                if product is not None:
                    old_qty = product.stock_quantity
                    new_qty = old_qty + quantity
                    product.stock_quantity = new_qty
                    restocked = True
                    db.session.add(
                        InventoryMovement(
                            product_id=product.id,
                            user_id=user.id,
                            old_quantity=old_qty,
                            new_quantity=new_qty,
                            difference=quantity,
                            reason="Return",
                            reference=record.return_number,
                            note=f"Return against {sale.receipt_number}",
                        )
                    )

            db.session.add(
                ReturnItem(
                    return_id=record.id,
                    sale_item_id=item.id,
                    product_id=item.product_id,
                    product_name=item.product_name,
                    quantity=quantity,
                    unit_price=item.unit_price,
                    amount=naira_of(amount),
                    restocked=restocked,
                )
            )

        if total_minor == 0:
            raise ValidationError("Those items have already been fully returned.")

        record.total_amount = naira_of(total_minor)
        db.session.add(
            Payment(
                sale_id=sale.id,
                method=refund_method,
                amount=naira_of(total_minor),
                amount_received=naira_of(total_minor),
                change_given=0,
                is_refund=True,
                reference=record.return_number,
            )
        )

        # Fully returned sales are marked REFUNDED; part returns leave COMPLETED.
        if all(i.returned_quantity >= i.quantity for i in sale.items):
            sale.status = "REFUNDED"

        if sale.shift_id and refund_method == "CASH":
            shift = db.session.get(Shift, sale.shift_id)
            if shift:
                shift.cash_refunds = naira_of(minor_of(shift.cash_refunds) + total_minor)

        audit.record(
            audit.SALE_REFUND, user=user, entity_type="Return", entity_id=record.return_number,
            description=(
                f"{user.full_name} refunded {from_minor(total_minor)} against "
                f"{sale.receipt_number}: {reason.strip()[:120]}"
            ),
            new_value={"return_number": record.return_number, "amount": str(from_minor(total_minor))},
            ip_address=_client_ip(),
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Refund failed for %s", sale.receipt_number)
        raise

    return db.session.get(ReturnRecord, record.id)


# ---------------------------------------------------------------------------
# Cash drawer events
# ---------------------------------------------------------------------------
def record_drawer_event(sale=None, user=None, terminal_id=None, trigger: str = "SALE") -> dict:
    """Open the drawer and log the result. A failure never cancels a sale."""
    from app.hardware import factory

    services = factory.get_hardware()
    drawer = services["drawer"]

    try:
        result = drawer.open_drawer()
        status = "OPENED" if result.success else "FAILED"
        message = result.message if result.success else (result.detail or "Cash drawer unavailable.")
    except Exception as exc:  # noqa: BLE001
        result = None
        status = "FAILED"
        message = str(exc)
        log.warning("Cash drawer raised: %s", exc)

    try:
        db.session.add(
            CashDrawerEvent(
                sale_id=sale.id if sale else None,
                terminal_id=terminal_id,
                user_id=getattr(user, "id", None),
                trigger=trigger,
                status=status,
                message=(message or "")[:255],
            )
        )
        if sale is not None:
            sale.drawer_opened_at = utcnow() if status == "OPENED" else None
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()

    if sale is not None:
        sale = db.session.get(Sale, sale.id)
    return {
        "status": status,
        "message": message if status == "FAILED" else "Cash drawer opened.",
        "result": result.to_dict() if result is not None else None,
    }
