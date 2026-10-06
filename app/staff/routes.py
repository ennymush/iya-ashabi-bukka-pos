"""Staff management routes (ADMIN > STAFF)."""
from __future__ import annotations

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from app.database.models import ROLE_ADMIN, ROLE_CASHIER, ROLE_MANAGER, Sale, Shift, User
from app.database.permissions import PERMISSIONS
from app.extensions import db
from app.staff import service as staff_service
from app.utils.errors import ValidationError
from app.utils.security import permission_required

bp = Blueprint("staff", __name__, url_prefix="/admin/staff")


@bp.route("")
@permission_required("staff.view")
def list_staff():
    role_filter = request.args.get("role", "all")
    status = request.args.get("status", "all")
    query = (request.args.get("q") or "").strip()

    stmt = User.query
    if query:
        like = f"%{query}%"
        stmt = stmt.filter(db.or_(User.full_name.ilike(like), User.username.ilike(like)))
    if role_filter != "all":
        stmt = stmt.filter(User.role.has(name=role_filter))
    if status == "active":
        stmt = stmt.filter(User.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.filter(User.is_active.is_(False))

    users = stmt.order_by(User.full_name).all()
    for user in users:
        user.stats = staff_service.staff_stats(user)

    return render_template(
        "admin/staff/list.html",
        users=users,
        role_filter=role_filter,
        status=status,
        query=query,
        can_manage=g.user.has_any_permission("staff.manage"),
    )


@bp.route("/new", methods=["GET", "POST"])
@permission_required("staff.manage")
def create_staff():
    """ADMIN > STAFF > + ADD STAFF"""
    if request.method == "POST":
        try:
            user = staff_service.create_staff(g.user, _form_data(request.form))
            flash(
                f"{user.full_name} has been added as {user.role_name.title()}. "
                f"They can now sign in with username '{user.username}'.",
                "success",
            )
            return redirect(url_for("staff.list_staff"))
        except ValidationError as exc:
            flash(exc.message, "error")

    return render_template(
        "admin/staff/form.html",
        staff=None,
        roles=[ROLE_CASHIER, ROLE_MANAGER, ROLE_ADMIN],
    )


@bp.route("/<int:user_id>")
@permission_required("staff.view")
def view_staff(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        return render_template("errors/error.html", code=404, message="Staff member not found."), 404

    sales = (
        Sale.query.filter_by(cashier_id=user.id)
        .order_by(Sale.created_at.desc())
        .limit(25)
        .all()
    )
    shifts = (
        Shift.query.filter_by(user_id=user.id)
        .order_by(Shift.opened_at.desc())
        .limit(25)
        .all()
    )
    deletable, delete_reason = staff_service.can_delete(user)
    return render_template(
        "admin/staff/view.html",
        staff=user,
        sales=sales,
        shifts=shifts,
        stats=staff_service.staff_stats(user),
        deletable=deletable,
        delete_reason=delete_reason,
    )


@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@permission_required("staff.manage")
def edit_staff(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        return render_template("errors/error.html", code=404, message="Staff member not found."), 404

    if request.method == "POST":
        try:
            user = staff_service.update_staff(g.user, user, _form_data(request.form))
            flash(f"{user.full_name}'s details have been updated.", "success")
            return redirect(url_for("staff.list_staff"))
        except ValidationError as exc:
            flash(exc.message, "error")

    return render_template(
        "admin/staff/form.html",
        staff=user,
        roles=[ROLE_CASHIER, ROLE_MANAGER, ROLE_ADMIN],
    )


@bp.route("/<int:user_id>/reset-password", methods=["GET", "POST"])
@permission_required("staff.manage")
def reset_password(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        return render_template("errors/error.html", code=404, message="Staff member not found."), 404

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if password != confirm:
            flash("The passwords do not match.", "error")
        else:
            try:
                staff_service.reset_password(
                    g.user, user, password,
                    must_change=bool(request.form.get("must_change_password")),
                )
                flash(f"The password for {user.full_name} has been reset.", "success")
                return redirect(url_for("staff.view_staff", user_id=user.id))
            except ValidationError as exc:
                flash(exc.message, "error")

    return render_template("admin/staff/reset_password.html", staff=user)


@bp.route("/<int:user_id>/toggle", methods=["POST"])
@permission_required("staff.manage")
def toggle_active(user_id: int):
    """DEACTIVATE / ACTIVATE. Sales history is always preserved."""
    user = db.session.get(User, user_id)
    if user is None:
        flash("Staff member not found.", "error")
    else:
        try:
            was_active = user.is_active
            staff_service.set_active(g.user, user, not was_active)
            flash(
                f"{user.full_name} has been {'deactivated' if was_active else 'activated'}. "
                + ("Their past sales are still kept in the reports." if not was_active else ""),
                "success",
            )
        except ValidationError as exc:
            flash(exc.message, "error")
    return redirect(request.referrer or url_for("staff.list_staff"))


@bp.route("/<int:user_id>/delete", methods=["POST"])
@permission_required("staff.manage")
def delete_staff(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        flash("Staff member not found.", "error")
    else:
        try:
            name = user.full_name
            staff_service.delete_staff(g.user, user)
            flash(f"Unused account '{name}' has been deleted.", "success")
        except ValidationError as exc:
            flash(exc.message, "error")
    return redirect(url_for("staff.list_staff"))


@bp.route("/permissions", methods=["GET", "POST"])
@permission_required("staff.permissions")
def permissions():
    """Let the owner decide exactly what a Manager or Cashier may do."""
    from app.database.models import Permission, Role, RolePermission

    if request.method == "POST":
        from app.database.permissions import ROLE_DEFAULT_PERMISSIONS

        role = db.session.get(Role, request.form.get("role_id", type=int))
        if role is None:
            flash("Choose a role.", "error")
            return redirect(url_for("staff.permissions"))

        selected = set(request.form.getlist("permissions"))
        if role.name == ROLE_ADMIN:
            flash("Administrators always have every permission.", "warning")
            return redirect(url_for("staff.permissions"))

        for rp in list(role.permissions):
            db.session.delete(rp)
        db.session.flush()
        for key in selected:
            perm = Permission.query.filter_by(key=key).first()
            if perm:
                db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db.session.commit()

        from app.audit import service as audit

        audit.record(
            audit.PERMISSIONS_UPDATE, user=g.user, entity_type="Role", entity_id=role.id,
            description=f"{g.user.full_name} changed {role.name} permissions",
            new_value={"permissions": sorted(selected)}, ip_address=request.remote_addr, commit=True,
        )
        flash(f"{role.name.title()} permissions have been saved.", "success")
        return redirect(url_for("staff.permissions"))

    roles = Role.query.filter(Role.name != ROLE_ADMIN).all()
    for role in roles:
        role.granted = {rp.permission.key for rp in role.permissions if rp.permission}

    grouped: dict[str, list] = {}
    for key, (group, description) in PERMISSIONS.items():
        grouped.setdefault(group, []).append({"key": key, "description": description})

    return render_template("admin/staff/permissions.html", roles=roles, grouped=grouped)


def _form_data(form) -> dict:
    data = dict(form.items())
    if "is_active" not in data:
        # An unchecked checkbox is not submitted; treat as off.
        data["is_active"] = "0"
    return data
