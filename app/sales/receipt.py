"""Receipt rendering and printing.

`build_receipt` produces both an ESC/POS-aware line list and a plain-text
version. The printer service is called through the hardware factory, so a
failure here is reported but NEVER reverses a saved sale.
"""
from __future__ import annotations

from datetime import datetime

from app.database import service_settings as settings_service

from app.database.types import minor_of
from app.hardware import escpos
from app.hardware.base import HardwareResult, HardwareStatus
from app.utils.logging_config import get_logger
from app.utils.money import CURRENCY_SYMBOL, from_minor, fmt

log = get_logger("receipt")


def _naira(minor) -> str:
    """Naira without the symbol (CP850 has no U+20A6, so the printer shows N)."""
    return f"N{from_minor(minor):,.2f}"


def _rule(chars: int) -> str:
    return "-" * chars


def build_receipt(sale, *, width_chars: int | None = None, escpos_mode: bool = False) -> list[str]:
    """Render a receipt as a list of print lines."""
    biz = settings_service.business()
    opts = settings_service.receipt_options()
    chars = width_chars or opts["characters"]
    if chars <= 0:
        chars = 48

    out: list[str] = []
    add = out.append

    def center(text: str) -> None:
        add(text.center(chars))

    def row(left: str, right: str) -> None:
        """Left label, right amount, padded to the paper width."""
        space = chars - len(left) - len(right)
        if space < 1:
            add(left)
            add(right.rjust(chars))
        else:
            add(left + " " * space + right)

    if escpos_mode:
        add(escpos.ALIGN_CENTER + escpos.BOLD_ON + escpos.SIZE_DOUBLE)
    add(biz["name"])
    if opts.get("show_tagline") and biz["tagline"]:
        add(biz["tagline"])
    if escpos_mode:
        add(escpos.BOLD_OFF + escpos.SIZE_NORMAL)

    if opts.get("show_address"):
        if biz["address_line1"]:
            add(biz["address_line1"])
        if biz["address_line2"]:
            add(biz["address_line2"])
        if biz["address_city"]:
            add(biz["address_city"])

    phone_bits = [p for p in (biz["phone"], biz["phone_2"]) if p]
    if phone_bits:
        add("  /  ".join(phone_bits))
    if opts.get("show_email") and biz["email"]:
        add(biz["email"])

    add(_rule(chars))
    row("Receipt:", sale.receipt_number)
    row("Date:", sale.sale_date.strftime("%d/%m/%Y"))
    row("Time:", sale.sale_time)
    row("Cashier:", sale.cashier_name)
    if sale.terminal_id and sale.terminal:
        row("Terminal:", sale.terminal.name)
    add(_rule(chars))

    for item in sale.items:
        add(item.product_name)
        detail_left = f"  {item.quantity} x {_naira(minor_of(item.unit_price))}"
        row(detail_left, _naira(minor_of(item.line_total)))
        if item.returned_quantity:
            add(f"  ({item.returned_quantity} returned)")

    add(_rule(chars))
    row("Subtotal:", _naira(minor_of(sale.subtotal)))
    if int(sale.discount_amount or 0):
        row("Discount:", "-" + _naira(minor_of(sale.discount_amount)))
    if int(sale.tax_amount or 0):
        rate = sale.items[0].tax_rate if sale.items else 0
        label = f"Tax ({rate:g}%):" if rate else "Tax:"
        row(label, _naira(minor_of(sale.tax_amount)))

    if escpos_mode:
        add(escpos.BOLD_ON)
    row("TOTAL:", _naira(minor_of(sale.total_amount)))
    if escpos_mode:
        add(escpos.BOLD_OFF)

    add("")
    row("Payment:", sale.payment_method.capitalize())
    if sale.payment_method == "CASH":
        row("Amount Received:", _naira(minor_of(sale.amount_received)))
        row("Change:", _naira(minor_of(sale.change_given)))

    if sale.status == "VOIDED":
        add("")
        add("*** VOIDED ***")
        if sale.void_reason:
            add(sale.void_reason[:chars])

    add(_rule(chars))
    footer = opts.get("footer") or biz["receipt_footer"]
    if footer:
        if escpos_mode:
            add(escpos.ALIGN_CENTER)
        add(footer[:chars])
    if escpos_mode:
        add(escpos.ALIGN_LEFT)
    add("")

    return out


