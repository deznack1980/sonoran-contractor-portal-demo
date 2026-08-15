(function () {
  function money(value) { return "$" + Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function render() {
    const items = CIQ.cart.items();
    document.getElementById("checkoutLines").innerHTML = items.length ? items.map((item) => `<article class="card" style="margin-bottom:10px"><div class="card-body spread"><div><strong>${CIQ.esc(item.name || item.sku || "Product")}</strong><div class="muted">${CIQ.esc(item.supplier || "")} · Qty ${Number(item.quantity) || 1} × ${money(item.unitPrice)}</div></div><strong>${money((Number(item.quantity) || 1) * (Number(item.unitPrice) || 0))}</strong></div></article>`).join("") : CIQ.emptyState({ title: "Your cart is empty", text: "Add products before checkout." });
    document.getElementById("checkoutTotal").textContent = money(CIQ.cart.total(items));
    document.getElementById("placeOrderBtn").disabled = !items.length;
  }
  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("products.view", { title: "Checkout", active: "product-search.html" });
    if (!user) return;
    document.addEventListener("ciq:cart-change", render);
    document.getElementById("placeOrderBtn").addEventListener("click", () => {
      if (!CIQ.cart.items().length) return;
      location.href = "material-list-intake.html?from_cart=1";
    });
    render();
  });
})();
