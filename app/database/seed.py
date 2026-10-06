"""Database seeding: roles, permissions, default settings, categories.

Safe to run repeatedly - every step is idempotent so the owner can re-seed
after a restore without duplicating anything.
"""
from __future__ import annotations

from app.database.default_data import DEFAULT_CATEGORIES, DEMO_PRODUCTS, DEFAULT_SETTINGS
from app.database.models import Category, Permission, Role, RolePermission, Setting
from app.database.permissions import ROLE_DEFAULT_PERMISSIONS
from app.extensions import db


def seed_permissions() -> None:
    """Create the permission catalogue and attach defaults to each role."""
    for key, (_group, description) in _catalogue().items():
        row = Permission.query.filter_by(key=key).first()
        if row is None:
            db.session.add(Permission(key=key, description=description))
    db.session.flush()

    for role_name, keys in ROLE_DEFAULT_PERMISSIONS.items():
        role = Role.query.filter_by(name=role_name).first()
        if role is None:
            role = Role(name=role_name, description=f"{role_name.title()} role")
            db.session.add(role)
            db.session.flush()

        if role_name == "ADMIN":
            # Admins implicitly hold every permission; keep rows for UI clarity.
            existing = {rp.permission.key for rp in role.permissions if rp.permission}
            for key in keys:
                if key not in existing:
                    perm = Permission.query.filter_by(key=key).first()
                    db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        else:
            for key in keys:
                perm = Permission.query.filter_by(key=key).first()
                if perm and not RolePermission.query.filter_by(
                    role_id=role.id, permission_id=perm.id
                ).first():
                    db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.session.commit()


def _catalogue():
    from app.database.permissions import PERMISSIONS

    return PERMISSIONS


def seed_settings() -> None:
    """Write the real business defaults into the settings table."""
    for key, (value, category, description) in DEFAULT_SETTINGS.items():
        if Setting.query.filter_by(key=key).first() is None:
            db.session.add(Setting(key=key, value=value, category=category, description=description))
    db.session.commit()


def seed_categories() -> None:
    for name, description, color, order in DEFAULT_CATEGORIES:
        if Category.query.filter_by(name=name).first() is None:
            db.session.add(
                Category(name=name, description=description, color=color, sort_order=order)
            )
    db.session.commit()


def seed_demo_products(with_demo: bool = True) -> int:
    """Optional sample products so the till is usable while setting up.

    These are ordinary editable rows - the owner can rename, reprice or
    deactivate them, and add their own.
    """
    if not with_demo:
        return 0
    created = 0
    for name, cat_name, cost, price, stock, minimum, sku, unit in DEMO_PRODUCTS:
        if ProductExists(sku):
            continue
        category = Category.query.filter_by(name=cat_name).first()
        from app.database.models import Product

        db.session.add(
            Product(
                name=name,
                sku=sku,
                category_id=category.id if category else None,
                cost_price=cost,
                selling_price=price,
                stock_quantity=stock,
                minimum_stock=minimum,
                unit=unit,
                is_active=True,
            )
        )
        created += 1
    db.session.commit()
    return created


def ProductExists(sku: str) -> bool:
    from app.database.models import Product

    return Product.query.filter_by(sku=sku).first() is not None


def seed_all(with_demo: bool = True) -> dict:
    seed_permissions()
    seed_settings()
    seed_categories()
    created = seed_demo_products(with_demo)
    return {"demo_products_created": created}
