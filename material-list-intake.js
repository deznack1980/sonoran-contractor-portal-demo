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
  let supplierOptions = [];
  let quoteRequests = [];
  let catalogPopup = null, catalogInput = null, catalogRow = null;
  let catalogItems = [], catalogIndex = -1, catalogRequest = 0;
  let dirty = false, autosaveTimer = null;

  const $ = (id) => document.getElementById(id);
  const esc = (value) => CIQ.esc(value == null ? "" : value);

  function setDraftSaveState(message) {
    const el = $("draftSaveState");
    if (el) el.textContent = message;
  }

  function persistLocalDraft(message) {
    if (!$("companyName")) return;
    localStorage.setItem(storageKey(), JSON.stringify(readForm()));
    dirty = false;
    setDraftSaveState(message || "Saved on this device");
  }

  function scheduleLocalSave() {
    dirty = true;
    setDraftSaveState("Saving…");
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(() => persistLocalDraft(), 350);
  }

  function id() {
    return (globalThis.crypto && crypto.randomUUID) ? crypto.randomUUID() :
      Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  function dateFromToday(days) {
    const date = new Date();
    date.setDate(date.getDate() + days);
    return date.toISOString().slice(0, 10);
  }

  function blankRow(values) {
    return Object.assign({ id: id(), qty: 1, description: "", unit: "each", manufacturer: "", allowSubstitution: true, productId: null, sku: "", supplierPrice: null, quantityAvailable: null, leadTimeDays: null }, values || {});
  }

  function ensureCatalogPopup() {
    if (catalogPopup) return catalogPopup;
    catalogPopup = document.createElement("div");
    catalogPopup.className = "catalog-suggestions catalog-suggestions-portal";
    catalogPopup.setAttribute("role", "listbox");
    catalogPopup.hidden = true;
    document.body.appendChild(catalogPopup);
    catalogPopup.addEventListener("pointerdown", (event) => {
      const option = event.target.closest("[data-option]");
      if (!option) return;
      event.preventDefault();
      selectCatalogItem(Number(option.dataset.option));
    });
    return catalogPopup;
  }

  function closeCatalogPopup() {
    if (!catalogPopup) return;
    if (catalogInput) catalogInput.setAttribute("aria-expanded", "false");
    catalogPopup.hidden = true; catalogPopup.innerHTML = "";
    catalogInput = null; catalogRow = null; catalogItems = []; catalogIndex = -1;
  }

  function positionCatalogPopup() {
    if (!catalogPopup || catalogPopup.hidden || !catalogInput || !catalogInput.isConnected) return;
    const rect = catalogInput.getBoundingClientRect(), gap = 5, margin = 8;
    const visual = window.visualViewport;
    const viewportWidth = visual ? visual.width : Math.min(window.innerWidth, document.documentElement.clientWidth || window.innerWidth);
    const viewportHeight = visual ? visual.height : Math.min(window.innerHeight, document.documentElement.clientHeight || window.innerHeight);
    const viewportLeft = visual ? visual.offsetLeft : 0;
    const viewportTop = visual ? visual.offsetTop : 0;
    const desired = Math.min(320, Math.max(120, catalogPopup.scrollHeight));
    const below = viewportHeight - rect.bottom - margin, above = rect.top - margin;
    const openAbove = below < Math.min(desired, 180) && above > below;
    const available = Math.max(96, Math.min(320, (openAbove ? above : below) - gap));
    const width = Math.min(Math.max(rect.width, 280), viewportWidth - margin * 2);
    const screenLeft = Math.max(margin, Math.min(rect.left, viewportWidth - margin - width));
    const screenTop = openAbove ? Math.max(margin, rect.top - gap - available) : rect.bottom + gap;
    Object.assign(catalogPopup.style, { left: viewportLeft + screenLeft + "px", width: width + "px", maxHeight: available + "px", top: viewportTop + screenTop + "px", bottom: "auto" });
    catalogPopup.classList.toggle("opens-above", openAbove);
  }

  function highlightCatalogItem(index) {
    if (!catalogItems.length) return;
    catalogIndex = (index + catalogItems.length) % catalogItems.length;
    catalogPopup.querySelectorAll("[data-option]").forEach((option, i) => {
      option.classList.toggle("active", i === catalogIndex);
      option.setAttribute("aria-selected", String(i === catalogIndex));
      if (i === catalogIndex) option.scrollIntoView({ block: "nearest" });
    });
  }

  function selectCatalogItem(index) {
    const item = catalogItems[index];
    if (!item || !catalogRow) return;
    catalogRow.productId = item.product_id; catalogRow.sku = item.sku || item.supplier_sku || "";
    catalogRow.description = item.product_name || item.description || item.sku || "";
    catalogRow.unit = item.unit_of_measure || "each"; catalogRow.manufacturer = item.manufacturer || "";
    catalogRow.supplierPrice = item.supplier_price; catalogRow.quantityAvailable = item.quantity_available;
    catalogRow.leadTimeDays = item.lead_time_days;
    closeCatalogPopup(); render(); scheduleLocalSave();
  }

  function readForm() {
    return {
      companyName: $("companyName").value.trim(),
      projectName: $("projectName").value.trim(),
      neededBy: $("neededBy").value,
      quoteNeededBy: $("quoteNeededBy").value,
      jobsitePostalCode: $("jobsitePostalCode").value.trim(),
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
        allowSubstitution: r.allowSubstitution !== false,
      })),
      updatedAt: new Date().toISOString(),
    };
  }

  function writeForm(draft) {
    $("companyName").value = draft.companyName || "";
    $("projectName").value = draft.projectName || "";
    $("neededBy").value = draft.neededBy || "";
    $("quoteNeededBy").value = draft.quoteNeededBy || draft.quote_needed_by || "";
    $("jobsitePostalCode").value = draft.jobsitePostalCode || draft.jobsite_postal_code || "";
    $("deliveryPreference").value = draft.deliveryPreference || "delivery";
    $("requestNotes").value = draft.requestNotes || "";
    sourceName = draft.sourceName || draft.source_name || "";
    status = CIQ.titleCase(String(draft.status || "Draft").replaceAll("_", " "));
    materialListId = draft.materialListId || draft.material_list_id || draft.id || null;
    rows = Array.isArray(draft.rows) && draft.rows.length ? draft.rows.map(blankRow) : [blankRow()];
    $("fileName").textContent = sourceName;
    $("fileName").style.display = sourceName ? "inline-block" : "none";
    render();
    dirty = false;
    setDraftSaveState("Saved on this device");
  }

  function render() {
    closeCatalogPopup();
    $("bomBody").innerHTML = rows.map((r) => `<tr data-id="${esc(r.id)}">
      <td data-label="Quantity"><input aria-label="Quantity" data-field="qty" type="number" min="0" step="1" value="${esc(r.qty)}"></td>
      <td data-label="Item"><div class="catalog-entry"><input aria-label="Description" data-field="description" value="${esc(r.description)}" placeholder="Type product, SKU, part number…" autocomplete="off" aria-haspopup="listbox" aria-expanded="false"></div></td>
      <td data-label="Unit"><input aria-label="Unit" data-field="unit" value="${esc(r.unit)}" placeholder="each"></td>
      <td data-label="Manufacturer"><input aria-label="Manufacturer" data-field="manufacturer" value="${esc(r.manufacturer)}" placeholder="Optional"></td>
      <td data-label="Alternates"><select aria-label="Alternates" data-field="allowSubstitution"><option value="true" ${r.allowSubstitution !== false ? "selected" : ""}>Allowed</option><option value="false" ${r.allowSubstitution === false ? "selected" : ""}>Exact only</option></select></td>
      <td data-label="Catalog match">${r.productId ? `<span><span class="badge green">Matched</span><small class="catalog-meta">${esc(r.sku)} · ${r.supplierPrice == null ? "Price unavailable" : CIQ.money(r.supplierPrice)}</small></span>` : `<span><span class="badge amber">Manual item</span><small class="catalog-meta">Search Sonoran above</small></span>`}</td>
      <td data-label="Action"><button class="btn btn-sm btn-ghost" type="button" data-remove="1" aria-label="Remove item">Remove</button></td>
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
        scheduleLocalSave();
      });
      input.addEventListener("keydown", (event) => {
        if (input.dataset.field === "description" && catalogInput === input && catalogPopup && !catalogPopup.hidden) {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); highlightCatalogItem(catalogIndex + (event.key === "ArrowDown" ? 1 : -1)); return; }
          if (event.key === "Escape") { event.preventDefault(); closeCatalogPopup(); return; }
          if (event.key === "Enter" && catalogIndex >= 0) { event.preventDefault(); selectCatalogItem(catalogIndex); return; }
        }
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
          scheduleLocalSave();
          setTimeout(() => {
            const descriptions = $("bomBody").querySelectorAll('input[data-field="description"]');
            const next = descriptions[Math.min(rowIndex + 1, descriptions.length - 1)];
            if (next) next.focus();
          }, 0);
        }
      });
      if (input.dataset.field === "description") {
        input.addEventListener("focus", () => { const row = rows.find((r) => r.id === input.closest("tr").dataset.id); if (input.value.trim().length >= 2) scheduleCatalogSearch(input, row); });
        input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) closeCatalogPopup(); }, 0));
      }
    });
    $("bomBody").querySelectorAll('select[data-field="allowSubstitution"]').forEach((select) => {
      select.addEventListener("change", () => {
        const row = rows.find((r) => r.id === select.closest("tr").dataset.id);
        row.allowSubstitution = select.value === "true";
        scheduleLocalSave();
      });
    });
    $("bomBody").querySelectorAll("[data-remove]").forEach((button) => {
      button.addEventListener("click", () => {
        rows = rows.filter((r) => r.id !== button.closest("tr").dataset.id);
        if (!rows.length) rows.push(blankRow());
        render();
        scheduleLocalSave();
      });
    });
  }

  let searchTimer = null;
  function scheduleCatalogSearch(input, row) {
    clearTimeout(searchTimer);
    const q = input.value.trim();
    const target = ensureCatalogPopup();
    catalogInput = input; catalogRow = row; catalogIndex = -1;
    if (q.length < 2) { closeCatalogPopup(); return; }
    const request = ++catalogRequest;
    searchTimer = setTimeout(async () => {
      try {
        const data = await CIQ.api.get("/api/products/search?q=" + encodeURIComponent(q) + "&page_size=8");
        if (request !== catalogRequest || catalogInput !== input || input.value.trim() !== q) return;
        catalogItems = data.items || [];
        target.innerHTML = (data.items || []).map((item, index) =>
          '<button type="button" role="option" aria-selected="false" class="catalog-option" data-option="' + index + '">' +
          '<strong>' + esc(item.product_name || item.sku) + '</strong>' +
          '<span>' + esc([item.sku, item.manufacturer, item.manufacturer_part_number].filter(Boolean).join(" · ")) + '</span>' +
          '<span>' + (item.supplier_price == null ? "Price unavailable" : CIQ.money(item.supplier_price)) +
          ' · ' + (item.quantity_available == null ? "Stock unknown" : Number(item.quantity_available).toLocaleString() + " available") + '</span></button>'
        ).join("") || '<div class="catalog-no-match">No Sonoran catalog match. Keep as a manual item.</div>';
        target.hidden = false;
        input.setAttribute("aria-expanded", "true");
        positionCatalogPopup();
      } catch (error) {
        if (request !== catalogRequest) return;
        target.innerHTML = '<div class="catalog-no-match">Catalog search unavailable: ' + esc(error.message) + '</div>';
        target.hidden = false;
        input.setAttribute("aria-expanded", "true");
        positionCatalogPopup();
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
    dirty = false;
    clearTimeout(autosaveTimer);
    setDraftSaveState("Saved on this device");
    $("bomStatus").textContent = status;
    if (payload.companyId) {
      try {
        const result = await CIQ.api.post("/api/material-lists", payload);
        materialListId = result.item.id;
        localStorage.setItem(storageKey(), JSON.stringify(readForm()));
        setDraftSaveState("Saved to CorridorIQ");
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
      $("intakeStatus").innerHTML = CIQ.errorBanner("Automatic import requires a CSV file.");
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
    scheduleLocalSave();
  }

  function submitForReview() {
    const error = validate();
    if (error) { CIQ.toast(error, "error"); return; }
    status = "Ready for review";
    localStorage.setItem(storageKey(), JSON.stringify(readForm()));
    dirty = false;
    clearTimeout(autosaveTimer);
    setDraftSaveState("Saved on this device");
    $("bomStatus").textContent = status;
    CIQ.api.post("/api/material-lists", readForm()).then((result) => {
      materialListId = result.item.id;
      localStorage.setItem(storageKey(), JSON.stringify(readForm()));
      setDraftSaveState("Saved to CorridorIQ");
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
    dirty = false;
    clearTimeout(autosaveTimer);
    setDraftSaveState("Saved on this device");
    if (pricedPayload.companyId) {
      CIQ.api.post("/api/material-lists", pricedPayload).then((result) => {
        materialListId = result.item.id;
        localStorage.setItem(storageKey(), JSON.stringify(readForm()));
        setDraftSaveState("Saved to CorridorIQ");
      }).catch((error) => {
        CIQ.toast("Pricing is visible, but the server save failed: " + error.message, "info");
      });
    }
    CIQ.toast(unmatched.length ? "Pricing calculated; unmatched items need review" : "Sonoran pricing calculated", unmatched.length ? "info" : "success");
  }

  function rfqBadge(statusValue) {
    return ({ prepared: "blue", sent: "amber", responded: "green", awarded: "green",
      declined: "red", cancelled: "slate" })[statusValue] || "slate";
  }

  function rfqStatus(statusValue) {
    return CIQ.titleCase(String(statusValue || "prepared").replaceAll("_", " "));
  }

  function rfqMailto(request) {
    if (!request.recipient_email) return "";
    return "mailto:" + encodeURIComponent(request.recipient_email) +
      "?subject=" + encodeURIComponent(request.subject || "") +
      "&body=" + encodeURIComponent(request.message || "");
  }

  async function copyRfq(request) {
    const text = "To: " + (request.recipient_email || request.supplier_name || "") +
      "\nSubject: " + request.subject + "\n\n" + request.message;
    try {
      await navigator.clipboard.writeText(text);
    } catch (error) {
      const area = document.createElement("textarea");
      area.value = text; document.body.appendChild(area); area.select();
      document.execCommand("copy"); area.remove();
    }
    CIQ.toast("RFQ copied", "success");
  }

  async function updateRfq(requestId, payload, successMessage) {
    try {
      await CIQ.api.patch("/api/material-quote-requests/" + requestId, payload);
      CIQ.toast(successMessage, "success");
      await loadRfqWorkspace();
    } catch (error) {
      CIQ.toast(error.message || "Could not update quote request", "error");
    }
  }

  function recordResponse(request) {
    const body = document.createElement("form");
    body.className = "form-grid";
    body.innerHTML = `
      <label class="fld">Quoted total<input name="quoted_total" type="number" min="0" step="0.01" placeholder="0.00" required /></label>
      <label class="fld">Delivery / lead days<input name="estimated_delivery_days" type="number" min="0" step="1" placeholder="Optional" /></label>
      <label class="fld">Quote valid until<input name="valid_until" type="date" /></label>
      <label class="fld full">Response notes<textarea name="response_notes" placeholder="Availability, exclusions, terms, or follow-up notes"></textarea></label>`;
    const footer = document.createElement("div");
    footer.innerHTML = '<button class="btn" type="button" data-cancel>Cancel</button><button class="btn btn-primary" type="button" data-save>Save response</button>';
    const modal = CIQ.modal({ title: "Record supplier response · " + request.supplier_name, body, footer });
    footer.querySelector("[data-cancel]").addEventListener("click", modal.close);
    footer.querySelector("[data-save]").addEventListener("click", async () => {
      const form = new FormData(body);
      const total = Number(form.get("quoted_total"));
      if (!Number.isFinite(total) || total < 0) { CIQ.toast("Enter the supplier's quoted total", "error"); return; }
      const button = footer.querySelector("[data-save]");
      button.disabled = true; button.textContent = "Saving…";
      try {
        await CIQ.api.patch("/api/material-quote-requests/" + request.id, {
          status: "responded", quoted_total: total,
          estimated_delivery_days: form.get("estimated_delivery_days") || null,
          valid_until: form.get("valid_until") || null,
          response_notes: String(form.get("response_notes") || "").trim() || null,
        });
        modal.close(); CIQ.toast("Supplier response recorded", "success");
        await loadRfqWorkspace();
      } catch (error) {
        CIQ.toast(error.message || "Could not record response", "error");
        button.disabled = false; button.textContent = "Save response";
      }
    });
  }

  function renderRfqWorkspace() {
    const supplier = $("rfqSupplier");
    const selected = supplier.value;
    supplier.innerHTML = '<option value="">Select supplier…</option>' + supplierOptions.map((item) =>
      `<option value="${item.id}">${esc(item.name)}${item.quote_email ? " · contact ready" : " · email needed"}</option>`
    ).join("");
    if (selected && supplier.querySelector('option[value="' + selected + '"]')) supplier.value = selected;

    const prepared = quoteRequests.find((request) => request.status === "prepared");
    if (!prepared) {
      $("rfqComposer").innerHTML = '<div class="rfq-empty">Choose a supplier and prepare the RFQ when this material list is ready.</div>';
    } else {
      const mailto = rfqMailto(prepared);
      $("rfqComposer").innerHTML = `<div class="rfq-composer">
        <div class="rfq-recipient"><span>To</span><strong>${esc(prepared.recipient_email || "No quote email on file")}</strong></div>
        <label class="fld">Subject<input value="${esc(prepared.subject)}" readonly /></label>
        <label class="fld">Request<textarea rows="12" readonly>${esc(prepared.message)}</textarea></label>
        ${prepared.recipient_email ? "" : '<div class="freshness-banner attention"><span class="freshness-pulse"></span><div><strong>Supplier email required</strong><span>Ask an administrator to add the quote email, then prepare this request again.</span></div></div>'}
        <div class="rfq-actions">
          <button class="btn" type="button" data-copy-rfq="${prepared.id}">Copy RFQ</button>
          ${mailto ? `<a class="btn btn-primary" href="${mailto}" data-open-email="${prepared.id}">Open Email</a>` : ""}
          <button class="btn btn-dark" type="button" data-mark-sent="${prepared.id}" ${prepared.recipient_email ? "" : "disabled"}>Mark Sent</button>
        </div>
      </div>`;
    }

    $("rfqHistory").innerHTML = quoteRequests.length ? '<div class="section-title">Quote request history</div>' +
      quoteRequests.map((request) => `<article class="rfq-record">
        <div><strong>${esc(request.supplier_name)}</strong><span>${esc(request.recipient_email || "No email on file")}</span></div>
        <div><span class="badge ${rfqBadge(request.status)}">${esc(rfqStatus(request.status))}</span>
          ${request.quoted_total != null ? `<strong>${CIQ.money(request.quoted_total)}</strong>` : ""}</div>
        <div class="rfq-record-actions">
          ${request.status === "sent" ? `<button class="btn btn-sm" type="button" data-record-response="${request.id}">Record response</button><button class="btn btn-sm btn-ghost" type="button" data-decline-rfq="${request.id}">Mark declined</button>` : ""}
          ${request.status === "responded" ? `<button class="btn btn-sm btn-primary" type="button" data-award-rfq="${request.id}">Award quote</button>` : ""}
        </div>
      </article>`).join("") : "";

    document.querySelectorAll("[data-copy-rfq]").forEach((button) => button.addEventListener("click", () => {
      const request = quoteRequests.find((item) => String(item.id) === button.dataset.copyRfq);
      if (request) copyRfq(request);
    }));
    document.querySelectorAll("[data-mark-sent]").forEach((button) => button.addEventListener("click", () =>
      updateRfq(button.dataset.markSent, { status: "sent" }, "Quote request marked sent")));
    document.querySelectorAll("[data-record-response]").forEach((button) => button.addEventListener("click", () => {
      const request = quoteRequests.find((item) => String(item.id) === button.dataset.recordResponse);
      if (request) recordResponse(request);
    }));
    document.querySelectorAll("[data-decline-rfq]").forEach((button) => button.addEventListener("click", async () => {
      if (!(await CIQ.confirm("Mark this supplier as declined?", { confirmLabel: "Mark declined" }))) return;
      await updateRfq(button.dataset.declineRfq, { status: "declined" }, "Supplier decline recorded");
    }));
    document.querySelectorAll("[data-award-rfq]").forEach((button) => button.addEventListener("click", async () => {
      if (!(await CIQ.confirm("Award this supplier quote?", { confirmLabel: "Award quote" }))) return;
      await updateRfq(button.dataset.awardRfq, { status: "awarded" }, "Supplier quote awarded");
    }));
  }

  async function loadRfqWorkspace() {
    if (!CIQ.hasPerm("products.quote")) { $("rfqPanel").style.display = "none"; return; }
    try {
      if (!supplierOptions.length) {
        supplierOptions = (await CIQ.api.get("/api/suppliers/quote-options")).items || [];
      }
      quoteRequests = materialListId
        ? (await CIQ.api.get("/api/material-lists/" + materialListId + "/quote-requests")).items || []
        : [];
      renderRfqWorkspace();
    } catch (error) {
      $("rfqComposer").innerHTML = CIQ.errorBanner(error.message || "Could not load supplier RFQs");
    }
  }

  async function prepareRfq() {
    const error = validate();
    if (error) { CIQ.toast(error, "error"); return; }
    if (!$("jobsitePostalCode").value.trim()) { CIQ.toast("Enter the jobsite ZIP before preparing the RFQ", "error"); return; }
    const supplierId = Number($("rfqSupplier").value);
    if (!supplierId) { CIQ.toast("Select a supplier", "error"); return; }
    const button = $("prepareRfqBtn");
    button.disabled = true; button.textContent = "Preparing…";
    status = "Pricing requested";
    try {
      const saved = await CIQ.api.post("/api/material-lists", readForm());
      materialListId = saved.item.id;
      localStorage.setItem(storageKey(), JSON.stringify(readForm()));
      dirty = false;
      clearTimeout(autosaveTimer);
      setDraftSaveState("Saved to CorridorIQ");
      const prepared = await CIQ.api.post("/api/material-lists/" + materialListId + "/quote-requests", {
        supplier_id: supplierId,
        quote_needed_by: $("quoteNeededBy").value || null,
        jobsite_postal_code: $("jobsitePostalCode").value.trim(),
      });
      CIQ.toast(prepared.reused ? "This supplier request is already in progress; see its history below" : "Supplier RFQ prepared", prepared.reused ? "info" : "success");
      await loadRfqWorkspace();
    } catch (requestError) {
      CIQ.toast(requestError.message || "Could not prepare RFQ", "error");
    } finally {
      button.disabled = false; button.textContent = "Prepare RFQ";
      $("bomStatus").textContent = status;
    }
  }

  async function loadServerDraft(localDraft) {
    const companyId = CIQ.qs("company_id");
    if (!companyId) return false;
    const params = new URLSearchParams({ company_id: companyId });
    const projectId = CIQ.qs("project_id");
    if (projectId) params.set("project_id", projectId);
    try {
      const result = await CIQ.api.get("/api/material-lists/latest?" + params.toString());
      if (!result.item) return false;
      const item = result.item;
      const serverUpdated = Date.parse(item.updated_at || "") || 0;
      const localUpdated = Date.parse(localDraft && localDraft.updatedAt || "") || 0;
      if (localDraft && localUpdated > serverUpdated) return true;
      writeForm({
        id: item.id,
        companyName: item.company_name,
        projectName: item.project_name || item.permit_number || "",
        neededBy: item.needed_by,
        quoteNeededBy: item.quote_needed_by,
        jobsitePostalCode: item.jobsite_postal_code,
        deliveryPreference: item.delivery_preference,
        requestNotes: item.notes,
        sourceName: item.source_name,
        status: item.status,
        rows: (item.rows || []).map((row) => ({
          qty: row.quantity, description: row.description, unit: row.unit,
          manufacturer: row.manufacturer, productId: row.product_id, sku: row.sku,
          supplierPrice: row.supplier_price, quantityAvailable: row.quantity_available,
          leadTimeDays: row.lead_time_days, allowSubstitution: Boolean(row.allow_substitution),
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

  function draftFromCart() {
    if (CIQ.qs("from_cart") !== "1" || !CIQ.cart || typeof CIQ.cart.items !== "function") return null;
    const items = CIQ.cart.items();
    if (!items.length) return null;
    return {
      sourceName: "CorridorIQ cart",
      rows: items.map((item) => blankRow({
        qty: Math.max(1, Number(item.quantity) || 1),
        description: item.name || item.sku || "Product",
        unit: "each",
        manufacturer: "",
        productId: item.productId || null,
        sku: item.sku || "",
        supplierPrice: Number.isFinite(Number(item.unitPrice)) ? Number(item.unitPrice) : null,
        allowSubstitution: true,
      })),
    };
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("projects.view_assigned", {
      title: "Material List Intake",
      subtitle: "Build a reviewable contractor bill of materials",
      active: "opportunity-board.html",
    });
    if (!user) return;

    ensureCatalogPopup();
    window.addEventListener("resize", positionCatalogPopup);
    window.addEventListener("scroll", positionCatalogPopup, true);
    document.addEventListener("pointerdown", (event) => {
      if (!catalogPopup || catalogPopup.hidden) return;
      if (catalogPopup.contains(event.target) || event.target === catalogInput) return;
      closeCatalogPopup();
    });

    let draft = null;
    try { draft = JSON.parse(localStorage.getItem(storageKey()) || "null"); } catch (e) {}
    const cartDraft = draft ? null : draftFromCart();
    writeForm(draft || cartDraft || { rows: [blankRow()] });
    const loadedServerDraft = await loadServerDraft(draft);
    if (!loadedServerDraft) await prefillContext();
    if (!$("quoteNeededBy").value) $("quoteNeededBy").value = dateFromToday(2);
    persistLocalDraft();
    await loadRfqWorkspace();
    if (cartDraft) CIQ.toast("Cart items loaded into the material list", "success");

    $("sourceFile").addEventListener("change", () => {
      const file = $("sourceFile").files[0];
      sourceName = file ? file.name : "";
      $("fileName").textContent = sourceName;
      $("fileName").style.display = sourceName ? "inline-block" : "none";
      scheduleLocalSave();
    });
    $("dropZone").addEventListener("dragover", (e) => e.preventDefault());
    $("dropZone").addEventListener("drop", (e) => {
      e.preventDefault();
      if (e.dataTransfer.files.length) {
        $("sourceFile").files = e.dataTransfer.files;
        $("sourceFile").dispatchEvent(new Event("change"));
      }
    });
    ["companyName", "projectName", "jobsitePostalCode", "neededBy", "quoteNeededBy",
     "deliveryPreference", "requestNotes"].forEach((fieldId) => {
      const field = $(fieldId);
      field.addEventListener(field.tagName === "SELECT" ? "change" : "input", scheduleLocalSave);
    });
    $("addRowBtn").addEventListener("click", () => { rows.push(blankRow()); render(); scheduleLocalSave(); });
    $("saveDraftBtn").addEventListener("click", () => saveDraft(true));
    $("parseBtn").addEventListener("click", parseSource);
    $("requestReviewBtn").addEventListener("click", submitForReview);
    $("priceBtn").addEventListener("click", priceWithSonoran);
    $("prepareRfqBtn").addEventListener("click", prepareRfq);
    $("clearBtn").addEventListener("click", async () => {
      if (!(await CIQ.confirm("Clear this material list?", { danger: true, confirmLabel: "Clear" }))) return;
      const context = {
        companyName: $("companyName").value,
        projectName: $("projectName").value,
        materialListId,
        rows: [blankRow()],
      };
      writeForm(context);
      $("sourceFile").value = "";
      $("intakeStatus").innerHTML = "";
      await saveDraft(false);
      CIQ.toast("Material list cleared", "success");
    });
    CIQ.guardUnsaved(() => dirty);
  });
})();
