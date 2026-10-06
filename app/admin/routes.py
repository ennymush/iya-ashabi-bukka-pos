"""Admin routes: dashboard, settings, hardware, backup/restore."""
from __future__ import annotations

from datetime import date, timedelta

from flask import (
    Blueprint, current_app, flash, g, jsonify, redirect, render_template,
    request, send_file, url_for,
)

from app.audit import service as audit
from app.database import backup as backup_service
from app.database import service_settings as settings_service
from app.database.models import (
    ROLE_ADMIN,
    STATUS_COMPLETED,
    STATUS_REFUNDED,
    STATUS_VOIDED,
    Product,
    Sale,
    Setting,
    Shift,
    Terminal,
    User,
)
from app.extensions import db
from app.hardware import factory
from app.hardware import monitors
from app.reports import service as report_service
from app.staff import service as staff_service
from app.uploads import service as upload_service
from app.utils.errors import ValidationError
from app.utils.security import permission_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@bp.route("/dashboard")
@permission_required("dashboard.view")
def dashboard():
    """Today's figures plus live updates from the cashier tills."""
    today = date.today()
    summary = report_service.sales_summary(today, today)
    yesterday = report_service.sales_summary(today - timedelta(days=1), today - timedelta(days=1))

    # Explicit onclause: Shift has TWO foreign keys to users (user_id and
    # closing_cashier_id), so SQLAlchemy cannot infer the join.
    open_shifts = Shift.query.filter_by(is_open=True).join(
        User, Shift.user_id == User.id
    ).all()

    recent = (
        Sale.query.filter_by(sale_date=today)
        .order_by(Sale.created_at.desc())
        .limit(10)
        .all()
    )

    compare = (summary["total"] - yesterday["total"]) if yesterday["total"] else 0
    percent = (compare / yesterday["total"] * 100) if yesterday["total"] else 0

    return render_template(
        "admin/dashboard.html",
        summary=summary,
        yesterday=yesterday,
        compare=compare,
        compare_percent=percent,
        low_stock=report_service.low_stock_count(),
        out_of_stock=Product.query.filter(
            Product.stock_quantity <= 0, Product.is_active.is_(True)
        ).count(),
        active_cashiers=report_service.active_cashier_count(),
        open_shifts=open_shifts,
        open_shift_count=report_service.open_shift_count(),
        recent=recent,
        daily=report_service.daily_sales(today - timedelta(days=6), today),
        can_see_profit=g.user.has_any_permission("reports.profit"),
    )


@bp.route("/api/dashboard/live")
@permission_required("dashboard.view")
def dashboard_live():
    """Polled by the dashboard so sales appear without a manual refresh."""
    today = date.today()
    summary = report_service.sales_summary(today, today)
    latest = (
        Sale.query.filter_by(sale_date=today)
        .order_by(Sale.created_at.desc())
        .limit(5)
        .all()
    )
    return jsonify({
        "ok": True,
        "total": str(summary["total"]),
        "transactions": summary["transactions"],
        "cash": str(summary["cash"]),
        "transfer": str(summary["transfer"]),
        "card": str(summary["card"]),
        "items_sold": summary["items_sold"],
        "low_stock": report_service.low_stock_count(),
        "open_shifts": report_service.open_shift_count(),
        "recent": [
            {"receipt": s.receipt_number, "cashier": s.cashier_name,
             "total": str(s.total_amount), "time": s.sale_time, "status": s.status}
            for s in latest
        ],
    })


