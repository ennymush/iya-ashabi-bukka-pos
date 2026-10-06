/* ===================================================================
   pos-init.js - bootstraps the POS: shared UI helpers, shift dialog,
   held sales, keyboard shortcuts and the live clock.
   =================================================================== */
(function (global) {
  "use strict";

  const Api = global.PosApi;

  /* ---------------- Small UI kit ---------------- */
  const Ui = {
    toast(message, kind, duration) {
      const area = document.getElementById("toast-area");
      if (!area) return;
      const node = document.createElement("div");
      node.className = "pos-toast" + (kind ? " " + kind : "");
      node.textContent = message;
      area.appendChild(node);
      setTimeout(() => node.remove(), duration || 2000);
    },

    openModal(id) {
      const node = document.getElementById(id);
      if (node) node.hidden = false;
    },

    beepOk() {
      try {
        const ctx = new (global.AudioContext || global.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.frequency.value = 1200;
        gain.gain.setValueAtTime(0.04, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.12);
        osc.start();
        osc.stop(ctx.currentTime + 0.12);
      } catch (err) {
        /* Sound is a nicety, never a requirement. */
      }
    },

    busy(on, label) {
      const overlay = document.getElementById("loading");
      if (!overlay) return;
      overlay.textContent = label || "Working…";
      overlay.hidden = !on;
    },
  };
  global.PosUi = Ui;

  /* ---------------- Clock ---------------- */
  function startClock() {
    const node = document.getElementById("pos-clock");
    if (!node) return;
    const tick = () => {
      const now = new Date();
      node.textContent = now.toLocaleDateString("en-GB", {
        day: "2-digit", month: "short", year: "numeric",
      }) + "  " + now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    };
    tick();
    setInterval(tick, 15000);
  }

  /* ---------------- Shift ---------------- */
  function bindShift() {
    const button = document.getElementById("btn-shift");
    const form = document.getElementById("form-shift");
    const error = document.getElementById("shift-error");
    const result = document.getElementById("shift-result");
    const isOpen = global.IAB.hasShift;

    if (!button) return;

    button.addEventListener("click", async () => {
      showError(error, "");
      if (result) result.innerHTML = "";
      document.getElementById("shift-open-fields").hidden = !!isOpen;
      document.getElementById("shift-close-fields").hidden = !isOpen;
      document.getElementById("shift-title").textContent = isOpen ? "Close Shift" : "Open Shift";
      document.getElementById("btn-shift-confirm").textContent = isOpen ? "CLOSE SHIFT" : "OPEN SHIFT";
      if (isOpen) {
        try {
          const status = await Api.shiftStatus();
          document.getElementById("shift-expected").textContent = status.expected_cash;
          document.getElementById("actual-cash").value = "";
        } catch (err) {
          showError(error, err.message);
        }
      } else {
        document.getElementById("opening-cash").value = "0.00";
      }
      Ui.openModal("modal-shift");
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      showError(error, "");
      const submit = document.getElementById("btn-shift-confirm");
      submit.disabled = true;
      try {
        if (isOpen) {
          const response = await Api.closeShift(
            document.getElementById("actual-cash").value.trim() || "0",
            document.getElementById("shift-note").value.trim()
          );
          result.innerHTML =
            '<div class="change-box"><div class="lbl">Shift Summary</div>' +
            "<p style='margin:.3rem 0'>Expected: <strong>" + response.expected + "</strong></p>" +
            "<p style='margin:.3rem 0'>Counted: <strong>" + response.actual + "</strong></p>" +
            "<p style='margin:.3rem 0'>Variance: <strong>" + response.variance + "</strong></p></div>" +
            '<button type="button" class="btn btn-primary btn-block" id="btn-shift-done">DONE</button>';
          document.getElementById("btn-shift-done").addEventListener("click", () => location.reload());
        } else {
          const response = await Api.openShift(document.getElementById("opening-cash").value.trim() || "0");
          Ui.toast(response.message, "ok");
          global.IAB.hasShift = true;
          setTimeout(() => location.reload(), 600);
        }
      } catch (err) {
        showError(error, err.message);
      } finally {
        submit.disabled = false;
      }
    });
  }

  function showError(node, message) {
    if (!node) return;
    node.hidden = !message;
    node.textContent = message || "";
  }

  /* ---------------- Held sales ---------------- */
  function bindHeld() {
    const button = document.getElementById("btn-hold");
    const retrieve = document.getElementById("btn-retrieve");
    const body = document.getElementById("held-body");
    const empty = document.getElementById("held-empty");

    if (button) {
      button.addEventListener("click", async () => {
        try {
          const response = await Api.hold("");
          Ui.toast(response.message, "ok", 2600);
          await global.PosCart.refresh();
        } catch (err) {
          Ui.toast(err.message, "err", 3200);
        }
      });
    }

    if (retrieve) {
      retrieve.addEventListener("click", async () => {
        await loadHeld(body, empty);
        Ui.openModal("modal-held");
      });
    }
  }

  async function loadHeld(body, empty) {
    try {
      const data = await Api.heldList();
      const rows = data.held || [];
      empty.hidden = rows.length > 0;
      body.innerHTML = rows
        .map(
          (h) =>
            "<tr>" +
            "<td>" + h.reference + "</td>" +
            "<td class='num'>" + h.item_count + "</td>" +
            "<td class='num'>" + h.total + "</td>" +
            "<td>" + new Date(h.created_at).toLocaleString("en-GB") + "</td>" +
            "<td><div style='display:flex;gap:.3rem;flex-wrap:wrap'>" +
            "<button type='button' class='btn btn-ghost btn-sm' data-held-retrieve='" + h.id + "'>RETRIEVE</button>" +
            "<button type='button' class='btn btn-ghost btn-sm' data-held-cancel='" + h.id + "'>CANCEL</button>" +
            "</div></td></tr>"
        )
        .join("");

      body.querySelectorAll("[data-held-retrieve]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try {
            const cart = await Api.retrieveHeld(btn.dataset.heldRetrieve);
            global.PosCart.renderCart(cart);
            document.getElementById("modal-held").hidden = true;
            Ui.toast("Held sale retrieved", "ok");
          } catch (err) {
            Ui.toast(err.message, "err");
          }
        });
      });

      body.querySelectorAll("[data-held-cancel]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try {
            await Api.cancelHeld(btn.dataset.heldCancel);
            Ui.toast("Held sale cancelled", "ok");
            await loadHeld(body, empty);
          } catch (err) {
            Ui.toast(err.message, "err");
          }
        });
      });
    } catch (err) {
      Ui.toast(err.message, "err");
    }
  }

  /* ---------------- Keyboard shortcuts ---------------- */
  function bindShortcuts() {
    document.addEventListener("keydown", (event) => {
      const inField =
        event.target &&
        (event.target.tagName === "INPUT" ||
          event.target.tagName === "TEXTAREA" ||
          event.target.isContentEditable);

      switch (event.key) {
        case "F1":
          event.preventDefault();
          focusSearch();
          break;
        case "F2":
          if (global.IAB.canHold !== false) {
            event.preventDefault();
            const hold = document.getElementById("btn-hold");
            if (hold) hold.click();
          }
          break;
        case "F3":
          event.preventDefault();
          const retrieve = document.getElementById("btn-retrieve");
          if (retrieve) retrieve.click();
          break;
        case "F4":
          event.preventDefault();
          global.PosPayment.openPayment();
          break;
        case "F5":
          event.preventDefault();
          const clear = document.getElementById("btn-clear-cart");
          if (clear) clear.click();
          break;
        case "F6":
          event.preventDefault();
          reprintLast();
          break;
        case "Escape":
          closeTopModal();
          break;
        default:
          break;
      }
    });
  }

  function focusSearch() {
    const search = document.getElementById("pos-search");
    if (search) {
      search.focus();
      search.select();
    }
  }

  async function reprintLast() {
    if (!global.IAB.lastSaleId) {
      try {
        const list = await fetch("/pos/transactions", { headers: { "X-Requested-With": "fetch" } });
        // Fall back to the normal receipts page when we have no id in memory.
        if (list.ok) {
          window.location.href = "/pos/transactions";
          return;
        }
      } catch (err) {
        /* ignore */
      }
      Ui.toast("Complete a sale first, then use REPRINT.", "err", 2400);
      return;
    }
    try {
      const result = await Api.reprint(global.IAB.lastSaleId);
      Ui.toast(result.message || "Receipt printed.", result.ok ? "ok" : "err", 2400);
    } catch (err) {
      Ui.toast(err.message, "err");
    }
  }

  function closeTopModal() {
    const open = Array.from(document.querySelectorAll(".modal-backdrop")).filter((m) => !m.hidden);
    if (open.length) open[open.length - 1].hidden = true;
  }

  /* ---------------- Boot ---------------- */
  document.addEventListener("DOMContentLoaded", () => {
    global.PosPayment.bind();
    global.PosCart.bind();
    global.PosScanner.bind();
    bindShift();
    bindHeld();
    bindShortcuts();
    startClock();
    bindReprintButton();
    focusSearch();
  });

  function bindReprintButton() {
    const button = document.getElementById("btn-print-last");
    if (button) button.addEventListener("click", reprintLast);
  }
})(window);
