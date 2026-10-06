"""DisplayService and Windows monitor detection.

The customer screen is an ordinary browser page that polls the server, so it
works on any machine. ``monitors.py`` reports the attached displays so the
launcher can place that window on the second monitor and go fullscreen there.
"""
from __future__ import annotations

from app.hardware.base import HardwareResult, HardwareStatus
from app.utils.logging_config import get_logger

log = get_logger("hardware")


class BrowserDisplay:
    """The real customer display: a fullscreen browser window on monitor 2."""

    name = "BrowserDisplay"

    def __init__(self, enabled: bool = True, fullscreen: bool = True, monitor_index: int = 2):
        self.enabled = enabled
        self.fullscreen = fullscreen
        self.monitor_index = monitor_index

    def is_available(self) -> HardwareResult:
        if not self.enabled:
            return HardwareResult(False, HardwareStatus.NOT_CONFIGURED,
                                  "Customer display is switched off in Settings.")
        return HardwareResult(True, HardwareStatus.CONNECTED,
                              f"Customer display will open on monitor {self.monitor_index}.")

    def update(self, state: dict) -> HardwareResult:
        # The browser page polls the server; nothing to push from here.
        return HardwareResult(True, HardwareStatus.CONNECTED, "Customer display updated.")

    def test(self) -> HardwareResult:
        return self.is_available()


class MockDisplay:
    name = "MockDisplay"

    def __init__(self):
        self.last_state: dict = {}

    def is_available(self) -> HardwareResult:
        return HardwareResult(True, HardwareStatus.MOCK, "Mock customer display ready.")

    def update(self, state: dict) -> HardwareResult:
        self.last_state = state
        log.info("MockDisplay update mode=%s items=%s",
                 state.get("mode"), len(state.get("items", [])))
        return HardwareResult(True, HardwareStatus.MOCK, "Customer display updated (mock).")

    def test(self) -> HardwareResult:
        return HardwareResult(True, HardwareStatus.MOCK,
                              "Customer display test message sent (mock).",
                              payload={"mode": "TEST", "message": "Customer display is working."})