def print_receipt(sale, user=None, reprint: bool = False) -> HardwareResult:
    """Send a receipt to the configured printer.

    Returns a HardwareResult. If printing fails the sale is untouched and the
    caller offers a REPRINT RECEIPT action instead.
    """
    from app.hardware import factory

    services = factory.get_hardware()
    printer = services["printer"]
    opts = settings_service.receipt_options()
    escpos_mode = type(printer).__name__ == "EscPosPrinter"

    lines = build_receipt(sale, escpos_mode=escpos_mode)
    try:
        result = printer.print_receipt(lines, cut=opts.get("cut_paper", True))
    except Exception as exc:  # noqa: BLE001
        log.error("Receipt print raised: %s", exc)
        return HardwareResult(False, HardwareStatus.DISCONNECTED,
                              "Sale completed, but receipt printing failed.", str(exc))

    if result.success:
        try:
            from app.database.models import utcnow
            from app.extensions import db

            sale.receipt_printed_at = utcnow()
            db.session.commit()
            log.info("Receipt %s printed (%s)", sale.receipt_number, "reprint" if reprint else "sale")
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            log.warning("Could not stamp receipt_printed_at: %s", exc)
    else:
        log.warning("Receipt %s failed to print: %s", sale.receipt_number, result.detail)

    return result


def open_cash_drawer(sale=None, user=None, trigger: str = "SALE") -> HardwareResult:
    """Kick the drawer and record the outcome."""
    from app.sales.service import record_drawer_event

    outcome = record_drawer_event(sale=sale, user=user, terminal_id=getattr(sale, "terminal_id", None),
                                  trigger=trigger)
    return HardwareResult(
        success=outcome["status"] == "OPENED",
        status=HardwareStatus.CONNECTED if outcome["status"] == "OPENED" else HardwareStatus.DISCONNECTED,
        message=outcome["message"],
    )


def receipt_as_text(sale) -> str:
    """Plain text receipt for the on-screen preview and browser printing."""
    return "\n".join(build_receipt(sale))


def html_receipt(sale) -> str:
    """HTML receipt for printing from the admin screens."""
    from markupsafe import escape

    biz = settings_service.business()
    rows = []
    for item in sale.items:
        rows.append(
            "<tr><td class='l'>{name}</td>"
            "<td class='c'>{qty} x {price}</td>"
            "<td class='r'>{total}</td></tr>".format(
                name=escape(item.product_name),
                qty=item.quantity,
                price=f"{CURRENCY_SYMBOL}{from_minor(minor_of(item.unit_price)):,.2f}",
                total=f"{CURRENCY_SYMBOL}{from_minor(minor_of(item.line_total)):,.2f}",
            )
        )
    rows_html = "\n".join(rows)

    def money(minor):
        return f"{CURRENCY_SYMBOL}{from_minor(int(minor)):,.2f}"

    discount_row = (
        f"<tr><td colspan='2'>Discount</td><td class='r'>-{money(sale.discount_amount)}</td></tr>"
        if int(sale.discount_amount or 0) else ""
    )
    tax_row = (
        f"<tr><td colspan='2'>Tax</td><td class='r'>{money(sale.tax_amount)}</td></tr>"
        if int(sale.tax_amount or 0) else ""
    )
    cash_rows = ""
    if sale.payment_method == "CASH":
        cash_rows = (
            f"<tr><td colspan='2'>Amount Received</td><td class='r'>{money(sale.amount_received)}</td></tr>"
            f"<tr><td colspan='2'>Change</td><td class='r'>{money(sale.change_given)}</td></tr>"
        )

    return f"""
<div class="receipt-paper" id="printable-receipt">
  <div class="r-center">
    <h2>{escape(biz['name'])}</h2>
    <div class="r-tag">{escape(biz['tagline'])}</div>
    <div class="r-small">{escape(biz['address_line1'])}<br>{escape(biz['address_line2'])}<br>{escape(biz['address_city'])}</div>
    <div class="r-small">{escape(biz['phone'])} / {escape(biz['phone_2'])}<br>{escape(biz['email'])}</div>
  </div>
  <hr>
  <table class="r-table">
    <tr><td colspan="2">Receipt</td><td class="r">{escape(sale.receipt_number)}</td></tr>
    <tr><td colspan="2">Date</td><td class="r">{sale.sale_date.strftime('%d/%m/%Y')}</td></tr>
    <tr><td colspan="2">Time</td><td class="r">{sale.sale_time}</td></tr>
    <tr><td colspan="2">Cashier</td><td class="r">{escape(sale.cashier_name)}</td></tr>
  </table>
  <hr>
  <table class="r-table">
    {rows_html}
  </table>
  <hr>
  <table class="r-table">
    <tr><td colspan="2">Subtotal</td><td class="r">{money(sale.subtotal)}</td></tr>
    {discount_row}{tax_row}
    <tr class="r-total"><td colspan="2">TOTAL</td><td class="r">{money(sale.total_amount)}</td></tr>
    <tr><td colspan="2">Payment</td><td class="r">{escape(sale.payment_method.capitalize())}</td></tr>
    {cash_rows}
  </table>
  <hr>
  <div class="r-center r-foot">{escape(settings_service.receipt_options().get('footer') or 'Thank you for your patronage.')}</div>
</div>
"""
