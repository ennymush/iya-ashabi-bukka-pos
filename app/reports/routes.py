"""Report routes and Excel export.

Every report supports the same filters (Today / Yesterday / This Week / This
Month / Custom, plus cashier, payment and status) and every report can be
exported to Excel.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from pathlib import Path

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    jsonify,
    render_template,
    request,
    send_file,
)

from app.database import service_settings as settings_service
from app.database.models import (
    PAYMENT_METHODS,
    ALL_STATUSES,
    AuditLog,
    Sale,
    SaleItem,
    User,
)
from app.extensions import db
from app.reports import excel as excel_service
from app.reports import service as report_service
from app.sales.routes import apply_filters, date_range_from_request, parse_date
from app.utils.logging_config import get_logger
from app.utils.security import permission_required

log = get_logger("reports")

bp = Blueprint("reports", __name__, url_prefix="/admin/reports")


def _range():
    start, end, preset = date_range_from_request()
    return start or date.today(), end or date.today(), preset


@bp.route("")
@permission_required("reports.view")
def index():
    start, end, preset = _range()
    summary = report_service.sales_summary(start, end)

    # Only offer reports this user is actually allowed to open.
    available = []
    for key, label in report_service.REPORT_TYPES.items():
        entry = report_service.REPORT_ENDPOINTS.get(key)
        if entry and g.user.has_any_permission(entry[1]):
            available.append((key, label, entry[0]))

    return render_template(
        "admin/reports/index.html",
        summary=summary,
        start=start, end=end, preset=preset,
        daily=report_service.daily_sales(start, end),
        available_reports=available,
    )


# ---------------------------------------------------------------------------
# Individual reports
# ---------------------------------------------------------------------------
@bp.route("/sales")
@permission_required("reports.view")
def sales_report():
    start, end, preset = _range()
    stmt = apply_filters(Sale.query)
    sales = stmt.order_by(Sale.created_at.desc()).limit(2000).all()
    summary = report_service.sales_summary(start, end)
    return render_template(
        "admin/reports/sales.html",
        sales=sales, summary=summary, start=start, end=end, preset=preset,
        cashiers=User.query.order_by(User.full_name).all(),
        payment_methods=PAYMENT_METHODS, statuses=ALL_STATUSES,
    )


@bp.route("/products")
@permission_required("reports.view")
def product_report():
    start, end, preset = _range()
    show_profit = g.user.has_any_permission("reports.profit")
    rows = report_service.product_sales(start, end)
    return render_template(
        "admin/reports/products.html",
        rows=rows, start=start, end=end, preset=preset, show_profit=show_profit,
    )


@bp.route("/cashiers")
@permission_required("reports.view")
def cashier_report():
    start, end, preset = _range()
    rows = report_service.cashier_report(start, end)
    return render_template(
        "admin/reports/cashiers.html", rows=rows, start=start, end=end, preset=preset
    )


@bp.route("/payments")
@permission_required("reports.view")
def payment_report():
    start, end, preset = _range()
    rows = report_service.payment_report(start, end)
    return render_template(
        "admin/reports/payments.html", rows=rows, start=start, end=end, preset=preset
    )


@bp.route("/inventory")
@permission_required("reports.view")
def inventory_report():
    rows = report_service.inventory_report()
    return render_template(
        "admin/reports/inventory.html",
        rows=rows,
        show_cost=g.user.has_any_permission("products.cost"),
    )


@bp.route("/profit")
@permission_required("reports.profit")
def profit_report():
    start, end, preset = _range()
    data = report_service.profit_report(start, end)
    products = report_service.product_sales(start, end)
    return render_template(
        "admin/reports/profit.html",
        data=data, products=products, start=start, end=end, preset=preset,
    )


@bp.route("/shifts")
@permission_required("shifts.manage")
def shift_report():
    start, end, preset = _range()
    rows = report_service.shift_report(start, end)
    return render_template(
        "admin/reports/shifts.html", rows=rows, start=start, end=end, preset=preset
    )


@bp.route("/refunds")
@permission_required("reports.view")
def refund_report():
    start, end, preset = _range()
    rows = report_service.refund_report(start, end)
    return render_template(
        "admin/reports/refunds.html", rows=rows, start=start, end=end, preset=preset
    )


@bp.route("/voids")
@permission_required("reports.view")
def void_report():
    start, end, preset = _range()
    rows = report_service.void_report(start, end)
    return render_template(
        "admin/reports/voids.html", rows=rows, start=start, end=end, preset=preset
    )


@bp.route("/audit")
@permission_required("audit.view")
def audit_report():
    start, end, preset = _range()
    rows = report_service.audit_report(start, end)
    return render_template(
        "admin/reports/audit.html",
        rows=rows, start=start, end=end, preset=preset,
        actions=_distinct_actions(),
        users=User.query.order_by(User.full_name).all(),
    )


def _distinct_actions() -> list[str]:
    rows = db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------
@bp.route("/export/<report_type>")
@permission_required("reports.export")
def export(report_type: str):
    """Stream a formatted .xlsx workbook for the requested report."""
    report_type = report_type.lower()
    start, end, preset = _range()

    if report_type == "sales":
        stmt = apply_filters(Sale.query)
        sales = stmt.order_by(Sale.created_at.desc()).limit(5000).all()
        workbook = excel_service.build_sales_workbook(
            start=start, end=end,
            sales=sales,
            summary=report_service.sales_summary(start, end),
            product_rows=report_service.product_sales(start, end),
            cashier_rows=report_service.cashier_report(start, end),
            payment_rows=report_service.payment_report(start, end),
        )
        filename = excel_service.default_export_name("Sales", end)
    elif report_type == "products":
        rows = report_service.product_sales(start, end)
        if not g.user.has_any_permission("reports.profit"):
            # Cashier-safe export: cost and profit columns removed.
            headers = ["Product", "SKU", "Category", "Quantity Sold", "Revenue"]
            body = [[r["name"], r["sku"] or "", r["category"] or "", r["quantity"], float(r["revenue"])]
                    for r in rows]
        else:
            headers = ["Product", "SKU", "Category", "Quantity Sold", "Revenue",
                       "Cost", "Gross Profit", "Margin %"]
            body = [[r["name"], r["sku"] or "", r["category"] or "", r["quantity"],
                     float(r["revenue"]), float(r["cost"]), float(r["profit"]),
                     round(r["margin"], 2)] for r in rows]
        workbook = excel_service.build_generic_workbook("Product Sales Report", headers, body)
        filename = excel_service.default_export_name("Products", end)
    elif report_type == "cashiers":
        rows = report_service.cashier_report(start, end)
        headers = ["Cashier", "Transactions", "Total Sales", "Cash", "Transfer", "Card",
                   "Other", "Discounts", "Refunds", "Voids"]
        body = [[r["name"], r["transactions"], float(r["total"]), float(r["cash"]),
                 float(r["transfer"]), float(r["card"]), float(r["other"]),
                 float(r["discount"]), float(r["refunds"]), float(r["voids"])] for r in rows]
        workbook = excel_service.build_generic_workbook("Cashier Report", headers, body)
        filename = excel_service.default_export_name("Cashier_Report", end)
    elif report_type == "payments":
        rows = report_service.payment_report(start, end)
        headers = ["Payment Method", "Transactions", "Amount", "Share of Sales %"]
        body = [[r["method"], r["count"], float(r["amount"]), round(r["share"], 2)] for r in rows]
        workbook = excel_service.build_generic_workbook("Payment Report", headers, body)
        filename = excel_service.default_export_name("Payments", end)
    elif report_type == "inventory":
        workbook = excel_service.build_inventory_workbook(report_service.inventory_report())
        filename = excel_service.default_export_name("Inventory", date.today())
    elif report_type == "profit":
        _require_profit()
        headers = ["Product", "SKU", "Category", "Quantity Sold", "Revenue", "Cost",
                   "Gross Profit", "Margin %"]
        body = [[r["name"], r["sku"] or "", r["category"] or "", r["quantity"],
                 float(r["revenue"]), float(r["cost"]), float(r["profit"]),
                 round(r["margin"], 2)] for r in report_service.product_sales(start, end)]
        workbook = excel_service.build_generic_workbook("Profit Report", headers, body)
        filename = excel_service.default_export_name("Profit", end)
    elif report_type == "shifts":
        _require_shift_access()
        rows = report_service.shift_report(start, end)
        headers = ["Cashier", "Opened", "Closed", "Opening Cash", "Expected Cash",
                   "Actual Cash", "Variance", "Cash Sales", "Transfer Sales",
                   "Card Sales", "Total Sales", "Transactions"]
        body = [[r["cashier"], r["opened_at"], r["closed_at"], float(r["opening_cash"]),
                 float(r["expected_cash"] or 0), float(r["actual_cash"] or 0),
                 float(r["variance"] or 0), float(r["cash_sales"]), float(r["transfer_sales"]),
                 float(r["card_sales"]), float(r["total_sales"]), r["transactions"]]
                for r in rows]
        workbook = excel_service.build_generic_workbook("Shift Report", headers, body)
        filename = excel_service.default_export_name("Shifts", end)
    elif report_type == "refunds":
        rows = report_service.refund_report(start, end)
        headers = ["Return Number", "Original Receipt", "Date", "Time", "Staff",
                   "Approved By", "Reason", "Method", "Amount"]
        body = [[r["return_number"], r["original_receipt"], r["date"], r["time"], r["user"],
                 r["approved_by"], r["reason"], r["method"], float(r["amount"])] for r in rows]
        workbook = excel_service.build_generic_workbook("Refund Report", headers, body)
        filename = excel_service.default_export_name("Refunds", end)
    elif report_type == "voids":
        rows = report_service.void_report(start, end)
        headers = ["Receipt Number", "Date", "Time", "Cashier", "Total",
                   "Voided By", "Voided At", "Reason"]
        body = [[r["receipt_number"], r["date"], r["time"], r["cashier"], float(r["total"]),
                 r["voided_by"], r["voided_at"], r["reason"]] for r in rows]
        workbook = excel_service.build_generic_workbook("Void Report", headers, body)
        filename = excel_service.default_export_name("Voids", end)
    elif report_type == "audit":
        _require_audit()
        workbook = excel_service.build_audit_workbook(report_service.audit_report(start, end))
        filename = excel_service.default_export_name("Audit_Log", end)
    elif report_type == "transactions":
        stmt = apply_filters(Sale.query)
        sales = stmt.order_by(Sale.created_at.desc()).limit(5000).all()
        workbook = excel_service.build_sales_workbook(
            start=start, end=end, sales=sales,
            summary=report_service.sales_summary(start, end),
            product_rows=report_service.product_sales(start, end),
            cashier_rows=report_service.cashier_report(start, end),
            payment_rows=report_service.payment_report(start, end),
        )
        filename = excel_service.default_export_name("Transactions", end)
    else:
        return jsonify({"ok": False, "error": "Unknown report type."}), 404

    data = excel_service.save_to_bytes(workbook)

    # Keep a copy in the app data folder so the owner can always find exports.
    _archive_export(filename, data)

    log.info("Exported %s report as %s", report_type, filename)
    return send_file(
        io_bytes(data),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


def io_bytes(data: bytes):
    import io

    stream = io.BytesIO(data)
    stream.seek(0)
    return stream


def _archive_export(filename: str, data: bytes) -> None:
    """Save a copy locally in case the browser's download folder is unclear."""
    try:
        from flask import current_app

        directory = Path(current_app.config["DATA_DIR"]) / "exports"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / excel_service.safe_filename(filename)
        target.write_bytes(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not archive export: %s", exc)



def _require_profit() -> None:
    if not g.user.has_any_permission("reports.profit"):
        abort(403)


def _require_audit() -> None:
    if not g.user.has_any_permission("audit.view"):
        abort(403)


def _require_shift_access() -> None:
    if not g.user.has_any_permission("shifts.manage"):
        abort(403)


@bp.route("/print")
@permission_required("reports.print")
def print_view():
    """Browser print view for the current report/filter combination."""
    start, end, preset = _range()
    which = request.args.get("type", "sales")
    context = {"start": start, "end": end, "preset": preset, "which": which}
    if which == "products":
        context["rows"] = report_service.product_sales(start, end)
        context["show_profit"] = g.user.has_any_permission("reports.profit")
    elif which == "cashiers":
        context["rows"] = report_service.cashier_report(start, end)
    elif which == "inventory":
        context["rows"] = report_service.inventory_report()
        context["show_cost"] = g.user.has_any_permission("products.cost")
    elif which == "profit":
        _require_profit()
        context["data"] = report_service.profit_report(start, end)
        context["rows"] = report_service.product_sales(start, end)
    else:
        stmt = apply_filters(Sale.query)
        context["sales"] = stmt.order_by(Sale.created_at.desc()).limit(500).all()
        context["summary"] = report_service.sales_summary(start, end)
    return render_template("admin/reports/print.html", **context)
