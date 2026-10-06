"""Money helpers.

Financial rule for this POS:
  * Naira amounts are stored in the database as INTEGER MINOR UNITS (kobo).
    100 kobo == N1.00.  This removes binary floating point errors entirely.
  * Arithmetic in Python is done with ``decimal.Decimal``.
  * Nothing in this application ever uses a bare ``float`` for money.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_DOWN

CURRENCY_SYMBOL = "\u20a6"  # Naira sign
CURRENCY_CODE = "NGN"
CURRENCY_NAME = "Nigerian Naira"
MINOR_UNITS = 100  # kobo in one Naira


class MoneyError(ValueError):
    """Raised when a monetary value cannot be interpreted."""


def to_decimal(value) -> Decimal:
    """Coerce user/database input into a 2dp Decimal."""
    if value is None or value == "":
        raise MoneyError("A monetary amount is required.")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, float):
        # str() first so 0.1 does not become 0.1000000000000000055
        dec = Decimal(str(value))
    else:
        text = str(value).strip().replace(",", "").replace(CURRENCY_SYMBOL, "")
        text = text.replace("\u20b6", "")
        if text.startswith("NGN"):
            text = text[3:].strip()
        if not text:
            raise MoneyError("A monetary amount is required.")
        try:
            dec = Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise MoneyError(f"'{value}' is not a valid amount.") from exc
    return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_minor(value) -> int:
    """Convert a user supplied amount into integer kobo for storage."""
    return int((to_decimal(value) * MINOR_UNITS).to_integral_value(rounding=ROUND_DOWN))


def from_minor(minor) -> Decimal:
    """Convert stored integer kobo back into a 2dp Decimal Naira value."""
    if minor is None:
        return Decimal("0.00")
    return (Decimal(int(minor)) / Decimal(MINOR_UNITS)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def fmt(amount) -> str:
    """Format for on-screen display, e.g. N2,500.00"""
    value = amount if isinstance(amount, Decimal) else to_decimal(amount)
    return f"{CURRENCY_SYMBOL}{value:,.2f}"


def fmt_minor(minor) -> str:
    """Format stored kobo for on-screen display."""
    return fmt(from_minor(minor))


def fmt_whole(amount) -> str:
    """Format without decimals, used on the customer display and buttons."""
    value = amount if isinstance(amount, Decimal) else to_decimal(amount)
    return f"{CURRENCY_SYMBOL}{value:,.0f}"


def parse_rate(value) -> Decimal:
    """Parse a tax rate percentage (e.g. 7.5 -> Decimal('7.5'))."""
    if value is None or value == "":
        return Decimal("0.00")
    try:
        return Decimal(str(value).strip()).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError("Tax rate must be a number.") from exc


def apply_tax(amount_minor: int, rate_percent: Decimal) -> int:
    """Calculate tax in kobo using banker-safe half-up rounding."""
    if not rate_percent:
        return 0
    base = Decimal(int(amount_minor))
    tax = (base * Decimal(str(rate_percent)) / Decimal(100)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(tax)


def excel_number_format(with_symbol: bool = True) -> str:
    return f'"{CURRENCY_SYMBOL}"#,##0.00' if with_symbol else "#,##0.00"
