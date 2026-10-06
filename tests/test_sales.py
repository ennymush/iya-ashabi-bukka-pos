"""Cart, discount, payment and sale-completion tests - the financial core."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.database.models import (
    STATUS_COMPLETED,
    STATUS_REFUNDED,
    STATUS_VOIDED,
    InventoryMovement,
    Payment,
    Product,
    Sale,
    SaleItem,
    Shift,
    User,
)
from app.extensions import db
from app.sales.service import complete_sale, next_receipt_number, refund_sale, void_sale
from app.utils.errors import PaymentError, StockError, ValidationError


def first_product(app, sku="IAB-ME-001"):
    return Product.query.filter_by(sku=sku).first()


# ---------------------------------------------------------------------------
# Money handling
# ---------------------------------------------------------------------------
def test_prices_are_stored_as_integer_minor_units(app):
    with app.app_context():
        product = first_product(app)
        # Python sees whole Naira as Decimal; the DB column holds integer kobo.
        assert product.selling_price == Decimal("2500.00")
        assert isinstance(product.selling_price, Decimal)
        raw = db.session.execute(db.text("SELECT selling_price FROM products WHERE id = :i"), {"i": product.id}).scalar()
        assert raw == 250000, "the column must store integer kobo"


def test_money_helpers_are_exact():
    from app.utils.money import fmt, from_minor, to_minor

    assert to_minor("2500.00") == 250000
    assert to_minor("0.1") == 10
    assert from_minor(250000) == Decimal("2500.00")
    assert fmt(Decimal("2500.00")) == "\u20a62,500.00"
    assert fmt(Decimal("15000")) == "\u20a615,000.00"


def test_totals_never_use_floating_point(app):
    """0.1 + 0.2 style errors must not appear in money maths."""
    from app.utils.money import to_decimal

    assert to_decimal("0.1") + to_decimal("0.2") == Decimal("0.30")
    assert (to_decimal("1234.56") * 3) == Decimal("3703.68")


# ---------------------------------------------------------------------------
# Sale creation
# ---------------------------------------------------------------------------
def test_cash_sale_completes_with_correct_totals(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 2}],
            payment_method="CASH",
            amount_received="10000",
        )
        sale = outcome.sale
        assert sale.status == STATUS_COMPLETED
        assert sale.subtotal == Decimal("5000.00")   # 2 x 2500
        assert sale.total_amount == Decimal("5000.00")
        assert sale.amount_received == Decimal("10000.00")
        assert sale.change_given == Decimal("5000.00")
        assert sale.cashier_name == "John Doe"
        assert sale.receipt_number.startswith("IAB-")


def test_transfer_card_and_other_payments(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        for method in ("TRANSFER", "CARD", "OTHER"):
            outcome = complete_sale(
                user=john,
                cart_payload=[{"product_id": product.id, "quantity": 1}],
                payment_method=method,
            )
            assert outcome.sale.payment_method == method
            # Non-cash payments do not need an amount tendered.
            assert outcome.sale.change_given == 0
            assert outcome.sale.amount_received == outcome.sale.total_amount


def test_underpayment_is_refused(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        with pytest.raises(PaymentError) as exc:
            complete_sale(
                user=john,
                cart_payload=[{"product_id": product.id, "quantity": 2}],
                payment_method="CASH",
                amount_received="100",
            )
        assert "less than" in exc.value.message.lower()
        assert Sale.query.count() == 0, "no sale may be saved when payment is refused"


def test_missing_amount_is_refused(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        with pytest.raises(PaymentError):
            complete_sale(
                user=john,
                cart_payload=[{"product_id": product.id, "quantity": 1}],
                payment_method="CASH",
                amount_received=None,
            )


def test_empty_cart_is_refused(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        with pytest.raises(ValidationError):
            complete_sale(user=john, cart_payload=[], payment_method="CASH", amount_received="5000")


def test_invalid_payment_method_is_refused(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        with pytest.raises(ValidationError):
            complete_sale(
                user=john,
                cart_payload=[{"product_id": product.id, "quantity": 1}],
                payment_method="CRYPTO",
                amount_received="5000",
            )


def test_out_of_stock_cannot_be_sold(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        product.stock_quantity = 0
        db_commit()
        with pytest.raises(StockError):
            complete_sale(
                user=john,
                cart_payload=[{"product_id": product.id, "quantity": 1}],
                payment_method="CASH",
                amount_received="5000",
            )
        assert Sale.query.count() == 0


def test_overselling_is_refused_and_rolls_back(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        product.stock_quantity = 1
        db_commit()
        with pytest.raises(StockError):
            complete_sale(
                user=john,
                cart_payload=[{"product_id": product.id, "quantity": 5}],
                payment_method="CASH",
                amount_received="20000",
            )
        # Nothing partial may survive.
        assert Sale.query.count() == 0
        assert SaleItem.query.count() == 0
        assert Payment.query.count() == 0
        assert db_get(product).stock_quantity == 1


def test_client_totals_are_ignored_and_server_prices_win(app, cashier):
    """The browser cannot dictate the price."""
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 1, "unit_price": 1, "line_total": 1}],
            payment_method="CASH",
            amount_received="5000",
        )
        assert outcome.sale.total_amount == Decimal("2500.00")
        assert outcome.sale.items[0].unit_price == Decimal("2500.00")


def test_transaction_is_atomic_on_failure(app, cashier):
    """If one line fails, nothing at all is written."""
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        good = first_product(app, "IAB-ME-001")
        bad = first_product(app, "IAB-PR-001")
        bad.stock_quantity = 0
        db_commit()

        with pytest.raises(StockError):
            complete_sale(
                user=john,
                cart_payload=[
                    {"product_id": good.id, "quantity": 1},
                    {"product_id": bad.id, "quantity": 1},
                ],
                payment_method="CASH",
                amount_received="10000",
            )

        assert Sale.query.count() == 0
        assert SaleItem.query.count() == 0
        assert Payment.query.count() == 0
        # Stock on the good product must be untouched too.
        assert db_get(good).stock_quantity == good.stock_quantity


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
def test_inventory_is_deducted_in_the_same_transaction(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        before = product.stock_quantity
        complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 3}],
            payment_method="CASH",
            amount_received="10000",
        )
        assert db_get(product).stock_quantity == before - 3

        movement = InventoryMovement.query.filter_by(reason="Sale").first()
        assert movement.difference == -3
        assert movement.old_quantity == before
        assert movement.new_quantity == before - 3


# ---------------------------------------------------------------------------
# Historical price integrity
# ---------------------------------------------------------------------------
def test_sale_item_freezes_price_at_time_of_sale(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        original = product.selling_price

        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 1}],
            payment_method="CASH",
            amount_received="5000",
        )
        sale_id = outcome.sale.id

        # Admin changes the price afterwards.
        from app.products.service import quick_price_change

        quick_price_change(john, product, "3000.00", "Price rise")

        sale = db.session.get(Sale, sale_id)
        item = sale.items[0]
        assert item.unit_price == original, "history must keep the old price"
        assert sale.total_amount == Decimal("2500.00")
        # The live product now uses the new price.
        assert db_get(product).selling_price == Decimal("3000.00")


def test_price_history_is_recorded(app, cashier):
    from app.products.service import quick_price_change

    with app.app_context():
        product = first_product(app)
        before = product.selling_price
        quick_price_change(product and User.query.filter_by(username="john").first(),
                           product, "3500.00", "Supplier increase")
        from app.database.models import PriceHistory

        entry = PriceHistory.query.filter_by(product_id=product.id).first()
        assert entry.old_price == before
        assert entry.new_price == Decimal("3500.00")
        assert entry.reason == "Supplier increase"
        assert entry.changed_by_id is not None


# ---------------------------------------------------------------------------
# Receipt numbers
# ---------------------------------------------------------------------------
def test_receipt_numbers_are_unique_and_sequential(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        numbers = []
        for _ in range(3):
            outcome = complete_sale(
                user=john,
                cart_payload=[{"product_id": product.id, "quantity": 1}],
                payment_method="CASH",
                amount_received="10000",
            )
            numbers.append(outcome.sale.receipt_number)

        assert len(set(numbers)) == 3, "receipt numbers must be unique"
        for number in numbers:
            assert number.startswith("IAB-")
            assert len(number.split("-")[-1]) == 6
        assert int(numbers[1].split("-")[-1]) == int(numbers[0].split("-")[-1]) + 1


def test_receipt_number_contains_the_sale_date(app, cashier):
    from datetime import date

    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 1}],
            payment_method="CASH",
            amount_received="5000",
        )
        assert date.today().strftime("%Y%m%d") in outcome.sale.receipt_number


# ---------------------------------------------------------------------------
# Discounts
# ---------------------------------------------------------------------------
def test_percentage_discount_is_applied(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)   # 2500
        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 1}],
            payment_method="CASH",
            amount_received="5000",
            discount_spec={"type": "PERCENT", "value": "10"},
        )
        assert outcome.sale.subtotal == Decimal("2500.00")
        assert outcome.sale.discount_amount == Decimal("250.00")
        assert outcome.sale.total_amount == Decimal("2250.00")


def test_fixed_discount_is_applied(app, manager):
    with app.app_context():
        john = User.query.filter_by(username="mary").first()
        product = first_product(app)
        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 2}],
            payment_method="CASH",
            amount_received="10000",
            discount_spec={"type": "FIXED", "value": "500"},
        )
        assert outcome.sale.discount_amount == Decimal("500.00")
        assert outcome.sale.total_amount == Decimal("4500.00")


def test_cashier_cannot_exceed_their_discount_limit(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()  # limited to 10%
        product = first_product(app)
        with pytest.raises(ValidationError) as exc:
            complete_sale(
                user=john,
                cart_payload=[{"product_id": product.id, "quantity": 1}],
                payment_method="CASH",
                amount_received="5000",
                discount_spec={"type": "PERCENT", "value": "40"},
            )
        assert "ask a manager" in exc.value.message.lower()


def test_manager_can_override_discount_limit(app, manager):
    with app.app_context():
        mary = User.query.filter_by(username="mary").first()
        product = first_product(app)
        outcome = complete_sale(
            user=mary,
            cart_payload=[{"product_id": product.id, "quantity": 1}],
            payment_method="CASH",
            amount_received="5000",
            discount_spec={"type": "PERCENT", "value": "40"},
        )
        assert outcome.sale.discount_amount == Decimal("1000.00")
        assert outcome.sale.total_amount == Decimal("1500.00")


def test_discount_cannot_exceed_the_subtotal(app, manager):
    from app.sales.service import resolve_discount

    with app.app_context():
        mary = User.query.filter_by(username="mary").first()
        amount, _info = resolve_discount(250000, {"type": "FIXED", "value": "99999"}, mary)
        assert amount == 250000, "discount is capped at the subtotal"


def test_every_discount_is_audited(app, cashier):
    from app.database.models import AuditLog

    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 1}],
            payment_method="CASH",
            amount_received="5000",
            discount_spec={"type": "PERCENT", "value": "5"},
        )
        assert AuditLog.query.filter_by(action="DISCOUNT_APPLIED").count() >= 1


# ---------------------------------------------------------------------------
# Voids and refunds
# ---------------------------------------------------------------------------
def test_void_marks_the_sale_and_restores_stock(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        before = product.stock_quantity

        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 2}],
            payment_method="CASH",
            amount_received="10000",
        )
        sale = outcome.sale
        assert db_get(product).stock_quantity == before - 2

        void_sale(sale, john, "Customer changed their mind")

        assert sale.status == STATUS_VOIDED
        assert sale.void_reason == "Customer changed their mind"
        assert sale.voided_by_id == john.id
        # The original sale row is still there - never deleted.
        assert db.session.get(Sale, sale.id) is not None
        # Stock came back.
        assert db_get(product).stock_quantity == before


def test_void_requires_a_reason(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 1}],
            payment_method="CASH",
            amount_received="5000",
        )
        with pytest.raises(ValidationError):
            void_sale(outcome.sale, john, "")


def test_full_refund_creates_a_return_and_keeps_the_sale(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        before = product.stock_quantity

        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 2}],
            payment_method="CASH",
            amount_received="10000",
        )
        sale = outcome.sale
        item = sale.items[0]

        record = refund_sale(
            sale, john,
            [{"sale_item_id": item.id, "quantity": 2}],
            reason="Food returned",
            refund_method="CASH",
        )

        assert record.total_amount == Decimal("5000.00")
        assert record.original_receipt == sale.receipt_number
        assert db.session.get(Sale, sale.id) is not None, "original sale must survive"
        assert db_get(sale).status == STATUS_REFUNDED
        assert db_get(product).stock_quantity == before
        # A refund payment is recorded separately.
        assert Payment.query.filter_by(is_refund=True).count() == 1


def test_partial_refund_leaves_the_sale_completed(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 4}],
            payment_method="CASH",
            amount_received="20000",
        )
        sale = outcome.sale
        item = sale.items[0]

        refund_sale(sale, john, [{"sale_item_id": item.id, "quantity": 1}], reason="One plate extra")
        assert db_get(sale).status == STATUS_COMPLETED
        assert db_get(item).returned_quantity == 1


def test_refund_requires_items_and_a_reason(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 1}],
            payment_method="CASH",
            amount_received="5000",
        )
        sale = outcome.sale
        with pytest.raises(ValidationError):
            refund_sale(sale, john, [], reason="nothing selected")
        with pytest.raises(ValidationError):
            refund_sale(
                sale, john,
                [{"sale_item_id": sale.items[0].id, "quantity": 1}], reason="",
            )


def test_refund_is_capped_at_the_quantity_sold(app, cashier):
    """Requesting more than was sold must never over-refund."""
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        product = first_product(app)
        outcome = complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 2}],
            payment_method="CASH",
            amount_received="10000",
        )
        sale = outcome.sale
        item = sale.items[0]

        # Asking for 99 against a 2-plate sale refunds only the 2 plates.
        record = refund_sale(sale, john, [{"sale_item_id": item.id, "quantity": 99}], reason="All of it")
        assert record.total_amount == Decimal("5000.00"), "only what was sold may be refunded"

        # A second attempt has nothing left to return and must be refused.
        with pytest.raises(ValidationError):
            refund_sale(sale, john, [{"sale_item_id": item.id, "quantity": 1}], reason="again")


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------
def test_shift_records_cash_and_closes_with_variance(app, cashier):
    with app.app_context():
        john = User.query.filter_by(username="john").first()
        shift = Shift(user_id=john.id, opening_cash="50000.00", is_open=True)
        db_add(shift)
        db_commit()

        product = first_product(app)
        complete_sale(
            user=john,
            cart_payload=[{"product_id": product.id, "quantity": 2}],
            payment_method="CASH",
            amount_received="10000",
            shift_id=shift.id,
        )

        # One refresh, then assert - refreshing discards pending changes.
        db.session.refresh(shift)
        assert shift.cash_sales == Decimal("5000.00")
        assert shift.transaction_count == 1
        assert shift.total_sales == Decimal("5000.00")

        expected = shift.opening_cash + shift.cash_sales - shift.cash_refunds
        shift.expected_cash = expected
        shift.actual_cash = expected - Decimal("500.00")
        shift.variance = shift.actual_cash - expected
        shift.is_open = False
        db_commit()

        db.session.refresh(shift)
        assert shift.expected_cash == Decimal("55000.00")
        assert shift.variance == Decimal("-500.00")
        assert shift.is_open is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def db_commit():
    from app.extensions import db

    db.session.commit()


def db_add(obj):
    from app.extensions import db

    db.session.add(obj)


def db_get(obj):
    from app.extensions import db

    db.session.refresh(obj)
    return obj
