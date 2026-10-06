"""Default business configuration.

These are the REAL Iya Ashabi Bukka details. They are written into the
``settings`` table on first run so the owner can change any of them later
from ADMIN > SETTINGS > BUSINESS INFORMATION - nothing is hardcoded in code.
"""
from __future__ import annotations

DEFAULT_BUSINESS = {
    "business.name": "Iya Ashabi Bukka Limited",
    "business.tagline": "Good Food \u2022 Great Vibes",
    "business.address_line1": "Km 34, Lagos-Abeokuta Express Way,",
    "business.address_line2": "Alakuko,",
    "business.address_city": "Lagos State, Nigeria",
    "business.country": "Nigeria",
    "business.email": "iyaashabibukka123@gmail.com",
    "business.phone": "08146171531",
    "business.phone_2": "07037315558",
    "business.currency_code": "NGN",
    "business.currency_symbol": "\u20a6",
    "business.logo_path": "",
    "business.receipt_footer": "Thank you for your patronage.",
    "business.receipt_welcome": "Good Food \u2022 Great Vibes",
    "business.promo_message": "Freshly cooked to order. Eat well, live well.",
    "business.promo_image": "",
    "business.tin": "",
}

DEFAULT_RECEIPT = {
    "receipt.width_mm": "80",
    "receipt.show_logo": "1",
    "receipt.show_address": "1",
    "receipt.show_email": "1",
    "receipt.show_tagline": "1",
    "receipt.footer_message": "Thank you for your patronage.",
    "receipt.show_item_names": "1",
    "receipt.copy_label": "",
    "receipt.paper_width": "32",  # printable characters for the chosen paper
    "receipt.cut_paper": "1",
    "receipt.open_drawer": "1",
    "receipt.print_logo_as_text": "1",
}

DEFAULT_DISPLAY = {
    "display.enabled": "1",
    "display.welcome_message": "Welcome to Iya Ashabi Bukka",
    "display.thankyou_message": "Thank you for your patronage!",
    "display.promotional_message": "Freshly cooked to order. Eat well, live well.",
    "display.show_totals": "1",
    "display.fullscreen": "1",
    "display.terminal_id": "",
    "display.show_date_time": "1",
    "display.logo_path": "",
}

DEFAULT_HARDWARE = {
    "hardware.printer_name": "",
    "hardware.printer_mode": "mock",  # mock | windows | escpos
    "hardware.paper_width_mm": "80",
    "hardware.drawer_mode": "mock",  # mock | printer | windows
    "hardware.drawer_pin": "2",
    "hardware.drawer_pulse_ms": "120",
    "hardware.scanner_mode": "keyboard",
    "hardware.scanner_prefix": "",
    "hardware.scanner_suffix": "",
    "hardware.scanner_beep": "1",
    "hardware.display_mode": "mock",  # mock | browser | window
    "hardware.display_second_monitor": "1",
    "hardware.display_position": "2",
}

DEFAULT_SYSTEM = {
    "system.setup_completed": "0",
    "system.auto_backup": "1",
    "system.backup_retention": "30",
    "system.allow_negative_stock": "0",
    "system.low_stock_alert": "1",
    "system.tax_enabled": "0",
    "system.default_tax_rate": "0",
    "system.session_timeout": "30",
    "system.receipt_footer": "Thank you for your patronage.",
    "system.allow_sale_without_shift": "0",
    "system.max_discount_percent_cashier": "10",
    "system.max_discount_amount_cashier": "0",
    "system.idle_customer_display": "1",
    "system.customer_display_idle_seconds": "60",
    "system.generated_by": "Iya Ashabi Bukka POS",
}

DEFAULT_SETTINGS: dict[str, tuple[str, str, str]] = {}
for _k, _v in DEFAULT_BUSINESS.items():
    DEFAULT_SETTINGS[_k] = (str(_v), "business", _k.replace("business.", "").replace("_", " ").title())
