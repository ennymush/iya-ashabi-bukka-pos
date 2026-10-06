"""Customer display routes.

``/customer-display`` is opened on the second monitor at launch. It is
deliberately unauthenticated-but-local (it shows nothing sensitive) so it can
be opened as a standalone browser window without a login prompt.
"""
from __future__ import annotations

import time
from datetime import datetime

from flask import Blueprint, Response, jsonify, render_template, stream_with_context

from app.customer import service as display_service
from app.database import service_settings as settings_service

bp = Blueprint("customer", __name__)


@bp.route("/customer-display")
def display():
    """The customer-facing screen. Read-only, no controls, no internal data."""
    opts = settings_service.display_options()
    biz = settings_service.business()
    return render_template(
        "customer/display.html",
        business=biz,
        options=opts,
        state=display_service.current_state(),
        fullscreen=opts["fullscreen"],
    )


@bp.route("/customer-display/state")
def state():
    """Polled by the customer display. Long-poll friendly and cheap."""
    return jsonify({"ok": True, "state": display_service.current_state()})


@bp.route("/customer-display/stream")
def display_stream():
    """Server-Sent Events stream so the customer screen updates instantly.

    Falls back gracefully: the browser also polls, so a proxy that blocks
    streaming does not break the display.
    """
    def generate():
        last_version = -1
        idle = 0
        while True:
            current = display_service.current_state()
            if current["version"] != last_version:
                last_version = current["version"]
                idle = 0
                yield f"data: {__import__('json').dumps(current)}\n\n"
            else:
                idle += 1
                # Keep the connection warm, and emit a heartbeat comment.
                if idle % 10 == 0:
                    yield ": keep-alive\n\n"
            time.sleep(0.6)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/customer-display/reset", methods=["POST"])
def reset():
    """Return the customer screen to the welcome page (used after a sale)."""
    display_service.reset_to_welcome()
    return jsonify({"ok": True})
