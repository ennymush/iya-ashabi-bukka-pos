"""Authentication routes: one login page for every role."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.audit import service as audit
from app.auth import service as auth_service
from app.database import service_settings as settings_service
from app.extensions import db
from app.utils.security import login_required

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Single sign-in page.

    The username is checked against the accounts in SQLite and the redirect is
    decided by the ROLE stored on that account - never by a form field.
    """
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        result = auth_service.login(username, password)

        if not result.ok:
            flash(result.message, "error")
            return render_template("auth/login.html", username=username), 401

        user = result.user
        if user.must_change_password:
            flash("Please set a new password before continuing.", "warning")
            session_next = url_for("auth.change_password")
        else:
            session_next = auth_service.home_route_for(user)

        nxt = request.form.get("next") or ""
        if nxt and nxt.startswith("/") and not nxt.startswith("//"):
            session_next = nxt

        flash(f"Welcome, {user.full_name}.", "success")
        return redirect(session_next)

    if request.args.get("next"):
        pass
    return render_template("auth/login.html", username="")


@bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    from flask import g

    auth_service.logout(getattr(g, "user", None))
    flash("You have signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    from flask import g

    user = g.user
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not user.check_password(current):
            flash("Your current password is not correct.", "error")
        elif new != confirm:
            flash("The new passwords do not match.", "error")
        else:
            from app.utils.security import password_problem

            problem = password_problem(new, user.username)
            if problem:
                flash(problem, "error")
            else:
                user.set_password(new)
                user.must_change_password = False
                db.session.commit()
                audit.record(audit.PASSWORD_RESET, user=user, entity_type="User", entity_id=user.id,
                             description=f"{user.full_name} changed their own password",
                             ip_address=request.remote_addr, commit=True)
                flash("Your password has been changed.", "success")
                return redirect(auth_service.home_route_for(user))

    return render_template("auth/change_password.html")


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Offline recovery for the system owner.

    The documented procedure uses the recovery code generated during setup.
    We confirm a username exists without revealing account status, then hand
    over to the recovery form.
    """
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        from app.database.models import User

        user = User.query.filter_by(username=username).first()
        if user is None:
            flash("Invalid login credentials.", "error")
        elif not user.is_active:
            flash("Invalid login credentials.", "error")
        else:
            session_reset_hint = username
            return redirect(url_for("auth.recover", username=session_reset_hint))
    return render_template("auth/forgot_password.html")


@bp.route("/recover/<username>", methods=["GET", "POST"])
def recover(username: str):
    """Reset a forgotten password using the owner's recovery code."""
    from app.database.models import User
    from app.utils.security import normalise_recovery_code, verify_password

    user = User.query.filter_by(username=username).first()
    if user is None or not user.is_active:
        flash("Invalid login credentials.", "error")
        return redirect(url_for("auth.login"))

    stored = settings_service.get("system.recovery_code_hash") or ""
    if not stored:
        flash(
            "No recovery code is set. Ask the system owner for the code created during installation.",
            "error",
        )
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        code = request.form.get("recovery_code", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not verify_password(stored, normalise_recovery_code(code)):
            audit.record(audit.LOGIN_FAILED, user=user, entity_type="User", entity_id=user.id,
                         description="Failed password recovery attempt", ip_address=request.remote_addr)
            flash("That recovery code is not correct.", "error")
        elif new != confirm:
            flash("The new passwords do not match.", "error")
        else:
            from app.utils.security import password_problem

            problem = password_problem(new, user.username)
            if problem:
                flash(problem, "error")
            else:
                user.set_password(new)
                user.must_change_password = False
                db.session.commit()
                audit.record(audit.PASSWORD_RESET, user=user, entity_type="User", entity_id=user.id,
                             description=f"Password reset for {user.full_name} using a recovery code",
                             ip_address=request.remote_addr, commit=True)
                flash("Your password has been reset. Please sign in.", "success")
                return redirect(url_for("auth.login"))

    return render_template("auth/recover.html", username=username)
