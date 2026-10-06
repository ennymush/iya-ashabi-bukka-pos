/* ===================================================================
   customer-display.js - keeps the customer screen in step with the till.

   Uses Server-Sent Events for instant updates and falls back to polling if
   the browser or environment blocks streaming. Customer-safe data only:
   the server never sends cost price, profit or any admin information.
   =================================================================== */
(function () {
  "use strict";

  const CONFIG = window.IAB_DISPLAY || {};
  const VIEWS = ["view-welcome", "view-cart", "view-payment", "view-complete", "view-test"];
  const NAIRA = "\u20a6";

  let lastVersion = -1;
  let pollTimer = null;
  let autoReturnTimer = null;

  function money(value) {
    const num = parseFloat(String(value == null ? "0" : value).replace(/[^0-9.\-]/g, "")) || 0;
    return NAIRA + num.toLocaleString("en-NG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function show(id) {
    VIEWS.forEach((name) => {
      const node = document.getElementById(name);
      if (node) node.hidden = name !== id;
    });
  }

  function render(state) {
    if (!state) return;
    if (state.version === lastVersion) return;
    lastVersion = state.version;

    const mode = state.mode;
    const data = state.data || {};

    if (mode === "CART") renderCart(data);
    else if (mode === "PAYMENT") renderPayment(data);
    else if (mode === "COMPLETE") renderComplete(data);
    else if (mode === "TEST") show("view-test");
    else renderWelcome(state);

    scheduleReturn();
  }

  function renderWelcome(state) {
    show("view-welcome");
    const welcome = document.getElementById("view-welcome");
    const message = state.welcome_message || "Welcome";
    welcome.querySelector(".big").textContent = message;
    const promo = state.business ? state.business.promo_message : "";
    const promoNode = welcome.querySelector(".small");
    if (promoNode) promoNode.textContent = promo || "";
  }

  function renderCart(data) {
    show("view-cart");
    const rows = document.getElementById("cart-rows");
    const items = data.items || [];
    rows.innerHTML = items
      .map(
        (item) =>
          "<tr>" +
          '<td class="item-name">' + escapeHtml(item.name) + "</td>" +
          '<td class="num item-qty">' + item.quantity + "</td>" +
          '<td class="num">' + money(item.unit_price) + "</td>" +
          '<td class="num">' + money(item.line_total) + "</td>" +
          "</tr>"
      )
      .join("");

    document.getElementById("c-subtotal").textContent = money(data.subtotal);
    document.getElementById("c-total").textContent = money(data.total);

    const discountRow = document.getElementById("c-discount-row");
    const discount = data.discount || "0";
    if (CONFIG.showTotals && parseFloat(discount) > 0) {
      discountRow.hidden = false;
      document.getElementById("c-discount").textContent = "-" + money(discount);
    } else {
      discountRow.hidden = true;
    }
  }

  function renderPayment(data) {
    show("view-payment");
    document.getElementById("p-total").textContent = money(data.total);

    const received = parseFloat(String(data.amount_received || "0"));
    const change = parseFloat(String(data.change || "0"));
    const paidRow = document.getElementById("p-paid-row");
    const changeRow = document.getElementById("p-change-row");

    if (received > 0) {
      paidRow.hidden = false;
      document.getElementById("p-paid").textContent = money(data.amount_received);
    } else {
      paidRow.hidden = true;
    }
    if (change > 0) {
      changeRow.hidden = false;
      document.getElementById("p-change").textContent = money(data.change);
    } else {
      changeRow.hidden = true;
    }
  }

  function renderComplete(data) {
    show("view-complete");
    document.getElementById("complete-thanks").textContent =
      data.thankyou_message || CONFIG.thankYou || "Thank you for your patronage!";
    const ref = document.getElementById("complete-ref");
    ref.textContent = data.reference ? "Receipt: " + data.reference : "";
  }

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  /** After a completed sale, drift back to the welcome screen automatically. */
  function scheduleReturn() {
    if (autoReturnTimer) clearTimeout(autoReturnTimer);
    autoReturnTimer = setTimeout(() => {
      fetch("/customer-display/reset", { method: "POST" }).catch(() => {});
    }, 12000);
  }

  /* ---------------- Transport ---------------- */
  function startPolling(intervalMs) {
    if (pollTimer) return;
    pollTimer = setInterval(poll, intervalMs || 1200);
  }

  async function poll() {
    try {
      const response = await fetch(CONFIG.stateUrl, { cache: "no-store" });
      const payload = await response.json();
      if (payload && payload.ok) render(payload.state);
    } catch (err) {
      /* keep trying - the display should recover when the server returns */
    }
  }

  function startStream() {
    if (!("EventSource" in window)) {
      startPolling(1000);
      return;
    }
    let source;
    try {
      source = new EventSource(CONFIG.streamUrl);
    } catch (err) {
      startPolling(1000);
      return;
    }
    source.onmessage = (event) => {
      try {
        render(JSON.parse(event.data));
      } catch (err) {
        /* ignore malformed frame */
      }
    };
    source.onerror = () => {
      // Stream dropped: fall back to polling so the display still updates.
      try { source.close(); } catch (e) {}
      startPolling(1200);
    };
  }

  /* ---------------- Clock ---------------- */
  function startClock() {
    const dateNode = document.getElementById("cust-date");
    const timeNode = document.getElementById("cust-time");
    if (!dateNode && !timeNode) return;
    const tick = () => {
      const now = new Date();
      if (dateNode) {
        dateNode.textContent = now.toLocaleDateString("en-GB", {
          weekday: "long", day: "2-digit", month: "long", year: "numeric",
        });
      }
      if (timeNode) {
        timeNode.textContent = now.toLocaleTimeString("en-GB", {
          hour: "2-digit", minute: "2-digit",
        });
      }
    };
    tick();
    setInterval(tick, 1000);
  }

  /* ---------------- Boot ---------------- */
  document.addEventListener("DOMContentLoaded", () => {
    startClock();
    poll();
    startStream();
    // Fullscreen on load when enabled in settings (kiosk-style display).
    const flag = document.querySelector('meta[name="iab-fullscreen"]');
    if (flag && document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().catch(() => {});
    }
  });
})();
