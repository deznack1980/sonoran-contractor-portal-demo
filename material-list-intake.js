/* CorridorIQ material-list MVP: editable BOM, CSV import, and resilient local drafts. */
(function () {
  const STORAGE_KEY_PREFIX = "ciq.material-list.draft.v2";
  function storageKey() {
    return STORAGE_KEY_PREFIX + "." + (CIQ.qs("company_id") || "new") + "." + (CIQ.qs("project_id") || "general");
  }
  let rows = [];
  let sourceName = "";
  let status = "Draft";
  let materialListId = null;

  const $ = (id) => document.getElementById(id);
  const esc = (value) => CIQ.esc(value == null ? "" : value);

  function id() {
    return (globalThis.crypto && crypto.randomUUID) ? crypto.randomUUID() :
      Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  function blankRow(values) {
    return Object.assign({ id: id(), qty: 1, description: "", unit: "each", manufacturer: "", productId: null, sku: "", supplierPrice: null, quantityAvailable: null, leadTimeDays: null }, values || {});
  }

  function readForm() {
    return {
      companyName: $("companyName").value.trim(),
      projectName: $("projectName").value.trim(),
      neededBy: $("neededBy").value,
      deliveryPreference: $("deliveryPreference").value,
      requestNotes: $("requestNotes").value.trim(),
      materialListId,
      companyId: CIQ.qs("company_id"),
      projectId: CIQ.qs("project_id"),
      sourceName,
      status,
      rows: rows.map((r) => ({
        id: r.id, qty: Number(r.qty) || 0, description: String(r.description || "").trim(),
        unit: String(r.unit || "").trim(), manufacturer: String(r.manufacturer || "").trim(),
        productId: r.productId || null, sku: String(r.sku || ""), supplierPrice: r.supplierPrice,
        quantityAvailable: r.quantityAvailable, leadTimeDays: r.leadTimeDays,
      })),
      updatedAt: new Date().toISOString(),
    };
  }

  function writeForm(draft) {
    $("companyName").value = draft.companyName || "";
    $("projectName").value = draft.projectName || "";
    $("neededBy").value = draft.neededBy || "";
    $("deliveryPreference").value = draft.deliveryPreference || "delivery";
    $("requestNotes").value = draft.requestNotes || "";
    sourceName = draft.sourceName || draft.source_name || "";
    status = CIQ.titleCase(String(draft.status || "Draft").replaceAll("_", " "));
    materialListId = draft.materialListId || draft.material_list_id || draft.id || null;
    rows = Array.isArray(draft.rows) && draft.rows.length ? draft.rows.map(blankRow) : [blankRow()];
    if (sourceName) {
      $("fileName").textContent = sourceName;
      $("fileName").style.display = "inline-block";
    }
    render();
  }

  function render() {
    $("bomBody").innerHTML = rows.map((r) => `<tr data-id="${esc(r.id)}">
      <td><input aria-label="Quantity" data-field="qty" type="number" min="0" step="1" value="${esc(r.qty)}"></td>
      <td><div class="catalog-entry"><input aria-label="Description" data-field="description" value="${esc(r.description)}" placeholder="Type product, SKU, part number…"><div class="catalog-suggestions" data-suggestions="${esc(r.id)}"></div></div></td>
      <td><input aria-label="Unit" data-field="unit" value="${esc(r.unit)}" placeholder="each"></td>
      <td><input aria-label="Manufacturer" data-field="manufacturer" value="${esc(r.manufacturer)}" placeholder="Optional"></td>
      <td>${r.productId ? `<span class="badge green">Matched</span><small class="catalog-meta">${esc(r.sku)} · ${r.supplierPrice == null ? "Price unavailable" : CIQ.money(r.supplierPrice)}</small>` : `<span class="badge amber">Manual item</span><small class="catalog-meta">Search Sonoran above</small>`}</td>
      <td><button class="btn btn-sm btn-ghost" type="button" data-remove="1" aria-label="Remove item">Remove</button></td>
    </tr>`).join("");
    $("lineCount").textContent = rows.filter((r) => String(r.description || "").trim()).length;
    $("qtyCount").textContent = rows.reduce((sum, r) => sum + (Number(r.qty) || 0), 0);
    $("bomStatus").textContent = status;

    $("bomBody").querySelectorAll("input[data-field]").forEach((input) => {
      input.addEventListener("input", () => {
        const row = rows.find((r) => r.id === input.closest("tr").dataset.id);
        row[input.dataset.field] = input.dataset.field === "qty" ? input.valueAsNumber : input.value;
        if (input.dataset.field === "description") {
          row.productId = null; row.sku = ""; row.supplierPrice = null;
          scheduleCatalogSearch(input, row);
        }
        renderCounts();
      });
      input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        const rowIndex = rows.findIndex((r) => r.id === input.closest("tr").dataset.id);
        if (input.dataset.field === "qty") {
          const description = $("bomBody").querySelectorAll('input[data-field="description"]')[rowIndex];
          if (description) description.focus();
          return;
        }
        if (input.dataset.field === "description") {
          if (rowIndex === rows.length - 1) rows.push(blankRow());
          render();
          setTimeout(() => {
            const descriptions = $("bomBody").querySelectorAll('input[data-field="description"]');
            const next = descriptions[Math.min(rowIndex + 1, descriptions.length - 1)];
            if (next) next.focus();
          }, 0);
        }
      });
    });
    $("bomBody").querySelectorAll("[data-remove]").forEach((button) => {
      button.addEventListener("click", () => {
        rows = rows.filter((r) => r.id !== button.closest("tr").dataset.id);
        if (!rows.length) rows.push(blankRow());
        render();
      });
    });
  }

  let searchTimer = null;
  function scheduleCatalogSearch(input, row) {
    clearTimeout(searchTimer);
    const q = input.value.trim();
    const target = input.parentElement.querySelector("[data-suggestions]");
    if (q.length < 2) { target.innerHTML = ""; return; }
    searchTimer = setTimeout(async () => {
      try {
        const data = await CIQ.api.get("/api/products/search?q=" + encodeURIComponent(q) + "&page_size=8");
        target.innerHTML = (data.items || []).map((item, index) =>
          '<button type="button" class="catalog-option" data-option="' + index + '">' +
          '<strong>' + esc(item.product_name || item.sku) + '</strong>' +
          '<span>' + esc([item.sku, item.manufacturer, item.manufacturer_part_number].filter(Boolean).join(" · ")) + '</span>' +
          '<span>' + (item.supplier_price == null ? "Price unavailable" : CIQ.money(item.supplier_price)) +
          ' · ' + (item.quantity_available == null ? "Stock unknown" : Number(item.quantity_available).toLocaleString() + " available") + '</span></button>'
        ).join("") || '<div class="catalog-no-match">No Sonoran catalog match. Keep as a manual item.</div>';
        target.querySelectorAll("[data-option]").forEach((button) => button.addEventListener("click", () => {
          const item = data.items[Number(button.dataset.option)];
          row.productId = item.product_id; row.sku = item.sku || item.supplier_sku || "";
          row.description = item.product_name || item.description || item.sku || "";
          row.unit = item.unit_of_measure || "each"; row.manufacturer = item.manufacturer || "";
          row.supplierPrice = item.supplier_price; row.quantityAvailable = item.quantity_available;
          row.leadTimeDays = item.lead_time_days; render();
        }));
      } catch (error) {
        target.innerHTML = '<div class="catalog-no-match">Catalog search unavailable: ' + esc(error.message) + '</div>';
      }
    }, 220);
  }

  function renderCounts() {
    $("lineCount").textContent = rows.filter((r) => String(r.description || "").trim()).length;
    $("qtyCount").textContent = rows.reduce((sum, r) => sum + (Number(r.qty) || 0), 0);
  }

  async function saveDraft(showToast) {
    status = "Draft";
    const payload = readForm();
    localStorage.setItem(storageKey(), JSON.stringify(payload));
    $("bomStatus").textContent = status;
    if (payload.companyId) {
      try {
        const result = await CIQ.api.post("/api/material-lists", payload);
        materialListId = result.item.id;
        localStorage.setItem(storageKey(), JSON.stringify(readForm()));
        if (showToast) CIQ.toast("Draft saved to CorridorIQ", "success");
        return;
      } catch (error) {
        if (showToast) CIQ.toast("Saved on this device; server save failed: " + error.message, "info");
        return;
      }
    }
    if (showToast) CIQ.toast("Draft saved on this device", "success");
  }

  function validate() {
    const draft = readForm();
    const validRows = draft.rows.filter((r) => r.qty > 0 && r.description);
    if (!draft.companyName) return "Enter the contractor or company.";
    if (!draft.projectName) return "Enter a project name or job address.";
    if (!validRows.length) return "Add at least one item with a quantity and description.";
    return "";
  }

  function csvCells(line) {
    const cells = []; let current = ""; let quoted = false;
    for (let i = 0; i < line.length; i += 1) {
      const c = line[i];
      if (c === '"' && line[i + 1] === '"') { current += '"'; i += 1; }
      else if (c === '"') quoted = !quoted;
      else if (c === "," && !quoted) { cells.push(current.trim()); current = ""; }
      else current += c;
    }
    cells.push(current.trim());
    return cells;
  }

  async function parseSource() {
    const file = $("sourceFile").files[0];
    if (!file) { CIQ.toast("Choose a source file first", "error"); return; }
    if (!/\.csv$/i.test(file.name)) {
      $("intakeStatus").innerHTML = CIQ.errorBanner("For launch, automatic parsing supports CSV. Add PDF, Excel, and photo items manually.");
      return;
    }
    const lines = (await file.text()).split(/\r?\n/).filter((line) => line.trim());
    if (!lines.length) { CIQ.toast("The CSV is empty", "error"); return; }
    const parsed = lines.map(csvCells);
    const header = parsed[0].map((v) => v.toLowerCase());
    const hasHeader = header.some((v) => /qty|quantity|description|item|unit|manufacturer|brand/.test(v));
    const index = (names, fallback) => {
      const found = header.findIndex((v) => names.some((n) => v.includes(n)));
      return found >= 0 ? found : fallback;
    };
    const q = index(["qty", "quantity"], 0), d = index(["description", "item", "product"], 1);
    const u = index(["unit", "uom"], 2), m = index(["manufacturer", "brand", "mfr"], 3);
    rows = parsed.slice(hasHeader ? 1 : 0).map((cells) => blankRow({
      qty: Number(cells[q]) || 1, description: cells[d] || "", unit: cells[u] || "each",
      manufacturer: cells[m] || "",
    })).filter((r) => r.description);
    if (!rows.length) rows = [blankRow()];
    status = "Draft";
    $("intakeStatus").innerHTML = '<div class="badge blue">' + rows.length + " CSV items loaded</div>";
    render();
  }

  function submitForReview() {
    const error = validate();
    if (error) { CIQ.toast(error, "error"); return; }
    status = "Ready for review";
    localStorage.setItem(storageKey(), JSON.stringify(readForm()));
    $("bomStatus").textContent = status;
    CIQ.api.post("/api/material-lists", readForm()).then((result) => {
      materialListId = result.item.id;
      CIQ.toast("Material list submitted for review", "success");
    }).catch((error) => CIQ.toast("Saved locally; review submission failed: " + error.message, "info"));
  }

  async function priceWithSonoran() {
    const error = validate();
    if (error) { CIQ.toast(error, "error"); return; }
    const draft = readForm();
    const valid = draft.rows.filter((r) => r.qty > 0 && r.description);
    const matched = valid.filter((r) => r.productId && r.supplierPrice != null);
    const unmatched = valid.filter((r) => !r.productId || r.supplierPrice == null);
    const total = matched.reduce((sum, r) => sum + Number(r.qty) * Number(r.supplierPrice), 0);
    status = unmatched.length ? "Needs catalog review" : "Priced";
    $("bomStatus").textContent = status;
    $("pricingResults").innerHTML = '<div class="pricing-head"><div><strong>Sonoran pricing summary</strong><span>' +
      matched.length + " matched · " + unmatched.length + ' manual review</span></div><strong>' + CIQ.money(total) + '</strong></div>' +
      '<div class="pricing-lines">' + valid.map((r) => '<div><span>' + esc(r.qty + " × " + r.description) +
      (r.sku ? " · " + esc(r.sku) : "") + '</span><strong>' +
      (r.productId && r.supplierPrice != null ? CIQ.money(Number(r.qty) * Number(r.supplierPrice)) : "Review") +
      '</strong></div>').join("") + '</div>' +
      (unmatched.length ? '<div class="freshness-banner attention" style="margin-top:12px"><span class="freshness-pulse"></span><div><strong>Manual review required</strong><span>Match the highlighted items to Sonoran products before sending pricing.</span></div></div>' : "");
    const pricedPayload = readForm();
    localStorage.setItem(storageKey(), JSON.stringify(pricedPayload));
    if (pricedPayload.companyId) {
      CIQ.api.post("/api/material-lists", pricedPayload).then((result) => {
        materialListId = result.item.id;
      }).catch(() => {});
    }
    CIQ.toast(unmatched.length ? "Pricing calculated; unmatched items need review" : "Sonoran pricing calculated", unmatched.length ? "info" : "success");
  }

  async function loadServerDraft() {
    const companyId = CIQ.qs("company_id");
    if (!companyId) return false;
    const params = new URLSearchParams({ company_id: companyId });
    const projectId = CIQ.qs("project_id");
    if (projectId) params.set("project_id", projectId);
    try {
      const result = await CIQ.api.get("/api/material-lists/latest?" + params.toString());
      if (!result.item) return false;
      const item = result.item;
      writeForm({
        id: item.id,
        companyName: item.company_name,
        projectName: item.project_name || item.permit_number || "",
        neededBy: item.needed_by,
        deliveryPreference: item.delivery_preference,
        requestNotes: item.notes,
        sourceName: item.source_name,
        status: item.status,
        rows: (item.rows || []).map((row) => ({
          qty: row.quantity, description: row.description, unit: row.unit,
          manufacturer: row.manufacturer, productId: row.product_id, sku: row.sku,
          supplierPrice: row.supplier_price, quantityAvailable: row.quantity_available,
          leadTimeDays: row.lead_time_days,
        })),
      });
      return true;
    } catch (error) {
      return false;
    }
  }

  async function prefillContext() {
    const companyId = Number(CIQ.qs("company_id"));
    const projectId = String(CIQ.qs("project_id") || "");
    if (!companyId) return;
    try {
      const detail = await CIQ.api.get("/api/sales/companies/" + companyId);
      if (!$("companyName").value) $("companyName").value = detail.company.display_name || detail.company.legal_name || "";
      if (projectId && !$("projectName").value) {
        const data = await CIQ.api.get("/api/sales/companies/" + companyId + "/projects");
        const project = (data.items || data || []).find((p) => String(p.project_id) === projectId);
        if (project) $("projectName").value = project.job_address || project.permit_number || "";
      }
    } catch (error) {
      CIQ.toast("Could not prefill project details", "info");
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("projects.view_assigned", {
      title: "Material List Intake",
      subtitle: "Build a reviewable contractor bill of materials",
      active: "opportunity-board.html",
    });
    if (!user) return;

    let draft = null;
    try { draft = JSON.parse(localStorage.getItem(storageKey()) || "null"); } catch (e) {}
    writeForm(draft || { rows: [blankRow()] });
    const loadedServerDraft = await loadServerDraft();
    if (!loadedServerDraft) await prefillContext();

    $("sourceFile").addEventListener("change", () => {
      const file = $("sourceFile").files[0];
      sourceName = file ? file.name : "";
      $("fileName").textContent = sourceName;
      $("fileName").style.display = sourceName ? "inline-block" : "none";
    });
    $("dropZone").addEventListener("dragover", (e) => e.preventDefault());
    $("dropZone").addEventListener("drop", (e) => {
      e.preventDefault();
      if (e.dataTransfer.files.length) {
        $("sourceFile").files = e.dataTransfer.files;
        $("sourceFile").dispatchEvent(new Event("change"));
      }
    });
    $("addRowBtn").addEventListener("click", () => { rows.push(blankRow()); render(); });
    $("saveDraftBtn").addEventListener("click", () => saveDraft(true));
    $("parseBtn").addEventListener("click", parseSource);
    $("requestReviewBtn").addEventListener("click", submitForReview);
    $("priceBtn").addEventListener("click", priceWithSonoran);
    $("clearBtn").addEventListener("click", async () => {
      if (!(await CIQ.confirm("Clear this material list?", { danger: true, confirmLabel: "Clear" }))) return;
      localStorage.removeItem(storageKey()); writeForm({ rows: [blankRow()] });
      $("sourceFile").value = ""; $("intakeStatus").innerHTML = "";
    });
    CIQ.guardUnsaved(() => false);
  });
})();