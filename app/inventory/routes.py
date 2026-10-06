"""Inventory routes: stock levels, adjustments, low stock."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.database.models import (
    MOVEMENT_REASONS,
    REASON_DELIVERY,
    Category,
    InventoryMovement,
    Product,
)
from app.extensions import db
from app.products import service as product_service
from app.utils.errors import ValidationError
from app.utils.security import permission_required

bp = Blueprint("inventory", __name__, url_prefix="/admin/inventory")


@bp.route("/stock")
@permission_required("inventory.view")
def stock_list():
    view = request.args.get("view", "all")
    category_id = request.args.get("category_id", type=int)
    query = (request.args.get("q") or "").strip()
    show_cost = g.user.has_any_permission("products.cost")

    stmt = Product.query
    if query:
        like = f"%{query}%"
        stmt = stmt.filter(db.or_(Product.name.ilike(like), Product.sku.ilike(like),
                                  Product.barcode.ilike(like)))
    if category_id:
        stmt = stmt.filter(Product.category_id == category_id)
    if view == "low":
        stmt = stmt.filter(Product.stock_quantity <= Product.minimum_stock,
                           Product.is_active.is_(True))
    elif view == "out":
        stmt = stmt.filter(Product.stock_quantity <= 0)
    elif view == "active":
        stmt = stmt.filter(Product.is_active.is_(True))

    products = stmt.order_by(Product.name).all()
    summary = {
        "total": db.session.query(db.func.count(Product.id)).scalar(),
        "low": db.session.query(db.func.count(Product.id)).filter(
            Product.stock_quantity <= Product.minimum_stock, Product.stock_quantity > 0,
            Product.is_active.is_(True)).scalar(),
        "out": db.session.query(db.func.count(Product.id)).filter(
            Product.stock_quantity <= 0, Product.is_active.is_(True)).scalar(),
        "stock_value": db.session.query(
            db.func.coalesce(db.func.sum(Product.stock_quantity * Product.cost_price), 0)
        ).scalar(),
    }

    return render_template(
        "admin/inventory/stock.html",
        products=products,
        categories=Category.query.order_by(Category.sort_order, Category.name).all(),
        view=view, query=query, selected_category=category_id,
        summary=summary, show_cost=show_cost,
    )


@bp.route("/stock/<int:product_id>/adjust", methods=["POST"])
@permission_required("inventory.manage")
def adjust_stock(product_id: int):
    """ADD STOCK / REMOVE STOCK / ADJUST STOCK - always records a movement."""
    product = db.session.get(Product, product_id)
    if product is None:
        flash("Product not found.", "error")
        return redirect(url_for("inventory.stock_list"))

    action = request.form.get("action", "set")
    reason = request.form.get("reason") or REASON_DELIVERY
    note = request.form.get("note", "")
    raw = request.form.get("quantity")
    current = product.stock_quantity

    try:
        if action == "set":
            product = product_service.adjust_stock(g.user, product, raw, reason, note)
            flash(f"Stock for '{product.name}' is now {product.stock_quantity}.", "success")
        else:
            try:
                delta = int(str(raw).strip())
            except (TypeError, ValueError):
                raise ValidationError("Enter a valid quantity.")
            if action == "remove":
                delta = -abs(delta)
                reason = reason or "Damaged"
            product = product_service.adjust_stock_by(g.user, product, delta, reason, note)
            verb = "removed from" if delta < 0 else "added to"
            flash(f"{abs(delta)} unit(s) {verb} '{product.name}'. Stock is now {product.stock_quantity}.",
                  "success")
    except ValidationError as exc:
        flash(exc.message, "error")

    return redirect(request.referrer or url_for("inventory.stock_list"))


@bp.route("/adjustments")
@permission_required("inventory.view")
def adjustments():
    page = request.args.get("page", 1, type=int)
    stmt = InventoryMovement.query
    if request.args.get("product_id", type=int):
        stmt = stmt.filter(InventoryMovement.product_id == request.args["product_id"])
    if request.args.get("reason"):
        stmt = stmt.filter(InventoryMovement.reason == request.args["reason"])
    if request.args.get("q"):
        like = f"%{request.args['q']}%"
        stmt = stmt.join(Product, isouter=True).filter(
            db.or_(Product.name.ilike(like), InventoryMovement.reference.ilike(like))
        )

    movements = stmt.order_by(InventoryMovement.created_at.desc()).paginate(
        page=page, per_page=50, error_out=False
    )
    return render_template(
        "admin/inventory/adjustments.html",
        movements=movements,
        reasons=MOVEMENT_REASONS,
    )


@bp.route("/low-stock")
@permission_required("inventory.view")
def low_stock():
    products = (
        Product.query.filter(
            Product.stock_quantity <= Product.minimum_stock,
            Product.is_active.is_(True),
        )
        .order_by(Product.stock_quantity.asc())
        .all()
    )
    return render_template("admin/inventory/low_stock.html", products=products)
