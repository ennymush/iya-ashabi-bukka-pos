"""Excel export using openpyxl.

Produces a professional multi-sheet workbook:
  SUMMARY | TRANSACTIONS | PRODUCT SALES | CASHIER PERFORMANCE | PAYMENTS

Naira values are written as real numbers with a currency number format, so the
owner can sum and chart them in Excel.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from app.database import service_settings as settings_service
from app.utils.money import CURRENCY_SYMBOL

# Brand colours (brown / orange / gold / green) matching the POS theme
BRAND_BROWN = "5D3A1A"
BRAND_ORANGE = "E67E22"
BRAND_GOLD = "F1C40F"
BRAND_GREEN = "1E8449"
HEADER_FILL = PatternFill("solid", fgColor=BRAND_BROWN)
TITLE_FONT = Font(name="Calibri", size=16, bold=True, color=BRAND_BROWN)
SUBTITLE_FONT = Font(name="Calibri", size=11, italic=True, color="666666")
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
TOTAL_FONT = Font(name="Calibri", size=11, bold=True)
LABEL_FONT = Font(name="Calibri", size=11, bold=True, color=BRAND_BROWN)

THIN = Side(style="thin", color="D5C4A1")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
NAIRA_FMT = f'"{CURRENCY_SYMBOL}"#,##0.00'
NUMBER_FMT = "#,##0"
DATE_FMT = "dd/mm/yyyy"
DATETIME_FMT = "dd/mm/yyyy hh:mm"


def _dec(value) -> float:
    """Convert a stored Naira Decimal to a float for Excel."""
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _autosize(ws, min_width: int = 10, max_width: int = 42) -> None:
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            length = len(str(cell.value))
            widths[cell.column] = max(widths.get(cell.column, 0), length)
    for index, width in widths.items():
        ws.column_dimensions[get_column_letter(index)].width = max(
            min_width, min(max_width, width + 3)
        )


def _style_header(ws, row: int, columns: int) -> None:
    for index in range(1, columns + 1):
        cell = ws.cell(row=row, column=index)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[row].height = 28


def safe_filename(base: str, extension: str = ".xlsx") -> str:
    """Never overwrite an existing file - append (1), (2), ..."""
    import os

    base = re.sub(r"[^\w\- ]+", "_", base).strip().replace(" ", "_")
    candidate = f"{base}{extension}"
    counter = 1
    while os.path.exists(candidate):
        candidate = f"{base}({counter}){extension}"
        counter += 1
    return candidate


def build_sales_workbook(
    *,
    start: date | None,
    end: date | None,
    sales: list,
    summary: dict,
    product_rows: list,
    cashier_rows: list,
    payment_rows: list,
) -> Workbook:
    wb = Workbook()

    _sheet_summary(wb.active, start, end, summary, payment_rows)
    _sheet_transactions(wb.create_sheet("TRANSACTIONS"), sales)
    _sheet_products(wb.create_sheet("PRODUCT SALES"), product_rows)
    _sheet_cashiers(wb.create_sheet("CASHIER PERFORMANCE"), cashier_rows)
    _sheet_payments(wb.create_sheet("PAYMENTS"), payment_rows, summary)
    return wb


def _sheet_summary(ws, start, end, summary: dict, payment_rows: list) -> None:
    biz = settings_service.business()
    ws.title = "SUMMARY"
    ws.sheet_view.showGridLines = False

    ws["A1"] = biz["name"].upper()
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "SALES REPORT"
    ws["A2"].font = Font(size=13, bold=True, color=BRAND_ORANGE)

    period = f"{start.strftime(DATE_FMT)} - {end.strftime(DATE_FMT)}" if start and end else "ALL TIME"
    ws["A3"] = f"Period: {period}"
    ws["A3"].font = SUBTITLE_FONT
    ws["A4"] = f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws["A4"].font = SUBTITLE_FONT
    ws["A5"] = f"Prepared by: {biz['name']} - Iya Ashabi Bukka POS"
    ws["A5"].font = SUBTITLE_FONT

    row = 7
    ws.cell(row=row, column=1, value="KEY FIGURES").font = Font(size=12, bold=True, color=BRAND_BROWN)
    row += 1

    entries = [
        ("Total Sales", summary["total"], True),
        ("Transactions", summary["transactions"], False),
        ("Items Sold", summary["items_sold"], False),
        ("Subtotal", summary["subtotal"], True),
        ("Discounts", summary["discount"], True),
        ("Tax", summary["tax"], True),
        ("Refunds", summary["refunds"], True),
        ("Voids", summary["voids"], True),
        ("Average Transaction", _avg(summary["total"], summary["transactions"]), True),
    ]
    for label, value, is_money in entries:
        ws.cell(row=row, column=1, value=label).font = LABEL_FONT
        cell = ws.cell(row=row, column=2, value=_dec(value) if is_money else value)
        if is_money:
            cell.number_format = NAIRA_FMT
        cell.font = TOTAL_FONT
        cell.border = BORDER
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="PAYMENT BREAKDOWN").font = Font(size=12, bold=True, color=BRAND_BROWN)
    row += 1
    for label, key in (("Cash", "cash"), ("Transfer", "transfer"), ("Card", "card"), ("Other", "other")):
        ws.cell(row=row, column=1, value=label).font = LABEL_FONT
        cell = ws.cell(row=row, column=2, value=_dec(summary[key]))
        cell.number_format = NAIRA_FMT
        cell.font = TOTAL_FONT
        cell.border = BORDER
        row += 1

    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 22


def _avg(total, count) -> Decimal:
    try:
        if not count:
            return Decimal("0.00")
        return (Decimal(str(total)) / Decimal(str(count))).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return Decimal("0.00")


TRANSACTION_HEADERS = [
    "Date", "Time", "Receipt Number", "Cashier", "Product", "SKU", "Category",
    "Quantity", "Unit Price", "Discount", "Tax", "Line Total",
    "Payment Method", "Transaction Status",
]


def _sheet_transactions(ws, sales: list) -> None:
    ws.append(TRANSACTION_HEADERS)
    _style_header(ws, 1, len(TRANSACTION_HEADERS))
    ws.freeze_panes = "A2"

    for sale in sales:
        items = sale.items or []
        if not items:
            continue
        sale_discount = int(sale.discount_amount or 0)
        for index, item in enumerate(items):
            # Spread a sale-level discount across its lines, all on line 1.
            line_discount = sale_discount if index == 0 else 0
            ws.append([
                sale.sale_date,
                sale.sale_time,
                sale.receipt_number,
                sale.cashier_name,
                item.product_name,
                item.sku or "",
                item.category_name or "",
                item.quantity,
                _dec(item.unit_price),
                _dec(line_discount),
                _dec(item.tax_amount),
                _dec(item.line_total),
                sale.payment_method,
                sale.status,
            ])

    last_row = ws.max_row
    money_cols = (9, 10, 11, 12)
    for row_index in range(2, last_row + 1):
        ws.cell(row=row_index, column=1).number_format = DATE_FMT
        for col in money_cols:
            ws.cell(row=row_index, column=col).number_format = NAIRA_FMT
        ws.cell(row=row_index, column=8).number_format = NUMBER_FMT
        for col in range(1, len(TRANSACTION_HEADERS) + 1):
            ws.cell(row=row_index, column=col).border = BORDER

    if last_row > 1:
        ref = f"A1:{get_column_letter(len(TRANSACTION_HEADERS))}{last_row}"
        table = Table(displayName="SalesTable", ref=ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium9", showRowStripes=True, showColumnStripes=False
        )
        ws.add_table(table)

    _autosize(ws)
    ws.column_dimensions["E"].width = 30
    ws.column_dimensions["C"].width = 22


def _sheet_products(ws, product_rows: list) -> None:
    headers = ["Product", "SKU", "Category", "Quantity Sold", "Revenue", "Cost", "Gross Profit", "Margin %"]
    ws.append(headers)
    _style_header(ws, 1, len(headers))
    ws.freeze_panes = "A2"

    for row in product_rows:
        ws.append([
            row["name"], row["sku"] or "", row["category"] or "",
            row["quantity"], _dec(row["revenue"]), _dec(row["cost"]),
            _dec(row["profit"]), round(row["margin"], 2),
        ])

    last_row = ws.max_row
    for row_index in range(2, last_row + 1):
        ws.cell(row=row_index, column=4).number_format = NUMBER_FMT
        for col in (5, 6, 7):
            ws.cell(row=row_index, column=col).number_format = NAIRA_FMT
        ws.cell(row=row_index, column=8).number_format = "0.00"
        for col in range(1, len(headers) + 1):
            ws.cell(row=row_index, column=col).border = BORDER

    if last_row > 1:
        total_row = last_row + 1
        ws.cell(row=total_row, column=1, value="TOTAL").font = TOTAL_FONT
        for col in (4, 5, 6, 7):
            cell = ws.cell(row=total_row, column=col,
                           value=f"=SUM({get_column_letter(col)}2:{get_column_letter(col)}{last_row})")
            cell.font = TOTAL_FONT
            cell.number_format = NUMBER_FMT if col == 4 else NAIRA_FMT
            cell.border = BORDER
        ws.cell(row=total_row, column=1).border = BORDER

    _autosize(ws)


def _sheet_cashiers(ws, cashier_rows: list) -> None:
    headers = ["Cashier", "Transactions", "Total Sales", "Cash", "Transfer", "Card",
               "Other", "Discounts", "Refunds", "Voids"]
    ws.append(headers)
    _style_header(ws, 1, len(headers))
    ws.freeze_panes = "A2"

    for row in cashier_rows:
        ws.append([
            row["name"], row["transactions"], _dec(row["total"]), _dec(row["cash"]),
            _dec(row["transfer"]), _dec(row["card"]), _dec(row["other"]),
            _dec(row["discount"]), _dec(row["refunds"]), _dec(row["voids"]),
        ])

    last_row = ws.max_row
    for row_index in range(2, last_row + 1):
        ws.cell(row=row_index, column=2).number_format = NUMBER_FMT
        for col in range(3, 11):
            ws.cell(row=row_index, column=col).number_format = NAIRA_FMT
        for col in range(1, len(headers) + 1):
            ws.cell(row=row_index, column=col).border = BORDER

    if last_row > 1:
        total_row = last_row + 1
        ws.cell(row=total_row, column=1, value="TOTAL").font = TOTAL_FONT
        for col in range(2, 11):
            cell = ws.cell(row=total_row, column=col,
                           value=f"=SUM({get_column_letter(col)}2:{get_column_letter(col)}{last_row})")
            cell.font = TOTAL_FONT
            cell.number_format = NUMBER_FMT if col == 2 else NAIRA_FMT
            cell.border = BORDER
        ws.cell(row=total_row, column=1).border = BORDER

    _autosize(ws)


def _sheet_payments(ws, payment_rows: list, summary: dict) -> None:
    headers = ["Payment Method", "Transactions", "Amount", "Share of Sales %"]
    ws.append(headers)
    _style_header(ws, 1, len(headers))

    for row in payment_rows:
        ws.append([row["method"], row["count"], _dec(row["amount"]), round(row["share"], 2)])

    for row_index in range(2, ws.max_row + 1):
        ws.cell(row=row_index, column=2).number_format = NUMBER_FMT
        ws.cell(row=row_index, column=3).number_format = NAIRA_FMT
        ws.cell(row=row_index, column=4).number_format = "0.00"
        for col in range(1, 5):
            ws.cell(row=row_index, column=col).border = BORDER

    row = ws.max_row + 2
    ws.cell(row=row, column=1, value="TOTAL CASH").font = LABEL_FONT
    cell = ws.cell(row=row, column=3, value=_dec(summary["cash"]))
    cell.number_format = NAIRA_FMT
    cell.font = TOTAL_FONT
    row += 1
    ws.cell(row=row, column=1, value="TOTAL TRANSFER").font = LABEL_FONT
    cell = ws.cell(row=row, column=3, value=_dec(summary["transfer"]))
    cell.number_format = NAIRA_FMT
    cell.font = TOTAL_FONT
    row += 1
    ws.cell(row=row, column=1, value="TOTAL CARD").font = LABEL_FONT
    cell = ws.cell(row=row, column=3, value=_dec(summary["card"]))
    cell.number_format = NAIRA_FMT
    cell.font = TOTAL_FONT

    _autosize(ws)


def save_to_bytes(wb: Workbook) -> bytes:
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream.getvalue()


# ---------------------------------------------------------------------------
# Single-table exports
# ---------------------------------------------------------------------------
def build_inventory_workbook(rows: list) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "INVENTORY"
    ws.sheet_view.showGridLines = False

    biz = settings_service.business()
    ws["A1"] = biz["name"].upper()
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "INVENTORY REPORT"
    ws["A2"].font = Font(size=13, bold=True, color=BRAND_ORANGE)
    ws["A3"] = f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws["A3"].font = SUBTITLE_FONT

    headers = ["Product", "SKU", "Category", "Stock", "Minimum Stock", "Status",
               "Cost Price", "Selling Price", "Stock Value", "Active"]
    ws.append([])
    ws.append(headers)
    header_row = ws.max_row
    _style_header(ws, header_row, len(headers))
    ws.freeze_panes = f"A{header_row + 1}"

    for row in rows:
        ws.append([
            row["name"], row["sku"] or "", row["category"] or "", row["stock"],
            row["minimum"], row["status"], _dec(row["cost"]), _dec(row["price"]),
            _dec(row["value"]), "Yes" if row["active"] else "No",
        ])

    for row_index in range(header_row + 1, ws.max_row + 1):
        ws.cell(row=row_index, column=4).number_format = NUMBER_FMT
        ws.cell(row=row_index, column=5).number_format = NUMBER_FMT
        for col in (7, 8, 9):
            ws.cell(row=row_index, column=col).number_format = NAIRA_FMT
        for col in range(1, len(headers) + 1):
            ws.cell(row=row_index, column=col).border = BORDER
        status_cell = ws.cell(row=row_index, column=6)
        if status_cell.value in ("LOW STOCK", "OUT OF STOCK"):
            status_cell.font = Font(bold=True, color="C0392B")

    _autosize(ws)
    return wb


def build_audit_workbook(rows: list) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "AUDIT LOG"
    ws.sheet_view.showGridLines = False

    biz = settings_service.business()
    ws["A1"] = biz["name"].upper()
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "AUDIT LOG"
    ws["A2"].font = Font(size=13, bold=True, color=BRAND_ORANGE)
    ws["A3"] = f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws["A3"].font = SUBTITLE_FONT

    headers = ["Date/Time", "User", "Role", "Action", "Entity", "Entity ID",
               "Description", "IP Address"]
    ws.append([])
    ws.append(headers)
    header_row = ws.max_row
    _style_header(ws, header_row, len(headers))
    ws.freeze_panes = f"A{header_row + 1}"

    for row in rows:
        ws.append([
            row["created_at"], row["username"], row["role"], row["action"],
            row["entity_type"], row["entity_id"], row["description"], row["ip"],
        ])

    for row_index in range(header_row + 1, ws.max_row + 1):
        ws.cell(row=row_index, column=1).number_format = DATETIME_FMT
        for col in range(1, len(headers) + 1):
            ws.cell(row=row_index, column=col).border = BORDER

    _autosize(ws)
    ws.column_dimensions["G"].width = 60
    return wb


def build_generic_workbook(title: str, headers: list[str], rows: list[list]) -> Workbook:
    """Any other report (cashier, product, refund, void, shift) as one sheet."""
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31].upper()
    ws.sheet_view.showGridLines = False

    biz = settings_service.business()
    ws["A1"] = biz["name"].upper()
    ws["A1"].font = TITLE_FONT
    ws["A2"] = title.upper()
    ws["A2"].font = Font(size=13, bold=True, color=BRAND_ORANGE)
    ws["A3"] = f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws["A3"].font = SUBTITLE_FONT

    ws.append([])
    ws.append(headers)
    header_row = ws.max_row
    _style_header(ws, header_row, len(headers))
    ws.freeze_panes = f"A{header_row + 1}"

    for row in rows:
        ws.append(row)

    for row_index in range(header_row + 1, ws.max_row + 1):
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=row_index, column=col)
            cell.border = BORDER
            if isinstance(cell.value, (int, float)) and abs(cell.value or 0) > 100:
                cell.number_format = NAIRA_FMT
            elif isinstance(cell.value, date):
                cell.number_format = DATE_FMT
            elif isinstance(cell.value, datetime):
                cell.number_format = DATETIME_FMT

    _autosize(ws)
    return wb


def default_export_name(prefix: str = "Sales", when: date | None = None) -> str:
    biz = settings_service.business()
    when = when or date.today()
    base = biz["name"].replace("Limited", "").strip().replace(" ", "_")
    return f"{base}_{prefix}_{when.strftime('%Y-%m-%d')}.xlsx"
