"""Cashier POS routes and the internal JSON API the POS screen uses.

Every API endpoint is permission-checked on the server and every figure is
recomputed from the database. The browser is treated as untrusted.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for

from app.audit import service as audit

from app.database.types import minor_of, naira_of
from app.customer import service as display_service
from app.database import service_settings as settings_service
from app.database.models import (
    PAYMENT_METHODS,
    Product,
    Sale,
    Shift,
    Terminal,
    User,
    utcnow,
)
from app.extensions import db
from app.hardware import factory
from app.pos import cart as cart_service
from app.sales import service as sales_service
from app.utils.errors import ValidationError
from app.utils.logging_config import get_logger
from app.utils.money import fmt, fmt_minor, from_minor
from app.utils.security import login_required, permission_required

log = get_logger("pos")

bp = Blueprint("pos", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _current_terminal() -> Terminal | None:
    return Terminal.query.order_by(Terminal.id).first()


def _open_shift(user) -> Shift | None:
    return Shift.query.filter_by(user_id=user.id, is_open=True).order_by(Shift.opened_at.desc()).first()


def _tax_settings() -> tuple[bool, object]:
    from decimal import Decimal

    enabled = settings_service.tax_enabled()
    try:
        rate = Decimal(str(settings_service.default_tax_rate() or "0"))
    except Exception:  # noqa: BLE001
        rate = Decimal("0")
    return enabled, rate


def _cart_payload() -> dict:
    tax_enabled, rate = _tax_settings()
    calculation = cart_service.calculate(session, tax_enabled=tax_enabled, default_tax_rate=rate)
    from app.utils.money import fmt

    return {
        "ok": True,
        "items": [
            {
                "product_id": item["product_id"],
                "name": item["name"],
                "quantity": item["quantity"],
                "unit_price": fmt(item["unit_price"]),
                "unit_price_raw": str(item["unit_price"]),
                "line_total": fmt(item["line_total"]),
                "line_total_raw": str(item["line_total"]),
                "note": item.get("note", ""),
                "stock": item.get("stock", 0),
                "low_stock": item.get("low_stock", False),
                "unavailable": item.get("unavailable", False),
            }
            for item in calculation["items"]
        ],
        "subtotal": fmt(calculation["subtotal"]),
        "discount": fmt(calculation["discount"]),
        "tax": fmt(calculation["tax"]),
        "total": fmt(calculation["total"]),
        "total_raw": str(calculation["total"]),
        "item_count": calculation["item_count"],
        "line_count": calculation["line_count"],
        "has_unavailable": calculation["has_unavailable"],
        "discount_spec": calculation["discount_spec"],
    }


def _push_display_cart() -> None:
    """Mirror the cart to the customer screen."""
    tax_enabled, rate = _tax_settings()
    calculation = cart_service.calculate(session, tax_enabled=tax_enabled, default_tax_rate=rate)
    display_service.publish(display_service.MODE_CART, display_service.cart_state(calculation))


def _after_sale_complete(sale) -> None:
    cart_service.clear(session)
    display_service.publish(display_service.MODE_COMPLETE, display_service.complete_state(sale))


# ---------------------------------------------------------------------------
# Main POS screen
# ---------------------------------------------------------------------------
@bp.route("/pos")
@permission_required("pos.use")
def index():
    user = g.user
    categories = _categories()

    products = (
        Product.query.filter_by(is_active=True)
        .order_by(Product.name)
        .limit(300)
        .all()
    )

    tax_enabled, rate = _tax_settings()
    calculation = cart_service.calculate(session, tax_enabled=tax_enabled, default_tax_rate=rate)

    show_prices = user.has_any_permission("products.view")
    return render_template(
        "pos/index.html",
        user=user,
        categories=categories,
        products=products,
        cart=calculation,
        shift=_open_shift(user),
        payment_methods=PAYMENT_METHODS,
        payment_labels={
            "CASH": "CASH",
            "TRANSFER": "TRANSFER",
            "CARD": "CARD",
            "OTHER": "OTHER",
        },
        discount_spec=cart_service.get_discount(session),
        allow_negative=settings_service.allow_negative_stock(),
        tax_enabled=settings_service.tax_enabled(),
        show_prices=show_prices,
        max_discount_percent=user.max_discount_percent,
        max_discount_amount=user.max_discount_amount,
        can_override_discount=user.has_any_permission("sales.discount_override"),
        can_hold=user.has_any_permission("sales.hold"),
    )


def _categories():
    from app.database.models import Category

    return Category.query.filter_by(is_active=True).order_by(Category.sort_order, Category.name).all()


@bp.route("/pos/shift/open", methods=["POST"])
@permission_required("shifts.use")
def open_shift():
    from app.utils.money import to_minor

    user = g.user
    if _open_shift(user):
        return jsonify({"ok": False, "error": "You already have an open shift."}), 400

    try:
        opening = to_minor(request.form.get("opening_cash") or 0)
    except Exception:  # noqa: BLE001
        return jsonify({"ok": False, "error": "Enter a valid opening float amount."}), 400

    shift = Shift(
        user_id=user.id,
        terminal_id=getattr(_current_terminal(), "id", None),
        opening_cash=naira_of(opening),
        opened_at=utcnow(),
        is_open=True,
    )
    db.session.add(shift)
    db.session.commit()

    audit.record(audit.SHIFT_OPEN, user=user, entity_type="Shift", entity_id=shift.id,
                 description=f"{user.full_name} opened a shift with {from_minor(opening)} float",
                 new_value={"opening_cash": str(from_minor(opening))},
                 ip_address=request.remote_addr, commit=True)
    return jsonify({"ok": True, "shift_id": shift.id,
                    "message": f"Shift opened with {from_minor(opening)} float."})


@bp.route("/pos/shift/close", methods=["POST"])
@permission_required("shifts.use")
def close_shift():
    """Close the shift with a cash count and record the variance."""
    from app.utils.money import to_minor

    user = g.user
    shift = _open_shift(user)
    if shift is None:
        return jsonify({"ok": False, "error": "You do not have an open shift."}), 400
    if session.get(cart_service.CART_KEY):
        return jsonify({"ok": False, "error": "Please finish or clear the current cart first."}), 400

    try:
        actual = to_minor(request.form.get("actual_cash") or 0)
    except Exception:  # noqa: BLE001
        return jsonify({"ok": False, "error": "Enter a valid cash count."}), 400

    expected = (shift.opening_cash or 0) + (shift.cash_sales or 0) - (shift.cash_refunds or 0)
    shift.expected_cash = naira_of(expected)
    shift.actual_cash = naira_of(actual)
    shift.variance = naira_of(actual - expected)
    shift.is_open = False
    shift.closed_at = utcnow()
    shift.closing_cashier_id = user.id
    shift.closing_note = (request.form.get("note") or "")[:255] or None

    db.session.commit()
    audit.record(audit.SHIFT_CLOSE, user=user, entity_type="Shift", entity_id=shift.id,
                 description=(
                     f"{user.full_name} closed shift. Expected {from_minor(expected)}, "
                     f"counted {from_minor(actual)}, variance {from_minor(shift.variance)}"
                 ),
                 new_value={"expected": str(from_minor(expected)),
                            "actual": str(from_minor(actual)),
                            "variance": str(from_minor(shift.variance))},
                 ip_address=request.remote_addr, commit=True)

    return jsonify({
        "ok": True,
        "expected": str(from_minor(expected)),
        "actual": str(from_minor(actual)),
        "variance": str(from_minor(shift.variance)),
        "message": "Shift closed successfully.",
    })


@bp.route("/pos/shift/status")
@permission_required("pos.use")
def shift_status():
    user = g.user
    shift = _open_shift(user)
    if shift is None:
        return jsonify({"ok": True, "open": False})
    expected = (shift.opening_cash or 0) + (shift.cash_sales or 0) - (shift.cash_refunds or 0)
    return jsonify({
        "ok": True,
        "open": True,
        "shift_id": shift.id,
        "opening_cash": str(from_minor(shift.opening_cash)),
        "expected_cash": str(from_minor(expected)),
        "transactions": shift.transaction_count,
        "total_sales": str(from_minor(shift.total_sales)),
    })


# ---------------------------------------------------------------------------
# Product search (SQLite, indexed; no hardcoded product list)
# ---------------------------------------------------------------------------
@bp.route("/api/pos/products")
@permission_required("pos.use")
def api_products():
    """Search products by name, SKU, barcode or product code."""
    query = (request.args.get("q") or "").strip()
    category_id = request.args.get("category_id", type=int)
    limit = min(request.args.get("limit", default=60, type=int), 200)

    stmt = Product.query.filter(Product.is_active.is_(True))
    if query:
        like = f"%{query}%"
        stmt = stmt.filter(
            db.or_(
                Product.name.ilike(like),
                Product.sku.ilike(like),
                Product.barcode.ilike(like),
                Product.product_code.ilike(like),
            )
        )
    if category_id:
        stmt = stmt.filter(Product.category_id == category_id)

    products = stmt.order_by(Product.name).limit(limit).all()
    return jsonify({"ok": True, "products": [_product_json(p) for p in products]})


def _product_json(p: Product) -> dict:
    from app.utils.money import fmt

    return {
        "id": p.id,
        "name": p.name,
        "sku": p.sku,
        "barcode": p.barcode,
        "price": fmt(p.selling_price),
        "price_raw": str(p.selling_price),
        "stock": p.stock_quantity,
        "low_stock": p.is_low,
        "out_of_stock": p.stock_quantity <= 0,
        "unit": p.unit,
        "category": p.category.name if p.category else "",
        "category_id": p.category_id,
        "image": p.image_filename,
        "description": p.description,
    }


@bp.route("/api/pos/categories")
@permission_required("pos.use")
def api_categories():
    return jsonify({"ok": True, "categories": [
        {"id": c.id, "name": c.name, "color": c.color, "icon": c.icon}
        for c in _categories()
    ]})


@bp.route("/api/pos/barcode/<path:code>")
@permission_required("pos.use")
def api_barcode(code: str):
    """Resolve a scanned barcode to a product (also tries SKU and code)."""
    services = factory.get_hardware()
    scanner = services["scanner"]
    result = scanner.resolve(code)
    if result is None:
        return jsonify({"ok": False, "error": f"Barcode '{code}' not found. Please check the code or search by name."}), 404
    product = db.session.get(Product, result["id"])
    return jsonify({"ok": True, "product": _product_json(product), "matched_on": result["matched_on"]})


# ---------------------------------------------------------------------------
# Cart API
# ---------------------------------------------------------------------------
@bp.route("/api/pos/cart", methods=["GET"])
@permission_required("pos.use")
def api_cart_get():
    return jsonify(_cart_payload())


@bp.route("/api/pos/cart/add", methods=["POST"])
@permission_required("pos.use")
def api_cart_add():
    product_id = request.form.get("product_id", type=int) or request.json.get("product_id")
    quantity = request.form.get("quantity", type=int) or (request.json or {}).get("quantity", 1)
    if not product_id:
        raise ValidationError("Product not found.")
    cart_service.add(session, product_id, quantity or 1,
                      allow_negative=settings_service.allow_negative_stock())
    _push_display_cart()
    return jsonify(_cart_payload())


@bp.route("/api/pos/cart/quantity", methods=["POST"])
@permission_required("pos.use")
def api_cart_quantity():
    payload = request.form or (request.json or {})
    product_id = payload.get("product_id")
    quantity = payload.get("quantity")
    if not product_id:
        raise ValidationError("Product not found.")
    cart_service.set_quantity(session, int(product_id), int(quantity),
                              allow_negative=settings_service.allow_negative_stock())
    _push_display_cart()
    return jsonify(_cart_payload())


@bp.route("/api/pos/cart/remove", methods=["POST"])
@permission_required("pos.use")
def api_cart_remove():
    payload = request.form or (request.json or {})
    product_id = payload.get("product_id")
    cart_service.remove(session, int(product_id))
    _push_display_cart()
    return jsonify(_cart_payload())


@bp.route("/api/pos/cart/note", methods=["POST"])
@permission_required("pos.use")
def api_cart_note():
    payload = request.form or (request.json or {})
    cart_service.set_note(session, int(payload.get("product_id")), payload.get("note", ""))
    _push_display_cart()
    return jsonify(_cart_payload())


@bp.route("/api/pos/cart/clear", methods=["POST"])
@permission_required("pos.use")
def api_cart_clear():
    cart_service.clear(session)
    display_service.reset_to_welcome()
    return jsonify(_cart_payload())


@bp.route("/api/pos/cart/discount", methods=["POST"])
@permission_required("pos.use")
def api_cart_discount():
    payload = request.form or (request.json or {})
    discount_type = (payload.get("type") or "").upper()
    value = payload.get("value")

    if not discount_type or not value:
        cart_service.clear_discount(session)
        _push_display_cart()
        return jsonify(_cart_payload())

    tax_enabled, rate = _tax_settings()
    calculation = cart_service.calculate(session, tax_enabled=tax_enabled, default_tax_rate=rate)
    # Authorise now so the cashier gets immediate feedback.
    try:
        sales_service.resolve_discount(calculation["subtotal_minor"],
                                       {"type": discount_type, "value": value}, g.user)
    except ValidationError as exc:
        return jsonify({"ok": False, "error": exc.message}), 400

    cart_service.set_discount(session, discount_type, value)
    _push_display_cart()
    payload_out = _cart_payload()
    audit.record(audit.DISCOUNT_APPLIED, user=g.user, entity_type="Cart",
                 description=f"{g.user.full_name} applied a {discount_type} discount of {value}",
                 new_value={"type": discount_type, "value": str(value)},
                 ip_address=request.remote_addr, commit=True)
    return jsonify(payload_out)


@bp.route("/api/pos/cart/discount/clear", methods=["POST"])
@permission_required("pos.use")
def api_cart_discount_clear():
    cart_service.clear_discount(session)
    _push_display_cart()
    return jsonify(_cart_payload())


# ---------------------------------------------------------------------------
# Held sales
# ---------------------------------------------------------------------------
@bp.route("/api/pos/hold", methods=["POST"])
@permission_required("sales.hold")
def api_hold():
    from app.database.models import HeldSale
    from app.utils.money import to_minor

    payload = cart_service.to_payload(session)
    if not payload:
        return jsonify({"ok": False, "error": "The cart is empty."}), 400

    tax_enabled, rate = _tax_settings()
    calculation = cart_service.calculate(session, tax_enabled=tax_enabled, default_tax_rate=rate)

    label = (request.form.get("label") or request.json.get("label") if request.is_json
             else request.form.get("label")) or ""
    if not label:
        first = calculation["items"][0]["name"] if calculation["items"] else "Sale"
        label = f"{first}" + (f" +{len(calculation['items']) - 1} more" if len(calculation["items"]) > 1 else "")

    held = HeldSale(
        reference=f"HLD-{utcnow().strftime('%H%M%S')}-{len(HeldSale.query.filter_by(cashier_id=g.user.id).all()) + 1:02d}",
        label=label[:120],
        cashier_id=g.user.id,
        terminal_id=getattr(_current_terminal(), "id", None),
        payload=json.dumps(payload),
        item_count=calculation["item_count"],
        subtotal=naira_of(calculation["subtotal_minor"]),
        total_amount=naira_of(calculation["total_minor"]),
        status="HELD",
    )
    db.session.add(held)
    cart_service.clear(session)
    db.session.commit()
    display_service.reset_to_welcome()

    audit.record(audit.SALE_HOLD, user=g.user, entity_type="HeldSale", entity_id=held.reference,
                 description=f"{g.user.full_name} held a sale ({held.label})",
                 ip_address=request.remote_addr, commit=True)
    return jsonify({"ok": True, "message": f"Sale held as {held.reference}."})


@bp.route("/api/pos/held")
@permission_required("pos.use")
def api_held_list():
    from app.database.models import HeldSale

    query = HeldSale.query.filter_by(status="HELD")
    if not g.user.has_any_permission("shifts.manage", "sales.view"):
        query = query.filter_by(cashier_id=g.user.id)
    held = query.order_by(HeldSale.created_at.desc()).limit(50).all()
    return jsonify({"ok": True, "held": [
        {"id": h.id, "reference": h.reference, "label": h.label, "item_count": h.item_count,
         "total": str(from_minor(int(h.total_amount))), "created_at": h.created_at.isoformat()}
        for h in held
    ]})


@bp.route("/api/pos/held/<int:held_id>/retrieve", methods=["POST"])
@permission_required("sales.hold")
def api_held_retrieve(held_id: int):
    from app.database.models import HeldSale

    held = db.session.get(HeldSale, held_id)
    if held is None or held.status != "HELD":
        return jsonify({"ok": False, "error": "That held sale is no longer available."}), 404

    cart_service.from_payload(session, json.loads(held.payload))
    held.status = "RETRIEVED"
    held.retrieved_at = utcnow()
    db.session.commit()
    _push_display_cart()

    audit.record(audit.SALE_HOLD_RETRIEVE, user=g.user, entity_type="HeldSale", entity_id=held.reference,
                 description=f"{g.user.full_name} retrieved held sale {held.reference}",
                 ip_address=request.remote_addr, commit=True)
    return jsonify(_cart_payload())


@bp.route("/api/pos/held/<int:held_id>/delete", methods=["POST"])
@permission_required("sales.hold")
def api_held_delete(held_id: int):
    from app.database.models import HeldSale

    held = db.session.get(HeldSale, held_id)
    if held is None:
        return jsonify({"ok": False, "error": "Held sale not found."}), 404
    held.status = "CANCELLED"
    db.session.commit()
    return jsonify({"ok": True, "message": "Held sale cancelled."})

# ---------------------------------------------------------------------------
# Completing a sale
# ---------------------------------------------------------------------------
@bp.route("/api/pos/checkout", methods=["POST"])
@permission_required("pos.use")
def api_checkout():
    """Validate and commit a sale.

    Sequence: server-side re-validation -> atomic DB commit -> THEN hardware.
    A printer or drawer failure after this point never reverses the sale; the
    cashier is offered a reprint instead.
    """
    payload = request.form if request.form else (request.json or {})
    payment_method = (payload.get("payment_method") or "CASH").upper()
    amount_received = payload.get("amount_received")
    note = (payload.get("note") or "")[:255]
    discount_spec = cart_service.get_discount(session)
    if payload.get("discount_type"):
        discount_spec = {"type": payload["discount_type"], "value": payload.get("discount_value")}

    tax_enabled, rate = _tax_settings()
    allow_negative = settings_service.allow_negative_stock()

    user = g.user
    shift = _open_shift(user)
    if shift is None and not settings_service.get_bool("system.allow_sale_without_shift", False):
        return jsonify({"ok": False, "error": "Please open your shift before completing a sale."}), 400

    cart_payload = cart_service.to_payload(session)
    if not cart_payload:
        return jsonify({"ok": False, "error": "The cart is empty."}), 400

    terminal = _current_terminal()

    # Publish the PAYMENT screen to the customer display while we work.
    if amount_received not in (None, ""):
        try:
            calculation = cart_service.calculate(session, tax_enabled=tax_enabled, default_tax_rate=rate)
            display_service.publish(
                display_service.MODE_PAYMENT,
                display_service.payment_state(calculation, payment_method, amount_received),
            )
        except Exception:  # noqa: BLE001 - display must never block a sale
            pass

    try:
        outcome = sales_service.complete_sale(
            user=user,
            cart_payload=cart_payload,
            payment_method=payment_method,
            amount_received=amount_received,
            discount_spec=discount_spec,
            tax_enabled=tax_enabled,
            default_tax_rate=rate,
            allow_negative_stock=allow_negative,
            note=note,
            shift_id=shift.id if shift else None,
            terminal_id=getattr(terminal, "id", None),
        )
    except ValidationError as exc:
        display_service.publish(display_service.MODE_CART, _cart_for_display())
        return jsonify({"ok": False, "error": exc.message}), exc.status_code
    except Exception as exc:  # noqa: BLE001
        log.exception("Checkout failed")
        display_service.publish(display_service.MODE_CART, _cart_for_display())
        return jsonify({
            "ok": False,
            "error": "The sale could not be completed. No changes were saved. Please try again.",
            "detail": str(exc),
        }), 500

    sale = outcome.sale

    # ---- Hardware AFTER the commit ----
    warnings: list[str] = []
    from app.sales import receipt as receipt_service

    print_result = receipt_service.print_receipt(sale, user=user)
    if not print_result.success:
        warnings.append("Sale completed, but receipt printing failed.")
        log.warning("Receipt failed for %s: %s", sale.receipt_number, print_result.detail)

    drawer_result = outcome.hardware.get("drawer")
    if drawer_result and drawer_result.get("status") == "FAILED":
        warnings.append("Sale completed, but the cash drawer did not open.")

    if settings_service.receipt_options().get("open_drawer") and payment_method == "CASH":
        if not drawer_result:
            receipt_service.open_cash_drawer(sale=sale, user=user, trigger="SALE")

    _after_sale_complete(sale)

    return jsonify({
        "ok": True,
        "sale": sale.to_dict(),
        "receipt_number": sale.receipt_number,
        "total": fmt_minor(minor_of(sale.total_amount)),
        "change": fmt(outcome.change),
        "amount_received": fmt_minor(minor_of(sale.amount_received)),
        "printed": print_result.success,
        "print_message": print_result.message,
        "drawer": drawer_result,
        "warnings": warnings,
        "message": f"Sale {sale.receipt_number} completed successfully.",
    })


def _cart_for_display() -> dict:
    tax_enabled, rate = _tax_settings()
    calculation = cart_service.calculate(session, tax_enabled=tax_enabled, default_tax_rate=rate)
    return display_service.cart_state(calculation)


@bp.route("/api/pos/quote", methods=["POST"])
@permission_required("pos.use")
def api_quote():
    """Compute change due for the amount typed into the payment pad."""
    from app.utils.money import to_decimal

    payload = request.form if request.form else (request.json or {})
    tax_enabled, rate = _tax_settings()
    calculation = cart_service.calculate(session, tax_enabled=tax_enabled, default_tax_rate=rate)
    total = calculation["total"]

    try:
        received = to_decimal(payload.get("amount_received") or 0)
    except Exception:  # noqa: BLE001
        received = total

    change = received - total
    return jsonify({
        "ok": True,
        "total": str(total),
        "amount_received": str(received),
        "change": str(change if change > 0 else 0),
        "sufficient": received >= total,
        "shortfall": str((total - received) if received < total else 0),
    })


# ---------------------------------------------------------------------------
# Cashier's own transactions
# ---------------------------------------------------------------------------
@bp.route("/pos/transactions")
@permission_required("pos.use")
def my_transactions():
    """A cashier only ever sees their OWN transactions."""
    user = g.user
    page = request.args.get("page", 1, type=int)
    stmt = Sale.query.filter_by(cashier_id=user.id)
    if request.args.get("status"):
        stmt = stmt.filter_by(status=request.args["status"])
    sales = stmt.order_by(Sale.created_at.desc()).paginate(page=page, per_page=25, error_out=False)
    return render_template("pos/transactions.html", sales=sales, user=user)


@bp.route("/pos/transactions/<int:sale_id>")
@permission_required("pos.use")
def my_transaction_detail(sale_id: int):
    user = g.user
    sale = db.session.get(Sale, sale_id)
    if sale is None:
        return render_template("errors/error.html", code=404, message="Transaction not found."), 404
    # Cashiers may only open their own; admin/manager may open any.
    if sale.cashier_id != user.id and not user.has_any_permission("sales.view"):
        return render_template(
            "errors/error.html", code=403,
            message="You do not have permission to view this transaction.",
        ), 403
    return render_template("pos/transaction_detail.html", sale=sale, is_receipt=True)


@bp.route("/pos/receipt/<int:sale_id>/print", methods=["POST"])
@permission_required("pos.use")
def reprint_receipt(sale_id: int):
    """Reprint a receipt. Allowed after a print failure - never re-saves."""
    from app.sales import receipt as receipt_service

    user = g.user
    sale = db.session.get(Sale, sale_id)
    if sale is None:
        return jsonify({"ok": False, "error": "Transaction not found."}), 404
    if sale.cashier_id != user.id and not user.has_any_permission("sales.view"):
        return jsonify({"ok": False, "error": "You do not have permission to do that."}), 403

    result = receipt_service.print_receipt(sale, user=user, reprint=True)
    if result.success:
        audit.record("REPRINT_RECEIPT", user=user, entity_type="Sale", entity_id=sale.receipt_number,
                     description=f"{user.full_name} reprinted receipt {sale.receipt_number}",
                     ip_address=request.remote_addr, commit=True)
    return jsonify({
        "ok": result.success,
        "message": "Receipt reprinted." if result.success else result.message,
        "detail": result.detail,
    })
