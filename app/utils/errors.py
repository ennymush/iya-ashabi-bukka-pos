"""Domain-specific error classes.

Services raise these instead of ValueError so routes can show the exact
friendly message. ``register_exception_handlers`` turns them into a JSON
error for the POS screens, or a flash + redirect for normal pages.
"""
from __future__ import annotations

from flask import flash, jsonify, redirect, request, url_for


class POSError(Exception):
    """Base class for readable, user-facing errors."""

    message = "Something went wrong."
    status_code = 400

    def __init__(self, message: str | None = None, status_code: int | None = None):
        super().__init__(message or self.message)
        if message:
            self.message = message
        if status_code:
            self.status_code = status_code


class ValidationError(POSError):
    message = "Please check the information and try again."
    status_code = 400


class PermissionError_(POSError):
    message = "You do not have permission to do that."
    status_code = 403


class NotFoundError(POSError):
    message = "Not found."
    status_code = 404


class StockError(ValidationError):
    message = "Insufficient stock."


class PaymentError(ValidationError):
    message = "Please enter the amount received."


class HardwareUnavailable(POSError):
    """Hardware failed. The sale stays saved; the caller decides what to do."""

    message = "Hardware unavailable."
    status_code = 503


class ConcurrencyError(ValidationError):
    message = "That record was changed by someone else. Please refresh and try again."


def register_exception_handlers(app) -> None:
    from app.utils.logging_config import get_logger

    log = get_logger("error")

    for exc_class in (ValidationError, StockError, PaymentError, ConcurrencyError,
                      PermissionError_, NotFoundError, HardwareUnavailable):
        def make_handler(cls):
            def handler(error):
                log.info("%s: %s", cls.__name__, error.message)
                if request.path.startswith("/api/") or request.headers.get("X-Requested-With") == "fetch":
                    return jsonify({"ok": False, "error": error.message}), error.status_code
                flash(error.message, "error")
                referrer = request.referrer or url_for("pos.index")
                return redirect(referrer)

            handler.__name__ = f"handle_{cls.__name__}"
            return handler

        app.register_error_handler(exc_class, make_handler(exc_class))
