"""Bulk price/product list importer.

Reads a spreadsheet, CSV, Word or PDF price list and turns it into plain rows
of ``(name, price, category, ...)`` so the rest of the system never has to
care where the data came from.

Deliberately free of database access: parsing is pure and easy to test.

Formats
-------
``.xlsx`` / ``.xlsm``  openpyxl (already a project dependency)
``.csv``               stdlib csv
``.docx``              stdlib zipfile + XML - no third-party library needed
``.pdf``               optional ``pypdf``; see ``missing_dependency()``

Legacy ``.doc`` (the old binary Word format) is a different format entirely
and is not supported - the file simply needs saving as ``.docx``.

Nothing is ever executed from an uploaded file: these formats are parsed as
data, and the size is capped by the caller.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from decimal import Decimal

from app.utils.logging_config import get_logger

log = get_logger("importer")

# --------------------------------------------------------------------------
# Column aliases - how a real-world price list might label its columns
# --------------------------------------------------------------------------
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": (
        "name", "product", "product name", "item", "item name", "dish",
        "food", "description", "menu item", "product description", "itemdescription",
    ),
    "price": (
        "price", "selling price", "selling", "amount", "rate", "unit price",
        "selling_price", "srp", "retail price", "cost", "new price",
        "current price", "price naira",
    ),
    "category": (
        "category", "cat", "group", "section", "type", "menu section",
        "department", "classification", "category name",
    ),
    "sku": ("sku", "code", "item code", "product code", "stock code", "ref", "reference"),
    "barcode": ("barcode", "bar code", "ean", "upc", "ean13", "isbn"),
    "cost": (
        "cost price", "buying", "buying price", "purchase price",
        "cost_price", "costprice",
    ),
    "stock": (
        "stock", "qty", "quantity", "stock quantity", "count",
        "opening stock", "stock level", "on hand",
    ),
}

SUPPORTED_EXTENSIONS = {"xlsx", "xlsm", "csv", "docx", "pdf"}

# A cell holding a price: 2,500.00 / ₦2500 / 2500 / 12.50
_TRAILING_INDEX_RE = re.compile(r"^\s*(?:\(?\d+[\.\)]|[-*•])\s*")


class ImportError_(ValueError):
    """The uploaded file could not be understood."""


# --------------------------------------------------------------------------
# Optional dependency helpers
# --------------------------------------------------------------------------
def missing_dependency(extension: str) -> str | None:
    """Return the pip name of a missing library, or None when all is present."""
    if extension == "pdf":
        try:
            import pypdf  # noqa: F401
        except ImportError:
            return "pypdf"
    return None


# --------------------------------------------------------------------------
# Text normalisation
# --------------------------------------------------------------------------
def clean_text(value) -> str:
    """Collapse whitespace and normalise unicode punctuation."""
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def normalise_key(value) -> str:
    """Lowercase and strip punctuation: 'Selling Price (₦)' -> 'selling price naira'."""
    text = clean_text(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalise_name(value) -> str:
    """Name key used to recognise a product that already exists."""
    text = clean_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------
def parse_amount(value) -> Decimal | None:
    """Read a price from a cell that may be a number, '₦2,500.00' or 'NGN 2500'.

    Returns None when there is no number to read, so a row with a blank price is
    reported as a problem instead of silently becoming free.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except Exception:  # noqa: BLE001 - exotic cell types
            return None

    text = clean_text(value)
    if not text:
        return None

    text = text.replace("₦", " ")
    text = re.sub(r"\bNGN\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bnaira\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bN\b(?=\s*\d)", " ", text)

    # Keep only digits, separators and a leading minus.
    cleaned = re.sub(r"[^0-9.,\-]", "", text).strip()
    if not cleaned or cleaned in {"-", ".", ","}:
        return None

    # Decide which separator is the decimal mark: the LAST one, and only when it
    # is followed by exactly two digits. Everything else is a thousands mark.
    if "," in cleaned and "." in cleaned:
        decimal_sep = "." if cleaned.rfind(".") > cleaned.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        cleaned = cleaned.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in cleaned:
        tail = cleaned.rsplit(",", 1)[-1]
        cleaned = cleaned.replace(",", "") if len(tail) == 3 else cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")

    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Column mapping