for _k, _v in DEFAULT_RECEIPT.items():
    DEFAULT_SETTINGS[_k] = (str(_v), "receipt", _k.replace("receipt.", "").replace("_", " ").title())
for _k, _v in DEFAULT_DISPLAY.items():
    DEFAULT_SETTINGS[_k] = (str(_v), "display", _k.replace("display.", "").replace("_", " ").title())
for _k, _v in DEFAULT_HARDWARE.items():
    DEFAULT_SETTINGS[_k] = (str(_v), "hardware", _k.replace("hardware.", "").replace("_", " ").title())
for _k, _v in DEFAULT_SYSTEM.items():
    DEFAULT_SETTINGS[_k] = (str(_v), "system", _k.replace("system.", "").replace("_", " ").title())

DEFAULT_CATEGORIES = [
    ("Main Meals", "Rice and pasta dishes", "#8B4513", 1),
    ("Soups", "Soups and stews", "#C0392B", 2),
    ("Swallow", "Eba, Pounded Yam, Amala", "#D68910", 3),
    ("Protein", "Chicken, Beef, Fish", "#7D6608", 4),
    ("Drinks", "Water, Soft Drinks, Juices", "#1E8449", 5),
    ("Snacks", "Small bites and sides", "#AF601A", 6),
    ("Desserts", "Sweets", "#B03A2E", 7),
    ("Takeaway", "Packaged items", "#2E86C1", 8),
    ("Other", "Everything else", "#566573", 9),
]

# DEMO / SAMPLE products - clearly marked as samples and fully editable.
# name, category, cost, price, stock, min, sku, unit
DEMO_PRODUCTS = [
    ("Jollof Rice", "Main Meals", "1800.00", "2500.00", 100, 10, "IAB-ME-001", "plate"),
    ("Fried Rice", "Main Meals", "1900.00", "2600.00", 80, 10, "IAB-ME-002", "plate"),
    ("White Rice", "Main Meals", "1500.00", "2200.00", 100, 10, "IAB-ME-003", "plate"),
    ("Pasta", "Main Meals", "1400.00", "2000.00", 60, 8, "IAB-ME-004", "plate"),
    ("Egusi Soup", "Soups", "1700.00", "2400.00", 50, 8, "IAB-SO-001", "plate"),
    ("Okra Soup", "Soups", "1600.00", "2300.00", 50, 8, "IAB-SO-002", "bowl"),
    ("Eba", "Swallow", "1200.00", "2000.00", 120, 15, "IAB-SW-001", "portion"),
    ("Pounded Yam", "Swallow", "1300.00", "2200.00", 100, 15, "IAB-SW-002", "portion"),
    ("Amala", "Swallow", "1200.00", "2000.00", 90, 15, "IAB-SW-003", "portion"),
    ("Fried Chicken (1/4)", "Protein", "1200.00", "2000.00", 60, 10, "IAB-PR-001", "piece"),
    ("Chicken", "Protein", "1000.00", "1800.00", 80, 10, "IAB-PR-002", "piece"),
    ("Beef", "Protein", "1300.00", "2200.00", 50, 10, "IAB-PR-003", "portion"),
    ("Grilled Fish", "Protein", "1400.00", "2400.00", 40, 8, "IAB-PR-004", "piece"),
    ("Moi Moi", "Snacks", "40.00", "80.00", 150, 20, "IAB-SN-001", "pack"),
    ("Bean Cake (Akara)", "Snacks", "30.00", "70.00", 150, 20, "IAB-SN-002", "pack"),
    ("Bottle Water", "Drinks", "150.00", "200.00", 200, 30, "IAB-DR-001", "bottle"),
    ("Soft Drink", "Drinks", "600.00", "1000.00", 80, 12, "IAB-DR-002", "bottle"),
    ("Water Sachet", "Drinks", "50.00", "100.00", 200, 30, "IAB-DR-003", "pack"),
]
