"""Business settings service.

Single access point for business info, receipt layout, display and hardware
options. Templates and services read through here, so nothing is hardcoded.
"""
from __future__ import annotations

from functools import lru_cache

from app.database.models import Setting
from app.extensions import db


def all_settings() -> dict:
    return {s.key: s.value for s in Setting.query.all()}


def by_category(category: str) -> dict:
    return Setting.all_by_category(category)


def get(key: str, default=None):
    return Setting.get(key, default)


def get_bool(key: str, default: bool = False) -> bool:
    return Setting.get_bool(key, default)


def get_int(key: str, default: int = 0) -> int:
    return Setting.get_int(key, default)


def set_value(key: str, value, user_id: int | None = None) -> Setting:
    row = Setting.set(key, value, user_id=user_id)
    db.session.commit()
    return row


def set_many(values: dict, user_id: int | None = None) -> dict:
    """Save a whole form's worth of settings and audit the differences."""
    before = all_settings()
    changed: dict[str, tuple[str | None, str | None]] = {}
    for key, value in values.items():
        old = before.get(key)
        new = None if value is None else str(value)
        if old != new:
            changed[key] = (old, new)
        Setting.set(key, new, user_id=user_id)
    db.session.commit()
    return changed


def business() -> dict:
    """The business profile used by receipts, dashboard and customer display."""
    data = by_category("business")
    return {
        "name": data.get("business.name") or "Iya Ashabi Bukka Limited",
        "tagline": data.get("business.tagline") or "Good Food \u2022 Great Vibes",
        "address_line1": data.get("business.address_line1") or "",
        "address_line2": data.get("business.address_line2") or "",
        "address_city": data.get("business.address_city") or "",
        "country": data.get("business.country") or "Nigeria",
        "email": data.get("business.email") or "",
        "phone": data.get("business.phone") or "",
        "phone_2": data.get("business.phone_2") or "",
        "currency_code": data.get("business.currency_code") or "NGN",
        "currency_symbol": data.get("business.currency_symbol") or "\u20a6",
        "logo_path": data.get("business.logo_path") or "",
        "receipt_footer": data.get("business.receipt_footer") or "Thank you for your patronage.",
        "promo_message": data.get("business.promo_message") or "",
        "promo_image": data.get("business.promo_image") or "",
        "tin": data.get("business.tin") or "",
    }


def receipt_options() -> dict:
    data = by_category("receipt")
    return {
        "width_mm": int(data.get("receipt.paper_width") or 80),
        "characters": int(data.get("receipt.paper_width_chars") or 48),
        "show_logo": _truthy(data.get("receipt.show_logo")),
        "show_address": _truthy(data.get("receipt.show_address")),
        "show_email": _truthy(data.get("receipt.show_email")),
        "show_tagline": _truthy(data.get("receipt.show_tagline")),
        "footer": data.get("receipt.footer_message") or "Thank you for your patronage.",
        "cut_paper": _truthy(data.get("receipt.cut_paper")),
        "open_drawer": _truthy(data.get("receipt.open_drawer")),
        "show_item_names": _truthy(data.get("receipt.show_item_names")),
    }


def display_options() -> dict:
    data = by_category("display")
    return {
        "enabled": _truthy(data.get("display.enabled")),
        "welcome_message": data.get("display.welcome_message") or "Welcome",
        "thankyou_message": data.get("display.thankyou_message") or "Thank you for your patronage!",
        "promotional_message": data.get("display.promotional_message") or "",
        "show_totals": _truthy(data.get("display.show_totals")),
        "fullscreen": _truthy(data.get("display.fullscreen")),
        "show_date_time": _truthy(data.get("display.show_date_time")),
        "logo_path": data.get("display.logo_path") or business()["logo_path"],
    }


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def clear_cache() -> None:
    clear_cache.cache_clear()


def setup_completed() -> bool:
    return get_bool("system.setup_completed", False)


def allow_negative_stock() -> bool:
    return get_bool("system.allow_negative_stock", False)


def tax_enabled() -> bool:
    return get_bool("system.tax_enabled", False)


def default_tax_rate() -> float:
    return float(get("system.default_tax_rate", "0") or 0)
