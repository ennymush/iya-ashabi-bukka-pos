"""Windows monitor detection.

Uses ctypes so there is no hard dependency on pywin32 for this. Returns the
attached monitors with their bounds in virtual-screen coordinates, so a window
can be positioned on the customer-facing display at launch time.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass, asdict

from app.utils.logging_config import get_logger

log = get_logger("hardware")

IS_WINDOWS = sys.platform == "win32"
user32 = ctypes.windll.user32 if IS_WINDOWS else None

MONITORINFOF_PRIMARY = 0x00000001
MONITOR_DEFAULTTONEAREST = 0x00000002


@dataclass
class MonitorInfo:
    """One attached display, in virtual-screen pixel coordinates."""

    index: int
    handle: int
    x: int
    y: int
    width: int
    height: int
    is_primary: bool

    def to_dict(self) -> dict:
        data = asdict(self)
        data["label"] = f"Monitor {self.index + 1}" + (" (primary)" if self.is_primary else "")
        return data


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)


def _make_callback(sink: list[MonitorInfo]):
    def _cb(monitor, hdc, lprect, lparam):
        rect = lprect.contents
        sink.append(
            MonitorInfo(
                index=len(sink),
                handle=int(monitor),
                x=int(rect.left),
                y=int(rect.top),
                width=int(rect.right - rect.left),
                height=int(rect.bottom - rect.top),
                is_primary=False,
            )
        )
        return True

    return _MONITORENUMPROC(_cb)


def detect_monitors() -> list[MonitorInfo]:
    """Return the attached monitors. Empty list on non-Windows or on failure."""
    if not IS_WINDOWS or user32 is None:
        return []
    collected: list[MonitorInfo] = []
    try:
        user32.EnumDisplayMonitors(None, None, _make_callback(collected), 0)
    except Exception as exc:  # noqa: BLE001 - hardware can fail in odd ways
        log.warning("Monitor detection failed: %s", exc)
        return []

    # The primary monitor is the one covering the virtual origin (0, 0).
    for m in collected:
        if m.x <= 0 < m.x + m.width and m.y <= 0 < m.y + m.height:
            m.is_primary = True
    if collected and not any(m.is_primary for m in collected):
        collected[0].is_primary = True
    return collected


def has_secondary_monitor() -> bool:
    """True when a second screen is attached and usable for the customer view."""
    return len(detect_monitors()) > 1


def monitor_summary() -> list[dict]:
    return [m.to_dict() for m in detect_monitors()]


def window_position(index: int) -> tuple[int, int, int, int]:
    """(x, y, width, height) for a monitor index, falling back to the primary
    screen when the requested monitor does not exist."""
    monitors = detect_monitors()
    if not monitors:
        return 0, 0, 1024, 768
    if 0 <= index < len(monitors):
        m = monitors[index]
    else:
        m = next((m for m in monitors if m.is_primary), monitors[0])
    return m.x, m.y, m.width, m.height
