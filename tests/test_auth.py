"""Authentication, role-based access control and staff lifecycle tests."""
from __future__ import annotations

from app.database.models import ROLE_ADMIN, ROLE_CASHIER, ROLE_MANAGER, Role, User
from app.utils.security import hash_password, password_problem, verify_password
from tests.conftest import csrf_for, login


# ---------------------------------------------------------------------------
# Password security
# ---------------------------------------------------------------------------
def test_password_is_hashed_never_plaintext(app, cashier):
    with app.app_context():
        assert cashier.password_hash != "Rice#Grill9"
        assert cashier.password_hash.startswith("scrypt:")
        assert verify_password(cashier.password_hash, "Rice#Grill9")
        assert not verify_password(cashier.password_hash, "wrong-password")


def test_same_password_gets_different_hashes(app):
    """Salting means two identical passwords must not hash identically."""
    a = hash_password("Identical#123")
    b = hash_password("Identical#123")
    assert a != b
    assert verify_password(a, "Identical#123") and verify_password(b, "Identical#123")


def test_password_policy_rejects_weak_passwords():
    assert password_problem("short") is not None
    assert password_problem("password") is not None
    assert password_problem("") is not None
    assert password_problem("Good#Pass9") is None


def test_password_policy_rejects_username_in_password():
    assert password_problem("johnsmith123", username="johnsmith") is not None


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def test_valid_login_redirects_cashier_to_pos(client, cashier):
    response = login(client, "john", "Rice#Grill9")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/pos")


def test_valid_login_redirects_admin_to_dashboard(client, admin):
    response = login(client, "owner", "Bakery#2026x")
    assert response.status_code == 302
    assert "/admin/dashboard" in response.headers["Location"]


def test_valid_login_redirects_manager_to_dashboard(client, manager):
    response = login(client, "mary", "Stew#Point7")
    assert response.status_code == 302
    assert "/admin/dashboard" in response.headers["Location"]


