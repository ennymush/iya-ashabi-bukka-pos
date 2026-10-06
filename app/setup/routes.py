"""First-run setup wizard.

Eight guided steps, then the login screen. No default admin/admin credential is
ever shipped: the owner creates their own administrator here on first launch.
"""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from app.audit import service as audit
from app.database import seed as seed_module
from app.database import service_settings as settings_service
from app.database.models import ROLE_ADMIN, ROLE_CASHIER, Role, Terminal, User
from app.extensions import db
from app.hardware import factory, monitors
from app.staff import service as staff_service
from app.utils.errors import ValidationError
from app.utils.security import generate_recovery_code, hash_password, login_required

bp = Blueprint("setup", __name__, url_prefix="/setup")

STEPS = [
    (1, "Business Information"),
    (2, "Create Admin Account"),
    (3, "Receipt Printer"),
    (4, "Customer Display"),
    (5, "Cash Drawer"),
    (6, "Create First Cashier"),
    (7, "Test Hardware"),
    (8, "Finish"),
]

WIZARD_KEY = "setup_progress"


def setup_needed() -> bool:
    """True until an administrator exists."""
    if session.get(WIZARD_KEY) is not None and session.get(f"{WIZARD_KEY}_admin"):
        return False
    return not User.query.filter_by(is_active=True).first() and not settings_service.setup_completed()


def _progress() -> int:
    return int(session.get(WIZARD_KEY, 1))


def _set_progress(step: int) -> None:
    session[WIZARD_KEY] = step
    session.modified = True


def _ensure_terminal() -> Terminal:
    terminal = Terminal.query.order_by(Terminal.id).first()
    if terminal is None:
        terminal = Terminal(name="Main POS Terminal", code="TILL-01", is_active=True)
        db.session.add(terminal)
        db.session.commit()
    return terminal


@bp.route("")
@bp.route("/")
def index():
    if User.query.filter_by(is_active=True).first() and settings_service.setup_completed():
        return redirect(url_for("auth.login"))
    _set_progress(1)
    _ensure_terminal()
    return redirect(url_for("setup.step", number=1))


@bp.route("/step/<int:number>", methods=["GET", "POST"])
def step(number: int):
    # Once setup is finished the wizard is closed.
    if settings_service.setup_completed() and User.query.filter_by(is_active=True).first():
        return redirect(url_for("auth.login"))
    if number < 1 or number > len(STEPS):
        return redirect(url_for("setup.index"))

    _ensure_terminal()

    if request.method == "POST":
        try:
            handler = {
                1: _save_business,
                2: _save_admin,
                3: _save_printer,
                4: _save_display,
                5: _save_drawer,
                6: _save_cashier,
                7: _test_hardware,
                8: _finish,
            }[number]
            handler()
            if number < len(STEPS):
                flash(f"Step {number} saved.", "success")
                return redirect(url_for("setup.step", number=number + 1))
            # Final step: the wizard is finished - hand over to the sign-in screen.
            flash("Setup is complete. Please sign in with your administrator account.", "success")
            return redirect(url_for("auth.login"))
        except ValidationError as exc:
            flash(exc.message, "error")
    else:
        _set_progress(max(_progress(), number))

    return render_template(
        "setup/step.html",
        number=number,
        total=len(STEPS),
        title=dict(STEPS)[number],
        steps=STEPS,
        settings=settings_service.by_category("hardware"),
        display_settings=settings_service.by_category("display"),
        business=settings_service.business(),
        printers=factory.printer_names(),
        monitors=monitors.monitor_summary(),
        paper_choices=[("58", "58 mm (short)"), ("80", "80 mm (standard)")],
    )


# --- Step handlers ---------------------------------------------------------
def _save_business() -> None:
    form = request.form
    name = (form.get("name") or "").strip()
    if not name:
        raise ValidationError("Business name is required.")
    settings_service.set_many({
        "business.name": name,
        "business.tagline": (form.get("tagline") or "").strip(),
        "business.address_line1": (form.get("address_line1") or "").strip(),
        "business.address_line2": (form.get("address_line2") or "").strip(),
        "business.address_city": (form.get("address_city") or "").strip(),
        "business.email": (form.get("email") or "").strip(),
        "business.phone": (form.get("phone") or "").strip(),
        "business.phone_2": (form.get("phone_2") or "").strip(),
        "business.receipt_footer": (form.get("receipt_footer") or "").strip(),
        "business.promo_message": (form.get("promo_message") or "").strip(),
    })
    _set_progress(2)