# ---------------------------------------------------------------------------
# Business information
# ---------------------------------------------------------------------------
@bp.route("/settings/business", methods=["GET", "POST"])
@permission_required("settings.business")
def settings_business():
    if request.method == "POST":
        form = request.form
        values = {
            "business.name": form.get("name", "").strip(),
            "business.tagline": form.get("tagline", "").strip(),
            "business.address_line1": form.get("address_line1", "").strip(),
            "business.address_line2": form.get("address_line2", "").strip(),
            "business.address_city": form.get("address_city", "").strip(),
            "business.country": form.get("country", "").strip() or "Nigeria",
            "business.email": form.get("email", "").strip(),
            "business.phone": form.get("phone", "").strip(),
            "business.phone_2": form.get("phone_2", "").strip(),
            "business.tin": form.get("tin", "").strip(),
            "business.receipt_footer": form.get("receipt_footer", "").strip(),
            "business.promo_message": form.get("promo_message", "").strip(),
            "business.currency_code": form.get("currency_code", "NGN").strip() or "NGN",
            "business.currency_symbol": (form.get("currency_symbol", "\u20a6").strip() or "\u20a6"),
        }
        if not values["business.name"]:
            flash("Business name cannot be empty.", "error")
        else:
            # Logo upload
            upload = request.files.get("logo")
            if upload and upload.filename:
                try:
                    old = settings_service.get("business.logo_path")
                    filename = upload_service.upload_image(upload, subfolder="branding")
                    values["business.logo_path"] = filename
                    upload_service.delete_image(old)
                except ValidationError as exc:
                    flash(exc.message, "error")
                    return redirect(url_for("admin.settings_business"))

            promo = request.files.get("promo_image")
            if promo and promo.filename:
                try:
                    old_promo = settings_service.get("business.promo_image")
                    filename = upload_service.upload_image(promo, subfolder="branding")
                    values["business.promo_image"] = filename
                    upload_service.delete_image(old_promo)
                except ValidationError as exc:
                    flash(exc.message, "error")
                    return redirect(url_for("admin.settings_business"))

            changed = settings_service.set_many(values, user_id=g.user.id)
            audit.record(audit.SETTINGS_UPDATE, user=g.user, entity_type="Setting",
                         description=f"{g.user.full_name} updated business information",
                         old_value={k: v[0] for k, v in changed.items()},
                         new_value={k: v[1] for k, v in changed.items()},
                         ip_address=request.remote_addr, commit=True)
            flash("Business information has been saved.", "success")
            return redirect(url_for("admin.settings_business"))

    if request.args.get("remove_logo"):
        old = settings_service.get("business.logo_path")
        upload_service.delete_image(old)
        settings_service.set_value("business.logo_path", "", user_id=g.user.id)
        flash("Logo removed.", "success")
        return redirect(url_for("admin.settings_business"))

    return render_template("admin/settings/business.html", settings=settings_service.business())


# ---------------------------------------------------------------------------
# Hardware settings
# ---------------------------------------------------------------------------
@bp.route("/settings/hardware", methods=["GET", "POST"])
@permission_required("settings.hardware")
def settings_hardware():
    if request.method == "POST":
        values = {
            "hardware.printer_name": request.form.get("printer_name", "").strip(),
            "hardware.printer_mode": request.form.get("printer_mode", "mock"),
            "hardware.paper_width_mm": request.form.get("paper_width_mm", "80"),
            "hardware.drawer_mode": request.form.get("drawer_mode", "mock"),
            "hardware.drawer_pin": request.form.get("drawer_pin", "2"),
            "hardware.drawer_pulse_ms": request.form.get("drawer_pulse_ms", "120"),
            "hardware.scanner_mode": request.form.get("scanner_mode", "keyboard"),
            "hardware.scanner_prefix": request.form.get("scanner_prefix", "").strip(),
            "hardware.scanner_suffix": request.form.get("scanner_suffix", "").strip(),
            "hardware.scanner_beep": "1" if request.form.get("scanner_beep") else "0",
            "hardware.display_mode": request.form.get("display_mode", "browser"),
            "hardware.display_second_monitor": "1" if request.form.get("display_second_monitor") else "0",
            "hardware.display_position": request.form.get("display_position", "2"),
        }
        changed = settings_service.set_many(values, user_id=g.user.id)
        factory.clear_cache()
        audit.record(audit.SETTINGS_UPDATE, user=g.user, entity_type="Setting",
                     description=f"{g.user.full_name} updated hardware settings",
                     new_value={k: v[1] for k, v in changed.items()},
                     ip_address=request.remote_addr, commit=True)
        flash("Hardware settings have been saved.", "success")
        return redirect(url_for("admin.settings_hardware"))

    services = factory.get_hardware()
    return render_template(
        "admin/settings/hardware.html",
        hardware=settings_service.by_category("hardware"),
        printers=factory.printer_names(),
        monitors=monitors.monitor_summary(),
        mock=current_app.config.get("MOCK_HARDWARE", False),
        paper_choices=[("58", "58 mm (short)"), ("80", "80 mm (standard)")],
    )


