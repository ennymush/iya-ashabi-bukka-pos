"""Iya Ashabi Bukka Limited - Point of Sale.

Application factory. A local-first, offline POS system for a Nigerian food
business: SQLite + Flask, packaged for a Windows dual-screen POS machine.
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, url_for

from config import get_config, load_environment

load_environment()

from app.extensions import db, login_manager, migrate  # noqa: E402
from app.utils.db_setup import integrity_check  # noqa: E402
from app.utils.logging_config import configure_logging, get_logger  # noqa: E402


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(get_config(config_name))

    configure_logging(app.config["LOG_DIR"], debug=app.config.get("DEBUG", False))
    log = get_logger("app")

    _ensure_directories(app)
    _init_extensions(app)
    _register_root_route(app)

    _register_blueprints(app)
    _register_template_helpers(app)
    _register_request_hooks(app)
    _register_error_handlers(app)
    _register_cli(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    log.info(
        "Iya Ashabi Bukka POS starting | env=%s db=%s",
        app.config.get("ENV_NAME"),
        app.config.get("SQLALCHEMY_DATABASE_URI"),
    )
    return app


# ---------------------------------------------------------------------------
def _ensure_directories(app: Flask) -> None:
    for key in ("DATA_DIR", "UPLOAD_DIR", "BACKUP_DIR", "LOG_DIR"):
        try:
            Path(app.config[key]).mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # pragma: no cover - permissions issue
            get_logger("app").error("Could not create %s: %s", key, exc)


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    login_manager.init_app(app)

    from app.database import models  # noqa: F401  (registers tables)

    @login_manager.user_loader
    def load_user(user_id: str):
        from app.database.models import User

        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        from flask import flash

        flash("Please sign in to continue.", "warning")
        return redirect(url_for("auth.login", next=request.full_path if request.query_string else request.path))


def _register_root_route(app: Flask) -> None:
    """Register ``/``.

    ``run.py`` opens this exact URL in the browser on launch, so it must never
    404. First run goes to the setup wizard; afterwards the POS decides where
    the visitor belongs (an anonymous visitor is sent on to the login page).
    """

    @app.route("/")
    def root():
        from app.setup.routes import setup_needed

        try:
            needs_setup = setup_needed()
        except Exception:  # noqa: BLE001 - database not initialised yet
            needs_setup = True

        # setup_needed() already covers "no active account yet".
        return redirect(url_for("setup.index") if needs_setup else url_for("pos.index"))


def _register_blueprints(app: Flask) -> None:
    from app.admin.routes import bp as admin_bp
    from app.auth.routes import bp as auth_bp
    from app.customer.routes import bp as customer_bp
    from app.inventory.routes import bp as inventory_bp
    from app.pos.routes import bp as pos_bp
    from app.products.routes import bp as products_bp
    from app.reports.routes import bp as reports_bp
    from app.sales.routes import bp as sales_bp
    from app.setup.routes import bp as setup_bp
    from app.staff.routes import bp as staff_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(setup_bp)
    app.register_blueprint(pos_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(customer_bp)


def _register_template_helpers(app: Flask) -> None:
    from app.database import service_settings as settings_service
    from app.utils.money import fmt, fmt_minor, fmt_whole
    from app.utils.security import generate_csrf_token
    from datetime import datetime

    @app.context_processor
    def inject_globals():
        return {
            "business": settings_service.business(),
            "csrf_token": generate_csrf_token,
            "fmt": fmt,
            "fmt_minor": fmt_minor,
            "fmt_whole": fmt_whole,
            "current_year": datetime.now().year,
            "now": datetime.now,
        }

    @app.template_filter("money")
    def money_filter(value):
        return fmt(value) if value is not None else fmt(0)

    @app.template_filter("money_minor")
    def money_minor_filter(value):
        return fmt_minor(value)

    @app.template_filter("qty")
    def qty_filter(value):
        return f"{int(value or 0):,}"


def _register_request_hooks(app: Flask) -> None:
    from app.auth import service as auth_service
    from app.database import service_settings as settings_service
    from app.utils.security import csrf_protect

    @app.before_request
    def _load_user_and_check_session():
        g.user = None
        user_id = auth_service.current_user_id()
        if user_id is not None:
            from app.database.models import User

            user = db.session.get(User, user_id)
            if user is None or not user.is_active:
                # Deactivated or deleted while signed in - end the session.
                session_clear()
                return None
            g.user = user
            if auth_service.session_expired(settings_service.get_int("system.session_timeout", 30)):
                auth_service.logout(user)
                session_clear()
                from flask import flash

                flash("Your session timed out. Please sign in again.", "warning")
                return redirect(url_for("auth.login"))
            auth_service.touch_session()
        return None

    @app.before_request
    def _csrf():
        csrf_protect()

    @app.context_processor
    def _inject_user():
        return {"g": g}


def session_clear() -> None:
    from flask import session

    session.clear()


def _register_error_handlers(app: Flask) -> None:
    log = get_logger("error")

    def wants_json() -> bool:
        return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"

    @app.errorhandler(400)
    def bad_request(error):
        return _error_response(400, getattr(error, "description", "Bad request."), wants_json())

    @app.errorhandler(403)
    def forbidden(error):
        return _error_response(
            403,
            "You do not have permission to view this page. Please contact your administrator.",
            wants_json(),
        )

    @app.errorhandler(404)
    def not_found(error):
        return _error_response(404, "Page not found.", wants_json())

    @app.errorhandler(413)
    def too_large(error):
        return _error_response(413, "That file is too large. Please choose a smaller image.", wants_json())

    @app.errorhandler(500)
    def server_error(error):
        # Technical detail goes to the log; the user sees a readable message.
        log.exception("Unhandled server error: %s", error)
        return _error_response(
            500, "Something went wrong. The problem has been recorded; please try again.", wants_json()
        )

    from app.utils.errors import register_exception_handlers

    register_exception_handlers(app)


def _error_response(code: int, message: str, as_json: bool):
    if as_json:
        return jsonify({"ok": False, "error": message}), code
    return render_template("errors/error.html", code=code, message=message), code


def _register_cli(app: Flask) -> None:
    from app.cli import register_commands

    register_commands(app)
