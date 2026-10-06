/* ===================================================================
   pos-payment.js - payment modal, keypad, change calculation and checkout.
   Change is recomputed on the server; this module only displays a preview.
   =================================================================== */
(function (global) {
  "use strict";

  const Api = global.PosApi;
  const state = { method: "CASH", typed: "", due: "0.00" };

  const el = {};

  function cacheElements() {
    el.payment = document.getElementById("modal-payment");
    el.success = document.getElementById("modal-success");
    el.due = document.getElementById("pay-due");
    el.changeLine = document.getElementById("pay-change-line");
    el.amount = document.getElementById("pay-method-amount");
    el.error = document.getElementById("pay-error");
    el.quick = document.getElementById("quick-cash");
    el.keypad = document.getElementById("keypad");
    el.confirm = document.getElementById("btn-confirm-payment");
    el.payGrid = document.getElementById("pay-grid");
    el.discount = document.getElementById("modal-discount");
    el.discountError = document.getElementById("disc-error");
    el.receiptOut = document.getElementById("success-receipt");
    el.totalOut = document.getElementById("success-total");
    el.changeOut = document.getElementById("success-change");
    el.warning = document.getElementById("success-warning");
    el.reprint = document.getElementById("btn-reprint");
  }

  /* ---------------- Modals ---------------- */
  function open(id) {
    const node = document.getElementById(id);
    if (node) node.hidden = false;
  }
  function close(id) {
    const node = document.getElementById(id);
    if (node) node.hidden = true;
  }
  function closeAll() {
    ["modal-payment", "modal-success", "modal-discount", "modal-shift", "modal-held"].forEach(close);
  }

  function showError(node, message) {
    if (!node) return;
    if (!message) {
      node.hidden = true;
      node.textContent = "";
      return;
    }
    node.hidden = false;
    node.textContent = message;
  }

  /* ---------------- Payment ---------------- */
  function openPayment() {
    const cart = global.PosCart.state.cart;
    if (!cart || !cart.items || !cart.items.length) {
      global.PosUi.toast("Add at least one product first.", "err", 2200);
      return;
    }
    if (cart.has_unavailable) {
      global.PosUi.toast("A product in this sale is no longer available. Please remove it.", "err", 3600);
      return;
    }
    if (!global.IAB.hasShift) {
      global.PosUi.openModal("modal-shift");
      global.PosUi.toast("Please open your shift before taking payment.", "err", 3000);
      return;
    }

    state.typed = "";
    state.due = cart.total;
    el.due.textContent = cart.total;
    el.amount.value = "";
    showError(el.error, "");
    buildQuickCash(cart.total);
    updateChange();
    open("modal-payment");
    setTimeout(() => el.amount.focus(), 60);
  }

  function buildQuickCash(totalText) {
    const total = parseFloat(String(totalText).replace(/[^0-9.]/g, "")) || 0;
    const notes = [1000, 2000, 5000, 10000, 20000, 50000];
    const options = [];
    notes.forEach((note) => {
      // Smallest tidy note combination that covers the amount due.
      const up = Math.ceil(total / note) * note;
      if (options.indexOf(up) === -1 && up >= total) options.push(up);
    });
    const exact = Math.ceil(total);
    if (exact > 0 && options.indexOf(exact) === -1) options.unshift(exact);
    el.quick.innerHTML = options
      .slice(0, 6)
      .map(
        (value) =>
          '<button type="button" data-amount="' + value + '">' +
          "&#8358;" + value.toLocaleString("en-NG") +
          "</button>"
      )
      .join("");
  }

  function setMethod(method) {
    state.method = method;
    el.payGrid.querySelectorAll(".pay-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.method === method);
    });
    // The amount pad is only meaningful for cash.
    const isCash = method === "CASH";
    el.keypad.style.display = isCash ? "grid" : "none";
    el.quick.style.display = isCash ? "grid" : "none";
    el.amount.parentElement.style.display = isCash ? "block" : "none";
    el.confirm.textContent = isCash ? "CONFIRM PAYMENT" : "COMPLETE SALE";
    updateChange();
  }

  function updateChange() {
    const typed = el.amount.value.trim();
    if (state.method !== "CASH") {
      el.changeLine.innerHTML = "";
      return;
    }
    const due = parseFloat(String(state.due).replace(/[^0-9.]/g, "")) || 0;
    const received = typed ? parseFloat(typed.replace(/[^0-9.]/g, "")) || 0 : due;
    const change = received - due;
    if (change >= 0) {
      el.changeLine.innerHTML =
        received > due
          ? '<span style="color:var(--green-500);font-weight:700;">Change: &#8358;' +
            change.toLocaleString("en-NG", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) +
            "</span>"
          : "";
    } else {
      el.changeLine.innerHTML =
        '<span style="color:var(--red-600);font-weight:700;">Short by &#8358;' +
        Math.abs(change).toLocaleString("en-NG", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) +
        "</span>";
    }
  }

  async function confirmPayment() {
    const cart = global.PosCart.state.cart;
    if (!cart || !cart.items.length) {
      close("modal-payment");
      return;
    }
    showError(el.error, "");
    el.confirm.disabled = true;

    const payload = { payment_method: state.method };
    if (state.method === "CASH") payload.amount_received = el.amount.value.trim() || cart.total;

    try {
      const result = await Api.checkout(payload);
      close("modal-payment");
      showSuccess(result);
      await global.PosCart.refresh();
      await global.PosCart.loadProducts(document.getElementById("pos-search").value.trim());
      if (global.IAB.hasShift) {
        // Shift stays open; totals refresh silently.
        Api.shiftStatus().catch(() => {});
      }
    } catch (err) {
      showError(el.error, err.message);
      if (err.status === 0 || err.status >= 500) {
        global.PosUi.toast("Please check the transaction list before retrying.", "err", 5000);
      }
      await global.PosCart.refresh();
    } finally {
      el.confirm.disabled = false;
    }
  }

  function showSuccess(result) {
    global.IAB.lastSaleId = result.sale ? result.sale.id : null;
    el.receiptOut.textContent = result.receipt_number || "";
    el.totalOut.textContent = result.total || "";
    el.changeOut.textContent = result.change || "₦0.00";

    const warnings = result.warnings || [];
    if (warnings.length) {
      el.warning.hidden = false;
      el.warning.innerHTML =
        warnings.join("<br>") +
        (result.printed === false
          ? '<br><button type="button" class="btn btn-ghost btn-sm" id="btn-reprint-2" style="margin-top:.4rem;">REPRINT RECEIPT</button>'
          : "");
    } else {
      el.warning.hidden = true;
    }
    el.reprint.hidden = !(result.printed === false && global.IAB.lastSaleId);
    open("modal-success");

    const inlineReprint = document.getElementById("btn-reprint-2");
    if (inlineReprint) inlineReprint.addEventListener("click", () => reprint());
  }

  async function reprint() {
    if (!global.IAB.lastSaleId) {
      global.PosUi.toast("No receipt to reprint.", "err");
      return;
    }
    try {
      const result = await Api.reprint(global.IAB.lastSaleId);
      global.PosUi.toast(result.message || "Receipt printed.", result.ok ? "ok" : "err", 2600);
      if (result.ok) {
        el.warning.hidden = true;
        el.reprint.hidden = true;
      }
    } catch (err) {
      global.PosUi.toast(err.message, "err", 3000);
    }
  }

  /* ---------------- Discount ---------------- */
  function openDiscount() {
    showError(el.discountError, "");
    open("modal-discount");
    setTimeout(() => document.getElementById("disc-value").focus(), 60);
  }

  async function submitDiscount(event) {
    event.preventDefault();
    const type = document.getElementById("disc-type").value;
    const value = document.getElementById("disc-value").value.trim();
    if (!value) {
      showError(el.discountError, "Enter a discount amount.");
      return;
    }
    try {
      await global.PosCart.applyDiscount(type, value);
      close("modal-discount");
      global.PosUi.toast("Discount applied", "ok", 1600);
      document.getElementById("disc-value").value = "";
    } catch (err) {
      showError(el.discountError, err.message);
    }
  }

  function bind() {
    cacheElements();

    el.payGrid.addEventListener("click", (event) => {
      const button = event.target.closest(".pay-btn");
      if (button) setMethod(button.dataset.method);
    });

    el.keypad.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-key]");
      if (!button) return;
      const key = button.dataset.key;
      if (key === "clear") el.amount.value = "";
      else el.amount.value = (el.amount.value + key).replace(/[^0-9.]/g, "");
      updateChange();
      el.amount.focus();
    });

    el.quick.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-amount]");
      if (!button) return;
      el.amount.value = button.dataset.amount;
      updateChange();
    });

    el.amount.addEventListener("input", updateChange);
    el.confirm.addEventListener("click", confirmPayment);

    document.getElementById("btn-complete").addEventListener("click", openPayment);
    document.getElementById("btn-discount").addEventListener("click", openDiscount);
    document.getElementById("form-discount").addEventListener("submit", submitDiscount);
    document.getElementById("btn-next-customer").addEventListener("click", () => {
      close("modal-success");
      const search = document.getElementById("pos-search");
      if (search) search.focus();
    });
    el.reprint.addEventListener("click", reprint);

    document.body.addEventListener("click", (event) => {
      const closer = event.target.closest("[data-close]");
      if (closer) close(closer.dataset.close);
    });

    // Backdrop click closes any modal.
    document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
      backdrop.addEventListener("click", (event) => {
        if (event.target === backdrop) backdrop.hidden = true;
      });
    });

    setMethod("CASH");
  }

  global.PosPayment = { bind, openPayment, closeAll, open, close };
})(window);