# --------------------------------------------------------------------------
def _match_column(header: str) -> str | None:
    """Map one header cell onto a field name, or None if unrecognised."""
    key = normalise_key(header)
    if not key:
        return None
    for field, aliases in COLUMN_ALIASES.items():
        if key in aliases:
            return field
    # "selling price naira", "unit price (naira)" and similar decorated headers.
    for field, aliases in COLUMN_ALIASES.items():
        if any(alias in key for alias in aliases):
            return field
    return None


def detect_header(rows: list[list]) -> tuple[int, dict[str, int]] | None:
    """Find the header row and the column index for each field.

    Scans the first rows because real price lists often carry a title or a blank
    line above the table.
    """
    best: tuple[int, dict[str, int]] | None = None
    for index, row in enumerate(rows[:10]):
        mapping: dict[str, int] = {}
        for column, cell in enumerate(row):
            field = _match_column(cell)
            # First column wins, so a later "price" header cannot steal it.
            if field and field not in mapping:
                mapping[field] = column
        if "name" in mapping and "price" in mapping:
            if best is None or len(mapping) > len(best[1]):
                best = (index, mapping)
    return best


# --------------------------------------------------------------------------
# Row building
# --------------------------------------------------------------------------
def _parse_stock(value) -> int | None:
    """Stock quantity as a whole number, or None when absent/unusable."""
    amount = parse_amount(value)
    if amount is None:
        return None
    try:
        return int(amount)
    except (ValueError, OverflowError):
        return None


def _row_from_mapping(row: list, mapping: dict[str, int]) -> dict | None:
    """Pull one product out of a table row using the header mapping."""
    def cell(field: str):
        column = mapping.get(field)
        if column is None or column >= len(row):
            return None
        return row[column]

    name = clean_text(cell("name"))
    if not name:
        return None
    return {
        "name": name,
        "price": parse_amount(cell("price")),
        "cost": parse_amount(cell("cost")),
        "category": clean_text(cell("category")) or None,
        "sku": clean_text(cell("sku")) or None,
        "barcode": clean_text(cell("barcode")) or None,
        "stock": _parse_stock(cell("stock")),
    }


def _split_text_row(text: str) -> dict | None:
    """Pull a price out of a plain sentence such as 'Jollof Rice - 2500'.

    Only the tail of the line is treated as the price, so digits inside a
    product name ('Coke 350ml') stay part of the name.
    """
    line = _TRAILING_INDEX_RE.sub("", clean_text(text))
    if not line:
        return None

    match = re.search(
        r"^(?P<name>.*?)[\s\-–—|:·•]+(?P<price>[^\s\-–—|:·•]*\d[\d,.]*)\s*$", line
    )
    if match:
        name = clean_text(match.group("name")).rstrip(".·•-–— \t")
        price = parse_amount(match.group("price"))
        if name and price is not None:
            return {
                "name": name, "price": price, "cost": None, "category": None,
                "sku": None, "barcode": None, "stock": None,
            }
    return None




def rows_from_table(table: list[list]) -> list[dict]:
    """Turn a rectangular table into product rows, header detected or not."""
    table = [row for row in table if any(clean_text(cell) for cell in row)]
    if not table:
        return []

    header = detect_header(table)
    if header:
        start, mapping = header
        products = []
        for row in table[start + 1:]:
            built = _row_from_mapping(row, mapping)
            if built:
                products.append(built)
        return products

    # No recognisable header: assume the common "name then price" layout.
    products = []
    for row in table:
        cells = [clean_text(cell) for cell in row if clean_text(cell)]
        if not cells:
            continue
        built = None
        if len(cells) >= 2 and parse_amount(cells[-1]) is not None:
            built = {
                "name": " ".join(cells[:-1]),
                "price": parse_amount(cells[-1]),
                "cost": None,
                "category": None,
                "sku": None,
                "barcode": None,
                "stock": None,
            }
        elif len(cells) == 1:
            built = _split_text_row(cells[0])
        if built and built.get("name"):
            products.append(built)
    return products


