"""Hardware service interfaces.

Every piece of POS hardware sits behind one of these contracts so a printer
model can be swapped without touching a single route or template.

    PrinterService      ESC/POS | Windows spooler | Mock
    CashDrawerService   Printer pulse | Windows | Mock
    DisplayService      Browser window | Mock
    BarcodeService      Keyboard-wedge | Mock

FAILURE PHILOSOPHY: hardware must never lose a sale. A driver returns a
``HardwareResult``; the caller records the failure and carries on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class HardwareStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    NOT_CONFIGURED = "NOT CONFIGURED"
    MOCK = "MOCK"


@dataclass
class HardwareResult:
    """Uniform result from every hardware call."""

    success: bool
    status: HardwareStatus = HardwareStatus.NOT_CONFIGURED
    message: str = ""
    detail: str = ""
    payload: dict = field(default_factory=dict)

    @property
    def user_message(self) -> str:
        return self.message or ("Completed." if self.success else "Hardware unavailable.")

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "status": self.status.value,
            "message": self.message,
            "detail": self.detail,
            "payload": self.payload,
        }


@runtime_checkable
class PrinterService(Protocol):
    name: str

    def list_printers(self) -> list[str]: ...
    def is_available(self) -> HardwareResult: ...
    def print_text(self, text: str) -> HardwareResult: ...
    def print_receipt(self, lines: list[str], cut: bool = True) -> HardwareResult: ...
    def test(self) -> HardwareResult: ...


@runtime_checkable
class CashDrawerService(Protocol):
    name: str

    def is_available(self) -> HardwareResult: ...
    def open_drawer(self) -> HardwareResult: ...
    def test(self) -> HardwareResult: ...


@runtime_checkable
class DisplayService(Protocol):
    name: str

    def is_available(self) -> HardwareResult: ...
    def update(self, state: dict) -> HardwareResult: ...
    def test(self) -> HardwareResult: ...


@runtime_checkable
class BarcodeService(Protocol):
    name: str

    def is_available(self) -> HardwareResult: ...
    def resolve(self, code: str) -> dict | None: ...
    def test(self, code: str = "TEST-12345") -> HardwareResult: ...
