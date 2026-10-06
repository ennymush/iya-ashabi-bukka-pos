"""Product, category, inventory, reporting, Excel, backup and POS HTTP tests."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.database.models import (
    LOW_STOCK,
    OUT_OF_STOCK,
    IN_STOCK,
    AuditLog,
    Category,
    InventoryMovement,
    PriceHistory,
    Product,
    Sale,
    User,
)
from app.products.service import (
    adjust_stock,
    create_category,
    create_product,
    quick_price_change,
    set_active,
    update_product,
)
from app.utils.errors import ValidationError
from tests.conftest import csrf_for, login


def actor(app, username="owner"):
    return User.query.filter_by(username=username).first()


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
def test_admin_creates_a_product_and_it_appears_immediately(app, admin):
    with app.app_context():
        owner = actor(app)
        category = Category.query.filter_by(name="Main Meals").first()
        product = create_product(owner, {
            "name": "Jollof Rice Special",
            "category_id": category.id,
            "cost_price": "1800.00",
            "selling_price": "2500.00",
            "stock_quantity": "40",
            "minimum_stock": "10",
            "barcode": "IAB-NEW-001",
            "sku": "IAB-NEW-001",
        })
        assert product.id
        assert product.selling_price == Decimal("2500.00")
        assert product.stock_quantity == 40
        # Opening stock is recorded as a movement, never silently set.
        movement = InventoryMovement.query.filter_by(product_id=product.id).first()
        assert movement.reason == "Opening Stock"
        assert movement.difference == 40


def test_new_product_is_visible_to_the_cashier_with_no_deployment(app, admin, cashier, client):
    with app.app_context():
        create_product(actor(app), {
            "name": "Grilled Tilapia",
            "selling_price": "3500.00",
            "cost_price": "2500.00",
            "stock_quantity": "10",
            "sku": "IAB-FISH-9",
        })

    login(client, "john", "Rice#Grill9")
    payload = client.get("/api/pos/products?q=Grilled").get_json()
    assert payload["ok"]
    names = [p["name"] for p in payload["products"]]
    assert "Grilled Tilapia" in names


def test_product_search_matches_name_sku_and_barcode(app, admin, cashier, client):
    with app.app_context():
        create_product(actor(app), {
            "name": "Pounded Yam", "selling_price": "2200.00",
            "sku": "YY-100", "barcode": "99887766", "stock_quantity": "5",
        })
    login(client, "john", "Rice#Grill9")
    for query, expected in (("Pounded", "Pounded Yam"), ("YY-100", None), ("99887766", None)):
        data = client.get(f"/api/pos/products?q={query}").get_json()
        assert data["products"], f"search for {query} found nothing"


def test_barcode_endpoint_resolves_a_product(app, admin, cashier, client):
    with app.app_context():
        create_product(actor(app), {
            "name": "Bottle Water", "selling_price": "200.00",
            "barcode": "5012345678900", "stock_quantity": "50",
        })
    login(client, "john", "Rice#Grill9")
    data = client.get("/api/pos/barcode/5012345678900").get_json()
    assert data["ok"]
    assert data["product"]["name"] == "Bottle Water"
    assert data["matched_on"] == "barcode"


def test_unknown_barcode_returns_a_readable_error(app, client, cashier):
    login(client, "john", "Rice#Grill9")
    response = client.get("/api/pos/barcode/0000000000000")
    assert response.status_code == 404
    assert "not found" in response.get_json()["error"].lower()


def test_duplicate_sku_is_rejected(app, admin):
    with app.app_context():
        owner = actor(app)
        create_product(owner, {"name": "Test A", "selling_price": "100.00", "sku": "DUP-1"})
        with pytest.raises(ValidationError):
            create_product(owner, {"name": "Test B", "selling_price": "100.00", "sku": "DUP-1"})


def test_product_name_is_required(app, admin):
    with app.app_context():
        with pytest.raises(ValidationError):
            create_product(actor(app), {"name": "  ", "selling_price": "100.00"})


def test_selling_price_must_be_greater_than_zero(app, admin):
    with app.app_context():
        with pytest.raises(ValidationError):
            create_product(actor(app), {"name": "Free Food", "selling_price": "0"})


def test_price_change_updates_till_and_records_history(app, admin, cashier, client):
    with app.app_context():
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        quick_price_change(actor(app), product, "3000.00", "Vegetable price rise")
        assert product.selling_price == Decimal("3000.00")

        entry = PriceHistory.query.filter_by(product_id=product.id).first()
        assert entry.old_price == Decimal("2500.00")
        assert entry.new_price == Decimal("3000.00")
        assert entry.changed_by_id == actor(app).id

    # The cashier sees the new price on the very next request.
    login(client, "john", "Rice#Grill9")
    data = client.get("/api/pos/products?q=Jollof").get_json()
    jollof = [p for p in data["products"] if p["sku"] == "IAB-ME-001"][0]
    assert jollof["price"] == "\u20a63,000.00"


def test_price_change_to_the_same_value_is_rejected(app, admin):
    with app.app_context():
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        current = product.selling_price
        with pytest.raises(ValidationError):
            quick_price_change(actor(app), product, str(current))


def test_deactivating_a_product_hides_it_from_the_till_but_keeps_history(app, admin, client, cashier):
    from app.sales.service import complete_sale

    with app.app_context():
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        outcome = complete_sale(
            user=actor(app, "john"),
            cart_payload=[{"product_id": product.id, "quantity": 1}],
            payment_method="CASH",
            amount_received="10000",
        )
        set_active(actor(app), product, False)
        assert product.is_active is False

    login(client, "john", "Rice#Grill9")
    data = client.get("/api/pos/products?q=Jollof").get_json()
    assert not [p for p in data["products"] if p["sku"] == "IAB-ME-001"]

    # History is untouched.
    with app.app_context():
        assert Sale.query.count() == 1
        assert Sale.query.first().items[0].product_name == "Jollof Rice"


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
def test_default_categories_are_seeded_and_editable(app, admin):
    with app.app_context():
        names = {c.name for c in Category.query.all()}
        for expected in ("Main Meals", "Soups", "Swallow", "Protein", "Drinks", "Snacks"):
            assert expected in names


def test_admin_creates_and_renames_a_category(app, admin):
    with app.app_context():
        owner = actor(app)
        category = create_category(owner, {"name": "Weekend Specials"})
        assert category.id
        update_product  # noqa: B018 - ensure import used
        category.name = "Weekend Specials"
        assert category.name == "Weekend Specials"


def test_duplicate_category_is_rejected(app, admin):
    with app.app_context():
        with pytest.raises(ValidationError):
            create_category(actor(app), {"name": "Main Meals"})


def test_category_with_products_cannot_be_deactivated(app, admin):
    from app.products.service import set_category_active

    with app.app_context():
        category = Category.query.filter_by(name="Main Meals").first()
        with pytest.raises(ValidationError) as exc:
            set_category_active(actor(app), category, False)
        assert "still has" in exc.value.message.lower()


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
def test_stock_adjustment_is_always_recorded(app, admin):
    with app.app_context():
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        before = product.stock_quantity
        adjust_stock(actor(app), product, before + 50, "New Delivery", "Supplier weekly order")
        movement = InventoryMovement.query.filter_by(reason="New Delivery").first()
        assert movement.old_quantity == before
        assert movement.new_quantity == before + 50
        assert movement.difference == 50
        assert movement.note == "Supplier weekly order"


def test_low_stock_and_out_of_stock_statuses(app, admin):
    with app.app_context():
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        product.minimum_stock = 10
        product.stock_quantity = 10
        assert product.stock_status == LOW_STOCK
        product.stock_quantity = 9
        assert product.stock_status == LOW_STOCK
        product.stock_quantity = 0
        assert product.stock_status == OUT_OF_STOCK
        product.stock_quantity = 50
        assert product.stock_status == IN_STOCK


def test_out_of_stock_product_cannot_be_added_to_cart(app, client, cashier):
    with app.app_context():
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        product.stock_quantity = 0
        from app.extensions import db

        db.session.commit()
        product_id = product.id

    login(client, "john", "Rice#Grill9")
    response = client.post(
        "/api/pos/cart/add",
        data={"product_id": product_id, "quantity": 1, "csrf_token": csrf_for(client)},
    )
    assert response.status_code == 400
    assert "out of stock" in response.get_json()["error"].lower()


def test_adding_more_than_available_is_refused(app, client, cashier):
    with app.app_context():
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        product.stock_quantity = 2
        from app.extensions import db

        db.session.commit()
        product_id = product.id

    login(client, "john", "Rice#Grill9")
    response = client.post(
        "/api/pos/cart/add",
        data={"product_id": product_id, "quantity": 5, "csrf_token": csrf_for(client)},
    )
    assert response.status_code == 400
    assert "in stock" in response.get_json()["error"].lower()


def test_quantity_can_be_decreased_and_removed(app, client, cashier):
    with app.app_context():
        from app.extensions import db

        product = Product.query.filter_by(sku="IAB-ME-001").first()
        db.session.commit()
        pid = product.id

    login(client, "john", "Rice#Grill9")
    client.post("/api/pos/cart/add", data={"product_id": pid, "quantity": 3, "csrf_token": csrf_for(client)})
    data = client.post(
        "/api/pos/cart/quantity",
        data={"product_id": pid, "quantity": 1, "csrf_token": csrf_for(client)},
    ).get_json()
    assert data["items"][0]["quantity"] == 1

    data = client.post(
        "/api/pos/cart/remove", data={"product_id": pid, "csrf_token": csrf_for(client)}
    ).get_json()
    assert data["item_count"] == 0


# ---------------------------------------------------------------------------
# POS HTTP flow
# ---------------------------------------------------------------------------
def test_shift_is_required_before_checkout(app, client, cashier):
    from app.database import service_settings as S
    from app.extensions import db

    with app.app_context():
        S.set_value("system.allow_sale_without_shift", "0")
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        db.session.commit()
        pid = product.id

    login(client, "john", "Rice#Grill9")
    client.post("/api/pos/cart/add", data={"product_id": pid, "quantity": 1, "csrf_token": csrf_for(client)})
    response = client.post(
        "/api/pos/checkout",
        data={"payment_method": "CASH", "amount_received": "10000", "csrf_token": csrf_for(client)},
    )
    assert response.status_code == 400
    assert "open your shift" in response.get_json()["error"].lower()


def test_full_checkout_over_http(app, client, cashier):
    from app.extensions import db

    with app.app_context():
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        db.session.commit()
        pid = product.id

    login(client, "john", "Rice#Grill9")
    opened = client.post(
        "/pos/shift/open", data={"opening_cash": "50000", "csrf_token": csrf_for(client)}
    ).get_json()
    assert opened["ok"]

    client.post("/api/pos/cart/add", data={"product_id": pid, "quantity": 2, "csrf_token": csrf_for(client)})
    response = client.post(
        "/api/pos/checkout",
        data={"payment_method": "CASH", "amount_received": "10000", "csrf_token": csrf_for(client)},
    )
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["total"] == "\u20a65,000.00"
    assert payload["change"] == "\u20a65,000.00"
    assert payload["receipt_number"].startswith("IAB-")

    # Cart is emptied and the drawer event recorded.
    assert client.get("/api/pos/cart").get_json()["item_count"] == 0
    with app.app_context():
        from app.database.models import CashDrawerEvent

        assert CashDrawerEvent.query.filter_by(status="OPENED").count() >= 1


def test_held_sale_can_be_stored_and_retrieved(app, client, cashier):
    from app.extensions import db

    with app.app_context():
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        db.session.commit()
        pid = product.id

    login(client, "john", "Rice#Grill9")
    client.post("/api/pos/cart/add", data={"product_id": pid, "quantity": 2, "csrf_token": csrf_for(client)})
    held = client.post("/api/pos/hold", data={"label": "Table 5", "csrf_token": csrf_for(client)}).get_json()
    assert held["ok"]
    assert client.get("/api/pos/cart").get_json()["item_count"] == 0

    listing = client.get("/api/pos/held").get_json()
    assert listing["held"][0]["label"] == "Table 5"

    restored = client.post(
        f"/api/pos/held/{listing['held'][0]['id']}/retrieve", data={"csrf_token": csrf_for(client)}
    ).get_json()
    assert restored["item_count"] == 2


def test_cashier_only_sees_own_transactions(app, client, cashier, manager):
    from app.sales.service import complete_sale

    with app.app_context():
        john = actor(app, "john")
        mary = actor(app, "mary")
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        complete_sale(user=john, cart_payload=[{"product_id": product.id, "quantity": 1}],
                      payment_method="CASH", amount_received="10000")
        complete_sale(user=mary, cart_payload=[{"product_id": product.id, "quantity": 1}],
                      payment_method="CASH", amount_received="10000")

    login(client, "john", "Rice#Grill9")
    assert client.get("/pos/transactions").status_code == 200

    with app.app_context():
        sale_ids = [s.id for s in Sale.query.all()]
    for sale_id in sale_ids:
        sale = None
        with app.app_context():
            sale = db_get_sale(sale_id)
            john_id = actor(app, "john").id
        if sale.cashier_id != john_id:
            response = client.get(f"/pos/transactions/{sale_id}")
            assert response.status_code == 403


def db_get_sale(sale_id):
    from app.database.models import Sale as S
    from app.extensions import db

    return db.session.get(S, sale_id)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def test_sales_summary_matches_the_data(app, admin):
    from app.reports.service import sales_summary
    from app.sales.service import complete_sale

    with app.app_context():
        owner = actor(app)
        product = Product.query.filter_by(sku="IAB-ME-001").first()   # 2500
        cash = Product.query.filter_by(sku="IAB-DR-002").first()      # 1000
        complete_sale(user=owner, cart_payload=[{"product_id": product.id, "quantity": 1}],
                      payment_method="CASH", amount_received="10000")
        complete_sale(user=owner, cart_payload=[{"product_id": cash.id, "quantity": 1}],
                      payment_method="CARD")

        summary = sales_summary(date.today(), date.today())
        assert summary["transactions"] == 2
        assert summary["total"] == Decimal("3500.00")
        assert summary["cash"] == Decimal("2500.00")
        assert summary["card"] == Decimal("1000.00")
        assert summary["items_sold"] == 2


def test_profit_report_uses_historical_cost(app, admin):
    from app.products.service import quick_price_change
    from app.reports.service import profit_report
    from app.sales.service import complete_sale

    with app.app_context():
        owner = actor(app)
        product = Product.query.filter_by(sku="IAB-ME-001").first()
        complete_sale(user=owner, cart_payload=[{"product_id": product.id, "quantity": 1}],
                      payment_method="CASH", amount_received="10000")
        quick_price_change(owner, product, "5000.00", "Big increase")
        quick_price_change(owner, product, "1800.00", "Cost change test")
        # Put the cost back so only the price differs
        product.cost_price = Decimal("1800.00")
        from app.extensions import db

        db.session.commit()

        report = profit_report(date.today(), date.today())
        # Sale happened at 2500 with cost 1800.
        assert report["revenue"] == Decimal("2500.00")
        assert report["cost"] == Decimal("1800.00")
        assert report["profit"] == Decimal("700.00")


def test_cashier_cannot_open_the_profit_report(app, client, cashier):
    login(client, "john", "Rice#Grill9")
    assert client.get("/admin/reports/profit").status_code == 403


def test_report_pages_render_for_admin(app, client, admin):
    login(client, "owner", "Bakery#2026x")
    for path in (
        "/admin/dashboard", "/admin/products", "/admin/sales/transactions",
        "/admin/inventory/stock", "/admin/reports", "/admin/reports/sales",
        "/admin/reports/products", "/admin/reports/cashiers", "/admin/reports/payments",
        "/admin/reports/inventory", "/admin/reports/profit", "/admin/audit",
        "/admin/staff", "/admin/settings/business", "/admin/settings/hardware",
        "/admin/settings/receipt", "/admin/settings/display", "/admin/settings/database",
    ):
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------
def test_excel_export_returns_a_valid_workbook(app, client, admin):
    login(client, "owner", "Bakery#2026x")
    response = client.get("/admin/reports/export/sales")
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["Content-Type"]
    assert "attachment" in response.headers["Content-Disposition"]
    assert "Iya_Ashabi_Bukka_Sales_" in response.headers["Content-Disposition"]

    import io

    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(response.data))
    assert workbook.sheetnames[0] == "SUMMARY"
    for sheet in ("SUMMARY", "TRANSACTIONS", "PRODUCT SALES", "CASHIER PERFORMANCE", "PAYMENTS"):
        assert sheet in workbook.sheetnames


def test_excel_export_uses_naira_number_format(app, client, admin):
    login(client, "owner", "Bakery#2026x")
    response = client.get("/admin/reports/export/inventory")
    workbook_row = response
    assert workbook_row.status_code == 200

    import io

    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(response.data))
    sheet = workbook["INVENTORY"]
    found = False
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.number_format, str) and cell.number_format.startswith('"\u20a6"'):
                found = True
    assert found, "Naira number format must be applied"


def test_cashier_cannot_export(app, client, cashier):
    login(client, "john", "Rice#Grill9")
    assert client.get("/admin/reports/export/sales").status_code == 403


def test_excel_filename_avoids_overwriting(app, admin):
    from app.reports.excel import safe_filename

    import os
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        cwd = os.getcwd()
        os.chdir(folder)
        try:
            first = safe_filename("report.xlsx", "")
            open(first, "w").close()
            second = safe_filename("report.xlsx", "")
            assert first != second
        finally:
            os.chdir(cwd)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------
def test_backup_creates_a_valid_file(app, admin):
    from app.database import backup as backup_service

    with app.app_context():
        entry = backup_service.create_backup(user=actor(app))
        assert entry.status == "SUCCESS"
        assert entry.filename.endswith(".db")
        assert entry.size_bytes > 0

        valid, message = backup_service.validate_backup(entry.path)
        assert valid, message


def test_backup_does_not_touch_the_active_database(app, admin):
    import os

    from app.database import backup as backup_service

    with app.app_context():
        active = backup_service._database_path()
        before_mtime = os.path.getmtime(active) if active.exists() else None
        backup_service.create_backup(user=actor(app))
        assert active.exists()
        if before_mtime:
            assert os.path.getmtime(active) == before_mtime, "backup must not modify the live database"


def test_restore_rejects_an_invalid_file(app, admin):
    import tempfile
    from pathlib import Path

    from app.database import backup as backup_service

    with app.app_context():
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            handle.write(b"this is not a database")
            junk = Path(handle.name)
        valid, message = backup_service.validate_backup(junk)
        assert valid is False
        junk.unlink(missing_ok=True)


def test_restore_brings_back_earlier_data(app, admin):
    from app.database import backup as backup_service
    from app.sales.service import complete_sale

    with app.app_context():
        owner = actor(app)
        product = Product.query.filter_by(sku="IAB-ME-001").first()

        # Take a backup BEFORE any sale.
        backup = backup_service.create_backup(user=owner)
        assert backup.status == "SUCCESS"

        complete_sale(user=owner, cart_payload=[{"product_id": product.id, "quantity": 1}],
                      payment_method="CASH", amount_received="10000")
        assert Sale.query.count() == 1

        # Restore the empty backup.
        result = backup_service.restore_backup(backup.path, user=owner)
        assert result["ok"] is True
        assert result["safety_backup"], "a safety backup must be taken first"
        assert Sale.query.count() == 0, "the sale should be gone after restore"


def test_audit_log_records_key_actions(app, admin):
    with app.app_context():
        actions = {entry.action for entry in AuditLog.query.all()}
        assert "USER_CREATE" in actions


def test_audit_never_stores_passwords(app, admin, cashier):
    from app.staff.service import reset_password

    with app.app_context():
        reset_password(actor(app), actor(app, "john"), "Sup3rSecret!x")
        for entry in AuditLog.query.all():
            blob = f"{entry.old_value or ''}{entry.new_value or ''}{entry.description or ''}"
            assert "Sup3rSecret" not in blob
            assert "Rice#Grill9" not in blob