@bp.route("/api/hardware/test/<device>")
@permission_required("settings.hardware")
def test_hardware(device: str):
    """TEST PRINTER / TEST CASH DRAWER / TEST CUSTOMER DISPLAY / TEST SCANNER."""
    services = factory.get_hardware()
    device = device.lower()

    if device == "printer":
        result = services["printer"].test()
    elif device in ("drawer", "cash_drawer"):
        from app.sales.service import record_drawer_event

        outcome = record_drawer_event(user=g.user, trigger="TEST")
        result = type(
            "R", (), {"success": outcome["status"] == "OPENED", "status": outcome["status"],
                      "message": outcome["message"], "detail": "", "payload": {},
                      "to_dict": lambda self: outcome}
        )()
    elif device in ("display", "customer_display"):
        from app.customer import service as display_service

        display_service.publish("TEST", {"message": "Customer display test - Iya Ashabi Bukka POS"})
        result = services["display"].test()
    elif device in ("scanner", "barcode_scanner"):
        result = services["scanner"].test(request.args.get("code", ""))
    else:
        return jsonify({"ok": False, "error": "Unknown device."}), 404

    status_value = getattr(result.status, "value", str(result.status))
    audit.record(audit.HARDWARE_TEST, user=g.user, entity_type="Hardware", entity_id=device,
                 description=f"{g.user.full_name} tested {device}: {'OK' if result.success else 'FAILED'}",
                 new_value={"status": status_value, "message": result.message},
                 ip_address=request.remote_addr, commit=True)

    return jsonify({
        "ok": result.success,
        "status": status_value,
        "message": result.message,
        "detail": result.detail,
    })


@bp.route("/api/hardware/status")
@permission_required("settings.hardware")
def hardware_status():
    services = factory.get_hardware()
    out = {}
    for key in ("printer", "drawer", "display", "scanner"):
        device = services[key]
        try:
            availability = device.is_available()
            out[key] = {
                "name": type(device).__name__,
                "status": getattr(availability.status, "value", str(availability.status)),
                "message": availability.message,
                "detail": availability.detail,
            }
        except Exception as exc:  # noqa: BLE001
            out[key] = {"name": key, "status": "DISCONNECTED", "message": "Unavailable.", "detail": str(exc)}
    out["monitors"] = monitors.monitor_summary()
    return jsonify({"ok": True, "hardware": out, "mock": current_app.config.get("MOCK_HARDWARE", False)})


# ---------------------------------------------------------------------------
# Receipt settings
# ---------------------------------------------------------------------------
@bp.route("/settings/receipt", methods=["GET", "POST"])
@permission_required("settings.receipt")
def settings_receipt():
    if request.method == "POST":
        values = {
            "receipt.paper_width": request.form.get("paper_width", "80"),
            "receipt.show_logo": "1" if request.form.get("show_logo") else "0",
            "receipt.show_address": "1" if request.form.get("show_address") else "0",
            "receipt.show_email": "1" if request.form.get("show_email") else "0",
            "receipt.show_tagline": "1" if request.form.get("show_tagline") else "0",
            "receipt.footer_message": request.form.get("footer_message", "").strip(),
            "receipt.cut_paper": "1" if request.form.get("cut_paper") else "0",
            "receipt.open_drawer": "1" if request.form.get("open_drawer") else "0",
        }
        settings_service.set_many(values, user_id=g.user.id)
        audit.record(audit.SETTINGS_UPDATE, user=g.user, entity_type="Setting",
                     description=f"{g.user.full_name} updated receipt settings",
                     ip_address=request.remote_addr, commit=True)
        flash("Receipt settings have been saved.", "success")
        return redirect(url_for("admin.settings_receipt"))

    return render_template(
        "admin/settings/receipt.html",
        receipt=settings_service.by_category("receipt"),
        business=settings_service.business(),
    )


