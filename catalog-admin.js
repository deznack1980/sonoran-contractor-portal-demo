/* Catalog admin — add suppliers, upload/preview/import price files, tune
 * delivery assumptions. Gated by supplier_pricing.manage; the API enforces it. */
(function () {
  const GENERIC_FIELDS = [
    ["sku", "SKU *"], ["product_name", "Product name"], ["description", "Description"],
    ["category", "Category"], ["price", "Selling price *"], ["cost", "Cost price"],
    ["quantity", "Quantity"], ["manufacturer", "Manufacturer"],
    ["manufacturer_part_number", "Mfr part #"], ["unit_of_measure", "Unit of measure"],
    ["lead_time", "Lead time"], ["supplier_sku", "Supplier SKU"], ["in_stock", "In stock"],
  ];
  const CFG_KEYS = ["delivery_per_mile", "delivery_base_fee", "delivery_min_charge",
    "delivery_markup_pct", "delivery_handling_cost", "delivery_road_factor",
    "delivery_default_miles"];

  let uploadB64 = null;
  let uploadName = null;
  let uploadHeaders = [];

  function msg(el, text, ok) {
    document.getElementById(el).innerHTML =
      `<div class="msg ${ok ? "ok" : "err"}">${CIQ.esc(text)}</div>`;
  }

  async function loadSuppliers() {
    const data = await CIQ.api.get("/api/admin/suppliers");
    document.querySelector("#suppliersTable tbody").innerHTML = data.items.map((s) => `
      <tr><td>${CIQ.esc(s.name)}</td><td>${CIQ.esc(s.code)}</td>
      <td>${CIQ.esc([s.city, s.state].filter(Boolean).join(", "))}</td>
      <td>${s.latitude != null ? s.latitude + ", " + s.longitude : '<span class="muted">—</span>'}</td></tr>`
    ).join("") || `<tr><td colspan="4" class="muted">No suppliers yet.</td></tr>`;
  }

  async function loadHistory() {
    const data = await CIQ.api.get("/api/admin/catalog/imports");
    document.querySelector("#historyTable tbody").innerHTML = data.items.map((h) => `
      <tr><td>${CIQ.esc((h.finished_at || "").replace("T", " ").slice(0, 16))}</td>
      <td>${CIQ.esc(h.supplier_name || h.supplier_code || "")}</td>
      <td class="muted">${CIQ.esc((h.source_file || "").split(/[\\/]/).pop())}</td>
      <td>${h.imported}</td><td>${h.updated}</td><td>${h.zero_price_excluded}</td><td>${h.failed}</td></tr>`
    ).join("") || `<tr><td colspan="7" class="muted">No imports yet.</td></tr>`;
  }

  async function loadConfig() {
    const data = await CIQ.api.get("/api/admin/pricing/delivery");
    CFG_KEYS.forEach((k) => {
      const el = document.getElementById("cfg_" + k);
      if (el && data.config[k] !== undefined) el.value = data.config[k];
    });
  }

  async function addSupplier() {
    const body = {
      name: document.getElementById("supName").value.trim(),
      code: document.getElementById("supCode").value.trim(),
      city: document.getElementById("supCity").value.trim(),
      state: document.getElementById("supState").value.trim(),
      warehouse_address: document.getElementById("supAddr").value.trim(),
      latitude: parseFloat(document.getElementById("supLat").value) || null,
      longitude: parseFloat(document.getElementById("supLng").value) || null,
    };
    try {
      await CIQ.api.post("/api/admin/suppliers", body);
      msg("supMsg", `Added supplier "${body.name}".`, true);
      await loadSuppliers();
    } catch (e) { msg("supMsg", e.message, false); }
  }

  async function saveConfig() {
    const updates = {};
    CFG_KEYS.forEach((k) => {
      const v = document.getElementById("cfg_" + k).value.trim();
      if (v !== "") updates[k] = Number(v);
    });
    try {
      await CIQ.api.patch("/api/admin/pricing/delivery", { updates });
      msg("cfgMsg", "Delivery assumptions saved.", true);
    } catch (e) { msg("cfgMsg", e.message, false); }
  }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => {
        const b64 = r.result.split(",")[1];
        resolve(b64);
      };
      r.onerror = reject;
      r.readAsDataURL(file);
    });
  }

  function buildMapping() {
    const sel = document.querySelectorAll("#mappingArea select[data-field]");
    if (!sel.length) return null;
    const mapping = {};
    sel.forEach((s) => { if (s.value) mapping[s.dataset.field] = s.value; });
    return mapping;
  }

  function renderMapping(headers, profile) {
    const area = document.getElementById("mappingArea");
    if (profile === "sonoran_magento") { area.innerHTML = ""; return; }
    if (!headers.length) { area.innerHTML = ""; return; }
    const opts = ['<option value="">—</option>']
      .concat(headers.map((h) => `<option value="${CIQ.esc(h)}">${CIQ.esc(h)}</option>`)).join("");
    area.innerHTML = `<p class="muted" style="font-size:12px;margin-top:6px">Map spreadsheet columns to product fields:</p>
      <div class="row">` +
      GENERIC_FIELDS.map(([f, label]) => `
        <label class="field" style="min-width:150px">${label}
          <select data-field="${f}">${opts}</select></label>`).join("") + `</div>`;
    // Auto-guess by case-insensitive header match.
    document.querySelectorAll("#mappingArea select[data-field]").forEach((s) => {
      const guess = headers.find((h) => h.toLowerCase().replace(/[^a-z]/g, "") ===
        s.dataset.field.replace(/[^a-z]/g, ""));
      if (guess) s.value = guess;
    });
  }

  async function preview() {
    const supplier = document.getElementById("upSupplier").value.trim();
    const profile = document.getElementById("upProfile").value;
    if (!uploadB64) { msg("upMsg", "Choose a file first.", false); return; }
    if (!supplier) { msg("upMsg", "Enter a supplier code.", false); return; }
    msg("upMsg", "Parsing…", true);
    try {
      const data = await CIQ.api.post("/api/admin/catalog/preview", {
        filename: uploadName, content_base64: uploadB64, supplier,
        profile: profile || undefined, mapping: buildMapping() || undefined,
      });
      uploadHeaders = data.headers || [];
      if (!document.querySelector("#mappingArea select")) renderMapping(uploadHeaders, data.profile);
      const c = data.counts;
      document.getElementById("previewResult").innerHTML = `
        <div class="counts">
          <div><b>${c.new_products}</b><span>New products</span></div>
          <div><b>${c.updated_offers}</b><span>Updated offers</span></div>
          <div><b>${c.zero_price_excluded}</b><span>Zero-price excluded</span></div>
          <div><b>${c.duplicate_skus}</b><span>Duplicate SKUs</span></div>
          <div><b>${c.missing_mpn}</b><span>Missing mfr part #</span></div>
          <div><b>${c.skipped}</b><span>Skipped</span></div>
        </div>
        <p class="muted" style="font-size:12px">${data.total_rows} total rows · profile ${CIQ.esc(data.profile)}</p>`;
      msg("upMsg", "Preview ready. Review counts, then approve.", true);
      document.getElementById("importBtn").disabled = false;
    } catch (e) { msg("upMsg", e.message, false); }
  }

  async function doImport() {
    const supplier = document.getElementById("upSupplier").value.trim();
    const profile = document.getElementById("upProfile").value;
    document.getElementById("importBtn").disabled = true;
    msg("upMsg", "Importing…", true);
    try {
      const stats = await CIQ.api.post("/api/admin/catalog/import", {
        filename: uploadName, content_base64: uploadB64, supplier,
        profile: profile || undefined, mapping: buildMapping() || undefined,
      });
      msg("upMsg", `Imported ${stats.imported}, updated ${stats.updated}, excluded ${stats.zero_price_excluded}.`, true);
      await loadHistory();
      await loadSuppliers();
    } catch (e) { msg("upMsg", e.message, false); }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("supplier_pricing.manage", { title: "Catalog Admin", subtitle: "Suppliers & pricing", active: "catalog-admin.html" });
    if (!user) return;
    document.getElementById("addSupplierBtn").addEventListener("click", addSupplier);
    document.getElementById("saveCfgBtn").addEventListener("click", saveConfig);
    document.getElementById("previewBtn").addEventListener("click", preview);
    document.getElementById("importBtn").addEventListener("click", doImport);
    document.getElementById("upFile").addEventListener("change", async (e) => {
      const file = e.target.files[0];
      document.getElementById("importBtn").disabled = true;
      document.getElementById("previewResult").innerHTML = "";
      document.getElementById("mappingArea").innerHTML = "";
      if (!file) { uploadB64 = null; return; }
      uploadName = file.name;
      uploadB64 = await readFile(file);
      msg("upMsg", `Loaded ${file.name} (${(file.size / 1024).toFixed(0)} KB). Click Preview.`, true);
    });
    document.getElementById("upProfile").addEventListener("change", () => {
      renderMapping(uploadHeaders, document.getElementById("upProfile").value);
    });
    await Promise.all([loadSuppliers(), loadHistory(), loadConfig()]);
  });
})();
