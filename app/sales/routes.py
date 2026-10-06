"""Admin sales routes: transaction list, detail, void, refund, print."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.audit import service as audit
from app.database.models import (
    PAYMENT_METHODS,
    STATUS_COMPLETED,
    STATUS_REFUNDED,
    STATUS_VOIDED,
    ReturnRecord,
    Sale,
SaleItem,
    User,
)
from app.extensions import db
from app.sales import service as sales_service
from app.sales import receipt as receipt_service
from app.utils.errors import ValidationError
from app.utils.security import permission_required

bp = Blueprint("sales", __name__, url_prefix="/admin/sales")


def parse_date(value: str | None, default: date | None = None):
    if not value:
        return default
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except (ValueError, TypeError):
            continue
    return default


def date_range_from_request():
    """Resolve the ?range= shortcut used by every report and list."""
    preset = request.args.get("range", "today")
    today = date.today()
    if request.args.get("date_from") or request.args.get("date_to"):
        return (
            parse_date(request.args.get("date_from"), today),
            parse_date(request.args.get("date_to"), today),
            "custom",
        )
    if preset == "today":
        return today, today, "today"
    if preset == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1), "yesterday"
    if preset == "week":
        return today - timedelta(days=today.weekday()), today, "week"
    if preset == "month":
        return today.replace(day=1), today, "month"
    if preset == "all":
        return None, None, "all"
    return today, today, "today"


def apply_filters(stmt):
    """Shared transaction filtering: date, cashier, payment, status, search."""
    start, end, _preset = date_range_from_request()
    if start:
        stmt = stmt.filter(Sale.sale_date >= start)
    if end:
        stmt = stmt.filter(Sale.sale_date <= end)

    if request.args.get("cashier_id", type=int):
        stmt = stmt.filter(Sale.cashier_id == request.args["cashier_id"])
    if request.args.get("payment"):
        stmt = stmt.filter(Sale.payment_method == request.args["payment"])
    if request.args.get("status"):
        stmt = stmt.filter(Sale.status == request.args["status"])
    if request.args.get("receipt"):
        stmt = stmt.filter(Sale.receipt_number.ilike(f"%{request.args['receipt']}%"))
    if request.args.get("q"):
        like = f"%{request.args['q']}%"
        sub = (
            db.session.query(db.func.distinct(SaleItem.sale_id))
            .filter(SaleItem.product_name.ilike(like))
        )
        stmt = stmt.filter(db.or_(Sale.receipt_number.ilike(like),
                                  Sale.cashier_name.ilike(like), Sale.id.in_(sub)))
    return stmt


@bp.route("/transactions")
@permission_required("sales.view")
def transactions():
    page = request.args.get("page", 1, type=int)
    stmt = apply_filters(Sale.query)
    sales = stmt.order_by(Sale.created_at.desc()).paginate(page=page, per_page=30, error_out=False)
    start, end, preset = date_range_from_request()

    totals = {
        "count": sales.total,
        "subtotal": sum(int(s.subtotal) for s in sales.items),
        "discount": sum(int(s.discount_amount) for s in sales.items),
        "tax": sum(int(s.tax_amount) for s in sales.items),
        "total": sum(int(s.total_amount) for s in sales.items
                     if s.status in (STATUS_COMPLETED, STATUS_REFUNDED)),
    }

    return render_template(
        "admin/sales/transactions.html",
        sales=sales,
        cashiers=User.query.order_by(User.full_name).all(),
        payment_methods=PAYMENT_METHODS,
        start=start, end=end, preset=preset,
        totals=totals,
    )


@bp.route("/transactions/<int:sale_id>")
@permission_required("sales.view")
def transaction_detail(sale_id: int):
    sale = db.session.get(Sale, sale_id)
    if sale is None:
        return render_template("errors/error.html", code=404, message="Transaction not found."), 404
    show_cost = g.user.has_any_permission("reports.profit")
    profit = sum(int(i.line_profit) for i in sale.items) if show_cost else None
    return render_template(
        "admin/sales/transaction_detail.html",
        sale=sale,
        show_cost=show_cost,
        profit=profit,
        returns=sale.returns,
    )


@bp.route("/transactions/<int:sale_id>/void", methods=["POST"])
@permission_required("sales.void")
def void_transaction(sale_id: int):
    sale = db.session.get(Sale, sale_id)
    if sale is None:
        flash("Transaction not found.", "error")
    else:
        try:
            sales_service.void_sale(sale, g.user, request.form.get("reason", ""))
            flash(f"Transaction {sale.receipt_number} has been voided. Stock was restored.", "success")
        except ValidationError as exc:
            flash(exc.message, "error")
    return redirect(request.referrer or url_for("sales.transactions"))


@bp.route("/transactions/<int:sale_id>/refund", methods=["GET", "POST"])
@permission_required("sales.refund")
def refund_transaction(sale_id: int):
    sale = db.session.get(Sale, sale_id)
    if sale is None:
        return render_template("errors/error.html", code=404, message="Transaction not found."), 404

    if request.method == "POST":
        payload = request.form.get("items_json") or "[]"
        try:
            import json

            lines = json.loads(payload)
        except (TypeError, ValueError):
            lines = []
        try:
            record = sales_service.refund_sale(
                sale, g.user, lines,
                reason=request.form.get("reason", ""),
                refund_method=request.form.get("refund_method", "CASH"),
                restock=bool(request.form.get("restock")),
                approved_by=g.user,
            )
            flash(f"Refund {record.return_number} processed successfully.", "success")
            return redirect(url_for("sales.transaction_detail", sale_id=sale.id))
        except ValidationError as exc:
            flash(exc.message, "error")

    return render_template(
        "admin/sales/refund.html",
        sale=sale,
        payment_methods=PAYMENT_METHODS,
    )


@bp.route("/transactions/<int:sale_id>/print-receipt", methods=["POST"])
@permission_required("sales.print")
def print_receipt(sale_id: int):
    sale = db.session.get(Sale, sale_id)
    if sale is None:
        return jsonify({"ok": False, "error": "Transaction not found."}), 404
    result = receipt_service.print_receipt(sale, user=g.user, reprint=True)
    if result.success:
        audit.record("REPRINT_RECEIPT", user=g.user, entity_type="Sale", entity_id=sale.receipt_number,
                     description=f"{g.user.full_name} reprinted receipt {sale.receipt_number}",
                     ip_address=request.remote_addr, commit=True)
    return jsonify({"ok": result.success, "message": result.message, "detail": result.detail})


@bp.route("/returns")
@permission_required("sales.refund")
def returns_list():
    page = request.args.get("page", 1, type=int)
    stmt = ReturnRecord.query
    start, end, preset = date_range_from_request()
    if start:
        stmt = stmt.filter(ReturnRecord.sale_date >= start)
    if end:
        stmt = stmt.filter(ReturnRecord.sale_date <= end)
    if request.args.get("q"):
        like = f"%{request.args['q']}%"
        stmt = stmt.filter(db.or_(ReturnRecord.original_receipt.ilike(like),
                                  ReturnRecord.return_number.ilike(like)))
    records = stmt.order_by(ReturnRecord.created_at.desc()).paginate(page=page, per_page=30, error_out=False)
    return render_template(
        "admin/sales/returns.html", records=records, start=start, end=end, preset=preset
    )


@bp.route("/voids")
@permission_required("sales.view")
def voids_list():
    page = request.args.get("page", 1, type=int)
    stmt = Sale.query.filter_by(status=STATUS_VOIDED)
    start, end, preset = date_range_from_request()
    if start:
        stmt = stmt.filter(Sale.sale_date >= start)
    if end:
        stmt = stmt.filter(Sale.sale_date <= end)
    sales = stmt.order_by(Sale.voided_at.desc()).paginate(page=page, per_page=30, error_out=False)
    return render_template(
        "admin/sales/voids.html", sales=sales, start=start, end=end, preset=preset
    )