# ---------------------------------------------------------------------------
# Customer display settings
# ---------------------------------------------------------------------------
@bp.route("/settings/display", methods=["GET", "POST"])
@permission_required("settings.display")
def settings_display():
    if request.method == "POST":
        values = {
            "display.enabled": "1" if request.form.get("enabled") else "0",
            "display.welcome_message": request.form.get("welcome_message", "").strip(),
            "display.thankyou_message": request.form.get("thankyou_message", "").strip(),
            "display.promotional_message": request.form.get("promotional_message", "").strip(),
            "display.show_totals": "1" if request.form.get("show_totals") else "0",
            "display.fullscreen": "1" if request.form.get("fullscreen") else "0",
            "display.show_date_time": "1" if request.form.get("show_date_time") else "0",
        }
        settings_service.set_many(values, user_id=g.user.id)
        factory.clear_cache()
        audit.record(audit.SETTINGS_UPDATE, user=g.user, entity_type="Setting",
                     description=f"{g.user.full_name} updated customer display settings",
                     ip_address=request.remote_addr, commit=True)
        flash("Customer display settings have been saved.", "success")
        return redirect(url_for("admin.settings_display"))

    return render_template(
        "admin/settings/display.html",
        display=settings_service.by_category("display"),
        business=settings_service.business(),
        monitors=monitors.monitor_summary(),
    )


# ---------------------------------------------------------------------------
# Database: backup and restore
# ---------------------------------------------------------------------------
@bp.route("/settings/database", methods=["GET", "POST"])
@permission_required("settings.database")
def settings_database():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "backup":
                entry = backup_service.create_backup(user=g.user)
                audit.record(audit.BACKUP_CREATE, user=g.user, entity_type="Backup",
                             entity_id=entry.filename,
                             description=f"{g.user.full_name} created backup {entry.filename}",
                             ip_address=request.remote_addr, commit=True)
                flash(f"Backup created: {entry.filename}", "success")
            elif action == "restore":
                filename = request.form.get("filename", "")
                from pathlib import Path

                source = Path(request.form.get("path") or (backup_service.backup_dir() / filename))
                result = backup_service.restore_backup(source, user=g.user)
                audit.record(audit.RESTORE_DATABASE, user=g.user, entity_type="Database",
                             entity_id=source.name,
                             description=f"{g.user.full_name} restored the database from {source.name}",
                             ip_address=request.remote_addr, commit=True)
                flash(result["message"], "success")
                return redirect(url_for("auth.logout"))
        except backup_service.BackupError as exc:
            db.session.rollback()
            flash(str(exc), "error")

    return render_template(
        "admin/settings/database.html",
        backups=backup_service.list_backups(),
        system=settings_service.by_category("system"),
        backup_dir=str(backup_service.backup_dir()),
        data_dir=str(current_app.config["DATA_DIR"]),
        integrity=backup_service.verify_active_database(),
    )


@bp.route("/settings/database/upload", methods=["POST"])
@permission_required("settings.database")
def upload_restore():
    """Restore from a backup file the admin picked from their computer."""
    from pathlib import Path

    upload = request.files.get("backup_file")
    if not upload or not upload.filename:
        flash("Please choose a backup file (.db).", "error")
        return redirect(url_for("admin.settings_database"))

    try:
        staged_dir = Path(current_app.config["DATA_DIR"]) / "restore"
        staged_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(upload.filename).name
        staged = staged_dir / safe_name
        upload.save(staged)

        result = backup_service.restore_backup(staged, user=g.user)
        audit.record(audit.RESTORE_DATABASE, user=g.user, entity_type="Database", entity_id=safe_name,
                     description=f"{g.user.full_name} restored the database from uploaded file {safe_name}",
                     ip_address=request.remote_addr, commit=True)
        flash(result["message"], "success")
        return redirect(url_for("auth.logout"))
    except backup_service.BackupError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        flash("That file could not be used. Please choose a valid POS backup.", "error")

    return redirect(url_for("admin.settings_database"))


@bp.route("/settings/database/download/<int:backup_id>")
@permission_required("settings.database")
def download_backup(backup_id: int):
    from app.database.models import BackupLog

    entry = db.session.get(BackupLog, backup_id)
    if entry is None or not entry.path:
        flash("Backup not found.", "error")
        return redirect(url_for("admin.settings_database"))
    from pathlib import Path

    path = Path(entry.path)
    if not path.exists():
        flash("That backup file is no longer on disk.", "error")
        return redirect(url_for("admin.settings_database"))
    return send_file(str(path), as_attachment=True, download_name=entry.filename)


