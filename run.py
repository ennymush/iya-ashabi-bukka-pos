"""Iya Ashabi Bukka Limited - POS entry point.

Development:  python run.py
Production:   IyaAshabiBukkaPOS.exe (PyInstaller build)

Starts the local Flask server, ensures the database exists, and (when the
monitor manager is available) launches the customer display on the second
screen.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import create_app  # noqa: E402
from app.database import seed as seed_module  # noqa: E402
from app.extensions import db  # noqa: E402
from app.hardware import monitors  # noqa: E402
from app.utils.logging_config import get_logger  # noqa: E402


def ensure_database(app) -> None:
    """Create tables and seed defaults on first run. Safe to call always."""
    from app.database import models  # noqa: F401

    with app.app_context():
        db.create_all()
        seed_module.seed_permissions()
        seed_module.seed_settings()
        seed_module.seed_categories()

        from app.database.models import Terminal, User

        if Terminal.query.count() == 0:
            db.session.add(Terminal(name="Main POS Terminal", code="TILL-01", is_active=True))
        if User.query.count() == 0:
            # First run: the wizard will create the real admin account.
            db.session.commit()
            log = get_logger("app")
            log.info("No staff accounts exist yet - first-run setup will be shown.")
        db.session.commit()


def run_startup_tasks(app) -> None:
    """Startup maintenance: integrity check and the daily automatic backup."""
    with app.app_context():
        from app.database import backup as backup_service

        try:
            backup_service.maybe_auto_backup()
        except Exception as exc:  # noqa: BLE001
            get_logger("app").warning("Startup backup skipped: %s", exc)


def open_browser_later(url: str, delay: float = 1.5) -> None:
    def _open() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_open, daemon=True).start()


def _wants_customer_display() -> bool:
    """The customer display needs a screen of its own to open on."""
    if not getattr(sys, "frozen", False):
        return True
    try:
        return monitors.has_secondary_monitor()
    except Exception as exc:  # noqa: BLE001 - hardware can fail in odd ways
        get_logger("app").warning("Monitor detection failed: %s", exc)
        return False


def main() -> int:
    app = create_app()
    ensure_database(app)
    run_startup_tasks(app)

    host = app.config.get("APP_HOST", "127.0.0.1")
    port = app.config.get("APP_PORT", 5000)
    debug = bool(app.config.get("DEBUG"))

    from app.database import service_settings as settings_service

    url = f"http://127.0.0.1:{port}"
    log = get_logger("app")

    with app.app_context():
        needs_setup = not settings_service.setup_completed()
        display_enabled = settings_service.get_bool("display.enabled", True)

    # The operator screen must always open: "/" shows the setup wizard on the
    # first run and the POS after that. This applies to the packaged build too.
    open_browser_later(url)

    # The customer display gets its own window only when there is a second
    # screen to put it on; a development machine still gets one for testing.
    if display_enabled and _wants_customer_display():
        open_browser_later(f"{url}/customer-display")

    if needs_setup:
        log.info("Opening first-run setup at %s/setup", url)

    try:
        app.run(host=host, port=port, debug=debug, use_reloader=debug, threaded=True)
    except KeyboardInterrupt:
        log.info("POS shutting down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
