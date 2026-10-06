"""Permission catalogue.

Access control is defined here as data, not scattered through the code.
Every protected route is gated with one of these keys on the SERVER.
"""
from __future__ import annotations

# key -> (group, human description shown in Admin > Staff > Permissions)
PERMISSIONS: dict[str, tuple[str, str]] = {
    # Sales
    "pos.use": ("Sales", "Open and use the cashier POS screen"),
    "sales.view": ("Sales", "View transactions and sales history"),
    "sales.view_own": ("Sales", "View only their own transactions"),
    "sales.void": ("Sales", "Void a completed transaction"),
    "sales.refund": ("Sales", "Approve and process refunds / returns"),
    "sales.discount": ("Sales", "Apply discounts within their limit"),
    "sales.discount_override": ("Sales", "Apply or override any discount"),
    "sales.hold": ("Sales", "Hold and retrieve parked sales"),
    "sales.print": ("Sales", "Print and reprint receipts"),
    "shifts.use": ("Sales", "Open and close their own cashier shift"),
    "shifts.manage": ("Sales", "Open, close and review all cashier shifts"),
    # Catalog
    "products.view": ("Products", "View products and prices at the till"),
    "products.admin": ("Products", "Open the Admin product screens"),
    "products.manage": ("Products", "Create, edit and deactivate products"),
    "products.price": ("Products", "Change selling prices"),
    "products.categories": ("Products", "Manage product categories"),
    "products.cost": ("Products", "View cost price and margin"),
    # Inventory
    "inventory.view": ("Inventory", "View stock levels"),
    "inventory.manage": ("Inventory", "Adjust stock and set reorder levels"),
    # Staff
    "staff.view": ("Staff", "View staff accounts and cashier activity"),
    "staff.manage": ("Staff", "Create, edit, deactivate staff and reset passwords"),
    "staff.permissions": ("Staff", "Change what managers and cashiers may do"),
    # Reporting
    "reports.view": ("Reports", "View sales and operational reports"),
    "reports.export": ("Reports", "Export reports to Excel"),
    "reports.print": ("Reports", "Print reports"),
    "reports.profit": ("Reports", "View revenue, cost and profit figures"),
    # Administration
    "dashboard.view": ("Administration", "View the admin dashboard"),
    "audit.view": ("Administration", "View audit logs"),
    "settings.business": ("Administration", "Edit business information and logo"),
    "settings.hardware": ("Administration", "Configure printer, drawer and displays"),
    "settings.receipt": ("Administration", "Configure the receipt layout"),
    "settings.display": ("Administration", "Configure the customer display"),
    "settings.database": ("Administration", "Backup and restore the database"),
    "users.manage": ("Administration", "Full user administration"),
}

# Admin receives every permission, always.
ADMIN_PERMISSIONS = tuple(PERMISSIONS.keys())

# Default Manager access - configurable by Admin under Staff > Permissions.
MANAGER_PERMISSIONS = (
    "pos.use",
    "sales.view",
    "sales.void",
    "sales.refund",
    "sales.discount",
    "sales.discount_override",
    "sales.hold",
    "sales.print",
    "shifts.use",
    "shifts.manage",
    "products.view",
"products.admin",
    "inventory.view",
    "staff.view",
    "reports.view",
    "reports.export",
    "reports.print",
    "reports.profit",
    "dashboard.view",
)

# Default Cashier access - the till and nothing more.
CASHIER_PERMISSIONS = (
    "pos.use",
    "sales.view_own",
    "sales.discount",
    "sales.hold",
    "sales.print",
    "shifts.use",
    "products.view",
    "inventory.view",
)

ROLE_DEFAULT_PERMISSIONS = {
    "ADMIN": ADMIN_PERMISSIONS,
    "MANAGER": MANAGER_PERMISSIONS,
    "CASHIER": CASHIER_PERMISSIONS,
}

# Permissions that reveal commercially sensitive numbers. Cashiers never get these.
PROFIT_PERMISSIONS = ("products.cost", "reports.profit")
