/* ===================================================================
   pos-api.js - every call to the POS backend.
   The server re-validates everything; this module never trusts local state.
   =================================================================== */
(function (global) {
  "use strict";

  const CSRF = (global.IAB && global.IAB.csrfToken) || "";

  async function request(url, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    opts.headers = Object.assign({ "X-CSRF-Token": CSRF, "X-Requested-With": "fetch" }, opts.headers);
    if (opts.body && !(opts.body instanceof FormData)) {
      opts.headers["Content-Type"] = "application/json";
    }

    let response;
    try {
      response = await fetch(url, opts);
    } catch (err) {
      throw new ApiError("Cannot reach the POS server. Please restart the application.", 0);
    }

    let payload = null;
    const type = response.headers.get("content-type") || "";
    if (type.includes("application/json")) {
      payload = await response.json().catch(() => null);
    }

    if (!response.ok) {
      const message = (payload && payload.error) || defaultMessage(response.status);
      throw new ApiError(message, response.status, payload);
    }
    return payload;
  }

  function defaultMessage(status) {
    if (status === 403) return "You do not have permission to do that. Please ask your manager.";
    if (status === 400) return "That request could not be completed.";
    if (status === 404) return "Not found.";
    if (status >= 500) return "Something went wrong. The problem has been recorded.";
    return "Request failed.";
  }

  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload;
    }
  }

  const form = (data) => {
    const body = new FormData();
    Object.keys(data || {}).forEach((key) => {
      if (data[key] !== undefined && data[key] !== null) body.append(key, data[key]);
    });
    return body;
  };

  global.PosApi = {
    ApiError,
    // catalog
    products: (query, categoryId) =>
      request(`/api/pos/products?q=${encodeURIComponent(query || "")}&category_id=${categoryId || ""}`),
    categories: () => request("/api/pos/categories"),
    barcode: (code) => request(`/api/pos/barcode/${encodeURIComponent(code)}`),
    // cart
    cart: () => request("/api/pos/cart"),
    addToCart: (productId, quantity) =>
      request("/api/pos/cart/add", { method: "POST", body: form({ product_id: productId, quantity: quantity || 1 }) }),
    setQuantity: (productId, quantity) =>
      request("/api/pos/cart/quantity", { method: "POST", body: form({ product_id: productId, quantity }) }),
    removeLine: (productId) =>
      request("/api/pos/cart/remove", { method: "POST", body: form({ product_id: productId }) }),
    clearCart: () => request("/api/pos/cart/clear", { method: "POST", body: form({}) }),
    applyDiscount: (type, value) =>
      request("/api/pos/cart/discount", { method: "POST", body: form({ type, value }) }),
    clearDiscount: () => request("/api/pos/cart/discount/clear", { method: "POST", body: form({}) }),
    // checkout
    quote: (amountReceived, method) =>
      request("/api/pos/quote", { method: "POST", body: form({ amount_received: amountReceived, payment_method: method }) }),
    checkout: (payload) => request("/api/pos/checkout", { method: "POST", body: form(payload) }),
    // held sales
    hold: (label) => request("/api/pos/hold", { method: "POST", body: form({ label: label || "" }) }),
    heldList: () => request("/api/pos/held"),
    retrieveHeld: (id) => request(`/api/pos/held/${id}/retrieve`, { method: "POST", body: form({}) }),
    cancelHeld: (id) => request(`/api/pos/held/${id}/delete`, { method: "POST", body: form({}) }),
    // shift
    shiftStatus: () => request("/api/pos/shift/status"),
    openShift: (openingCash) => request("/api/pos/shift/open", { method: "POST", body: form({ opening_cash: openingCash }) }),
    closeShift: (actualCash, note) =>
      request("/api/pos/shift/close", { method: "POST", body: form({ actual_cash: actualCash, note: note || "" }) }),
    // receipts
    reprint: (saleId) => request(`/pos/receipt/${saleId}/print`, { method: "POST", body: form({}) }),
  };
})(window);
