/* ===================================================================
   pos-scanner.js - keyboard-wedge barcode scanner support.

   USB barcode scanners behave like a fast keyboard: they type the digits then
   press Enter. So we detect that pattern - a burst of keystrokes ending in
   Enter while the search box is focused - and treat it as a scan.
   =================================================================== */
(function (global) {
  "use strict";

  const Api = global.PosApi;
  const MIN_LENGTH = 3;
  const MAX_MS = 90; // keystrokes further apart than this mean a human is typing

  let buffer = "";
  let lastKeyTime = 0;
  let enabled = true;

  function reset() {
    buffer = "";
    lastKeyTime = 0;
  }

  function isScannable(value) {
    const clean = (value || "").trim();
    if (clean.length < MIN_LENGTH) return false;
    return /^[A-Za-z0-9\-_.]+$/.test(clean);
  }

  async function handleCode(code) {
    try {
      const data = await Api.barcode(code);
      if (data && data.ok && data.product) {
        const product = data.product;
        if (product.stock <= 0 && product.out_of_stock) {
          global.PosUi.toast(product.name + " is out of stock.", "err", 3000);
          return;
        }
        global.PosCart.add(product.id, 1);
        if (global.PosUi.beep) global.PosUi.beepOk();
      } else {
        global.PosUi.toast("Barcode " + code + " was not found.", "err", 3000);
      }
    } catch (err) {
      if (err.status === 404) {
        global.PosUi.toast("No product found for barcode " + code + ".", "err", 3000);
      } else {
        global.PosUi.toast(err.message, "err");
      }
    }
  }

  /** Public entry point: used by the manual "Test Barcode" box too. */
  async function scan(raw) {
    const code = (raw || "").trim();
    if (!isScannable(code)) {
      global.PosUi.toast("That does not look like a barcode.", "err", 2000);
      return false;
    }
    await handleCode(code);
    return true;
  }

  function onKeyDown(event) {
    if (!enabled) return;
    const target = event.target;
    const typing =
      target &&
      (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
    if (!typing) return;
    // Only the main search box participates; never steal from a payment field.
    if (target.id !== "pos-search") return;
    if (event.ctrlKey || event.altKey || event.metaKey) return;

    const now = Date.now();
    if (now - lastKeyTime > MAX_MS) reset();
    lastKeyTime = now;

    if (event.key === "Enter") {
      if (isScannable(buffer)) {
        event.preventDefault();
        const code = buffer;
        reset();
        target.value = "";
        global.PosCart.loadProducts("");
        handleCode(code);
      }
      return;
    }

    if (event.key.length === 1) {
      buffer += event.key;
    }
  }

  /** Manual entry: press Enter in the search box with no match => try as barcode. */
  async function onSearchEnter(event) {
    const value = event.target.value.trim();
    if (event.key !== "Enter" || !value) return;
    event.preventDefault();
    reset();
    if (isScannable(value)) {
      event.target.value = "";
      await global.PosCart.loadProducts("");
      const found = global.PosCart.state.products.some((p) => p.barcode === value || p.sku === value);
      if (!found) {
        await handleCode(value);
        return;
      }
    }
    await global.PosCart.loadProducts(value);
  }

  function bind() {
    document.addEventListener("keydown", onKeyDown);
    const search = document.getElementById("pos-search");
    if (search) search.addEventListener("keydown", onSearchEnter);
  }

  global.PosScanner = { bind, scan, isScannable };
})(window);
