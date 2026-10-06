/* ===================================================================
   pos-cart.js - cart rendering and product grid interaction.
   =================================================================== */
(function (global) {
  "use strict";

  const Api = global.PosApi;
  const state = { cart: null, products: [], categoryId: "" };

  const el = {
    grid: document.getElementById("product-grid"),
    items: document.getElementById("cart-items"),
    empty: document.getElementById("cart-empty"),
    count: document.getElementById("cart-count"),
    subtotal: document.getElementById("t-subtotal"),
    discount: document.getElementById("t-discount"),
    tax: document.getElementById("t-tax"),
    taxRow: document.getElementById("tax-row"),
    total: document.getElementById("t-total"),
    completeTotal: document.getElementById("complete-total"),
    complete: document.getElementById("btn-complete"),
    search: document.getElementById("pos-search"),
    categories: document.getElementById("category-row"),
    removeDiscount: document.getElementById("btn-remove-discount"),
  };

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  /* ---------------- Product grid ---------------- */
  async function loadProducts(query) {
    try {
      const data = await Api.products(query || "", state.categoryId);
      state.products = data.products || [];
      renderProducts();
    } catch (err) {
      global.PosUi.toast(err.message, "err");
      renderProducts();
    }
  }

  function renderProducts() {
    const products = state.products;
    if (!products.length) {
      el.grid.innerHTML =
        '<div class="empty" style="grid-column:1/-1;">' +
        '<div class="empty-icon">&#128269;</div>' +
        "<h3>No matching products</h3>" +
        "<p>Try a different search, or ask your manager to add the product.</p></div>";
      return;
    }
    el.grid.innerHTML = products
      .map((p) => {
        const out = p.stock <= 0;
        const low = !out && p.low_stock;
        const corner = out
          ? '<span class="tag-corner out">OUT</span>'
          : low
            ? '<span class="tag-corner low">LOW</span>'
            : "";
        return (
          '<button type="button" class="product-card ' + (out ? "out " : "") + (low ? "low" : "") + '"' +
          ' data-product-id="' + p.id + '"' +
          ' data-name="' + escapeHtml(p.name) + '"' +
          (out ? " disabled" : "") + ">" +
          corner +
          '<span class="p-name">' + escapeHtml(p.name) + "</span>" +
          '<span class="p-price">' + escapeHtml(p.price) + "</span>" +
          (global.IAB.showPrices ? '<span class="p-stock">Stock: ' + p.stock + "</span>" : "") +
          "</button>"
        );
      })
      .join("");
  }

  /* ---------------- Cart ---------------- */
  function renderCart(cart) {
    state.cart = cart;
    const hasItems = cart.items && cart.items.length > 0;

    if (!hasItems) {
      el.items.innerHTML = "";
      el.items.appendChild(el.empty);
      el.empty.hidden = false;
    } else {
      el.empty.hidden = true;
      el.items.innerHTML = cart.items.map((line) => renderLine(line)).join("");
    }

    el.count.textContent = cart.item_count + " item" + (cart.item_count === 1 ? "" : "s");
    el.subtotal.textContent = cart.subtotal;
    el.discount.textContent = cart.discount;
    el.tax.textContent = cart.tax;
    el.taxRow.style.display = cart.tax && cart.tax !== "₦0.00" ? "flex" : "none";
    el.total.textContent = cart.total;
    el.completeTotal.textContent = cart.total;
    el.complete.disabled = !hasItems || cart.has_unavailable;
    el.complete.title = cart.has_unavailable ? "A product in this sale is no longer available." : "";

    if (el.removeDiscount) el.removeDiscount.hidden = !cart.discount_spec;
  }

  function renderLine(line) {
    return (
      '<div class="cart-line' + (line.unavailable ? " unavailable" : "") + '" data-product-id="' + line.product_id + '">' +
      '<span class="cl-name">' + escapeHtml(line.name) + "</span>" +
      '<span class="cl-total">' + escapeHtml(line.line_total) + "</span>" +
      '<span class="cl-meta">' + line.quantity + " &times; " + escapeHtml(line.unit_price) +
      (line.unavailable ? " &mdash; NO LONGER AVAILABLE" : "") +
      (line.low_stock ? " &mdash; low stock" : "") + "</span>" +
      '<span class="qty-ctl">' +
      '<button type="button" class="qty-btn" data-action="dec" aria-label="Decrease quantity">&minus;</button>' +
      '<span class="qty-value">' + line.quantity + "</span>" +
      '<button type="button" class="qty-btn" data-action="inc" aria-label="Increase quantity">+</button>' +
      '<button type="button" class="line-remove" data-action="remove">Remove</button>' +
      "</span>" +
      "</div>"
    );
  }

  async function refresh() {
    try {
      renderCart(await Api.cart());
    } catch (err) {
      global.PosUi.toast(err.message, "err");
    }
  }

  async function add(productId, quantity, sourceEvent) {
    try {
      const cart = await Api.addToCart(productId, quantity || 1);
      renderCart(cart);
      const product = findProduct(productId);
      global.PosUi.toast((product ? product.name : "Item") + " added", "ok", 1200);
    } catch (err) {
      global.PosUi.toast(err.message, "err", 3200);
    }
  }

  async function setQty(productId, quantity) {
    try {
      renderCart(await Api.setQuantity(productId, quantity));
    } catch (err) {
      global.PosUi.toast(err.message, "err", 3200);
      refresh();
    }
  }

  async function removeLine(productId) {
    try {
      renderCart(await Api.removeLine(productId));
    } catch (err) {
      global.PosUi.toast(err.message, "err");
    }
  }

  async function clear() {
    if (!state.cart || !state.cart.items || !state.cart.items.length) return;
    if (!confirm("Remove all items from this sale?")) return;
    try {
      renderCart(await Api.clearCart());
      global.PosUi.toast("Cart cleared", "ok", 1200);
    } catch (err) {
      global.PosUi.toast(err.message, "err");
    }
  }

  async function applyDiscount(type, value) {
    const cart = await Api.applyDiscount(type, value);
    renderCart(cart);
    return cart;
  }

  async function clearDiscount() {
    renderCart(await Api.clearDiscount());
  }

  function findProduct(productId) {
    return state.products.find((p) => String(p.id) === String(productId)) || null;
  }

  /* ---------------- Events ---------------- */
  function bind() {
    el.grid.addEventListener("click", (event) => {
      const card = event.target.closest(".product-card");
      if (!card || card.disabled) return;
      add(card.dataset.productId, 1);
    });

    el.items.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const productId = button.closest(".cart-line").dataset.productId;
      const action = button.dataset.action;
      if (action === "inc") setQty(productId, currentQty(productId) + 1);
      else if (action === "dec") {
        const next = currentQty(productId) - 1;
        if (next <= 0) removeLine(productId);
        else setQty(productId, next);
      } else if (action === "remove") removeLine(productId);
    });

    el.categories.addEventListener("click", (event) => {
      const button = event.target.closest(".cat-btn");
      if (!button) return;
      el.categories.querySelectorAll(".cat-btn").forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      button.classList.add("active");
      button.setAttribute("aria-selected", "true");
      state.categoryId = button.dataset.category;
      loadProducts(el.search.value.trim());
    });

    let searchTimer = null;
    el.search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadProducts(el.search.value.trim()), 180);
    });

    document.getElementById("btn-clear-search").addEventListener("click", () => {
      el.search.value = "";
      loadProducts("");
      el.search.focus();
    });

    document.getElementById("btn-clear-cart").addEventListener("click", clear);

    if (el.removeDiscount) {
      el.removeDiscount.addEventListener("click", async () => {
        try {
          await clearDiscount();
          global.PosUi.toast("Discount removed", "ok", 1400);
        } catch (err) {
          global.PosUi.toast(err.message, "err");
        }
      });
    }
  }

  function currentQty(productId) {
    if (!state.cart) return 0;
    const line = state.cart.items.find((i) => String(i.product_id) === String(productId));
    return line ? line.quantity : 0;
  }

  global.PosCart = { bind, refresh, renderCart, loadProducts, add, clear, state };
})(window);