# --------------------------------------------------------------------------
# Format readers - each returns a list of rows
# --------------------------------------------------------------------------
def _read_xlsx(data: bytes) -> list[list]:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        if sheet is None:
            return []
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _read_csv(data: bytes) -> list[list]:
    text = None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ImportError_("The CSV file could not be read as text.")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def _read_docx(data: bytes) -> list[list]:
    """Read a .docx using the standard library only.

    A .docx is a zip archive whose text lives in word/document.xml. Real price
    lists are almost always a table, so tables are read row by row, while plain
    paragraphs become single-cell rows for the loose-line parser.
    """
    from xml.etree import ElementTree

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if "word/document.xml" not in archive.namelist():
                raise ImportError_("That Word file does not look complete.")
            xml_bytes = archive.read("word/document.xml")
    except zipfile.BadZipFile:
        raise ImportError_(
            "That file is not a valid .docx document. If it is an older .doc "
            "file, open it in Word and save it as .docx first."
        )

    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ImportError_(f"The Word document could not be read ({exc}).")

    def cell_text(node) -> str:
        return clean_text("".join(t.text or "" for t in node.iter(f"{namespace}t")))

    rows: list[list] = []
    body = root.find(f"{namespace}body")
    for element in body if body is not None else []:
        if element.tag == f"{namespace}tbl":
            for row in element.findall(f"{namespace}tr"):
                cells = [cell_text(cell) for cell in row.findall(f"{namespace}tc")]
                if any(cells):
                    rows.append(cells)
        elif element.tag == f"{namespace}p":
            text = cell_text(element)
            if text:
                rows.append([text])
    return rows


def _read_pdf(data: bytes) -> list[list]:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError_(
            "Reading PDF files needs one extra library. Install it with: "
            "python -m pip install pypdf"
        )

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - pypdf raises a wide variety
        raise ImportError_(f"The PDF could not be opened ({exc}).")

    rows: list[list] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill the run
            log.warning("Skipping unreadable PDF page: %s", exc)
            continue
        for line in text.splitlines():
            if clean_text(line):
                rows.append([line])
    return rows



# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def read_products(filename: str, data: bytes) -> list[dict]:
    """Parse an uploaded price list into product rows.

    Raises ``ImportError_`` with a message meant for the owner when the file
    cannot be read at all.
    """
    name = filename or ""
    extension = name.rsplit(".", 1)[-1].lower().strip() if "." in name else ""
    if extension not in SUPPORTED_EXTENSIONS:
        raise ImportError_("Please upload an Excel, CSV, Word (.docx) or PDF file.")

    if not data:
        raise ImportError_("That file is empty.")

    if extension in {"xlsx", "xlsm"}:
        try:
            table = _read_xlsx(data)
        except ImportError_:
            raise
        except Exception as exc:  # noqa: BLE001 - openpyxl raises broadly
            raise ImportError_(
                f"The Excel file could not be read ({exc}). If it is password "
                "protected or open in Excel, save a copy and try again."
            )
    elif extension == "csv":
        table = _read_csv(data)
    elif extension == "docx":
        table = _read_docx(data)
    else:
        table = _read_pdf(data)

    products = rows_from_table(table)
    if not products:
        raise ImportError_(
            "No products could be found in that file. Each row needs at least a "
            "product name and a price."
        )

    log.info("Parsed %d product row(s) from a %s file", len(products), extension)
    return products
