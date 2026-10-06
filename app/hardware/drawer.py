"""CashDrawerService implementations.

Most POS receipt printers expose a cash drawer on pins 2 or 5 and open it with
an ESC/POS pulse. Failing that, a Windows driver (OPOS/Star/Zebra) can be used.
"""
from __future__ import annotations

from datetime import datetime

from app.hardware.base import HardwareResult, HardwareStatus
from app.utils.logging_config import get_logger

log = get_logger("hardware")


class MockCashDrawer:
    name = "MockCashDrawer"

    def __init__(self, pin: int = 2, pulse_ms: int = 120):
        self.pin = pin
        self.pulse_ms = pulse_ms

    def is_available(self) -> HardwareResult:
        return HardwareResult(True, HardwareStatus.MOCK, "Mock cash drawer ready.")

    def open_drawer(self) -> HardwareResult:
        log.info("MockCashDrawer OPENED (pin=%s pulse=%sms)", self.pin, self.pulse_ms)
        return HardwareResult(True, HardwareStatus.MOCK, "Cash drawer opened (mock).")

    def test(self) -> HardwareResult:
        return self.open_drawer()


class PrinterCashDrawer:
    """Sends the drawer kick through the receipt printer's ESC/POS channel."""

    name = "PrinterCashDrawer"

    def __init__(self, printer: object, pin: int = 2, pulse_ms: int = 120):
        self.printer = printer
        self.pin = pin
        self.pulse_ms = pulse_ms

    def is_available(self) -> HardwareResult:
        opener = getattr(self.printer, "open_drawer", None)
        if opener is None:
            return HardwareResult(False, HardwareStatus.NOT_CONFIGURED,
                                  "Cash drawer unavailable.",
                                  "The selected printer driver cannot send a drawer pulse. Choose ESC/POS mode or use mock hardware.")
        return self.printer.is_available()

    def open_drawer(self) -> HardwareResult:
        opener = getattr(self.printer, "open_drawer", None)
        if opener is None:
            return self.is_available()
        result = opener(pin=self.pin, pulse_ms=self.pulse_ms)
        if not result.success:
            result.message = "Cash drawer unavailable."
            log.warning("Drawer pulse failed: %s", result.detail)
        else:
            log.info("Drawer pulse sent via printer pin %s", self.pin)
        return result

    def test(self) -> HardwareResult:
        return self.open_drawer()


class WindowsCashDrawer:
    """Driver-based drawer kick (OPOS/Star/Zebra), used when ESC/POS is not available."""

    name = "WindowsCashDrawer"

    def __init__(self, printer_name: str = ""):
        self.printer_name = printer_name

    def is_available(self) -> HardwareResult:
        if not self.printer_name:
            return HardwareResult(False, HardwareStatus.NOT_CONFIGURED,
                                  "Cash drawer unavailable.", "No printer selected for the drawer.")
        return HardwareResult(True, HardwareStatus.CONNECTED,
                              f"Cash drawer routed through '{self.printer_name}'.")

    def open_drawer(self) -> HardwareResult:
        availability = self.is_available()
        if not availability.success:
            return availability
        try:
            import win32print  # type: ignore

            handle = win32print.OpenPrinter(self.printer_name)
            try:
                win32print.StartDocPrinter(handle, 1, ("IAB Drawer", None, "RAW"))
                try:
                    win32print.StartPagePrinter(handle)
                    win32print.WritePrinter(handle, b"\x1b\x70\x00\x19\xfa")  # generic ESC/POS kick
                    win32print.EndPagePrinter(handle)
                finally:
                    win32print.EndDocPrinter(handle)
            finally:
                win32print.ClosePrinter(handle)
            log.info("WindowsCashDrawer pulse sent to %s", self.printer_name)
            return HardwareResult(True, HardwareStatus.CONNECTED, "Cash drawer opened.")
        except Exception as exc:  # noqa: BLE001
            log.warning("WindowsCashDrawer failed: %s", exc)
            return HardwareResult(False, HardwareStatus.DISCONNECTED,
                                  "Cash drawer unavailable.", str(exc))

    def test(self) -> HardwareResult:
        return self.open_drawer()
