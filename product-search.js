/* Product search — sales-facing. No cost fields are ever returned to sales
 * users (the backend omits them), so this view only shows selling price. */
(function () {
  let user = null;

  function fmtMoney(v) {
    if (v === null || v === undefined) return '<span class="muted">—</span>';
    return "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function fmtLead(v) {
    if (v === null || v === undefined) return '<span class="muted">—</span>';
    if (Number(v) === 0) return "Same day";
    return CIQ.esc(v) + " day" + (Number(v) === 1 ? "" : "s");
  }
  function fmtQty(v) {
    if (v === null || v === undefined) return '<span class="muted">?</span>';
    return Number(v).toLocaleString();
  }

  async function runSearch() {
    const q = document.getElementById("q").value.trim();
    const category = document.getElementById("category").value.trim();
    const inStock = document.getElementById("inStock").checked;
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (category) params.set("category", category);
    if (inStock) params.set("in_stock", "1");
    params.set("page_size", "100");

    const tbody = document.querySelector("#productsTable tbody");
    tbody.innerHTML = `<tr><td colspan="10" class="muted">Searching…</td></tr>`;
    let data;
    try {
      data = await CIQ.api.get("/api/products/search?" + params.toString());
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="10" class="muted">Error: ${CIQ.esc(e.message)}</td></tr>`;
      return;
    }
    document.getElementById("resultCount").textContent =
      `${data.total} match${data.total === 1 ? "" : "es"} (showing ${data.items.length})`;
    if (!data.items.length) {
      tbody.innerHTML = `<tr><td colspan="10" class="muted">No products found.</td></tr>`;
      return;
    }
    tbody.innerHTML = data.items.map((it) => `
      <tr>
        <td>${CIQ.esc(it.product_name || "")}</td>
        <td>${CIQ.esc(it.sku || "")}</td>
        <td>${CIQ.esc(it.manufacturer_part_number || "")}</td>
        <td class="muted">${CIQ.esc(it.category || "")}</td>
        <td>${CIQ.esc(it.supplier || "")}</td>
        <td>${fmtMoney(it.supplier_price)}</td>
        <td>${fmtQty(it.quantity_available)}</td>
        <td>${fmtLead(it.lead_time_days)}</td>
        <td class="muted">${CIQ.esc(it.warehouse_location || "")}</td>
        <td><div class="row"><button class="btn btn-sm btn-primary" type="button" data-add-cart data-product="${CIQ.esc(JSON.stringify(it))}">Add</button><a class="btn btn-sm" href="quote-compare.html?product_id=${it.product_id}&name=${encodeURIComponent(it.product_name || it.sku)}">Compare</a></div></td>
      </tr>`).join("");
  }

  document.addEventListener("DOMContentLoaded", async () => {
    user = await CIQ.guard("products.view", { title: "Products", subtitle: "Supplier catalog search", active: "product-search.html" });
    if (!user) return;
    document.getElementById("searchBtn").addEventListener("click", runSearch);
    document.querySelector("#productsTable tbody").addEventListener("click", (event) => {
      const button = event.target.closest("[data-add-cart]");
      if (!button) return;
      const item = JSON.parse(button.dataset.product);
      CIQ.cart.add({ productId: item.product_id, supplierId: item.supplier_id, name: item.product_name, sku: item.sku, supplier: item.supplier, unitPrice: item.supplier_price, quantity: 1 });
      CIQ.toast("Added to cart", "success");
    });
    document.getElementById("q").addEventListener("keydown", (e) => {
      if (e.key === "Enter") runSearch();
    });
    const preset = CIQ.qs("q");
    if (preset) { document.getElementById("q").value = preset; }
    runSearch();
  });
})();
