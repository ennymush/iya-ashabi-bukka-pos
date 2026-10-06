"""Hardware factory.

Reads the saved hardware settings and builds the right driver objects. This is
the only place that knows which concrete class backs each service, so changing
drivers later is a one-line edit here.
"""
from __future__ import annotations

from app.hardware.display import BrowserDisplay, MockDisplay
from app.hardware.drawer import MockCashDrawer, PrinterCashDrawer, WindowsCashDrawer
from app.hardware.printer import EscPosPrinter, MockPrinter, WindowsPrinter
from app.hardware.scanner import KeyboardWedgeScanner, MockScanner

# Cached per configuration so the POS does not rebuild drivers per request.
_CACHE: dict = {}


def build_printer(settings: dict, mock_override: bool = False) -> object:
    mode = "mock" if mock_override else (settings.get("hardware.printer_mode") or "mock")
    name = settings.get("hardware.printer_name") or ""
    width = int(settings.get("hardware.paper_width_mm") or 80)

    if mode == "escpos":
        return EscPosPrinter(printer_name=name, paper_width_mm=width)
    if mode == "windows":
        return WindowsPrinter(printer_name=name, paper_width_mm=width)
    return MockPrinter(printer_name=name or "MOCK RECEIPT PRINTER", paper_width_mm=width)


def build_drawer(settings: dict, printer: object, mock_override: bool = False) -> object:
    mode = "mock" if mock_override else (settings.get("hardware.drawer_mode") or "mock")
    name = settings.get("hardware.printer_name") or ""
    pin = int(settings.get("hardware.drawer_pin") or 2)
    pulse = int(settings.get("hardware.drawer_pulse_ms") or 120)

    if mode == "printer":
        return PrinterCashDrawer(printer, pin=pin, pulse_ms=pulse)
    if mode == "windows":
        return WindowsCashDrawer(printer_name=name)
    return MockCashDrawer(pin=pin, pulse_ms=pulse)


def build_display(settings: dict, mock_override: bool = False) -> object:
    mode = "mock" if mock_override else (settings.get("hardware.display_mode") or "browser")
    enabled = str(settings.get("display.enabled", "1")).lower() in {"1", "true", "yes", "on"}
    fullscreen = str(settings.get("display.fullscreen", "1")).lower() in {"1", "true", "yes", "on"}
    monitor = int(settings.get("hardware.display_position") or 2)
    if mode == "mock":
        return MockDisplay()
    return BrowserDisplay(enabled=enabled, fullscreen=fullscreen, monitor_index=monitor)


def build_scanner(settings: dict, mock_override: bool = False) -> object:
    mode = "mock" if mock_override else (settings.get("hardware.scanner_mode") or "keyboard")
    prefix = settings.get("hardware.scanner_prefix") or ""
    suffix = settings.get("hardware.scanner_suffix") or ""
    beep = str(settings.get("hardware.scanner_beep", "1")).lower() in {"1", "true", "yes", "on"}
    if mode == "mock":
        return MockScanner()
    return KeyboardWedgeScanner(prefix=prefix, suffix=suffix, beep=beep)


def get_hardware(use_cache: bool = True) -> dict:
    """Return the full hardware service bundle for the current settings."""
    from app.database.models import Setting

    settings = Setting.all_by_category("hardware")
    display_settings = Setting.all_by_category("display")
    settings.update(display_settings)

    if use_cache and settings == _CACHE.get("settings"):
        return _CACHE["services"]

    mock = _mock_override()
    printer = build_printer(settings, mock_override=mock)
    services = {
        "printer": printer,
        "drawer": build_drawer(settings, printer, mock_override=mock),
        "display": build_display(settings, mock_override=mock),
        "scanner": build_scanner(settings, mock_override=mock),
        "settings": settings,
        "mock": mock,
    }
    _CACHE["settings"] = settings
    _CACHE["services"] = services
    return services


def _mock_override() -> bool:
    try:
        from flask import current_app

        if current_app:
            return bool(current_app.config.get("MOCK_HARDWARE"))
    except Exception:  # noqa: BLE001 - outside app context
        pass
    return False


def clear_cache() -> None:
    """Call after the admin saves new hardware settings."""
    _CACHE.clear()


def printer_names() -> list[str]:
    """All printers visible to Windows, for the hardware settings dropdown."""
    names: list[str] = []
    try:
        import win32print  # type: ignore

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names = sorted({p[2] for p in win32print.EnumPrinters(flags, None, 2)})
    except Exception as exc:  # noqa: BLE001
        from app.utils.logging_config import get_logger

        get_logger("hardware").info("Printer enumeration unavailable: %s", exc)
    return names
