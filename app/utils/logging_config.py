"""Logging configuration.

Three separate logs are produced so problems can be diagnosed quickly:
  application.log - normal operational events
  hardware.log    - printer / drawer / display / scanner events
  error.log       - warnings and errors (never passwords)
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_CONFIGURED = False


class _RedactingFilter(logging.Filter):
    """Defence in depth: scrub anything that looks like a password."""

    PATTERNS = ("password", "passwd", "pwd", "secret", "recovery_code")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        lowered = message.lower()
        if any(p in lowered for p in self.PATTERNS):
            record.msg = "[REDACTED]"
            record.args = ()
        return True


def configure_logging(log_dir: Path | str, debug: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("iab")
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    redactor = _RedactingFilter()

    app_handler = logging.handlers.RotatingFileHandler(
        log_dir / "application.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    app_handler.setFormatter(formatter)
    app_handler.addFilter(redactor)

    hw_handler = logging.handlers.RotatingFileHandler(
        log_dir / "hardware.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    hw_handler.setFormatter(formatter)
    hw_handler.addFilter(redactor)

    err_handler = logging.handlers.RotatingFileHandler(
        log_dir / "error.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    err_handler.setFormatter(formatter)
    err_handler.setLevel(logging.WARNING)
    err_handler.addFilter(redactor)

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    root.addHandler(app_handler)
    root.addHandler(console)

    logging.getLogger("iab.hardware").addHandler(hw_handler)
    logging.getLogger("iab.error").addHandler(err_handler)
    # Errors should also land in the error log even if raised from other loggers.
    root.addHandler(err_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"iab.{name}")
