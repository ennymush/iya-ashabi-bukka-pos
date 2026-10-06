"""Bulk import of price and product lists.

Two clear phases:

1. ``build_plan`` reads the parsed rows and works out, WITHOUT touching the
   database, what would happen: which products are new, which prices would
   change, which rows are a problem. The owner sees this and confirms.
2. ``apply_plan`` performs the work by calling the ordinary
   :mod:`app.products.service` functions, so every price change still writes a
   ``price_history`` row and an audit-log entry exactly as a manual change does.

Categorisation
--------------
The rules, in order of trust:

1. A ``Category`` column in the file. An existing category of that name is
   reused; an unknown name creates the category.
2. Keywords in the product name, matched against the shop's own categories
   (Jollof Rice -> Main Meals, Zobo -> Drinks, and so on).
3. ``Other`` when nothing matches, so nothing is ever lost.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.database.models import Category, Product
from app.database.types import minor_of, naira_of
from app.extensions import db
from app.products import service as product_service
from app.products.importer import normalise_name
from app.utils.errors import ValidationError
from app.utils.logging_config import get_logger
from app.utils.money import from_minor, to_minor

log = get_logger("import")

# Row outcomes shown to the owner on the preview screen.
ACTION_NEW = "new"
ACTION_PRICE = "price"
ACTION_DETAIL = "detail"
ACTION_SAME = "same"
ACTION_PROBLEM = "problem"

# Keyword -> category name. Lower-cased product names are searched for these.
CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "Main Meals": (
        "jollof", "rice", "fried rice", "pasta", "spaghetti", "noodle", "yam",
        "pounded yam", "egusi", "okra", "soup", "stew", "afang", "edikang",
        "ogbono", "pepper soup", "banga", "vegetable", "fried plantain",
        "chips", "meal", "plate", "grilled", "roti",
    ),
    "Soups": (
        "soup", "egusi", "okra", "pepper", "light soup", "bone marrow",
        "palm nut", "afang", "banga", "ogbono", "vegetable soup",
    ),
    "Swallow": (
        "swallow", "eba", "iwu", "fufu", "pounded yam", "yam", "garri",
    ),
    "Protein": (
        "chicken", "beef", "fish", "pork", "turkey", "goat", "egg", "eggs",
        "shaki", "bush meat", "snail", "periwinkle", "grilled fish",
        "fried fish", "omelette",
    ),
    "Drinks": (
        "zobo", "juice", "water", "coke", "pepsi", "fanta", "sprite",
        "milo", "nescafe", "coffee", "tea", "smoothie", "drink", "beverage",
        "chilled", "yoghurt", "yogurt", "lassi", "chapman", "punnet",
    ),
    "Snacks": (
        "puff puff", "puff-puff", "samosa", "spring roll", "suya", "skew",
        "meat pie", "fish pie", "sausage", "burger", "hot dog", "chips",
        "cracker", "biscuit", "nuts", "salad",
    ),
    "Desserts": (
        "cake", "pastry", "pudding", "ice cream", "gelato", "sweet",
        "dessert", "pie", "cookie", "biscuit", "donut", "doughnut", "smoothie",
    ),
    "Takeaway": (
        "takeaway", "take away", "pack", "packed", "delivery",
    ),
}

FALLBACK_CATEGORY = "Other"

# How many rows we are willing to process in one go.
MAX_ROWS = 2000


class PlanRow(dict):
    """One parsed row plus what would happen to it. A dict so Jinja is simple."""


def _hint_category(name: str) -> str | None:
    """Guess a category from keywords in the product name."""
    haystack = normalise_name(name)
    if not haystack:
        return None
    best: tuple[int, str] | None = None
    for category, keywords in CATEGORY_HINTS.items():
        for keyword in keywords:
            # Whole-word match keeps "rice" from matching "ricotta".
            pattern = rf"\b{re.escape(keyword)}\b"
            if re.search(pattern, haystack) and (best is None or len(keyword) > best[0]):
                best = (len(keyword), category)
    return best[1] if best else None


def _resolve_category(existing: list[Category], label: str | None, product_name: str):
    """Return (category_or_None, name_of_category_that_will_be_created)."""
    label = (label or "").strip()
    if label:
        for category in existing:
            if normalise_name(category.name) == normalise_name(label):
                return category, None
        return None, label
    guessed = _hint_category(product_name) or FALLBACK_CATEGORY
    for category in existing:
        if normalise_name(category.name) == normalise_name(guessed):
            return category, None
    return None, guessed
# --------------------------------------------------------------------------
# Phase 1: plan
# --------------------------------------------------------------------------
def _find_existing(products: list[Product], row: dict) -> Product | None:
    """Match a row to a product the shop already sells.

    SKU and barcode are authoritative because they are unambiguous. The name is
    a fallback, compared on a normalised form so 'jollof rice' finds
    'Jollof Rice'.
    """
    sku = (row.get("sku") or "").strip().lower()
    if sku:
        for product in products:
            if (product.sku or "").strip().lower() == sku:
                return product
    barcode = (row.get("barcode") or "").strip().lower()
    if barcode:
        for product in products:
            if (product.barcode or "").strip().lower() == barcode:
                return product
    key = normalise_name(row.get("name"))
    if key:
        for product in products:
            if normalise_name(product.name) == key:
                return product
    return None


def build_plan(rows: list[dict]) -> dict:
    """Work out what an import would do, without writing anything.

    Returns ``rows`` (one per input line) and ``summary`` counts, which is
    exactly what the preview screen renders.
    """
    if not rows:
        raise ValidationError("There is nothing to import in that file.")
    if len(rows) > MAX_ROWS:
        raise ValidationError(
            f"That file has {len(rows)} rows. Please import at most {MAX_ROWS} at a time."
        )

    products = Product.query.order_by(Product.name).all()
    categories = Category.query.order_by(Category.sort_order, Category.name).all()

    planned: list[dict] = []
    seen_names: set[str] = set()
    new_category_names: list[str] = []
    skipped = 0

    for index, row in enumerate(rows, start=1):
        name = (row.get("name") or "").strip()
        price = row.get("price")
        entry = {
            "line": index,
            "name": name,
            "action": ACTION_PROBLEM,
            "detail": "",
            "product": None,
            "old_price": None,
            "new_price": price,
            "cost_price": row.get("cost"),
            # Plain values only: the plan is serialised into the confirmation
            # form, so it must not carry live ORM objects.
            "category_id": None,
            "category_name": None,
            "category_new": None,
            # Carried through to the apply step so nothing from the file is lost.
            "sku": (row.get("sku") or "").strip() or None,
            "barcode": (row.get("barcode") or "").strip() or None,
            "stock": row.get("stock"),
        }

        if not name:
            entry["detail"] = "No product name on this line."
            planned.append(entry)
            continue

        if price is None:
            entry["detail"] = "No price found on this line."
            planned.append(entry)
            continue

        try:
            new_minor = to_minor(price)
        except Exception:  # noqa: BLE001
            entry["detail"] = f"'{price}' is not a valid amount."
            planned.append(entry)
            continue

        if new_minor <= 0:
            entry["detail"] = "Price must be greater than zero."
            planned.append(entry)
            continue

        key = normalise_name(name)
        if key in seen_names:
            entry["detail"] = "This product is listed twice in the file."
            planned.append(entry)
            continue
        seen_names.add(key)

        entry["new_price"] = new_minor
        existing = _find_existing(products, row)
        category, category_new = _resolve_category(categories, row.get("category"), name)
        entry["category_id"] = category.id if category is not None else None
        entry["category_name"] = category.name if category is not None else category_new
        entry["category_new"] = category_new
        if category_new and category_new not in new_category_names:
            new_category_names.append(category_new)

        if existing is None:
            entry["action"] = ACTION_NEW
        else:
            entry["product"] = existing
            old_minor = minor_of(existing.selling_price)
            entry["old_price"] = old_minor
            if old_minor == new_minor:
                entry["action"] = ACTION_SAME
            elif old_minor > new_minor:
                entry["action"] = ACTION_DETAIL
                entry["detail"] = (
                    "This is lower than the current price - existing customers "
                    "may notice. Check it before importing."
                )
            else:
                entry["action"] = ACTION_PRICE
        planned.append(entry)

    summary = {
        "total": len(planned),
        "new": sum(1 for r in planned if r["action"] == ACTION_NEW),
        "price": sum(1 for r in planned if r["action"] == ACTION_PRICE),
        "detail": sum(1 for r in planned if r["action"] == ACTION_DETAIL),
        "same": sum(1 for r in planned if r["action"] == ACTION_SAME),
        "problem": sum(1 for r in planned if r["action"] == ACTION_PROBLEM),
        "new_categories": new_category_names,
    }
    summary["changes"] = summary["new"] + summary["price"] + summary["detail"]
    return {"rows": planned, "summary": summary}


# --------------------------------------------------------------------------
# Phase 2: apply
# --------------------------------------------------------------------------
def _category_cache() -> dict[str, Category]:
    return {
        normalise_name(category.name): category
        for category in Category.query.all()
    }


def _category_for(entry: dict, cache: dict[str, Category], user) -> Category | None:
    """Resolve the category a row should use, creating it when it is new.

    The plan travels through the browser as JSON, so it identifies a category
    by id or by name rather than holding a live model object.
    """
    category_id = entry.get("category_id")
    if category_id is not None:
        found = db.session.get(Category, int(category_id))
        if found is not None:
            return found

    label = entry.get("category_new") or entry.get("category_name") or FALLBACK_CATEGORY
    key = normalise_name(label)
    if key in cache:
        return cache[key]

    created = product_service.create_category(
        user, {"name": label, "color": "#8B4513", "sort_order": 0}
    )
    cache[key] = created
    log.info("Created category '%s' during import", label)
    return created


def apply_plan(user, entries: list[dict], *, include_price_drops: bool = True) -> dict:
    """Perform the planned import.

    Only rows the owner agreed to are touched: problems and unchanged rows are
    skipped. New products and price changes go through ``product_service`` so
    price history and the audit trail behave exactly as they do for a manual
    change. A row that fails is collected rather than aborting the whole import,
    because one bad line should not cost the owner the rest of the price list.
    """
    cache = _category_cache()
    created = 0
    repriced = 0
    failed: list[str] = []

    for entry in entries:
        action = entry.get("action")
        if action in (ACTION_PROBLEM, ACTION_SAME) or entry.get("skipped"):
            continue
        if action == ACTION_DETAIL and not include_price_drops:
            continue

        try:
            category = _category_for(entry, cache, user)

            if action == ACTION_NEW:
                product_service.create_product(
                    user,
                    {
                        "name": entry["name"],
                        "selling_price": str(from_minor(entry["new_price"])),
                        "cost_price": str(from_minor(entry.get("cost_price") or 0)),
                        "category_id": category.id if category else None,
                        "sku": entry.get("sku") or "",
                        "barcode": entry.get("barcode") or "",
                        # create_product reads the opening quantity from the
                        # form data; the keyword argument is for direct callers.
                        "stock_quantity": entry.get("stock") or 0,
                    },
                )
                created += 1

            elif action in (ACTION_PRICE, ACTION_DETAIL):
                product = entry.get("product")
                if product is None:
                    failed.append(f"{entry.get('name')}: product no longer exists.")
                    continue
                product_service.quick_price_change(
                    user,
                    product,
                    from_minor(entry["new_price"]),
                    reason="Bulk price list import",
                )
                if category is not None and product.category_id != category.id:
                    product.category_id = category.id
                repriced += 1
        except ValidationError as exc:
            failed.append(f"{entry.get('name')}: {exc.message}")
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the rest
            log.exception("Import row failed: %s", entry.get("name"))
            failed.append(f"{entry.get('name')}: {exc}")

    db.session.commit()

    result = {"created": created, "repriced": repriced, "failed": failed}
    log.info(
        "Import applied by %s: %d new, %d repriced, %d failed",
        getattr(user, "full_name", "?"), created, repriced, len(failed),
    )
    return result