def test_invalid_login_is_rejected(client, cashier):
    client.get("/login")
    response = client.post(
        "/login",
        data={"username": "john", "password": "definitely-wrong", "csrf_token": csrf_for(client)},
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_login_for_unknown_user_is_rejected(client, app):
    client.get("/login")
    response = client.post(
        "/login",
        data={"username": "ghost", "password": "whatever1", "csrf_token": csrf_for(client)},
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_role_is_decided_by_the_server_not_the_form(client, cashier):
    """A cashier cannot reach admin pages by adding a hidden role field."""
    client.get("/login")
    response = client.post(
        "/login",
        data={
            "username": "john", "password": "Rice#Grill9",
            "role": "ADMIN", "csrf_token": csrf_for(client),
        },
        follow_redirects=False,
    )
    assert response.headers["Location"].endswith("/pos")
    # And the admin area still refuses the session.
    assert client.get("/admin/products").status_code == 403


def test_deactivated_account_cannot_log_in(client, app, cashier):
    from app.staff.service import set_active

    with app.app_context():
        set_active(None, cashier, False)

    response = login(client, "john", "Rice#Grill9")
    assert response.status_code == 401


def test_logout_ends_the_session(client, cashier):
    login(client, "john", "Rice#Grill9")
    assert client.get("/pos").status_code == 200
    client.get("/logout")
    response = client.get("/pos")
    assert response.status_code == 302
    assert "login" in response.headers["Location"]


def test_anonymous_users_are_redirected_to_login(client, app):
    for path in ("/pos", "/admin/dashboard", "/admin/products", "/admin/staff"):
        response = client.get(path)
        assert response.status_code == 302, path
        assert "login" in response.headers["Location"]


def test_login_rate_limiting_blocks_repeated_failures(client, app, cashier):
    from app.utils.security import login_throttle

    login_throttle.reset("127.0.0.1|john")
    client.get("/login")
    token = csrf_for(client)
    for _ in range(6):
        client.post("/login", data={"username": "john", "password": "nope", "csrf_token": token})
    response = login(client, "john", "Rice#Grill9")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Role permissions
# ---------------------------------------------------------------------------
def test_cashier_cannot_view_cost_or_profit(app, cashier):
    with app.app_context():
        assert not cashier.has_any_permission("products.cost")
        assert not cashier.has_any_permission("reports.profit")


def test_cashier_has_till_permissions(app, cashier):
    with app.app_context():
        assert cashier.has_any_permission("pos.use")
        assert cashier.has_any_permission("sales.hold")
        assert not cashier.has_any_permission("sales.void")


def test_admin_holds_every_permission(app, admin):
    with app.app_context():
        from app.database.permissions import PERMISSIONS

        for key in PERMISSIONS:
            assert admin.has_any_permission(key), key


def test_manager_can_refund_and_void_but_not_manage_settings(app, manager):
    with app.app_context():
        assert manager.has_any_permission("sales.refund")
        assert manager.has_any_permission("sales.void")
        assert not manager.has_any_permission("settings.database")
        assert not manager.has_any_permission("staff.manage")


def test_cashier_cannot_reach_admin_urls(client, cashier):
    login(client, "john", "Rice#Grill9")
    for path in (
        "/admin/dashboard", "/admin/products", "/admin/staff", "/admin/settings/database",
        "/admin/reports/profit", "/admin/audit",
    ):
        assert client.get(path).status_code == 403, path


def test_cashier_cannot_call_admin_api(client, cashier):
    login(client, "john", "Rice#Grill9")
    response = client.post(
        "/admin/products/1/price",
        data={"selling_price": "1.00", "csrf_token": csrf_for(client)},
        follow_redirects=False,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Staff lifecycle
# ---------------------------------------------------------------------------
def test_admin_creates_cashier_account(app, admin):
    from app.staff.service import create_staff

    with app.app_context():
        created = create_staff(admin, {
            "full_name": "Maryam",
            "username": "maryam",
            "password": "Chin#Chin55",
            "role": ROLE_CASHIER,
            "is_active": "1",
        })
        assert created.role_name == ROLE_CASHIER
        assert created.is_active is True
        assert created.check_password("Chin#Chin55")


def test_new_cashier_can_immediately_log_in(app, client, admin):
    from app.staff.service import create_staff

    with app.app_context():
        create_staff(admin, {
            "full_name": "Peter", "username": "peter",
            "password": "Pounded#Yam7", "role": ROLE_CASHIER,
        })

    response = login(client, "peter", "Pounded#Yam7")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/pos")


def test_duplicate_username_is_rejected(app, admin):
    from app.staff.service import create_staff
    from app.utils.errors import ValidationError

    with app.app_context():
        create_staff(admin, {"full_name": "A", "username": "dupe", "password": "Aa#1234567", "role": ROLE_CASHIER})
        try:
            create_staff(admin, {"full_name": "B", "username": "dupe", "password": "Bb#1234567", "role": ROLE_CASHIER})
            assert False, "duplicate username should be rejected"
        except ValidationError as exc:
            assert "already taken" in exc.message


def test_deactivated_cashier_keeps_historical_sales(app, admin, cashier):
    from app.database.models import Sale, SaleItem
    from app.sales.service import complete_sale
    from app.staff.service import set_active

    with app.app_context():
        john = User.query.filter_by(username="john").first()
        complete_sale(
            user=john,
            cart_payload=[{"product_id": Product_id(app), "quantity": 1}],
            payment_method="CASH",
            amount_received="10000",
        )
        assert Sale.query.count() == 1

        set_active(admin, john, False)
        assert john.is_active is False

        # History must survive the deactivation.
        assert Sale.query.count() == 1
        sale = Sale.query.first()
        assert sale.cashier_name == "John Doe"
        assert sale.cashier_id == john.id


def Product_id(app):
    from app.database.models import Product

    return Product.query.first().id


def test_account_with_history_cannot_be_deleted(app, admin, cashier):
    from app.sales.service import complete_sale
    from app.staff.service import can_delete, delete_staff
    from app.utils.errors import ValidationError

    with app.app_context():
        john = User.query.filter_by(username="john").first()
        complete_sale(
            user=john,
            cart_payload=[{"product_id": Product_id(app), "quantity": 1}],
            payment_method="CASH",
            amount_received="10000",
        )
        allowed, reason = can_delete(john)
        assert allowed is False
        assert "deactivate" in reason.lower()
        try:
            delete_staff(admin, john)
            assert False, "delete should be refused"
        except ValidationError:
            pass


def test_unused_account_can_be_deleted(app, admin):
    from app.staff.service import can_delete, create_staff, delete_staff

    with app.app_context():
        temp_user = create_staff(admin, {
            "full_name": "Temp", "username": "tempstaff",
            "password": "Zz#12345678", "role": ROLE_CASHIER,
        })
        allowed, _ = can_delete(temp_user)
        assert allowed is True
        delete_staff(admin, temp_user)
        assert User.query.filter_by(username="tempstaff").first() is None


def test_last_admin_cannot_be_deactivated(app, admin):
    from app.staff.service import set_active
    from app.utils.errors import ValidationError

    with app.app_context():
        owner = User.query.filter_by(username="owner").first()
        try:
            set_active(admin, owner, False)
            assert False, "the only admin must not be deactivated"
        except ValidationError as exc:
            assert "administrator" in exc.message.lower()


def test_password_reset_works_and_is_audited_without_the_password(app, admin, cashier):
    from app.audit.service import SENSITIVE_KEYS
    from app.database.models import AuditLog
    from app.staff.service import reset_password

    with app.app_context():
        john = User.query.filter_by(username="john").first()
        reset_password(admin, john, "Fresh#Start8")
        assert john.check_password("Fresh#Start8")

        entries = AuditLog.query.filter(AuditLog.action == "PASSWORD_RESET").all()
        assert entries, "password reset must be audited"
        for entry in entries:
            blob = f"{entry.old_value or ''} {entry.new_value or ''} {entry.description or ''}"
            assert "Fresh#Start8" not in blob, "plaintext password must never be stored"


def test_permission_catalogue_is_seeded(app):
    from app.database.models import Permission

    with app.app_context():
        keys = {p.key for p in Permission.query.all()}
        assert "pos.use" in keys
        assert "reports.profit" in keys
        assert "settings.database" in keys


def test_roles_are_seeded(app):
    with app.app_context():
        for name in (ROLE_ADMIN, ROLE_MANAGER, ROLE_CASHIER):
            assert Role.query.filter_by(name=name).first() is not None
