"""Flask CLI commands for installation, seeding, backup and maintenance.

These support the installer and the documented manual procedures. Normal
business operation never needs them - the owner uses the Admin screens.
"""
from __future__ import annotations

import sys
from datetime import datetime

import click
from flask.cli import with_appcontext

from app.database import backup as backup_service
from app.database import seed as seed_module
from app.database.models import ROLE_ADMIN, Role, User
from app.extensions import db
from app.utils.db_setup import integrity_check
from app.utils.logging_config import get_logger
from app.utils.security import generate_recovery_code, hash_password

log = get_logger("cli")


def register_commands(app) -> None:
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(backup_command)
    app.cli.add_command(check_db_command)
    app.cli.add_command(recovery_code_command)
    app.cli.add_command(show_recovery_command)


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create the database tables (used by the installer)."""
    from app.database import models  # noqa: F401

    db.create_all()
    seed_module.seed_permissions()
    seed_module.seed_settings()
    seed_module.seed_categories()

    from app.database.models import Terminal

    if Terminal.query.count() == 0:
        db.session.add(Terminal(name="Main POS Terminal", code="TILL-01", is_active=True))
        db.session.commit()

    click.echo("Database initialised and seeded with business defaults.")


@click.command("seed")
@click.option("--demo/--no-demo", default=True, help="Include sample products for testing.")
@with_appcontext
def seed_command(demo: bool):
    """Seed roles, permissions, settings, categories and optional demo products."""
    from app.database import models  # noqa: F401

    db.create_all()
    seed_module.seed_permissions()
    seed_module.seed_settings()
    seed_module.seed_categories()
    created = seed_module.seed_demo_products(with_demo=demo)
    click.echo(f"Seeding complete. {created} demo product(s) added.")
    if created:
        click.echo("Demo products are ordinary editable records - rename or deactivate them any time.")


@click.command("create-admin")
@click.option("--username", prompt=True)
@click.option("--full-name", prompt="Full name")
@click.password_option(confirmation_prompt=True)
@with_appcontext
def create_admin_command(username: str, full_name: str, password: str):
    """Create an administrator account from the terminal."""
    from app.database import models  # noqa: F401

    db.create_all()
    seed_module.seed_permissions()

    username = username.strip().lower()
    if User.query.filter_by(username=username).first():
        click.echo(f"User '{username}' already exists.")
        return

    from app.staff.service import create_staff

    system = User(full_name="System")
    user = create_staff(system, {
        "full_name": full_name,
        "username": username,
        "password": password,
        "role": ROLE_ADMIN,
    })
    click.echo(f"Administrator '{user.full_name}' ({user.username}) created.")


@click.command("backup")
@with_appcontext
def backup_command():
    """Take a database backup right now."""
    try:
        entry = backup_service.create_backup()
        click.echo(f"Backup created: {entry.filename} ({entry.size_bytes} bytes)")
    except backup_service.BackupError as exc:
        click.echo(f"Backup failed: {exc}")
        sys.exit(1)


@click.command("check-db")
@with_appcontext
def check_db_command():
    """Verify database integrity."""
    ok, detail = backup_service.verify_active_database()
    click.echo(f"Database integrity: {detail}")
    if not ok:
        sys.exit(1)


@click.command("generate-recovery-code")
@with_appcontext
def recovery_code_command():
    """Generate a new offline password-recovery code (printed once)."""
    from app.database import service_settings as settings_service

    code = generate_recovery_code()
    settings_service.set_value("system.recovery_code_hash", hash_password(code))
    click.echo("=" * 60)
    click.echo("IYA ASHABI BUKKA POS - ADMIN RECOVERY CODE")
    click.echo(f"Code: {code}")
    click.echo("Store this in a safe place. It is shown only once and is not recoverable.")
    click.echo("=" * 60)


@click.command("show-recovery-hash")
@with_appcontext
def show_recovery_command():
    """Show whether a recovery code has been configured."""
    from app.database import service_settings as settings_service

    stored = settings_service.get("system.recovery_code_hash")
    click.echo("Recovery code configured." if stored else "No recovery code configured yet.")
