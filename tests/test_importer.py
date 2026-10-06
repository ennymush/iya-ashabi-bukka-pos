"""Tests for the bulk price / product list importer."""
import io
import zipfile

import pytest

from app.products import import_service
from app.products.importer import (
    detect_header,
    normalise_key,
    parse_amount,
    read_products,
    rows_from_table,
)
from app.utils.errors import ValidationError

from .conftest import csrf_for, login

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ---------------------------------------------------------------------------
# Money parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value,expected",
    [
        (2500, "2500.00"),
        ("2500", "2500.00"),
        ("2,500.00", "2500.00"),
        ("12,345.67", "12345.67"),
        ("12.50", "12.50"),
        ("\u20a62,500", "2500.00"),
        ("NGN 2500", "2500.00"),
        ("  1,500  ", "1500.00"),
    ],
)
def test_parse_amount_reads_real_world_spellings(value, expected):
    assert str(parse_amount(value)) == expected


@pytest.mark.parametrize("value", ["", None, "abc", "not a price"])
def test_parse_amount_returns_none_when_there_is_no_number(value):
    assert parse_amount(value) is None


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------
def test_header_aliases_are_recognised():
    mapping = detect_header([["Item", "Group", "Amount", "Qty"]])[1]
    assert mapping == {"name": 0, "category": 1, "price": 2, "stock": 3}


def test_decorated_headers_still_match():
    mapping = detect_header([["Product Name", "Selling Price (\u20a6)"]])[1]
    assert mapping["name"] == 0
    assert mapping["price"] == 1


def test_header_is_found_below_a_title_row():
    table = [
        ["IYA ASHABI BUKKA LIMITED"],
        [],
        ["Product Name", "Selling Price"],
        ["Jollof Rice", "2500"],
    ]
    rows = rows_from_table(table)
    assert len(rows) == 1
    assert rows[0]["name"] == "Jollof Rice"
    assert str(rows[0]["price"]) == "2500.00"


def test_normalise_key_strips_currency_symbols():
    assert normalise_key("Selling Price (\u20a6)") == "selling price"


# ---------------------------------------------------------------------------
# Loose text (PDF / plain Word lines)
# ---------------------------------------------------------------------------
def test_loose_lines_are_split_into_name_and_price():
    rows = rows_from_table([
        ["IYA ASHABI BUKKA"],
        ["1. Jollof Rice ....... 2500"],
        ["Puff Puff - 500"],
        ["Coke 350ml 700"],
        ["Just a heading with no price"],
    ])
    names = [row["name"] for row in rows]
    assert "Jollof Rice" in names
    # A number inside the name stays part of the name; the tail is the price.
    assert "Coke 350ml" in names
    assert "Just a heading with no price" not in names


# ---------------------------------------------------------------------------
# File formats
# ---------------------------------------------------------------------------
def test_csv_file_is_read():
    data = b"Item,Category,Price\nJollof Rice,Main Meals,2500\nZobo,Drinks,1000\n"
    rows = read_products("menu.csv", data)
    assert [row["name"] for row in rows] == ["Jollof Rice", "Zobo"]
    assert rows[0]["category"] == "Main Meals"


def test_excel_file_is_read_with_stock():
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Product Name", "Selling Price", "Stock"])
    sheet.append(["Egusi Soup", "3,500", 20])
    stream = io.BytesIO()
    workbook.save(stream)

    rows = read_products("list.xlsx", stream.getvalue())
    assert rows[0]["name"] == "Egusi Soup"
    assert rows[0]["stock"] == 20


def test_word_table_is_read_without_a_third_party_library():
    body = (
        "<w:tbl>"
        "<w:tr><w:tc><w:p><w:r><w:t>Product</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Price</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>Amala</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>1500</w:t></w:r></w:p></w:tc></w:tr>"
        "</w:tbl>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{WORD_NS}"><w:body>{body}</w:body></w:document>'
    ).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)

    rows = read_products("menu.docx", buffer.getvalue())
    assert rows[0]["name"] == "Amala"
    assert str(rows[0]["price"]) == "1500.00"


def test_unsupported_extension_is_refused():
    with pytest.raises(Exception) as exc:
        read_products("menu.doc", b"legacy binary")
    assert "Excel, CSV, Word" in str(exc.value)


