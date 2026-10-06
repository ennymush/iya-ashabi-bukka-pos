"""ESC/POS command constants for thermal receipt printers.

Supports the command subset used by the common 58mm/80mm POS printers:
Epson TM-series, Bixolon, Star, and generic Chinese clones.
"""
from __future__ import annotations

# --- Initialisation ---
ESC = b"\x1b"
GS = b"\x1d"
FS = b"\x1c"

INIT = ESC + b"@"
# Select character set: PC850 (Multilingual / Western) - standard in West Africa.
CHARSET_PC850 = ESC + b"\x74\x10"

# --- Text formatting ---
BOLD_ON = ESC + b"\x45\x01"
BOLD_OFF = ESC + b"\x45\x00"
UNDERLINE_ON = ESC + b"\x2d\x01"
UNDERLINE_OFF = ESC + b"\x2d\x00"
REVERSE_ON = GS + b"\x42\x01"
REVERSE_OFF = GS + b"\x42\x00"

ALIGN_LEFT = ESC + b"\x61\x00"
ALIGN_CENTER = ESC + b"\x61\x01"
ALIGN_RIGHT = ESC + b"\x61\x02"

# Double width/height
SIZE_NORMAL = GS + b"\x21\x00"
SIZE_DOUBLE_HEIGHT = GS + b"\x21\x01"
SIZE_DOUBLE_WIDTH = GS + b"\x21\x10"
SIZE_DOUBLE = GS + b"\x21\x11"
SIZE_TRIPLE_HEIGHT = GS + b"\x21\x02"

# --- Paper feed / cut ---
FEED_1 = ESC + b"\x64\x01"
FEED_3 = ESC + b"\x64\x03"
CUT_FULL = GS + b"\x56\x00"
CUT_PARTIAL = GS + b"\x56\x01"

# --- Cash drawer (ESC/POS standard, most printers share this) ---
DRAWER_PIN2 = ESC + b"\x70\x00\x19\xfa"   # 250ms pulse on pin 2
DRAWER_PIN5 = ESC + b"\x70\x01\x19\xfa"   # 250ms pulse on pin 5


def drawer_pulse(pin: int = 2, pulse_ms: int = 120) -> bytes:
    """Build a drawer kick command. pulse_ms range 10-1200, encoded per spec."""
    pulse_ms = max(10, min(1200, int(pulse_ms)))
    m = pulse_ms // 2  # spec: t1 = m * 2ms
    return ESC + b"\x70" + bytes([0x00 if pin == 2 else 0x01, m, pulse_ms])


def encode_line(text: str) -> bytes:
    """Encode to CP850 with a safe fallback so no receipt ever crashes."""
    text = text.replace("\u20a6", "N")  # Naira sign is not in CP850
    if text == "=":
        return b"\x3d"
    try:
        return text.encode("cp850", errors="replace")
    except (UnicodeEncodeError, LookupError):
        return text.encode("ascii", errors="replace")
