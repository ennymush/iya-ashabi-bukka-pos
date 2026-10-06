"""End-to-end smoke test of the first-run setup wizard and public pages.

Renders every setup step (all 8 template branches) and the public screens
against a throwaway database, then reports any failure with its traceback.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

workdir = Path(tempfile.mkdtemp(prefix="iab-smoke-"))
os.environ["IAB_DATA_DIR"] = str(workdir)
os.environ["APP_ENV"] = "development"

from app import create_app  # noqa: E402
from app.database import seed as seed_module  # noqa: E402
from app.extensions import db  # noqa: E402

app = create_app("development")

with app.app_context():
    from app.database import models  # noqa: F401
    from app.database.models import Terminal

    db.create_all()
    seed_module.seed_permissions()
    seed_module.seed_settings()
    seed_module.seed_categories()
    db.session.add(Terminal(name="Main POS Terminal", code="TILL-01", is_active=True))
    db.session.commit()

failures: list[str] = []
checks: list[tuple[str, str, int]] = []


def check(label: str, path: str, expected: int) -> None:
    with app.test_client() as client:
        response = client.get(path, follow_redirects=False)
        checks.append((label, path, response.status_code))
        if response.status_code != expected:
            body = response.get_data(as_text=True)
            failures.append(
                f"{label}: GET {path} -> {response.status_code}, expected {expected}\n"
                f"    {body[:400].strip()}"
            )


# Public / first-run pages.
check("root redirects to setup", "/", 302)
check("login page", "/login", 200)
check("customer display", "/customer-display", 200)

# Every wizard step - this is what exercises setup/step.html end to end.
for step in range(1, 9):
    check(f"setup step {step}", f"/setup/step/{step}", 200)

# Out-of-range step must bounce back to the wizard entry point.
check("setup step 99 redirects", "/setup/step/99", 302)

# Static assets and error page.
check("app.css", "/static/css/app.css", 200)
check("pos.css", "/static/css/pos.css", 200)
check("customer.css", "/static/css/customer.css", 200)
check("favicon", "/static/images/favicon.svg", 200)
check("pos.js bundle", "/static/js/pos-init.js", 200)
check("404 page", "/no-such-page", 404)

# The root route must point at the wizard while no account exists.
with app.test_client() as client:
    response = client.get("/", follow_redirects=False)
    location = response.headers.get("Location", "")
    if "/setup" not in location:
        failures.append(f"root did not redirect to /setup (got {location!r})")


# ---------------------------------------------------------------------------
# Walk the whole wizard by POSTing each step, exactly as the browser would.
# ---------------------------------------------------------------------------
WIZARD_POSTS: list[tuple[int, dict, int, str]] = [
    (1, {"name": "Iya Ashabi Bukka Limited", "tagline": "Good Food", "address_line1": "12 Market Road",
         "address_city": "Ibadan", "phone": "08012345678", "receipt_footer": "Thank you!",
         "promo_message": "Try our jollof"}, 302, "/setup/step/2"),
    (2, {"full_name": "Iya Ashabi", "username": "owner", "password": "Bukka#Pass2026",
         "confirm_password": "Bukka#Pass2026", "phone": "08012345678"}, 302, "/setup/step/3"),
    (3, {"printer_mode": "mock", "paper_width_mm": "80"}, 302, "/setup/step/4"),
    (4, {"promotional_message": "Welcome", "display_mode": "browser", "display_position": "2"}, 302, "/setup/step/5"),
    (5, {"drawer_mode": "mock", "drawer_pin": "2", "drawer_pulse_ms": "120"}, 302, "/setup/step/6"),
    (6, {"full_name": "John Cashier", "username": "john", "password": "Cashier#2026",
         "confirm_password": "Cashier#2026", "max_discount_percent": "5",
         "max_discount_amount": "500"}, 302, "/setup/step/7"),
    (7, {}, 302, "/setup/step/8"),          # no device chosen -> just continue
    (8, {}, 302, "/login"),                 # FINISH SETUP -> login screen
]

CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')

with app.test_client() as client:
    for number, payload, expected, expected_location in WIZARD_POSTS:
        # Fetch the form first and replay its CSRF token, exactly like a browser.
        page = client.get(f"/setup/step/{number}", follow_redirects=False)
        match = CSRF_RE.search(page.get_data(as_text=True))
        if match is None:
            failures.append(f"setup step {number}: form has no csrf_token field")
            continue
        response = client.post(
            f"/setup/step/{number}",
            data={**payload, "csrf_token": match.group(1)},
            follow_redirects=False,
        )
        location = response.headers.get("Location", "")
        if response.status_code != expected or expected_location not in location:
            body = response.get_data(as_text=True)
            failures.append(
                f"POST setup step {number} -> {response.status_code} {location!r}, "
                f"expected {expected} containing {expected_location!r}\n    {body[:300].strip()}"
            )
        checks.append((f"POST setup step {number}", f"/setup/step/{number}", response.status_code))

    # The recovery code page must be reachable during the same session.
    recovery = client.get("/setup/recovery-code", follow_redirects=False)
    checks.append(("recovery code page", "/setup/recovery-code", recovery.status_code))
    if recovery.status_code != 200:
        failures.append(f"recovery code page -> {recovery.status_code}, expected 200")
    elif "IYA ASHABI BUKKA" not in recovery.get_data(as_text=True).upper():
        failures.append("recovery code page did not show the code heading")

    # After finishing, the wizard is closed and the login page is served.
    after = client.get("/setup/step/1", follow_redirects=False)
    if "/login" not in after.headers.get("Location", ""):
        failures.append("setup step 1 still reachable after completion")

# Verify the created accounts and the completed flag landed in the database.
with app.app_context():
    from app.database import service_settings as settings_service
    from app.database.models import ROLE_ADMIN, ROLE_CASHIER, User

    admin = User.query.filter_by(username="owner").first()
    cashier = User.query.filter_by(username="john").first()
    if admin is None or admin.role_name != ROLE_ADMIN:
        failures.append("administrator account was not created by the wizard")
    elif not admin.is_admin:
        failures.append("administrator account is not recognised as an admin")
    elif not admin.check_password("Bukka#Pass2026"):
        failures.append("administrator password was not stored correctly")
    if cashier is None:
        failures.append("cashier account was not created by the wizard")
    elif cashier.role_name != ROLE_CASHIER:
        failures.append("cashier account was created with the wrong role")
    elif not cashier.check_password("Cashier#2026"):
        failures.append("cashier password was not stored correctly")
    if not settings_service.setup_completed():
        failures.append("setup_completed flag was not set by the wizard")
    if not settings_service.get("system.recovery_code_hash"):
        failures.append("recovery code hash was not stored during setup")

print(f"smoke checks run: {len(checks)}")
for label, path, status in checks:
    print(f"  {status}  {label:<26} {path}")

if failures:
    print(f"\n{len(failures)} FAILURE(S)")
    for item in failures:
        print("  " + item)
else:
    print("\nAll smoke checks passed.")

shutil.rmtree(workdir, ignore_errors=True)
print(f"\nRESULT: {'FAIL' if failures else 'OK'}")
sys.exit(1 if failures else 0)
