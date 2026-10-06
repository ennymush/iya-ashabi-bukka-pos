"""Reporting service.

All figures are computed in SQL against the historical snapshot columns, never
against the current product price. Reports are therefore stable forever.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.database.types import minor_of
from app.database.models import (
    PAY_CARD,
    PAY_CASH,
    PAY_OTHER,
    PAY_TRANSFER,
    STATUS_COMPLETED,
    STATUS_REFUNDED,
    STATUS_VOIDED,
    InventoryMovement,
    Payment,
    Product,
    ReturnRecord,
    Sale,
    SaleItem,
    Shift,
    User,
)
from app.extensions import db
from app.utils.money import to_minor



def _naira(value) -> Decimal:
    """Normalise a SQL aggregate over a Money column to whole-Naira Decimal.

    SQLAlchemy propagates the Money type through ``func.sum()``, so aggregate
    results are ALREADY whole Naira. Only plain ints from non-typed expressions
    need scaling, and those are never produced here - hence no division.
    """
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


REPORT_TYPES = {
    "sales": "Sales Report",
    "products": "Product Sales Report",
    "cashiers": "Cashier Report",
    "payments": "Payment Report",
    "inventory": "Inventory Report",
    "shifts": "Shift Report",
    "refunds": "Refund Report",
    "voids": "Void Report",
    "profit": "Profit Report",
    "audit": "Audit Report",
}

# Report key -> (endpoint name, permission required to open it).
# Kept beside REPORT_TYPES so adding a report cannot silently break the menu.
REPORT_ENDPOINTS = {
    "sales": ("sales_report", "reports.view"),
    "products": ("product_report", "reports.view"),
    "cashiers": ("cashier_report", "reports.view"),
    "payments": ("payment_report", "reports.view"),
    "inventory": ("inventory_report", "reports.view"),
    "shifts": ("shift_report", "shifts.manage"),
    "refunds": ("refund_report", "reports.view"),
    "voids": ("void_report", "reports.view"),
    "profit": ("profit_report", "reports.profit"),
    "audit": ("audit_report", "audit.view"),
}


def _in_range(column, start: date | None, end: date | None):
    if start:
        column = column >= start
    if end:
        column = column <= end
    return column


def sales_summary(start: date | None = None, end: date | None = None,
                  cashier_id: int | None = None, payment: str | None = None) -> dict:
    """Headline totals. Completed and partially-refunded sales count."""
    stmt = db.session.query(
        db.func.count(Sale.id).label("transactions"),
        db.func.coalesce(db.func.sum(Sale.subtotal), 0).label("subtotal"),
        db.func.coalesce(db.func.sum(Sale.discount_amount), 0).label("discount"),
        db.func.coalesce(db.func.sum(Sale.tax_amount), 0).label("tax"),
        db.func.coalesce(db.func.sum(Sale.total_amount), 0).label("total"),
    ).filter(Sale.status.in_((STATUS_COMPLETED, STATUS_REFUNDED)))

    if start or end:
        stmt = stmt.filter(_in_range(Sale.sale_date, start, end))
    if cashier_id:
        stmt = stmt.filter(Sale.cashier_id == cashier_id)
    if payment:
        stmt = stmt.filter(Sale.payment_method == payment)

    row = stmt.one()

    payments = {method: 0 for method in (PAY_CASH, PAY_TRANSFER, PAY_CARD, PAY_OTHER)}
    payment_stmt = (
        db.session.query(Payment.method, db.func.coalesce(db.func.sum(Payment.amount), 0))
        .join(Sale, Payment.sale_id == Sale.id)
        .filter(Payment.is_refund.is_(False), Sale.status.in_((STATUS_COMPLETED, STATUS_REFUNDED)))
    )
    if start or end:
        payment_stmt = payment_stmt.filter(_in_range(Sale.sale_date, start, end))
    for method, amount in payment_stmt.group_by(Payment.method).all():
        payments[method] = amount

    items_sold = (
        db.session.query(db.func.coalesce(db.func.sum(SaleItem.quantity), 0))
        .join(Sale, SaleItem.sale_id == Sale.id)
        .filter(Sale.status.in_((STATUS_COMPLETED, STATUS_REFUNDED)))
    )
    if start or end:
        items_sold = items_sold.filter(_in_range(Sale.sale_date, start, end))
    if cashier_id:
        items_sold = items_sold.filter(Sale.cashier_id == cashier_id)
    if payment:
        items_sold = items_sold.filter(Sale.payment_method == payment)

    refunds = db.session.query(db.func.coalesce(db.func.sum(ReturnRecord.total_amount), 0))
    voids = db.session.query(db.func.coalesce(db.func.sum(Sale.total_amount), 0)).filter(
        Sale.status == STATUS_VOIDED)
    if start or end:
        refunds = refunds.filter(_in_range(ReturnRecord.sale_date, start, end))
        voids = voids.filter(_in_range(Sale.sale_date, start, end))

    return {
        "transactions": row.transactions or 0,
        "subtotal": _naira(row.subtotal),
        "discount": _naira(row.discount),
        "tax": _naira(row.tax),
        "total": _naira(row.total),
        "cash": _naira(payments.get(PAY_CASH)),
        "transfer": _naira(payments.get(PAY_TRANSFER)),
        "card": _naira(payments.get(PAY_CARD)),
        "other": _naira(payments.get(PAY_OTHER)),
        "items_sold": items_sold.scalar() or 0,
        "refunds": _naira(refunds.scalar()),
        "voids": _naira(voids.scalar()),
    }


def product_sales(start: date | None = None, end: date | None = None,
                  category_id: int | None = None, cashier_id: int | None = None) -> list[dict]:
    """Revenue, cost and gross profit per product, from frozen snapshots."""
    stmt = (
        db.session.query(
            SaleItem.product_name.label("name"),
            SaleItem.sku.label("sku"),
            SaleItem.category_name.label("category"),
            db.func.coalesce(db.func.sum(SaleItem.quantity), 0).label("quantity"),
            db.func.coalesce(db.func.sum(SaleItem.line_total), 0).label("revenue"),
            db.func.coalesce(db.func.sum(SaleItem.cost_price * SaleItem.quantity) / 100.0, 0).label("cost"),
        )
        .join(Sale, SaleItem.sale_id == Sale.id)
        .filter(Sale.status.in_((STATUS_COMPLETED, STATUS_REFUNDED)))
    )
    if start or end:
        stmt = stmt.filter(_in_range(Sale.sale_date, start, end))
    if cashier_id:
        stmt = stmt.filter(Sale.cashier_id == cashier_id)
    if category_id:
        # Category filtering joins through the product's current category link.
        stmt = stmt.filter(
            SaleItem.product_id.in_(
                db.session.query(Product.id).filter(Product.category_id == category_id)
            )
        )

    rows = stmt.group_by(SaleItem.product_name, SaleItem.sku, SaleItem.category_name).all()

    results = []
    for row in rows:
        # `revenue` is a typed Money aggregate (Decimal naira); `cost` is scaled
        # in SQL and arrives as a float. Normalise both before doing maths.
        revenue = _naira(row.revenue)
        cost = _naira(row.cost)
        profit = _naira(revenue - cost)
        results.append({
            "name": row.name,
            "sku": row.sku,
            "category": row.category or "",
            "quantity": row.quantity or 0,
            "revenue": revenue,
            "cost": cost,
            "profit": profit,
            "margin": float((profit / revenue) * 100) if revenue else 0.0,
        })
    return sorted(results, key=lambda r: r["revenue"], reverse=True)


def cashier_report(start: date | None = None, end: date | None = None) -> list[dict]:
    stmt = (
        db.session.query(
            Sale.cashier_id.label("cashier_id"),
            Sale.cashier_name.label("name"),
            db.func.count(Sale.id).label("transactions"),
            db.func.coalesce(db.func.sum(Sale.total_amount), 0).label("total"),
            db.func.coalesce(db.func.sum(Sale.discount_amount), 0).label("discount"),
        )
        .filter(Sale.status.in_((STATUS_COMPLETED, STATUS_REFUNDED)))
    )
    if start or end:
        stmt = stmt.filter(_in_range(Sale.sale_date, start, end))
    base = {r.cashier_id: r for r in stmt.group_by(Sale.cashier_id, Sale.cashier_name).all()}

    payments: dict[int, dict] = {}
    pay_stmt = (
        db.session.query(
            Sale.cashier_id.label("cashier_id"),
            Payment.method,
            db.func.coalesce(db.func.sum(Payment.amount), 0).label("amount"),
        )
        .join(Payment, Payment.sale_id == Sale.id)
        .filter(Payment.is_refund.is_(False), Sale.status.in_((STATUS_COMPLETED, STATUS_REFUNDED)))
    )
    if start or end:
        pay_stmt = pay_stmt.filter(_in_range(Sale.sale_date, start, end))
    for row in pay_stmt.group_by(Sale.cashier_id, Payment.method).all():
        payments.setdefault(row.cashier_id, {})[row.method] = row.amount

    refunds: dict[int, int] = {}
    refund_stmt = db.session.query(
        ReturnRecord.user_id, db.func.coalesce(db.func.sum(ReturnRecord.total_amount), 0)
    )
    if start or end:
        refund_stmt = refund_stmt.filter(_in_range(ReturnRecord.sale_date, start, end))
    for user_id, amount in refund_stmt.group_by(ReturnRecord.user_id).all():
        refunds[user_id] = amount

    voids_stmt = db.session.query(
        Sale.cashier_id, db.func.coalesce(db.func.sum(Sale.total_amount), 0)
    ).filter(Sale.status == STATUS_VOIDED)
    if start or end:
        voids_stmt = voids_stmt.filter(_in_range(Sale.sale_date, start, end))
    voids = {cid: amount for cid, amount in voids_stmt.group_by(Sale.cashier_id).all()}

    results = []
    for cashier_id, row in base.items():
        by_method = payments.get(cashier_id, {})
        results.append({
            "cashier_id": cashier_id,
            "name": row.name,
            "transactions": row.transactions,
            "total": _naira(row.total),
            "cash": _naira(by_method.get(PAY_CASH)),
            "transfer": _naira(by_method.get(PAY_TRANSFER)),
            "card": _naira(by_method.get(PAY_CARD)),
            "other": _naira(by_method.get(PAY_OTHER)),
            "discount": _naira(row.discount),
            "refunds": _naira(refunds.get(cashier_id)),
            "voids": _naira(voids.get(cashier_id)),
        })
    return sorted(results, key=lambda r: r["total"], reverse=True)


def payment_report(start: date | None = None, end: date | None = None) -> list[dict]:
    stmt = (
        db.session.query(
            Payment.method,
            db.func.count(db.distinct(Payment.sale_id)).label("count"),
            db.func.coalesce(db.func.sum(Payment.amount), 0).label("amount"),
        )
        .join(Sale, Payment.sale_id == Sale.id)
        .filter(Payment.is_refund.is_(False), Sale.status.in_((STATUS_COMPLETED, STATUS_REFUNDED)))
    )
    if start or end:
        stmt = stmt.filter(_in_range(Sale.sale_date, start, end))
    rows = stmt.group_by(Payment.method).all()
    grand = sum(r.amount or 0 for r in rows) or 1
    return [{
        "method": r.method,
        "count": r.count,
        "amount": _naira(r.amount),
        "share": (r.amount or 0) / grand * 100,
    } for r in sorted(rows, key=lambda r: r.amount or 0, reverse=True)]


def daily_sales(start: date | None = None, end: date | None = None,
                cashier_id: int | None = None) -> list[dict]:
    stmt = (
        db.session.query(
            Sale.sale_date.label("day"),
            db.func.count(Sale.id).label("transactions"),
            db.func.coalesce(db.func.sum(Sale.total_amount), 0).label("total"),
            db.func.coalesce(db.func.sum(Sale.discount_amount), 0).label("discount"),
        )
        .filter(Sale.status.in_((STATUS_COMPLETED, STATUS_REFUNDED)))
    )
    if start or end:
        stmt = stmt.filter(_in_range(Sale.sale_date, start, end))
    if cashier_id:
        stmt = stmt.filter(Sale.cashier_id == cashier_id)
    return [{
        "date": r.day,
        "transactions": r.transactions,
        "total": _naira(r.total),
        "discount": _naira(r.discount),
    } for r in stmt.group_by(Sale.sale_date).order_by(Sale.sale_date).all()]


def inventory_report() -> list[dict]:
    from app.database.models import Category

    products = Product.query.order_by(Product.name).all()
    rows = []
    for p in products:
        rows.append({
            "name": p.name,
            "sku": p.sku,
            "category": p.category.name if p.category else "",
            "stock": p.stock_quantity,
            "minimum": p.minimum_stock,
            "status": p.stock_status,
            "cost": _naira(p.cost_price),
            "price": _naira(p.selling_price),
            "value": _naira(to_minor(p.cost_price) * p.stock_quantity / 100.0),
            "active": p.is_active,
        })
    return rows


def shift_report(start: date | None = None, end: date | None = None) -> list[dict]:
    stmt = db.session.query(Shift).join(User, Shift.user_id == User.id)
    if start:
        stmt = stmt.filter(db.func.date(Shift.opened_at) >= start)
    if end:
        stmt = stmt.filter(db.func.date(Shift.opened_at) <= end)
    shifts = stmt.order_by(Shift.opened_at.desc()).limit(300).all()
    return [{
        "id": s.id,
        "cashier": s.user.full_name if s.user else "",
        "opened_at": s.opened_at,
        "closed_at": s.closed_at,
        "opening_cash": _naira(s.opening_cash),
        "expected_cash": _naira(s.expected_cash) if s.expected_cash is not None else None,
        "actual_cash": _naira(s.actual_cash) if s.actual_cash is not None else None,
        "variance": _naira(s.variance) if s.variance is not None else None,
        "cash_sales": _naira(s.cash_sales),
        "transfer_sales": _naira(s.transfer_sales),
        "card_sales": _naira(s.card_sales),
        "total_sales": _naira(s.total_sales),
        "transactions": s.transaction_count,
        "is_open": s.is_open,
    } for s in shifts]


def refund_report(start: date | None = None, end: date | None = None) -> list[dict]:
    stmt = db.session.query(ReturnRecord)
    if start or end:
        stmt = stmt.filter(_in_range(ReturnRecord.sale_date, start, end))
    records = stmt.order_by(ReturnRecord.created_at.desc()).limit(500).all()
    return [{
        "return_number": r.return_number,
        "original_receipt": r.original_receipt,
        "sale_id": r.sale_id,
        "user": r.user.full_name if r.user else "",
        "approved_by": r.approved_by.full_name if r.approved_by else "",
        "reason": r.reason,
        "method": r.refund_method,
        "amount": _naira(r.total_amount),
        "date": r.sale_date,
        "time": r.sale_time,
        "items": [{"name": i.product_name, "quantity": i.quantity} for i in r.items],
    } for r in records]


def void_report(start: date | None = None, end: date | None = None) -> list[dict]:
    stmt = db.session.query(Sale).filter(Sale.status == STATUS_VOIDED)
    if start or end:
        stmt = stmt.filter(_in_range(Sale.sale_date, start, end))
    sales = stmt.order_by(Sale.voided_at.desc()).limit(500).all()
    return [{
        "receipt_number": s.receipt_number,
        "date": s.sale_date,
        "time": s.sale_time,
        "cashier": s.cashier_name,
        "total": _naira(s.total_amount),
        "voided_by": s.voided_by.full_name if s.voided_by else "",
        "voided_at": s.voided_at,
        "reason": s.void_reason or "",
    } for s in sales]


def profit_report(start: date | None = None, end: date | None = None) -> dict:
    stmt = (
        db.session.query(
            db.func.coalesce(db.func.sum(SaleItem.line_total), 0).label("revenue"),
            db.func.coalesce(db.func.sum(SaleItem.cost_price * SaleItem.quantity) / 100.0, 0).label("cost"),
        )
        .join(Sale, SaleItem.sale_id == Sale.id)
        .filter(Sale.status.in_((STATUS_COMPLETED, STATUS_REFUNDED)))
    )
    if start or end:
        stmt = stmt.filter(_in_range(Sale.sale_date, start, end))
    row = stmt.one()
    # `revenue` keeps the Money type (whole Naira Decimal); `cost` is a scaled
    # SQL expression so it arrives as a float. Normalise before doing maths.
    revenue = _naira(row.revenue)
    cost = _naira(row.cost)
    profit = _naira(revenue - cost)
    return {
        "revenue": revenue,
        "cost": cost,
        "profit": profit,
        "margin": float((profit / revenue) * 100) if revenue else 0.0,
    }


def audit_report(start: date | None = None, end: date | None = None,
                 action: str | None = None, user_id: int | None = None) -> list[dict]:
    from app.database.models import AuditLog

    stmt = db.session.query(AuditLog)
    if start:
        stmt = stmt.filter(AuditLog.created_at >= start)
    if end:
        from datetime import datetime, time as dtime

        stmt = stmt.filter(AuditLog.created_at <= datetime.combine(end, dtime.max))
    if action:
        stmt = stmt.filter(AuditLog.action == action)
    if user_id:
        stmt = stmt.filter(AuditLog.user_id == user_id)

    entries = stmt.order_by(AuditLog.created_at.desc()).limit(2000).all()
    return [{
        "created_at": e.created_at,
        "username": e.username or "",
        "role": e.user_role or "",
        "action": e.action,
        "entity_type": e.entity_type,
        "entity_id": e.entity_id or "",
        "description": e.description or "",
        "ip": e.ip_address or "",
    } for e in entries]


def low_stock_count() -> int:
    return db.session.query(db.func.count(Product.id)).filter(
        Product.is_active.is_(True),
        Product.stock_quantity <= Product.minimum_stock,
    ).scalar() or 0


def open_shift_count() -> int:
    return Shift.query.filter_by(is_open=True).count()


def active_cashier_count() -> int:
    from app.database.models import ROLE_CASHIER, Role

    role = Role.query.filter_by(name=ROLE_CASHIER).first()
    if role is None:
        return 0
    return User.query.filter_by(role_id=role.id, is_active=True).count()