# ---------------------------------------------------------------------------
# Matching and categorising (plan phase)
# ---------------------------------------------------------------------------
def test_plan_reports_new_products_and_price_changes(app, admin):
    from app.products.service import create_product

    # A name that is not in the demo seed, so this test measures the importer
    # rather than the sample menu that the fixture loads.
    existing = create_product(admin, {
        "name": "Importer Test Rice",
        "selling_price": "2000",
        "cost_price": "1000",
    })

    plan = import_service.build_plan([
        {"name": "Importer Test Rice", "price": 2500, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
        {"name": "Importer Brand New Dish", "price": 1800, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
    ])

    summary = plan["summary"]
    assert summary["new"] == 1
    assert summary["price"] == 1
    # The existing product must be matched, not duplicated.
    price_row = next(r for r in plan["rows"] if r["name"] == "Importer Test Rice")
    assert price_row["action"] == import_service.ACTION_PRICE
    assert price_row["product"].id == existing.id
    assert price_row["new_price"] == 250000  # kobo


def test_plan_matches_on_barcode_before_name(app, admin):
    from app.products.service import create_product

    existing = create_product(admin, {
        "name": "Chicken",
        "selling_price": "2000",
        "cost_price": "800",
        "barcode": "5060112233445",
    })

    plan = import_service.build_plan([{
        "name": "Chicken Special", "price": 2300, "cost": None, "category": None,
        "sku": None, "barcode": "5060112233445", "stock": None,
    }])
    row = plan["rows"][0]
    assert row["action"] == import_service.ACTION_PRICE
    assert row["product"].id == existing.id


def test_price_drop_is_flagged_separately(app, admin):
    from app.products.service import create_product

    create_product(admin, {"name": "Zobo", "selling_price": "1000", "cost_price": "400"})

    plan = import_service.build_plan([{
        "name": "Zobo", "price": 800, "cost": None, "category": None,
        "sku": None, "barcode": None, "stock": None,
    }])
    assert plan["rows"][0]["action"] == import_service.ACTION_DETAIL
    assert plan["summary"]["detail"] == 1


def test_identical_price_is_reported_as_no_change(app, admin):
    from app.products.service import create_product

    create_product(admin, {"name": "Suya", "selling_price": "2000", "cost_price": "900"})

    plan = import_service.build_plan([{
        "name": "Suya", "price": 2000, "cost": None, "category": None,
        "sku": None, "barcode": None, "stock": None,
    }])
    assert plan["rows"][0]["action"] == import_service.ACTION_SAME
    assert plan["summary"]["changes"] == 0


def test_problem_rows_are_reported_not_silently_dropped(app, admin):
    plan = import_service.build_plan([
        {"name": "", "price": 1000, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
        {"name": "No Price", "price": None, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
        {"name": "Zero Price", "price": 0, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
        {"name": "Twice", "price": 500, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
        {"name": "Twice", "price": 600, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
    ])
    assert plan["summary"]["problem"] == 4
    assert plan["summary"]["new"] == 1  # only the first "Twice" is usable
    problems = [r["detail"] for r in plan["rows"] if r["action"] == "problem"]
    assert any("listed twice" in detail for detail in problems)


def test_category_column_is_used(app, admin):
    plan = import_service.build_plan([{
        "name": "Soya Salad", "price": 1000, "cost": None, "category": "Snacks",
        "sku": None, "barcode": None, "stock": None,
    }])
    # "Snacks" is one of the seeded categories, so it is matched, not created.
    assert plan["rows"][0]["category_name"] == "Snacks"
    assert plan["rows"][0]["category_new"] is None
    assert plan["rows"][0]["category_id"] is not None


def test_unknown_category_is_planned_for_creation(app, admin):
    plan = import_service.build_plan([{
        "name": "Mystery Item", "price": 1000, "cost": None, "category": "Grills",
        "sku": None, "barcode": None, "stock": None,
    }])
    assert plan["rows"][0]["category_id"] is None
    assert plan["rows"][0]["category_new"] == "Grills"
    assert "Grills" in plan["summary"]["new_categories"]


def test_category_is_inferred_from_the_product_name(app, admin):
    plan = import_service.build_plan([
        {"name": "Jollof Rice", "price": 1000, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
        {"name": "Zobo", "price": 500, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
        {"name": "Total Mystery", "price": 700, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
    ])
    categories = [row["category_name"] for row in plan["rows"]]
    assert categories == ["Main Meals", "Drinks", "Other"]


def test_a_huge_file_is_refused(app, admin):
    rows = [
        {"name": f"Item {n}", "price": 100, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None}
        for n in range(import_service.MAX_ROWS + 1)
    ]
    with pytest.raises(ValidationError):
        import_service.build_plan(rows)
# ---------------------------------------------------------------------------
# Applying the plan (writes to the database)
# ---------------------------------------------------------------------------
def test_apply_creates_products_with_inferred_categories(app, admin):
    from app.database.models import Product
    from app.utils.money import to_minor

    plan = import_service.build_plan([
        {"name": "Zobo Special", "price": 1200, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": 10},
    ])
    result = import_service.apply_plan(admin, plan["rows"])

    assert result["created"] == 1
    assert result["failed"] == []
    product = Product.query.filter_by(name="Zobo Special").first()
    assert product is not None
    assert product.category.name == "Drinks"
    assert to_minor(product.selling_price) == 120000
    assert product.stock_quantity == 10


def test_apply_creates_a_category_named_in_the_file(app, admin):
    from app.database.models import Category, Product

    plan = import_service.build_plan([{
        "name": "Grilled Tilapia", "price": 4500, "cost": None, "category": "Grills",
        "sku": None, "barcode": None, "stock": None,
    }])
    import_service.apply_plan(admin, plan["rows"])

    assert Category.query.filter_by(name="Grills").first() is not None
    product = Product.query.filter_by(name="Grilled Tilapia").first()
    assert product.category.name == "Grills"


def test_apply_records_price_history_like_a_manual_change(app, admin):
    from app.database.models import PriceHistory, Product
    from app.products.service import create_product
    from app.utils.money import to_minor

    create_product(admin, {"name": "Importer Fried Rice", "selling_price": "2000",
                            "cost_price": "1000"})

    plan = import_service.build_plan([{
        "name": "Importer Fried Rice", "price": 2600, "cost": None, "category": None,
        "sku": None, "barcode": None, "stock": None,
    }])
    result = import_service.apply_plan(admin, plan["rows"])
    assert result["repriced"] == 1

    product = Product.query.filter_by(name="Importer Fried Rice").first()
    assert to_minor(product.selling_price) == 260000

    # The audit guarantee: a bulk change is recorded just like a manual one.
    history = PriceHistory.query.filter_by(product_id=product.id).all()
    assert len(history) == 1
    assert to_minor(history[0].old_price) == 200000
    assert to_minor(history[0].new_price) == 260000
    assert history[0].reason == "Bulk price list import"
    assert history[0].changed_by_id == admin.id


def test_apply_skips_price_drops_unless_asked(app, admin):
    from app.database.models import Product
    from app.products.service import create_product
    from app.utils.money import to_minor

    create_product(admin, {"name": "Importer Zobo", "selling_price": "1000",
                            "cost_price": "400"})

    plan = import_service.build_plan([{
        "name": "Importer Zobo", "price": 800, "cost": None, "category": None,
        "sku": None, "barcode": None, "stock": None,
    }])

    import_service.apply_plan(admin, plan["rows"], include_price_drops=False)
    product = Product.query.filter_by(name="Importer Zobo").first()
    assert to_minor(product.selling_price) == 100000  # untouched

    import_service.apply_plan(admin, plan["rows"], include_price_drops=True)
    product = Product.query.filter_by(name="Importer Zobo").first()
    assert to_minor(product.selling_price) == 80000


def test_apply_ignores_rows_not_selected(app, admin):
    from app.database.models import Product

    plan = import_service.build_plan([{
        "name": "Should Not Appear", "price": 1000, "cost": None, "category": None,
        "sku": None, "barcode": None, "stock": None,
    }])
    plan["rows"][0]["skipped"] = True
    import_service.apply_plan(admin, plan["rows"])

    assert Product.query.filter_by(name="Should Not Appear").first() is None


def test_one_bad_row_does_not_abort_the_whole_import(app, admin):
    from app.database.models import Product

    plan = import_service.build_plan([
        {"name": "Good One", "price": 1000, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
        {"name": "", "price": 500, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
        {"name": "Good Two", "price": 1500, "cost": None, "category": None,
         "sku": None, "barcode": None, "stock": None},
    ])
    result = import_service.apply_plan(admin, plan["rows"])

    assert result["created"] == 2
    assert Product.query.filter_by(name="Good One").first() is not None
    assert Product.query.filter_by(name="Good Two").first() is not None
# ---------------------------------------------------------------------------
# Through the web screen
# ---------------------------------------------------------------------------
def test_import_screen_needs_permission(app, client, cashier):
    login(client, "john", "Rice#Grill9")
    response = client.get("/admin/products/import")
    assert response.status_code in (302, 403)


def test_admin_can_open_the_import_screen(app, client, admin):
    login(client, "owner", "Bakery#2026x")
    response = client.get("/admin/products/import")
    assert response.status_code == 200
    assert "Choose your file" in response.get_data(as_text=True)


def test_upload_previews_and_writes_nothing_yet(app, client, admin):
    from app.database.models import Product

    login(client, "owner", "Bakery#2026x")
    body = "Product Name,Category,Selling Price\nImporter Preview Dish,Main Meals,3000\n"

    client.get("/admin/products/import")
    response = client.post(
        "/admin/products/import",
        data={
            "stage": "preview",
            "csrf_token": csrf_for(client),
            "price_file": (io.BytesIO(body.encode("utf-8")), "menu.csv"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "NEW PRODUCT" in page
    assert "Importer Preview Dish" in page
    # A preview must never write to the database.
    assert Product.query.filter_by(name="Importer Preview Dish").first() is None


def test_confirmed_rows_are_imported(app, client, admin):
    import json

    from app.database.models import Product
    from app.utils.money import to_minor

    login(client, "owner", "Bakery#2026x")
    rows = [{
        "line": 1, "name": "Importer Preview Dish", "action": "new", "detail": "",
        "old_price": None, "new_price": 300000, "cost_price": None,
        "category_id": None, "category_name": "Main Meals", "category_new": "Main Meals",
        "sku": None, "barcode": None, "stock": None, "include": "1",
    }]
    response = client.post(
        "/admin/products/import",
        data={
            "stage": "apply",
            "csrf_token": csrf_for(client),
            "plan": json.dumps(rows),
        },
    )
    assert response.status_code == 302
    product = Product.query.filter_by(name="Importer Preview Dish").first()
    assert product is not None
    assert to_minor(product.selling_price) == 300000


def test_rows_not_ticked_are_not_imported(app, client, admin):
    import json

    from app.database.models import Product

    login(client, "owner", "Bakery#2026x")
    rows = [{
        "line": 1, "name": "Unticked Item", "action": "new", "detail": "",
        "old_price": None, "new_price": 100000, "cost_price": None,
        "category_id": None, "category_name": None, "category_new": None,
        "sku": None, "barcode": None, "stock": None, "include": "0",
    }]
    client.post(
        "/admin/products/import",
        data={"stage": "apply", "csrf_token": csrf_for(client),
              "plan": json.dumps(rows)},
    )
    assert Product.query.filter_by(name="Unticked Item").first() is None


def test_a_tampered_action_cannot_trigger_a_write(app, client, admin):
    """A hand-edited hidden field must not be able to run an unknown action."""
    import json

    from app.database.models import Product

    login(client, "owner", "Bakery#2026x")
    rows = [{
        "line": 1, "name": "Sneaky Item", "action": "delete_everything",
        "detail": "", "old_price": None, "new_price": 100000, "cost_price": None,
        "category_id": None, "category_name": None, "category_new": None,
        "sku": None, "barcode": None, "stock": None, "include": "1",
    }]
    client.post(
        "/admin/products/import",
        data={"stage": "apply", "csrf_token": csrf_for(client),
              "plan": json.dumps(rows)},
    )
    assert Product.query.filter_by(name="Sneaky Item").first() is None


def test_an_unreadable_file_shows_an_error_instead_of_crashing(app, client, admin):
    login(client, "owner", "Bakery#2026x")
    client.get("/admin/products/import")
    response = client.post(
        "/admin/products/import",
        data={
            "stage": "preview",
            "csrf_token": csrf_for(client),
            "price_file": (io.BytesIO(b"not a real document"), "notes.docx"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert "Choose your file" in response.get_data(as_text=True)
def test_a_file_with_no_products_is_refused():
    with pytest.raises(Exception) as exc:
        read_products("empty.csv", b"just a heading\nanother line\n")
    assert "No products" in str(exc.value)