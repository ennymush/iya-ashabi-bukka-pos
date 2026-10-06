"""Admin product and category routes."""
from __future__ import annotations

import json

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.database.models import Category, PriceHistory, Product
from app.extensions import db
from app.products import service as product_service
from app.utils.errors import ValidationError
from app.utils.money import fmt
from app.utils.security import permission_required

bp = Blueprint("products", __name__, url_prefix="/admin")


@bp.route("/products")
@permission_required("products.admin")
def list_products():
    query = request.args.get("q", "").strip()
    category_id = request.args.get("category_id", type=int)
    status = request.args.get("status", "all")
    show_cost = g.user.has_any_permission("products.cost")

    stmt = Product.query
    if query:
        like = f"%{query}%"
        stmt = stmt.filter(db.or_(Product.name.ilike(like), Product.sku.ilike(like),
                                  Product.barcode.ilike(like)))
    if category_id:
        stmt = stmt.filter(Product.category_id == category_id)
    if status == "active":
        stmt = stmt.filter(Product.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.filter(Product.is_active.is_(False))
    elif status == "low":
        stmt = stmt.filter(Product.is_active.is_(True),
                           Product.stock_quantity <= Product.minimum_stock)

    products = stmt.order_by(Product.name).all()
    return render_template(
        "admin/products/list.html",
        products=products,
        categories=Category.query.order_by(Category.sort_order, Category.name).all(),
        query=query,
        selected_category=category_id,
        status=status,
        show_cost=show_cost,
    )


@bp.route("/products/new", methods=["GET", "POST"])
@permission_required("products.manage")
def create_product():
    if request.method == "POST":
        data = _form_data(request.form)
        try:
            product = product_service.create_product(g.user, data)
            flash(f"Product '{product.name}' has been added.", "success")
            return redirect(url_for("products.list_products"))
        except ValidationError as exc:
            flash(exc.message, "error")

    return render_template(
        "admin/products/form.html",
        product=None,
        categories=Category.query.filter_by(is_active=True).order_by(Category.sort_order).all(),
        show_cost=g.user.has_any_permission("products.cost"),
    )


@bp.route("/products/<int:product_id>")
@permission_required("products.admin")
def view_product(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        return render_template("errors/error.html", code=404, message="Product not found."), 404
    movements = product.movements.order_by(db.desc("created_at")).limit(30).all()
    history = product.price_history.order_by(db.desc("created_at")).limit(30).all()
    return render_template(
        "admin/products/view.html",
        product=product,
        movements=movements,
        history=history,
        show_cost=g.user.has_any_permission("products.cost"),
    )


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@permission_required("products.manage")
def edit_product(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        return render_template("errors/error.html", code=404, message="Product not found."), 404

    if request.method == "POST":
        try:
            product = product_service.update_product(g.user, product, _form_data(request.form))
            flash(f"'{product.name}' has been updated.", "success")
            return redirect(url_for("products.list_products"))
        except ValidationError as exc:
            flash(exc.message, "error")

    return render_template(
        "admin/products/form.html",
        product=product,
        categories=Category.query.order_by(Category.sort_order, Category.name).all(),
        show_cost=g.user.has_any_permission("products.cost"),
    )


@bp.route("/products/<int:product_id>/price", methods=["POST"])
@permission_required("products.price")
def change_price(product_id: int):
    """CHANGE PRICE - the single most-used admin action."""
    product = db.session.get(Product, product_id)
    if product is None:
        flash("Product not found.", "error")
        return redirect(url_for("products.list_products"))
    try:
        new_price = request.form.get("selling_price")
        reason = request.form.get("reason", "")
        product = product_service.quick_price_change(g.user, product, new_price, reason)
        flash(
            f"'{product.name}' now sells at {fmt(product.selling_price)}. "
            "The new price applies immediately at the till.",
            "success",
        )
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(request.referrer or url_for("products.list_products"))


@bp.route("/products/<int:product_id>/toggle", methods=["POST"])
@permission_required("products.manage")
def toggle_product(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        flash("Product not found.", "error")
    else:
        try:
            was_active = product.is_active
            product_service.set_active(g.user, product, not was_active)
            flash(
                f"'{product.name}' has been {'deactivated' if was_active else 'reactivated'}.",
                "success",
            )
        except ValidationError as exc:
            flash(exc.message, "error")
    return redirect(request.referrer or url_for("products.list_products"))


@bp.route("/products/price-history")
@permission_required("products.admin")
def price_history():
    page = request.args.get("page", 1, type=int)
    history = PriceHistory.query.order_by(PriceHistory.created_at.desc()).paginate(
        page=page, per_page=50, error_out=False
    )
    return render_template("admin/products/price_history.html", history=history)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
@bp.route("/categories", methods=["GET", "POST"])
@permission_required("products.categories")
def categories():
    if request.method == "POST":
        try:
            category = product_service.create_category(g.user, _form_data(request.form))
            flash(f"Category '{category.name}' has been added.", "success")
            return redirect(url_for("products.categories"))
        except ValidationError as exc:
            flash(exc.message, "error")

    return render_template(
        "admin/products/categories.html",
        categories=Category.query.order_by(Category.sort_order, Category.name).all(),
        editing=None,
    )


@bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
@permission_required("products.categories")
def edit_category(category_id: int):
    category = db.session.get(Category, category_id)
    if category is None:
        return render_template("errors/error.html", code=404, message="Category not found."), 404

    if request.method == "POST":
        try:
            product_service.update_category(g.user, category, _form_data(request.form))
            flash(f"Category '{category.name}' has been updated.", "success")
            return redirect(url_for("products.categories"))
        except ValidationError as exc:
            flash(exc.message, "error")

    return render_template(
        "admin/products/categories.html",
        categories=Category.query.order_by(Category.sort_order, Category.name).all(),
        editing=category,
    )


@bp.route("/categories/<int:category_id>/toggle", methods=["POST"])
@permission_required("products.categories")
def toggle_category(category_id: int):
    category = db.session.get(Category, category_id)
    if category is None:
        flash("Category not found.", "error")
    else:
        try:
            product_service.set_category_active(g.user, category, not category.is_active)
            flash(f"Category '{category.name}' updated.", "success")
        except ValidationError as exc:
            flash(exc.message, "error")
    return redirect(url_for("products.categories"))


# ---------------------------------------------------------------------------
# Bulk price / product list import
# ---------------------------------------------------------------------------
@bp.route("/products/import", methods=["GET", "POST"])
@permission_required("products.manage")
def import_products():
    """Upload a price list, preview it, then confirm before anything is saved.

    Three states, one screen:
      GET              - choose a file
      POST (stage=preview) - parse and show what would change
      POST (stage=apply)   - perform the import for the rows that were ticked
    """
    from app.products import import_service
    from app.products.importer import (
        SUPPORTED_EXTENSIONS,
        ImportError_,
        missing_dependency,
        read_products,
    )

    if request.method == "GET":
        return render_template(
            "admin/products/import.html",
            supported=", ".join(sorted(SUPPORTED_EXTENSIONS)),
            pdf_missing=missing_dependency("pdf") is not None,
            plan=None,
            rows=[],
        )

    stage = request.form.get("stage", "preview")

    # ---- Stage 2: apply the rows the owner ticked ------------------------
    if stage == "apply":
        try:
            payload = json.loads(request.form.get("plan") or "[]")
        except json.JSONDecodeError:
            flash("That import could not be read. Please upload the file again.", "error")
            return redirect(url_for("products.import_products"))

        entries = [row for row in payload if isinstance(row, dict)]
        # Only act on rows the owner explicitly ticked, and only on the
        # outcomes we understand - never trust hidden form fields blindly.
        chosen = []
        for row in entries:
            if not str(row.get("include", "")).lower() in {"1", "true", "on", "yes"}:
                continue
            if row.get("action") not in import_service_plan_actions():
                continue
            chosen.append(row)

        if not chosen:
            flash("No rows were selected, so nothing was imported.", "error")
            return redirect(url_for("products.list_products"))

        try:
            result = import_service.apply_plan(
                g.user, chosen,
                include_price_drops=request.form.get("include_drops") == "1",
            )
        except ValidationError as exc:
            flash(exc.message, "error")
            return redirect(url_for("products.import_products"))

        message = f"Imported {result['created']} new product(s) and updated {result['repriced']} price(s)."
        flash(message, "success")
        for problem in result["failed"][:5]:
            flash(f"Skipped {problem}", "error")
        if len(result["failed"]) > 5:
            flash(f"...and {len(result['failed']) - 5} more row(s) were skipped.", "error")
        return redirect(url_for("products.list_products"))

    # ---- Stage 1: parse and preview -------------------------------------
    upload = request.files.get("price_file")
    if upload is None or not upload.filename:
        flash("Please choose a price list file to upload.", "error")
        return render_template(
            "admin/products/import.html",
            supported=", ".join(sorted(SUPPORTED_EXTENSIONS)),
            pdf_missing=missing_dependency("pdf") is not None,
            plan=None,
            rows=[],
        )

    data = upload.read()
    try:
        parsed = read_products(upload.filename, data)
        plan = import_service.build_plan(parsed)
    except (ImportError_, ValidationError) as exc:
        flash(getattr(exc, "message", str(exc)), "error")
        return render_template(
            "admin/products/import.html",
            supported=", ".join(sorted(SUPPORTED_EXTENSIONS)),
            pdf_missing=missing_dependency("pdf") is not None,
            plan=None,
            rows=[],
        )

    summary = plan["summary"]
    if not summary["changes"]:
        flash("Everything in that file is already up to date. Nothing was changed.", "info")

    return render_template(
        "admin/products/import.html",
        supported=", ".join(sorted(SUPPORTED_EXTENSIONS)),
        pdf_missing=missing_dependency("pdf") is not None,
        plan=plan,
        rows=plan["rows"],
        filename=upload.filename,
    )


def import_service_plan_actions() -> tuple[str, ...]:
    """The outcomes an import is allowed to act on."""
    from app.products import import_service

    return (import_service.ACTION_NEW, import_service.ACTION_PRICE, import_service.ACTION_DETAIL)


def _form_data(form) -> dict:
    """Normalise form values, treating empty strings as None where relevant."""
    data = {key: value for key, value in form.items()}
    if "category_id" in data:
        try:
            data["category_id"] = int(data["category_id"]) if data["category_id"] else None
        except (TypeError, ValueError):
            data["category_id"] = None
    if "sort_order" in data:
        try:
            data["sort_order"] = int(data["sort_order"] or 0)
        except (TypeError, ValueError):
            data["sort_order"] = 0
    return data
