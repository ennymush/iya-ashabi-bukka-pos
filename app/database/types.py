"""Shared column types.

MONEY CONTRACT (this is the single most important rule in the codebase):
  * The DATABASE stores Naira as INTEGER MINOR UNITS (kobo). N2,500.00 -> 250000.
  * SQLAlchemy/Python code always sees a ``Decimal`` in whole Naira.
  * Internal arithmetic uses integer kobo, converted at the boundary with
    ``to_minor()`` when reading and ``from_minor()`` when assigning.

There is deliberately no "guess if this int is already kobo" heuristic: that
ambiguity silently turns N2,500 into N25. ``process_bind_param`` always applies
``to_minor()``, so an attribute assignment is ALWAYS in whole Naira.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import DateTime, Integer, TypeDecorator

from app.utils.money import from_minor, to_minor


class Money(TypeDecorator):
    """Naira amount stored as integer kobo, surfaced to Python as Decimal."""

    impl = Integer
    cache_ok = True
    python_type = Decimal

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return to_minor(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return from_minor(value)


def minor_of(money_attribute) -> int:
    """Read a Money ORM attribute (whole Naira Decimal) as integer kobo.

    Use this whenever you need to do integer arithmetic on a stored amount.
    """
    return to_minor(money_attribute)


def naira_of(minor) -> Decimal:
    """Convert integer kobo into a whole-Naira Decimal for assignment."""
    return from_minor(minor)


class UTCDateTime(TypeDecorator):
    """Store naive UTC datetimes consistently across platforms."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            from datetime import timezone

            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return value
