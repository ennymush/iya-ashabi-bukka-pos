"""PrinterService implementations.

MockPrinter   - development / test; writes to a file instead of paper
WindowsPrinter- sends a raw job through the Windows spooler
EscPosPrinter  - writes raw ESC/POS bytes to a shared printer or USB device
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from app.hardware import escpos
from app.hardware.base import HardwareResult, HardwareStatus
from app.utils.logging_config import get_logger

log = get_logger("hardware")


class MockPrinter:
    """Always succeeds. Used for development, tests and hardware-free demos."""

    name = "MockPrinter"

    def __init__(self, printer_name: str = "", paper_width_mm: int = 80, spool_dir: Path | None = None):
        self.printer_name = printer_name or "MOCK RECEIPT PRINTER"
        self.paper_width_mm = paper_width_mm
        self.spool_dir = Path(spool_dir or (Path(os.environ.get("TEMP", ".")) / "IAB_MockSpool"))
        self.spool_dir.mkdir(parents=True, exist_ok=True)

    @property
    def characters(self) -> int:
        return 32 if int(self.paper_width_mm) <= 58 else 48

    def list_printers(self) -> list[str]:
        return [self.printer_name]

    def is_available(self) -> HardwareResult:
        return HardwareResult(True, HardwareStatus.MOCK, "Mock printer ready (no paper used).")

    def print_text(self, text: str) -> HardwareResult:
        return self.print_receipt(text.splitlines())

    def print_receipt(self, lines: list[str], cut: bool = True) -> HardwareResult:
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            target = self.spool_dir / f"receipt_{stamp}.txt"
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            log.info("MockPrinter printed %s lines to %s", len(lines), target.name)
            return HardwareResult(
                True, HardwareStatus.MOCK, "Receipt printed (mock).",
                payload={"spool_file": str(target), "lines": len(lines)},
            )
        except OSError as exc:
            log.error("MockPrinter failed: %s", exc)
            return HardwareResult(False, HardwareStatus.DISCONNECTED, "Mock printer error.", str(exc))

    def test(self) -> HardwareResult:
        lines = [
            escpos.ALIGN_CENTER.decode("latin1"),
            "IYA ASHABI BUKKA LIMITED",
            "Good Food - Great Vibes",
            "",
            "*** PRINTER TEST SUCCESSFUL ***",
            datetime.now().strftime("%d/%m/%Y %H:%M"),
        ]
        return self.print_receipt(["PRINTER TEST", "IYA ASHABI BUKKA LIMITED", "", "Printer is working.", datetime.now().strftime("%d/%m/%Y %H:%M")])


class WindowsPrinter:
    """Prints through the Windows print spooler using pywin32."""

    name = "WindowsPrinter"

    def __init__(self, printer_name: str = "", paper_width_mm: int = 80):
        self.printer_name = printer_name
        self.paper_width_mm = paper_width_mm
        self.win32print = None
        if printer_name:
            try:
                import win32print  # type: ignore

                self.win32print = win32print
            except ImportError:
                log.warning("pywin32 is not available; Windows printing disabled.")

    @property
    def characters(self) -> int:
        return 32 if int(self.paper_width_mm) <= 58 else 48

    def list_printers(self) -> list[str]:
        if not self.win32print:
            return []
        try:
            flags = self.win32print.PRINTER_ENUM_LOCAL | self.win32print.PRINTER_ENUM_CONNECTIONS
            return [p[2] for p in self.win32print.EnumPrinters(flags, None, 2)]
        except Exception as exc:  # noqa: BLE001 - hardware can throw anything
            log.error("Could not enumerate printers: %s", exc)
            return []

    def is_available(self) -> HardwareResult:
        if not self.printer_name:
            return HardwareResult(False, HardwareStatus.NOT_CONFIGURED,
                                  "Receipt printer unavailable.", "No printer selected.")
        if not self.win32print:
            return HardwareResult(False, HardwareStatus.DISCONNECTED,
                                  "Receipt printer unavailable.", "pywin32 not installed.")
        names = self.list_printers()
        if self.printer_name not in names:
            return HardwareResult(False, HardwareStatus.DISCONNECTED,
                                  "Receipt printer unavailable.",
                                  f"Printer '{self.printer_name}' not found. Installed: {', '.join(names) or 'none'}")
        return HardwareResult(True, HardwareStatus.CONNECTED, f"Printer '{self.printer_name}' is ready.")

    def print_text(self, text: str) -> HardwareResult:
        return self.print_receipt(text.splitlines())

    def print_receipt(self, lines: list[str], cut: bool = True) -> HardwareResult:
        availability = self.is_available()
        if not availability.success:
            return availability
        try:
            handle = self.win32print.OpenPrinter(self.printer_name)
            try:
                job = self.win32print.StartDocPrinter(handle, 1, ("IAB Receipt", None, "RAW"))
                try:
                    data = ("\n".join(lines) + "\n").encode("cp850", errors="replace")
                    self.win32print.StartPagePrinter(handle)
                    self.win32print.WritePrinter(handle, data)
                    self.win32print.EndPagePrinter(handle)
                finally:
                    self.win32print.EndDocPrinter(handle)
            finally:
                self.win32print.ClosePrinter(handle)
            log.info("WindowsPrinter sent receipt to %s", self.printer_name)
            return HardwareResult(True, HardwareStatus.CONNECTED, "Receipt printed.")
        except Exception as exc:  # noqa: BLE001
            log.error("WindowsPrinter print failed: %s", exc)
            return HardwareResult(False, HardwareStatus.DISCONNECTED,
                                  "Receipt printer unavailable.", str(exc))

    def test(self) -> HardwareResult:
        return self.print_receipt([
            "IYA ASHABI BUKKA LIMITED",
            "Good Food - Great Vibes",
            "",
            "*** PRINTER TEST ***",
            datetime.now().strftime("%d/%m/%Y %H:%M"),
        ])


class EscPosPrinter:
    """Sends raw ESC/POS bytes, giving real thermal formatting and drawer kick.

    Two transport options:
      * a Windows shared printer opened with RAW datatype (network/driver path)
      * a local share such as ``\\\\localhost\\MyPrinter`` or ``\\\\.\\pipe\\...``
    """

    name = "EscPosPrinter"

    def __init__(self, printer_name: str = "", paper_width_mm: int = 80, transport: str = "win32"):
        self.printer_name = printer_name
        self.paper_width_mm = paper_width_mm
        self.transport = transport
        self.win32print = None
        try:
            import win32print  # type: ignore

            self.win32print = win32print
        except ImportError:
            pass

    @property
    def characters(self) -> int:
        return 32 if int(self.paper_width_mm) <= 58 else 48

    def list_printers(self) -> list[str]:
        if not self.win32print:
            return []
        try:
            flags = self.win32print.PRINTER_ENUM_LOCAL | self.win32print.PRINTER_ENUM_CONNECTIONS
            return [p[2] for p in self.win32print.EnumPrinters(flags, None, 2)]
        except Exception:  # noqa: BLE001
            return []

    def is_available(self) -> HardwareResult:
        if not self.printer_name:
            return HardwareResult(False, HardwareStatus.NOT_CONFIGURED,
                                  "Receipt printer unavailable.", "No printer selected.")
        return HardwareResult(True, HardwareStatus.CONNECTED, f"ESC/POS printer '{self.printer_name}' ready.")

    def _send_raw(self, data: bytes) -> HardwareResult:
        if not self.win32print:
            return HardwareResult(False, HardwareStatus.DISCONNECTED,
                                  "Receipt printer unavailable.", "pywin32 not installed.")
        try:
            handle = self.win32print.OpenPrinter(self.printer_name)
            try:
                self.win32print.StartDocPrinter(handle, 1, ("IAB ESCPOS", None, "RAW"))
                try:
                    self.win32print.StartPagePrinter(handle)
                    self.win32print.WritePrinter(handle, data)
                    self.win32print.EndPagePrinter(handle)
                finally:
                    self.win32print.EndDocPrinter(handle)
            finally:
                self.win32print.ClosePrinter(handle)
            return HardwareResult(True, HardwareStatus.CONNECTED, "Sent to printer.")
        except Exception as exc:  # noqa: BLE001
            log.error("ESC/POS raw send failed: %s", exc)
            return HardwareResult(False, HardwareStatus.DISCONNECTED,
                                  "Receipt printer unavailable.", str(exc))

    def print_text(self, text: str) -> HardwareResult:
        return self.print_receipt(text.splitlines())

    def print_receipt(self, lines: list[str], cut: bool = True) -> HardwareResult:
        availability = self.is_available()
        if not availability.success:
            return availability
        buffer = bytearray()
        buffer += escpos.INIT + escpos.CHARSET_PC850
        for line in lines:
            if isinstance(line, bytes):
                buffer += line
            else:
                buffer += escpos.encode_line(line) + b"\n"
        if cut:
            buffer += escpos.FEED_3 + escpos.CUT_PARTIAL
        return self._send_raw(bytes(buffer))

    def open_drawer(self, pin: int = 2, pulse_ms: int = 120) -> HardwareResult:
        availability = self.is_available()
        if not availability.success:
            return availability
        return self._send_raw(escpos.drawer_pulse(pin, pulse_ms))

    def test(self) -> HardwareResult:
        lines = [
            escpos.ALIGN_CENTER + escpos.BOLD_ON + escpos.SIZE_DOUBLE,
            escpos.encode_line("IYA ASHABI BUKKA LIMITED"),
            escpos.BOLD_OFF + escpos.SIZE_NORMAL,
            escpos.encode_line("Good Food - Great Vibes"),
            escpos.ALIGN_LEFT,
            b"",
            escpos.ALIGN_CENTER + escpos.encode_line("*** PRINTER TEST ***"),
            escpos.encode_line(datetime.now().strftime("%d/%m/%Y %H:%M")),
        ]
        return self.print_receipt(lines)