def _save_admin() -> None:
    """Create the first administrator. No default credentials are shipped."""
    if User.query.filter_by(is_active=True).first():
        raise ValidationError("An administrator already exists. Please sign in instead.")

    data = {
        "full_name": request.form.get("full_name"),
        "username": request.form.get("username"),
        "password": request.form.get("password"),
        "role": ROLE_ADMIN,
        "phone": request.form.get("phone"),
        "email": request.form.get("email"),
    }
    confirm = request.form.get("confirm_password")
    if confirm and confirm != data["password"]:
        raise ValidationError("The passwords do not match.")

    system_user = User(full_name="System")
    user = staff_service.create_staff(system_user, data)

    # Generate the owner's recovery code (only a hash is stored).
    code = generate_recovery_code()
    settings_service.set_value("system.recovery_code_hash", hash_password(code))
    session[f"{WIZARD_KEY}_recovery"] = code

    audit.record(audit.USER_CREATE, user=user, entity_type="User", entity_id=user.id,
                 description=f"Administrator account '{user.full_name}' created during setup",
                 ip_address=request.remote_addr)
    db.session.commit()
    _set_progress(3)
    session[f"{WIZARD_KEY}_admin"] = True


def _save_printer() -> None:
    values = {
        "hardware.printer_name": request.form.get("printer_name", "").strip(),
        "hardware.printer_mode": request.form.get("printer_mode", "mock"),
        "hardware.paper_width_mm": request.form.get("paper_width_mm", "80"),
    }
    if values["hardware.printer_mode"] != "mock" and not values["hardware.printer_name"]:
        raise ValidationError("Please select your receipt printer, or keep mock mode for now.")
    settings_service.set_many(values)
    factory.clear_cache()
    _set_progress(4)


def _save_display() -> None:
    values = {
        "display.enabled": "1" if request.form.get("enabled") else "0",
        "display.welcome_message": (request.form.get("welcome_message") or "Welcome").strip(),
        "display.thankyou_message": (request.form.get("thankyou_message") or "Thank you!").strip(),
        "display.promotional_message": (request.form.get("promotional_message") or "").strip(),
        "display.fullscreen": "1" if request.form.get("fullscreen") else "0",
        "hardware.display_mode": request.form.get("display_mode", "browser"),
        "hardware.display_position": request.form.get("display_position", "2"),
    }
    settings_service.set_many(values)
    factory.clear_cache()
    _set_progress(5)


def _save_drawer() -> None:
    values = {
        "hardware.drawer_mode": request.form.get("drawer_mode", "mock"),
        "hardware.drawer_pin": request.form.get("drawer_pin", "2"),
        "hardware.drawer_pulse_ms": request.form.get("drawer_pulse_ms", "120"),
    }
    settings_service.set_many(values)
    factory.clear_cache()
    _set_progress(6)


def _save_cashier() -> None:
    """Optional - the owner can skip and add cashiers later."""
    username = (request.form.get("username") or "").strip()
    if not username:
        _set_progress(7)
        return

    data = {
        "full_name": request.form.get("full_name"),
        "username": username,
        "password": request.form.get("password"),
        "role": ROLE_CASHIER,
        "phone": request.form.get("phone"),
        "max_discount_percent": request.form.get("max_discount_percent") or "0",
        "max_discount_amount": request.form.get("max_discount_amount") or "0",
    }
    confirm = request.form.get("confirm_password")
    if confirm and confirm != data["password"]:
        raise ValidationError("The passwords do not match.")

    system_user = User(full_name="System")
    staff_service.create_staff(system_user, data)
    _set_progress(7)


def _test_hardware() -> None:
    """Run each hardware test; failures are shown but never block setup."""
    services = factory.get_hardware()
    device = request.form.get("device")
    if not device:
        _set_progress(8)
        return

    if device == "printer":
        result = services["printer"].test()
    elif device == "drawer":
        result = services["drawer"].test()
    elif device == "display":
        result = services["display"].test()
    elif device == "scanner":
        result = services["scanner"].test()
    else:
        raise ValidationError("Unknown device.")

    session["setup_last_test"] = {
        "device": device,
        "ok": result.success,
        "message": result.message,
        "detail": result.detail,
    }
    audit.record(audit.HARDWARE_TEST, entity_type="Hardware", entity_id=device,
                 description=f"Setup test: {device} -> {'OK' if result.success else 'FAILED'}",
                 new_value={"message": result.message}, commit=True)


def _finish() -> None:
    if not User.query.filter_by(is_active=True).first():
        raise ValidationError("An administrator account is required before finishing setup.")

    settings_service.set_value("system.setup_completed", "1")
    audit.record(audit.SETTINGS_UPDATE, description="First-run setup completed",
                 ip_address=request.remote_addr, commit=True)
    session.pop(WIZARD_KEY, None)
    session.pop(f"{WIZARD_KEY}_admin", None)


@bp.route("/recovery-code")
def show_recovery_code():
    """Shown once during setup so the owner can store the recovery code."""
    code = session.get(f"{WIZARD_KEY}_recovery")
    if not code:
        return redirect(url_for("auth.login"))
    return render_template("setup/recovery_code.html", code=code)
