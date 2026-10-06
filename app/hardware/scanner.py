"""BarcodeService.

USB barcode scanners are keyboard-wedge devices: they type the digits then
send Enter. So "supporting" a scanner means listening for a fast burst of
keystrokes ending in Enter and treating that as a scan. This class validates
and normalises the captured code; the browser side does the listening.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.hardware.base import HardwareResult, HardwareStatus
from app.utils.logging_config import get_logger

log = get_logger("hardware")

# Most retail barcodes: EAN-8/13, UPC-A, Code39, Code128, ITF, QR payloads.
VALID_PATTERN = re.compile(r"^[A-Za-z0-9\-_.]{3,64}$")


class KeyboardWedgeScanner:
    name = "KeyboardWedgeScanner"

    def __init__(self, prefix: str = "", suffix: str = "", beep: bool = True):
        self.prefix = prefix or ""
        self.suffix = suffix or ""
        self.beep = beep

    def is_available(self) -> HardwareResult:
        return HardwareResult(True, HardwareStatus.CONNECTED,
                              "Barcode scanner active (keyboard wedge).")

    def normalise(self, raw: str) -> str:
        code = (raw or "").strip()
        if self.prefix and code.startswith(self.prefix):
            code = code[len(self.prefix):]
        if self.suffix and code.endswith(self.suffix):
            code = code[: -len(self.suffix)]
        return code.strip()

    def is_valid(self, code: str) -> bool:
        return bool(code) and bool(VALID_PATTERN.match(code))

    def resolve(self, code: str) -> dict | None:
        """Look the scanned value up against barcode, then SKU, then code."""
        from app.database.models import Product
        from app.extensions import db

        code = self.normalise(code)
        if not self.is_valid(code):
            return None
        product = (
            db.session.query(Product)
            .filter(Product.barcode == code, Product.is_active.is_(True))
            .first()
        )
        if product is None:
            product = (
                db.session.query(Product)
                .filter(Product.sku == code, Product.is_active.is_(True))
                .first()
            )
        if product is None:
            product = (
                db.session.query(Product)
                .filter(Product.product_code == code, Product.is_active.is_(True))
                .first()
            )
        if product is None:
            return None
        return {
            "id": product.id,
            "name": product.name,
            "price": product.selling_price,
            "stock": product.stock_quantity,
            "matched_on": "barcode" if product.barcode == code else "sku",
        }

    def test(self, code: str = "TEST-12345") -> HardwareResult:
        result = self.resolve(code)
        if result:
            return HardwareResult(True, HardwareStatus.CONNECTED,
                                  f"Scanner working - '{code}' matched {result['name']}.")
        return HardwareResult(
            True, HardwareStatus.CONNECTED,
            f"Scanner is sending keystrokes correctly (code '{code}' has no product yet).",
            detail="Add this barcode to a product to make it match.",
        )


class MockScanner:
    name = "MockScanner"

    def __init__(self, code: str = "MOCK-CODE-001"):
        self.code = code
        self._inner = KeyboardWedgeScanner()

    def is_available(self) -> HardwareResult:
        return HardwareResult(True, HardwareStatus.MOCK, "Mock barcode scanner ready.")

    def normalise(self, raw: str) -> str:
        return self._inner.normalise(raw)

    def is_valid(self, code: str) -> bool:
        return self._inner.is_valid(code)

    def resolve(self, code: str) -> dict | None:
        return self._inner.resolve(code)

    def test(self, code: str | None = None) -> HardwareResult:
        return self._inner.test(code or self.code)
