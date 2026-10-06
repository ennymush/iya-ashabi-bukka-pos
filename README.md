<<<<<<< HEAD
# Iya Ashabi Bukka Limited — Point of Sale

A **local-first** point-of-sale system built for Iya Ashabi Bukka Limited
(Km 34, Lagos–Abeokuta Express Way, Alakuko, Lagos State).

It runs entirely on the shop's own Windows till. There is **no internet
requirement, no cloud account and no subscription**: every sale, product and
staff record lives in a single SQLite database on that machine, and the cashier
works through a normal browser window talking to a local server.

Flask + SQLite behind a browser interface, with the thermal receipt printer,
cash drawer, barcode scanner and second-screen customer display driven through
Windows.

---

## Contents

1. [What the system does](#what-the-system-does)
2. [Requirements](#requirements)
3. [Quick start (development)](#quick-start-development)
4. [First run: the setup wizard](#first-run-the-setup-wizard)
5. [Importing a price list](#importing-a-price-list)
6. [Day-to-day operation](#day-to-day-operation)
7. [Roles and security](#roles-and-security)
8. [Where the data lives](#where-the-data-lives)
9. [Configuration reference](#configuration-reference)
10. [Backups and restore](#backups-and-restore)
11. [Database migrations](#database-migrations)
12. [Command line reference](#command-line-reference)
13. [Testing and checks](#testing-and-checks)
14. [Packaging for a Windows till](#packaging-for-a-windows-till)
15. [Upgrading an existing till](#upgrading-an-existing-till)
16. [Project layout](#project-layout)
17. [Troubleshooting](#troubleshooting)
18. [Housekeeping](#housekeeping)

---

## What the system does

**Selling (cashier screen, `/pos`)**

* Touch/click product grid with category tabs, search and live cart.
* Barcode scanning (keyboard-wedge scanners supported out of the box).
* Quantities, per-line and whole-cart discounts with cashier limits, line notes.
* Hold a sale and pick it up again later, or clear the cart.
* Checkout with **cash, transfer, card or other**, including split payments;
  the change due is calculated and shown.
* Receipts printed on a 58 mm or 80 mm thermal printer, with reprint from the
  transaction history, plus welcome/promo messages on the header.
* Shift open/close with cash-up figures, so the owner can see who held the till.

**Managing (admin area, `/admin`)**

* **Dashboard** with live sales figures (`/admin/api/dashboard/live`).
* **Products** — create, edit, price changes with a full price history, activate
  or deactivate, product images, per-product stock and low-stock level.
* **Import Price List** — upload the menu as an **Excel, CSV, Word or PDF**
  file and the system sorts it out for you (see
  [Importing a price list](#importing-a-price-list)).
* **Categories** — including the nine defaults: Main Meals, Soups, Swallow,
  Protein, Drinks, Snacks, Desserts, Takeaway, Other.
* **Inventory** — stock list, manual adjustments with a reason, adjustment
  history, low-stock report.
* **Sales** — transaction list and detail, voids, refunds, returns register.
* **Staff** — create accounts, edit, reset passwords, deactivate, delete,
  and grant fine-grained permissions per role.
* **Reports** — sales, products, profit, payments, cashiers, inventory, shifts,
  voids, refunds and audit trail; each can be exported to Excel (`.xlsx`) or
  sent to the printer as a print view.
* **Settings** — business details and receipt wording, hardware setup, backup
  and restore, database tools, system preferences.
* **Audit log** — who changed what, when.

**Customer display (`/customer-display`)**

* A second window for the customer-facing screen showing the running totals,
  the welcome/promo messages and a thank-you screen, streamed live from the POS.

**Nothing in the interface needs the internet.** CSS, JavaScript, fonts and
images are all served from the local machine, which is why the till keeps
working during a network outage.

---

## Requirements

| Item | Requirement |
| --- | --- |
| Operating system | Windows 10 or 11 on the till. The code also runs on macOS/Linux for development, but receipt printing and the cash drawer need Windows. |
| Python | 3.10 or newer — **development only**; the packaged executable bundles its own Python. |
| Browser | Any current Edge, Chrome or Firefox. |
| Disk | About 200 MB for the program plus room for the database, logs and backups. |
| Optional hardware | 58 mm or 80 mm ESC/POS thermal printer, cash drawer on the printer's RJ11 port, keyboard-style barcode scanner, second monitor for the customer display. |

Everything the Python side needs is pinned in `requirements.txt`: Flask 3,
SQLAlchemy 2, Flask-Migrate/Alembic, Flask-Login, openpyxl (Excel exports),
Pillow, python-dotenv and pywin32 for Windows printing.

---

## Quick start (development)

```powershell
cd "C:\Users\Ennymush\Desktop\IYA ASHABI BUKKA"

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

Copy-Item .env.example .env   # optional - the built-in defaults work as-is
python run.py
```

`run.py` then:

1. creates the database and seeds roles, permissions, business settings and
   categories if they are missing,
2. takes the automatic startup backup of `pos.db`,
3. serves the POS on <http://127.0.0.1:5000>,
4. opens the operator screen in the browser — and the customer display as well
   when it is enabled (a development machine always gets both windows).

Because the default host is `127.0.0.1`, the till is **not** reachable from
other machines on the network. That is deliberate: a single till, isolated.

For command-line work (`flask db upgrade`, `flask backup`, …) point Flask at the
entry script first — either once per session:

```powershell
$env:FLASK_APP = "run.py"
```

or on every command with `flask --app run.py <command>`.

---

## First run: the setup wizard

There is no shipped `admin`/`admin` account, so the first launch redirects to
`/setup` and walks the owner through eight short steps:

| Step | Screen | What it collects |
| --- | --- | --- |
| 1 | Business Information | Trading name, tagline, address, phone, receipt footer, promo message |
| 2 | Create Admin Account | Owner's full name, username, password and phone — this is the administrator |
| 3 | Receipt Printer | Printer mode, Windows printer name and paper width (58 / 80 mm) |
| 4 | Customer Display | Whether the customer screen is used and on which monitor |
| 5 | Cash Drawer | Drawer mode and the printer pulse that kicks it open |
| 6 | Create First Cashier | The first cashier account plus that cashier's discount ceiling |
| 7 | Test Hardware | Fires a test receipt, drawer pulse and display update |
| 8 | Finish | Confirms the setup and offers the offline recovery code |

On the last step a **recovery code** (`IAB-XXXX-XXXX-XXXX`) is generated. It is
displayed once and only its hash is stored, so it can unlock the administrator
account at `/forgot-password` if the owner forgets the password. Print it or
write it down; it cannot be shown again.

Finishing the wizard marks the installation as set up, and `/` then takes the
cashier straight to the POS. While setup is still pending, every page redirects
to the wizard.

> Hardware can be configured later under **Admin → Settings → Hardware**, so the
> wizard can be completed with mock devices and the real printer added when it
> is plugged in. Set `hardware.printer_mode` (and friends) to `mock` to keep the
> till usable without hardware.

---

## Importing a price list

When the kitchen changes prices, or the menu grows, editing products one at a
time is slow. **Admin → Products → IMPORT PRICE LIST** takes a file instead.

**How it works**

1. Choose a file. Nothing is saved yet.
2. The system reads it and shows a **preview** of exactly what will happen to
   every single row — new product, price increase, price decrease, no change,
   or a problem it could not use.
3. Tick the rows you want and press **IMPORT**. Only then is anything written.

**Laying out your file**

The easiest file is a spreadsheet whose first row holds the headings:

| Product Name | Category | Selling Price | Stock |
| --- | --- | --- | --- |
| Jollof Rice | Main Meals | 2,500 | 20 |
| Zobo | Drinks | 1,000 | 50 |

Only **Product Name** and **Selling Price** are required. Headings are matched
forgivingly, so `Item`, `Product`, `Dish`, `Amount`, `Unit Price`, `Rate`,
`Qty` and similar all work, and a title or blank line above the table is
ignored. Prices may be written as `2500`, `2,500.00`, `₦2,500` or `NGN 2500`.

A Word or PDF file does not need to be a table at all — a plain list works,
one product per line:

```
Jollof Rice ......... 2500
Puff Puff - 500
Coke 350ml .......... 700
```

> Word files must be `.docx`. If you have an older `.doc`, open it in Word and
> choose **Save As → Word Document (.docx)** first.

**How your products are matched**

Existing products are matched by **SKU**, then by **barcode**, then by name
(ignoring case and spacing). This is what makes re-uploading an updated price
list safe: it changes the prices you already have instead of creating a second
Jollof Rice.

**How they are categorised**

1. If the file has a **Category** column, that wins. An existing category of
   the same name is reused, and a category the shop does not have yet is
   created for you (the preview lists them first).
2. If there is no category column, the product name is matched against the
   shop's own categories — *Jollof Rice* goes to **Main Meals**, *Zobo* to
   **Drinks**, *Amala and Eba* to **Swallow**, *Suya* to **Snacks**, and so on.
3. Anything unrecognised goes to **Other**, so no product is ever lost.

**Safeguards**

* **Price decreases are not applied unless you ask.** They are shown separately
  and stay unticked until you tick "Also apply the price decreases". Customers
  who already paid the old price may query it, so this is worth a glance.
* Every change is written through the ordinary product service, so each one
  gets a **price history** entry and an **audit log** entry — a bulk import is
  traceable exactly like a manual price change.
* Rows with no name, no price, a price of zero, or a name listed twice in the
  same file are reported as problems and skipped; one bad line never stops the
  rest of the import.
* Nothing is written until you confirm, and only the rows you ticked are used.
* Maximum 2,000 rows per file, and the usual 8 MB upload limit applies.

---

## Day-to-day operation

**Opening the till.** Double-click the shortcut (or run `python run.py` during
development). The operator screen opens on `/`. If the browser is closed by
accident, reopen it and browse to <http://127.0.0.1:5000> — the server keeps
running in the background.

**Starting a sale.** Sign in, pick a product or scan a barcode, adjust
quantities, apply a discount if permitted, then take payment. Cash, transfer,
card and other payments can be mixed on one sale, and the change due is shown
before the receipt prints.

**Holding a sale.** If a customer goes back for a forgotten item, hold the
current cart and serve the next person; held carts are listed on the POS screen
and can be pulled back at any time.

**End of shift.** Close the shift from the POS screen to record cash-up figures.
Voids and refunds are recorded against the original receipt for the audit trail
rather than deleted, so the reports stay meaningful.

**Owner's checks.** The admin dashboard shows live takings. Reports → Sales /
Payments / Profit answer "what did we take, by what method, and what did it
cost" for any date range, and every report exports to Excel for the safe or an
accountant.

---

## Roles and security

Three roles ship with the system:

| Role | Typical use |
| --- | --- |
| `ADMIN` | The owner. Holds every permission implicitly, including settings, staff and database tools. |
| `MANAGER` | Everyday supervision: products, inventory, reports, staff listing. |
| `CASHIER` | Selling only: the POS screen, their own transactions, opening and closing shifts. |

Permissions are stored in the `permissions` table and attached to roles, so the
owner can grant or withdraw individual capabilities under **Admin → Staff →
Permissions** without touching code. The person who did what, and when, is kept
in the audit log.

Security features that are on by default:

* **No default credentials.** The administrator account is created by the owner
  during the setup wizard, so a fresh install cannot be logged into by a
  stranger.
* **Salted password hashing.** Only hashes are stored — never the passwords
  themselves. Passwords must be at least 6 characters, are rejected if they are
  an obvious guess (`password`, `123456`, `admin`, `pos`, `changeme`) and must
  not contain the username.
* **CSRF protection** on every state-changing form and API call.
* **Brute-force lockout.** After `LOGIN_MAX_ATTEMPTS` (default 5) failed logins
  within `LOGIN_LOCKOUT_MINUTES` (default 15), that account is locked out.
* **Idle session timeout.** Sessions expire after `SESSION_TIMEOUT_MINUTES`
  (default 30) of inactivity; cookies are `HttpOnly` and `SameSite=Lax`.
* **Offline recovery.** A one-time recovery code (shown by the wizard, or
  regenerated with `flask generate-recovery-code`) unlocks the administrator
  account without email or internet.
* **Localhost only.** `APP_HOST` defaults to `127.0.0.1`; nothing is exposed to
  the shop's Wi-Fi unless the owner deliberately changes it.

---

## Where the data lives

Program code and business data are kept apart on purpose, so replacing the
program folder never touches a single sale.

| What | Where |
| --- | --- |
| Program code | The project folder, or the `dist\IyaAshabiBukkaPOS` folder of a packaged build |
| Database | `%PROGRAMDATA%\IyaAshabiBukkaPOS\pos.db` (SQLite, WAL journalling) |
| Logs | `%PROGRAMDATA%\IyaAshabiBukkaPOS\logs\` — `application.log`, `error.log`, `hardware.log` |
| Backups | `%PROGRAMDATA%\IyaAshabiBukkaPOS\backups\Iya_Ashabi_Bukka_Backup_<date>_<time>.db` |
| Product images / logos | `%PROGRAMDATA%\IyaAshabiBukkaPOS\uploads\` |
| Excel exports | `%PROGRAMDATA%\IyaAshabiBukkaPOS\exports\` |
| Session signing key | `%PROGRAMDATA%\IyaAshabiBukkaPOS\secret.key`, generated on first run unless `SECRET_KEY` is set |

On macOS and Linux the same tree lives in `~/.iya_ashabi_bukka_pos`, which is
what the automated tests use. Setting `IAB_DATA_DIR` to another folder moves the
whole tree — handy for a demo copy or a second set of books.

**To copy the shop's books to another machine**, copy the whole
`%PROGRAMDATA%\IyaAshabiBukkaPOS` folder while the POS is closed. The database,
images and settings all come across together.

---

## Configuration reference

Copy `.env.example` to `.env` to override anything. Values already in the
Windows environment take precedence over the file, and **anything left unset
falls back to a safe default** — the POS starts with no `.env` at all.

| Variable | Default | Purpose |
| --- | --- | --- |
| `IAB_DATA_DIR` | `%PROGRAMDATA%\IyaAshabiBukkaPOS` | Moves the whole data tree (database, logs, backups, uploads). |
| `SECRET_KEY` | auto-generated `secret.key` | Session signing key. Set a long random value to pin it, e.g. `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `APP_ENV` | `development` | `development`, `production` or `testing`. Use `production` on the till. |
| `DEBUG` | `False` | Must stay `False` on the till. |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `5000` | Where the local server listens. Changing the host exposes the till to the network. |
| `DATABASE_PATH` | `%PROGRAMDATA%\IyaAshabiBukkaPOS\pos.db` | Override the database location (an absolute path is safest). |
| `PRINTER_NAME` | *(empty)* | Windows printer name for receipts. |
| `PRINTER_WIDTH_MM` | `80` | `58` or `80`. |
| `MOCK_HARDWARE` | `False` | `True` simulates printer, drawer, display and scanner — used for training and development. |
| `CUSTOMER_DISPLAY_ENABLED` | `True` | Whether the customer screen is used. |
| `AUTO_BACKUP_ENABLED` | `True` | Take a backup of `pos.db` at every launch. |
| `BACKUP_DIRECTORY` | `%PROGRAMDATA%\IyaAshabiBukkaPOS\backups` | Where backups go. Point this at a second drive if one is fitted. |
| `BACKUP_RETENTION_COUNT` | `30` | Number of backup files kept; the oldest are pruned. |
| `SESSION_TIMEOUT_MINUTES` | `30` | Idle time before a cashier is signed out. |
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed logins allowed before lockout. |
| `LOGIN_LOCKOUT_MINUTES` | `15` | Lockout duration. |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` only if the POS is ever served over HTTPS. |

Business details, receipt wording, hardware modes, tax and discount limits are
**not** environment variables — they live in the database and are edited under
**Admin → Settings** (business, receipt, display, hardware and system pages).
That means the owner can change the phone number on a receipt without touching
a config file.

---

## Backups and restore

The database is small enough to copy, and copying a file is exactly what the
system does.

**Automatic.** On every launch the POS takes an integrity-checked backup of
`pos.db` into the backups folder, then prunes old copies down to
`BACKUP_RETENTION_COUNT`. Turn it off with `AUTO_BACKUP_ENABLED=False`.

**Manual, from the till.** **Admin → Settings → Database** creates a backup on
demand, lists existing backups with sizes and dates, and deletes old ones.

**Manual, from a terminal.**

```powershell
flask --app run.py backup        # write a backup now
flask --app run.py check-db      # run PRAGMA integrity_check on the live database
```

**Restoring.** Use **Admin → Settings → Database → Restore** and pick a backup
file. The current database is kept aside first (as a `.previous` file), so a
restore that turns out to be the wrong choice can itself be undone.

> Keep a copy off the machine. A backup sitting next to the database it protects
> survives a mistake, not a theft or a failed drive — copying the backups folder
> to a USB stick or a second drive weekly is the single most valuable habit in
> this document.

---

## Database migrations

The schema is versioned with Alembic, driven through Flask-Migrate. The initial
migration in `migrations/versions/` matches the current models exactly.

```powershell
$env:FLASK_APP = "run.py"

flask db current                 # which revision the database is on
flask db migrate -m "add loyalty points"   # autogenerate after changing a model
flask db upgrade                 # apply pending migrations
flask db downgrade -1            # step back one revision
```

Two things worth knowing:

1. **Review every autogenerated migration before running it.** SQLite cannot
   alter a column in place, so Flask-Migrate is configured with
   `render_as_batch=True` and rewrites table copies correctly — but the
   autogenerator still cannot guess data migrations, so add those by hand.
2. **Autogenerated files need two imports.** New migration scripts must keep
   `import app.database.types` and rely on the `render_item` hook in
   `migrations/env.py`; without them the custom `UTCDateTime` and `Money` types
   are not rendered and `flask db upgrade` fails with a `NameError`.

For a brand-new installation the application creates any missing tables on
startup, so migrations matter when an **existing** database has to move forward
— see [Upgrading an existing till](#upgrading-an-existing-till).

---

## Command line reference

Flask CLI commands are registered in `app/cli.py`. Day-to-day work never needs
them; they exist for installation, recovery and maintenance.

| Command | What it does |
| --- | --- |
| `flask init-db` | Create the tables and seed roles, permissions, business settings, categories and the default terminal. |
| `flask seed [--demo/--no-demo]` | The above, plus 18 sample Nigerian menu items (Jollof Rice, Egusi Soup, …) so a new install is not an empty grid. Demo products are ordinary records — rename or deactivate them. |
| `flask create-admin` | Create an administrator from the terminal, prompting for username, full name and password. |
| `flask backup` | Take a database backup immediately. |
| `flask check-db` | Verify the database is not corrupt (exits non-zero when it is). |
| `flask generate-recovery-code` | Generate a fresh admin recovery code and print it **once**. |
| `flask show-recovery-hash` | Report whether a recovery code has ever been configured. |
| `flask db upgrade` | Apply pending schema migrations. |

---

## Testing and checks

The project ships 143 automated tests plus two audit scripts. All of them run
offline and use a throwaway database under a temporary folder, so they never
touch the shop's real books.

```powershell
python -m pytest                     # 143 tests: auth, POS flows, sales, importing
python scripts\check_templates.py    # template syntax + url_for audit
python scripts\smoke_test.py         # page-by-page and full wizard walk-through
```

| Check | What it proves |
| --- | --- |
| `python -m pytest` | 143 tests across `tests/test_auth.py` (30), `tests/test_pos_flows.py` (38), `tests/test_sales.py` (31) and `tests/test_importer.py` (44): sign-in, lockout, password policy, cart maths, discounts with cashier limits, split payments, change, held sales, shifts, voids, refunds, receipts, reports, and the whole price-list importer. |
| `scripts\check_templates.py` | All templates parse under Jinja and every `url_for` endpoint they reference exists on the app — catches typo'd endpoints and broken `{% include %}`s without starting a server. |
| `scripts\smoke_test.py` | 27 live HTTP checks against a real (temporary) database: every public page, all 8 wizard steps POSTed with a genuine CSRF token, the recovery-code page, the post-completion redirect, and database assertions that the admin account, the cashier account, the recovery hash and the "setup complete" flag were all written correctly. |

`scripts\smoke_test.py` is the closest thing to a fresh install: it drives the
wizard exactly as a browser would, so it catches the problems that only appear
on a brand-new till.

---

## Packaging for a Windows till

`IyaAshabiBukkaPOS.spec` (PyInstaller) turns the project into a folder that runs
on a Windows machine with **no Python installed**.

```powershell
pwsh -File scripts\installer\build_windows.ps1          # build
pwsh -File scripts\installer\build_windows.ps1 -Zip     # build + zip for the till
```

The script checks the toolchain, builds through the spec, and then verifies that
the executable **and** the bundled templates and static assets are present —
the one failure mode that is easy to miss, because a bundle without templates
starts happily and then errors on every page.

Or run PyInstaller directly:

```powershell
python -m pip install -r requirements.txt
pyinstaller --clean --noconfirm IyaAshabiBukkaPOS.spec
```

Result: `dist\IyaAshabiBukkaPOS\IyaAshabiBukkaPOS.exe`.

| Build choice | Why |
| --- | --- |
| One folder, not one file | Starts faster, and nothing is unpacked into `%TEMP%` on every launch — a pattern antivirus tools often flag. |
| Windowed (`console=False`) | The operator sees the browser, not a black command window; everything is written to the log files instead. |
| Data outside the bundle | The database, logs, backups and uploads stay in `%PROGRAMDATA%\IyaAshabiBukkaPOS`, so replacing the program folder on upgrade cannot destroy the books. |

**Installing on the till**

1. Copy the `dist\IyaAshabiBukkaPOS` folder to the till, e.g. `C:\IyaAshabiBukkaPOS`.
2. Right-click `IyaAshabiBukkaPOS.exe` → **Send to → Desktop (create shortcut)**, and rename it something an operator recognises.
3. Launch it once and complete the setup wizard. Windows may show a SmartScreen
   warning for an unsigned executable: **More info → Run anyway**, or sign the
   binary if the shop has a code-signing certificate.
4. Optional: set machine-level overrides (for example `BACKUP_DIRECTORY` to a USB
   drive) as **System environment variables**, or drop a `.env` file next to the
   executable — a packaged build reads `.env` from the folder that holds the
   `.exe`.
5. Put the shortcut in the startup folder so the till is ready when the machine
   is switched on.

> A packaged build must be rebuilt after any code change; the folder is a copy
> of the program, not a link to the source.

---

## Upgrading an existing till

1. **Back up first** — `flask --app run.py backup`, or copy the backups folder.
   Never skip this step; it is the difference between a two-minute rollback and
   a lost day of sales.
2. Close the POS completely (the browser window *and* the executable).
3. Replace the program folder with the new build, leaving
   `%PROGRAMDATA%\IyaAshabiBukkaPOS` untouched.
4. Start the POS. Missing tables are created automatically.
5. If the release changed the schema, move the database forward from a source
   checkout of the new version:

   ```powershell
   $env:FLASK_APP = "run.py"
   flask db upgrade
   ```

6. Sign in and confirm that yesterday's sales and the product list are intact.

Because business data never lives inside the program folder, step 3 is the whole
upgrade — the risky part is only ever the schema step, which is why the backup
in step 1 comes first.

---

## Project layout

```
IYA ASHABI BUKKA/
├─ run.py                      entry point: seeds the database, serves the app, opens the browser
├─ IyaAshabiBukkaPOS.spec      PyInstaller build definition
├─ requirements.txt            pinned dependencies
├─ .env.example                every supported environment variable, with notes
├─ pytest.ini                  test configuration
│
├─ app/                        the application
│  ├─ __init__.py              application factory, blueprint wiring, error pages
│  ├─ cli.py                   flask init-db / seed / create-admin / backup / recovery commands
│  ├─ auth/                    sign-in, password change, offline admin recovery
│  ├─ setup/                   the 8-step first-run wizard
│  ├─ pos/                     till screen, cart and checkout API, shifts, receipts
│  ├─ products/                catalogue, categories, images, price history, bulk import
│  ├─ inventory/               stock levels, adjustments, low-stock report
│  ├─ sales/                   transactions, voids, refunds, returns
│  ├─ staff/                   accounts, roles, permissions, profile pages
│  ├─ reports/                 ten reports, Excel export, print views
│  ├─ admin/                   dashboard, settings pages, hardware status, audit log view
│  ├─ customer/                customer display screen and its live stream
│  ├─ audit/                   audit-trail recording
│  ├─ database/                models/, seeding, default data, backups, custom column types
│  ├─ hardware/                printer, ESC/POS encoding, cash drawer, scanner, monitor detection
│  ├─ utils/                   security (hashing, CSRF, lockout), logging, money, errors, SQLite setup
│  ├─ templates/               53 Jinja templates: setup, pos, admin, auth, customer, errors
│  └─ static/                  css/ and js/ - all served locally, no CDN
│
├─ config/                     environment configuration and data-directory resolution
├─ migrations/                 Alembic revisions and the render_item hook
├─ scripts/
│  ├─ check_templates.py       template syntax and url_for audit
│  ├─ smoke_test.py            end-to-end setup-wizard smoke test
│  └─ installer/
│     └─ build_windows.ps1     Windows build, verification and packaging
└─ tests/                      99 automated tests with their fixtures
```

---

## Troubleshooting

**The browser opens but the page will not load.**
The server is not running. Start it again with the shortcut (or
`python run.py`) and check `logs\error.log` for the reason it stopped.

**"Address already in use" / port 5000 is taken.**
Another copy of the POS (or another program) already holds the port. Close the
other window, or start on a different port:

```powershell
$env:APP_PORT = "5001"
python run.py
```

**Receipts do not print.**
Check **Admin → Settings → Hardware**: the printer mode must be `windows` (or
`escpos`), the printer name must match exactly what Windows shows under
*Printers & scanners*, and the paper width must match the roll (58 or 80 mm).
**Admin → Settings → Hardware** has a **TEST** button for each device, so you can
fire a test receipt, drawer pulse or display update and prove the printer works
without ringing up a sale. If the printer is shared over the network, print a
Windows test page first — that isolates the printer from the POS.

**The cash drawer does not open.**
Drawers almost always hang off the printer, so fix printing first. Then check
`hardware.drawer_mode` and the pulse pin (2 is standard).

**The customer display shows an old sale, or nothing.**
Reload `/customer-display`. For a second screen, confirm
`hardware.display_second_monitor` is on and that Windows itself is in
*Extend* mode, not *Duplicate* — a mirrored screen has only one desktop to
place the window on.

**Nobody can sign in, and the owner has forgotten the password.**
Use the recovery code at `/forgot-password`. If it is lost too, generate a new
one from a terminal on the till:

```powershell
flask --app run.py generate-recovery-code
```

and if no code has ever been configured, create a fresh administrator instead:

```powershell
flask --app run.py create-admin
```

**"Database is locked" or the POS hangs on a busy night.**
SQLite is running in WAL mode with a 15-second busy timeout, which comfortably
handles a single till. Locking messages almost always mean a second copy is
writing to the same file — check for another POS window, a stray `python run.py`
or a backup script pointed at `pos.db`.

**Windows blocks the executable.**
SmartScreen warns about any unsigned program: **More info → Run anyway**. If the
till runs antivirus software, add an exclusion for the POS folder so `pos.db` is
not scanned mid-write.

**Everything looks wrong after a restore.**
Restores keep the replaced database as a `.previous` file next to the live one;
**Admin → Settings → Database** can restore that too.

**Where do I look when something fails?**
`%PROGRAMDATA%\IyaAshabiBukkaPOS\logs\error.log` records the full traceback, and
`application.log` records the story leading up to it. Both are plain text and
safe to send to whoever maintains the system.

---

## Housekeeping

* **Daily** — nothing to do: the POS backs up on launch.
* **Weekly** — copy the backups folder to a USB stick or a second drive, and
  check **Reports → Sales** against the day's cash.
* **After adding staff** — grant exactly the permissions they need under
  **Admin → Staff → Permissions**, and deactivate accounts when people leave
  rather than deleting them, so their sales history stays attributable.
* **Before any upgrade** — take a backup and read
  [Upgrading an existing till](#upgrading-an-existing-till).

---

Maintained for **Iya Ashabi Bukka Limited** — Km 34, Lagos–Abeokuta Express Way,
Alakuko, Lagos State. Built to keep working when the internet, the network and
the cloud do not.





=======
# iya-ashabi-bukka-pos
>>>>>>> bdbc41345044081565c7525c2168de33f2e56c34
