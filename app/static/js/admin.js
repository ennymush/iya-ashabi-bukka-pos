/* ===================================================================
   admin.js - shared admin behaviour: clock, sidebar, live dashboard,
   hardware test buttons and simple confirmations.
   =================================================================== */
(function () {
  "use strict";

  /* ---------------- Clock ---------------- */
  function startClock() {
    const node = document.getElementById("admin-clock");
    if (!node) return;
    const tick = () => {
      const now = new Date();
      node.textContent =
        now.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) +
        "  " +
        now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    };
    tick();
    setInterval(tick, 20000);
  }

  /* ---------------- Sidebar (mobile) ---------------- */
  function bindSidebar() {
    const toggle = document.getElementById("sidebar-toggle");
    const sidebar = document.getElementById("sidebar");
    if (!toggle || !sidebar) return;
    toggle.addEventListener("click", () => sidebar.classList.toggle("open"));
  }

  /* ---------------- Confirm destructive actions ---------------- */
  function bindConfirms() {
    document.addEventListener("submit", (event) => {
      const form = event.target;
      const message = form.dataset.confirm;
      if (message && !window.confirm(message)) {
        event.preventDefault();
      }
    });
  }

  /* ---------------- Live dashboard ---------------- */
  function bindLiveDashboard() {
    const root = document.getElementById("live-dashboard");
    if (!root) return;

    let failures = 0;
    async function refresh() {
      try {
        const response = await fetch("/admin/api/dashboard/live", {
          headers: { "X-Requested-With": "fetch" },
          cache: "no-store",
        });
        if (!response.ok) throw new Error("bad status");
        const data = await response.json();
        failures = 0;
        apply(data);
      } catch (err) {
        failures += 1;
        if (failures > 20) return; // server clearly gone; stop polling
      }
    }

    function setText(key, value) {
      const node = root.querySelector('[data-live="' + key + '"]');
      if (node && value !== undefined && value !== null) node.textContent = value;
    }

    function apply(data) {
      setText("total", data.total);
      setText("transactions", data.transactions);
      setText("cash", data.cash);
      setText("transfer", data.transfer);
      setText("card", data.card);
      setText("items", data.items_sold);
      setText("low-stock", data.low_stock);
      setText("open-shifts", data.open_shifts);

      const body = root.querySelector("[data-live-recent]");
      if (body && Array.isArray(data.recent)) {
        body.innerHTML = data.recent
          .map(
            (row) =>
              "<tr><td>" + row.receipt + "</td><td>" + row.time + "</td><td>" + row.cashier +
              '</td><td class="num">' + row.total + "</td><td>" + row.status + "</td></tr>"
          )
          .join("");
      }
      root.setAttribute("data-last-update", new Date().toLocaleTimeString("en-GB"));
    }

    refresh();
    setInterval(refresh, 10000);
  }

  /* ---------------- Hardware test buttons ---------------- */
  function bindHardwareTests() {
    document.querySelectorAll("[data-test-device]").forEach((button) => {
      button.addEventListener("click", async () => {
        const device = button.dataset.testDevice;
        const output = document.getElementById("hw-result-" + device);
        button.disabled = true;
        const original = button.textContent;
        button.textContent = "TESTING…";

        try {
          const code = document.getElementById("scanner-test-code");
          const query = device === "scanner" && code ? "?code=" + encodeURIComponent(code.value) : "";
          const response = await fetch("/admin/api/hardware/test/" + device + query, {
            headers: { "X-Requested-With": "fetch" },
          });
          const data = await response.json();
          if (output) {
            output.hidden = false;
            output.className = data.ok ? "hint-box" : "err-box";
            output.textContent = data.message + (data.detail ? " (" + data.detail + ")" : "");
          }
        } catch (err) {
          if (output) {
            output.hidden = false;
            output.className = "err-box";
            output.textContent = "Could not reach the POS server.";
          }
        } finally {
          button.disabled = false;
          button.textContent = original;
        }
      });
    });
  }

  /* ---------------- Print helpers ---------------- */
  function bindPrint() {
    document.querySelectorAll("[data-print]").forEach((button) => {
      button.addEventListener("click", () => window.print());
    });
  }

  /* ---------------- Reprint receipt buttons ---------------- */
  function bindReprint() {
    document.querySelectorAll("[data-reprint]").forEach((button) => {
      button.addEventListener("click", async () => {
        const saleId = button.dataset.reprint;
        button.disabled = true;
        try {
          const response = await fetch("/admin/sales/transactions/" + saleId + "/print-receipt", {
            method: "POST",
            headers: { "X-Requested-With": "fetch" },
          });
          const data = await response.json();
          button.textContent = data.ok ? "PRINTED" : "FAILED";
          if (!data.ok) button.title = data.message || "";
        } catch (err) {
          button.textContent = "FAILED";
        } finally {
          setTimeout(() => {
            button.disabled = false;
            button.textContent = "PRINT";
          }, 2500);
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    startClock();
    bindSidebar();
    bindConfirms();
    bindLiveDashboard();
    bindHardwareTests();
    bindPrint();
    bindReprint();
  });
})();
