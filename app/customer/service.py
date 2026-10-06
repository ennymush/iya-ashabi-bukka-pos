"""Customer display service.

The customer screen shows ONLY customer-safe information: items, quantities,
unit prices and totals. Cost price, profit, stock levels, cashier names and any
admin control never reach this payload - see the field whitelist below.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.database import service_settings as settings_service
from app.database.models import DisplayState, Terminal, utcnow
from app.extensions import db

MODE_WELCOME = "WELCOME"
MODE_CART = "CART"
MODE_PAYMENT = "PAYMENT"
MODE_COMPLETE = "COMPLETE"


def _terminal() -> Terminal | None:
    """The terminal this display belongs to (settings-configured or first)."""
    configured = settings_service.get_int("display.terminal_id", 0)
    if configured:
        return db.session.get(Terminal, configured)
    return Terminal.query.order_by(Terminal.id).first()


def _state_row() -> DisplayState:
    terminal = _terminal()
    row = DisplayState.query.filter_by(terminal_id=terminal.id if terminal else None).first()
    if row is None:
        row = DisplayState(terminal_id=terminal.id if terminal else None, mode=MODE_WELCOME)
        db.session.add(row)
        db.session.commit()
    return row


def publish(mode: str, payload: dict) -> dict:
    """Write the current customer-facing state."""
    row = _state_row()
    row.mode = mode
    row.set_payload(_sanitise(payload))
    db.session.commit()
    return row.get_payload()


def _sanitise(payload: dict) -> dict:
    """Whitelist the fields allowed on the customer screen."""
    allowed = {
        "items", "subtotal", "discount", "tax", "total",
        "amount_received", "change", "payment_method", "item_count",
        "thankyou_message", "message", "reference", "business",
    }
    return {k: v for k, v in (payload or {}).items() if k in allowed}


def cart_state(calculation: dict, discount_spec: dict | None = None) -> dict:
    """Build the CART payload from the server-side cart calculation."""
    return {
        "items": [
            {
                "name": item["name"],
                "quantity": item["quantity"],
                "unit_price": str(item["unit_price"]),
                "line_total": str(item["line_total"]),
            }
            for item in calculation["items"]
        ],
        "subtotal": str(calculation["subtotal"]),
        "discount": str(calculation["discount"]),
        "tax": str(calculation["tax"]),
        "total": str(calculation["total"]),
        "item_count": calculation["item_count"],
    }


def payment_state(calculation: dict, payment_method: str, amount_received) -> dict:
    from app.utils.money import to_decimal

    total = calculation["total"]
    received = to_decimal(amount_received) if amount_received else total
    change = received - total
    if change < 0:
        change = type(total)(0)
    return {
        "total": str(total),
        "amount_received": str(received),
        "change": str(change),
        "payment_method": str(payment_method).upper(),
        "item_count": calculation["item_count"],
    }


def complete_state(sale) -> dict:
    return {
        "reference": sale.receipt_number,
        "total": str(sale.total_amount),
        "amount_received": str(sale.amount_received),
        "change": str(sale.change_given),
        "payment_method": sale.payment_method,
        "thankyou_message": settings_service.display_options()["thankyou_message"],
    }


def welcome() -> dict:
    biz = settings_service.business()
    opts = settings_service.display_options()
    return {
        "business": {
            "name": biz["name"],
            "tagline": biz["tagline"],
            "logo_path": biz["logo_path"],
        },
        "message": opts["welcome_message"],
        "promotional_message": opts["promotional_message"],
        "promo_image": biz["promo_image"],
    }


def reset_to_welcome() -> dict:
    publish(MODE_WELCOME, welcome())
    return welcome()


def current_state() -> dict:
    """Payload consumed by the customer display page."""
    row = _state_row()
    payload = row.get_payload()
    biz = settings_service.business()
    opts = settings_service.display_options()
    return {
        "mode": row.mode,
        "version": row.version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "data": payload,
        "business": {
            "name": biz["name"],
            "tagline": biz["tagline"],
            "logo_path": opts["logo_path"] or biz["logo_path"],
            "promo_message": opts["promotional_message"],
        },
        "welcome_message": opts["welcome_message"],
        "thankyou_message": opts["thankyou_message"],
        "show_totals": opts["show_totals"],
        "show_date_time": opts["show_date_time"],
    }


def public_business() -> dict:
    """Business details the customer display is allowed to show."""
    biz = settings_service.business()
    return {
        "name": biz["name"],
        "tagline": biz["tagline"],
        "address": [biz["address_line1"], biz["address_line2"], biz["address_city"]],
        "phone": biz["phone"],
        "phone_2": biz["phone_2"],
        "email": biz["email"],
        "logo_path": biz["logo_path"],
    }