@bp.route("/settings/database/delete/<int:backup_id>", methods=["POST"])
@permission_required("settings.database")
def delete_backup(backup_id: int):
    from app.database.models import BackupLog

    entry = db.session.get(BackupLog, backup_id)
    if entry is None:
        flash("Backup not found.", "error")
    else:
        name = entry.filename
        backup_service.delete_backup(entry)
        audit.record("BACKUP_DELETE", user=g.user, entity_type="Backup", entity_id=name,
                     description=f"{g.user.full_name} deleted backup {name}",
                     ip_address=request.remote_addr, commit=True)
        flash(f"Backup {name} has been deleted.", "success")
    return redirect(url_for("admin.settings_database"))


# ---------------------------------------------------------------------------
# System settings (misc) and recovery code
# ---------------------------------------------------------------------------
@bp.route("/settings/system", methods=["GET", "POST"])
@permission_required("settings.business")
def settings_system():
    if request.method == "POST":
        values = {
            "system.allow_negative_stock": "1" if request.form.get("allow_negative_stock") else "0",
            "system.low_stock_alert": "1" if request.form.get("low_stock_alert") else "0",
            "system.tax_enabled": "1" if request.form.get("tax_enabled") else "0",
            "system.default_tax_rate": request.form.get("default_tax_rate", "0"),
            "system.session_timeout": request.form.get("session_timeout", "30"),
            "system.allow_sale_without_shift": "1" if request.form.get("allow_sale_without_shift") else "0",
            "system.max_discount_percent_cashier": request.form.get("max_discount_percent_cashier", "10"),
            "system.max_discount_amount_cashier": request.form.get("max_discount_amount_cashier", "0"),
            "system.auto_backup": "1" if request.form.get("auto_backup") else "0",
            "system.backup_retention": request.form.get("backup_retention", "30"),
            "system.customer_display_idle_seconds": request.form.get("customer_display_idle_seconds", "60"),
        }
        settings_service.set_many(values, user_id=g.user.id)
        audit.record(audit.SETTINGS_UPDATE, user=g.user, entity_type="Setting",
                     description=f"{g.user.full_name} updated system settings",
                     ip_address=request.remote_addr, commit=True)
        flash("System settings have been saved.", "success")
        return redirect(url_for("admin.settings_system"))

    return render_template("admin/settings/system.html", system=settings_service.by_category("system"))


@bp.route("/settings/recovery-code", methods=["GET", "POST"])
@permission_required("users.manage")
def recovery_code():
    """Regenerate the owner's offline password-recovery code.

    Only a HASH is stored. The plain code is shown exactly once.
    """
    if request.method == "POST":
        from app.utils.security import generate_recovery_code, hash_password

        code = generate_recovery_code()
        settings_service.set_value("system.recovery_code_hash", hash_password(code), user_id=g.user.id)
        audit.record("RECOVERY_CODE_RESET", user=g.user, entity_type="Setting",
                     entity_id="recovery_code",
                     description=f"{g.user.full_name} generated a new recovery code",
                     ip_address=request.remote_addr, commit=True)
        return render_template("admin/settings/recovery_code.html",
                               settings=settings_service.by_category("system"),
                               new_code=code, generated=True)

    return render_template("admin/settings/recovery_code.html",
                           settings=settings_service.by_category("system"),
                           new_code=None, generated=False)


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------
@bp.route("/audit")
@permission_required("audit.view")
def audit_logs():
    page = request.args.get("page", 1, type=int)
    entries = audit.query_audit(
        action=request.args.get("action") or None,
        entity_type=request.args.get("entity_type") or None,
        date_from=None,
        search=request.args.get("q") or None,
        page=page, per_page=60,
    )
    return render_template(
        "admin/audit.html",
        entries=entries,
        actions=audit.distinct_actions(),
        users=User.query.order_by(User.full_name).all(),
    )


# ---------------------------------------------------------------------------
# POS access for admins/managers
# ---------------------------------------------------------------------------
@bp.route("/admin-pos-link")
def admin_pos_link():
    if not g.user:
        return redirect(url_for("auth.login"))
    return redirect(url_for("pos.index"))
