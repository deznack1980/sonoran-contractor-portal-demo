/* Quote comparison — ranks supplier offers by lowest total fulfillment cost.
 * Cost fields are never shown to sales users (the backend omits them). */
(function () {
  const productId = CIQ.qs("product_id");

  const FLAG_LABELS = {
    missing_price: "No price on file",
    out_of_stock: "Out of stock",
    insufficient_inventory: "Insufficient inventory",
    unknown_inventory: "Inventory unknown",
    inactive_offer: "Offer inactive",
    missing_lead_time: "Lead time unknown",
    estimated_miles_default: "Miles estimated (no coordinates)",
  };

  function money(v) {
    if (v === null || v === undefined) return "—";
    return "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function lead(v) {
    if (v === null || v === undefined) return "—";
    if (Number(v) === 0) return "Same day";
    return v + " day" + (Number(v) === 1 ? "" : "s");
  }

  function renderOffer(o, recommendedId) {
    const isRec = o.supplier_id === recommendedId;
    const cls = "offer-card" + (isRec ? " recommended" : "") + (o.available ? "" : " unavailable");
    const flags = (o.warnings || []).map((w) =>
      `<span class="flag">${CIQ.esc(FLAG_LABELS[w] || w)}</span>`).join("");
    return `
      <div class="${cls}">
        <div class="offer-head">
          <div><span class="offer-rank">${o.rank ? "#" + o.rank : "—"}</span>
            &nbsp;<strong>${CIQ.esc(o.supplier)}</strong>
            ${isRec ? ' <span class="flag" style="background:rgba(212,175,55,.2);color:#d4af37">Recommended</span>' : ""}
          </div>
          <div class="offer-total">${money(o.estimated_total)}</div>
        </div>
        <div class="offer-grid">
          <div><span>Materials</span>${money(o.material_subtotal)}</div>
          <div><span>Delivery miles</span>${o.estimated_delivery_miles}</div>
          <div><span>Delivery cost</span>${money(o.estimated_delivery_cost)}</div>
          <div><span>Handling</span>${money(o.handling_cost)}</div>
          <div><span>Available</span><span class="${o.available ? "avail-yes" : "avail-no"}">${o.available ? "Yes" : "No"}</span></div>
          <div><span>Qty available</span>${o.quantity_available === null ? "?" : o.quantity_available}</div>
          <div><span>Lead time</span>${lead(o.lead_time_days)}</div>
          <div><span>Warehouse</span>${CIQ.esc(o.warehouse_location || "—")}</div>
        </div>
        ${flags ? `<div style="margin-top:8px">${flags}</div>` : ""}
      </div>`;
  }

  async function compare() {
    const quantity = Number(document.getElementById("quantity").value) || 1;
    const city = document.getElementById("city").value.trim();
    const state = document.getElementById("state").value.trim();
    const offersEl = document.getElementById("offers");
    const recEl = document.getElementById("recBanner");
    offersEl.innerHTML = `<p class="muted">Comparing suppliers…</p>`;
    recEl.innerHTML = "";
    let data;
    try {
      data = await CIQ.api.post("/api/products/quote", {
        product_id: Number(productId), quantity,
        jobsite: { city, state },
      });
    } catch (e) {
      offersEl.innerHTML = `<p class="muted">Error: ${CIQ.esc(e.message)}</p>`;
      return;
    }
    if (data.product) {
      document.getElementById("prodTitle").textContent =
        (data.product.product_name || data.product.sku) + " — quote";
    }
    recEl.innerHTML = `<div class="rec-banner">Recommended: ${CIQ.esc(data.recommended_reason)}
      <span class="muted"> · jobsite basis: ${CIQ.esc(data.jobsite_basis)}</span></div>`;
    if (!data.offers.length) {
      offersEl.innerHTML = `<p class="muted">No supplier offers for this product yet.</p>`;
      return;
    }
    offersEl.innerHTML = data.offers.map((o) =>
      renderOffer(o, data.recommended_supplier_id)).join("");
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("products.view", { title: "Quote Comparison", active: "product-search.html" });
    if (!user) return;
    const name = CIQ.qs("name");
    if (name) document.getElementById("prodTitle").textContent = name + " — quote";
    if (!productId) {
      document.getElementById("offers").innerHTML =
        `<p class="muted">No product selected. <a href="product-search.html">Search products</a>.</p>`;
      return;
    }
    document.getElementById("compareBtn").addEventListener("click", compare);
    compare();
  });
})();
