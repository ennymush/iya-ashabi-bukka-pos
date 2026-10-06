"""Shared pytest fixtures for the Iya Ashabi Bukka POS test suite."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Keep every test run inside a throwaway data directory.
os.environ.setdefault("MOCK_HARDWARE", "1")
os.environ.setdefault("IAB_DATA_DIR", tempfile.mkdtemp(prefix="iab-tests-"))
os.environ.setdefault("APP_ENV", "testing")


def _remove_test_database_files(application) -> None:
    """Delete the test database and its WAL/SHM sidecars between tests."""
    uri = application.config["SQLALCHEMY_DATABASE_URI"]
    path = Path(uri.replace("sqlite:///", "", 1))
    for suffix in ("", "-wal", "-shm"):
        leftover = Path(str(path) + suffix)
        if leftover.exists():
            leftover.unlink()
@pytest.fixture()
def app():
    from app import create_app
    from app.database import seed as seed_module
    from app.database.models import Terminal
    from app.extensions import db

    application = create_app("testing")
    from app.utils.security import login_throttle

    # The throttle is a process-wide singleton; clear it between tests.
    login_throttle._attempts.clear()

    with application.app_context():
        db.create_all()
        seed_module.seed_permissions()
        seed_module.seed_settings()
        seed_module.seed_categories()
        seed_module.seed_demo_products(with_demo=True)
        if Terminal.query.count() == 0:
            db.session.add(Terminal(name="Test Terminal", code="TILL-01", is_active=True))
            db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()
        # Close every pooled connection before deleting the files: Windows
        # refuses to unlink a file that SQLite still holds open.
        db.engine.dispose()
        _remove_test_database_files(application)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def ctx(app):
    return app.app_context()


@pytest.fixture()
def admin(app):
    """A real administrator created through the staff service."""
    from app.database.models import User
    from app.staff.service import create_staff

    system = User(full_name="System")
    return create_staff(system, {
            "full_name": "Owner Boss",
            "username": "owner",
            "password": "Bakery#2026x",
            "role": "ADMIN",
    })


@pytest.fixture()
def cashier(app):
    from app.database.models import User
    from app.staff.service import create_staff

    system = User(full_name="System")
    return create_staff(system, {
            "full_name": "John Doe",
            "username": "john",
            "password": "Rice#Grill9",
            "role": "CASHIER",
            "max_discount_percent": 10,
    })


@pytest.fixture()
def manager(app):
    from app.database.models import User
    from app.staff.service import create_staff

    system = User(full_name="System")
    return create_staff(system, {
            "full_name": "Mary Manager",
            "username": "mary",
            "password": "Stew#Point7",
            "role": "MANAGER",
    })


def csrf_for(client) -> str:
    """Fetch a valid CSRF token from a page render (the token lives in session)."""
    from app.utils.security import CSRF_SESSION_KEY

    with client.session_transaction() as session:
        token = session.get(CSRF_SESSION_KEY)
    if token:
        return token
    # The token is generated lazily by the template context processor.
    client.get("/login")
    with client.session_transaction() as session:
        return session.get(CSRF_SESSION_KEY, "")


def login(client, username: str, password: str):
    """Sign in through the real login form."""
    client.get("/login")
    token = csrf_for(client)
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
