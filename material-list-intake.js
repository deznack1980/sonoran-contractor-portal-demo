/* CorridorIQ material-list MVP: editable BOM, CSV import, and resilient local drafts. */
(function () {
  const STORAGE_KEY = "ciq.material-list.draft.v1";
  let rows = [];
  let sourceName = "";
  let status = "Draft";

  const $ = (id) => document.getElementById(id);
  const esc = (value) => CIQ.esc(value == null ? "" : value);

  function id() {
    return (globalThis.crypto && crypto.randomUUID) ? crypto.randomUUID() :
      Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  function blankRow(values) {
    return Object.assign({ id: id(), qty: 1, description: "", unit: "each", manufacturer: "" }, values || {});
  }

  function readForm() {
    return {
      companyName: $("companyName").value.trim(),
      projectName: $("projectName").value.trim(),
      neededBy: $("neededBy").value,
      deliveryPreference: $("deliveryPreference").value,
      requestNotes: $("requestNotes").value.trim(),
      companyId: CIQ.qs("company_id"),
      projectId: CIQ.qs("project_id"),
      sourceName,
      status,
      rows: rows.map((r) => ({
        id: r.id, qty: Number(r.qty) || 0, description: String(r.description || "").trim(),
        unit: String(r.unit || "").trim(), manufacturer: String(r.manufacturer || "").trim(),
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
    sourceName = draft.sourceName || "";
    status = draft.status || "Draft";
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
      <td><input aria-label="Description" data-field="description" value="${esc(r.description)}" placeholder="Item description"></td>
      <td><input aria-label="Unit" data-field="unit" value="${esc(r.unit)}" placeholder="each"></td>
      <td><input aria-label="Manufacturer" data-field="manufacturer" value="${esc(r.manufacturer)}" placeholder="Optional"></td>
      <td><button class="btn btn-sm btn-ghost" type="button" data-remove="1" aria-label="Remove item">Remove</button></td>
    </tr>`).join("");
    $("lineCount").textContent = rows.filter((r) => String(r.description || "").trim()).length;
    $("qtyCount").textContent = rows.reduce((sum, r) => sum + (Number(r.qty) || 0), 0);
    $("bomStatus").textContent = status;

    $("bomBody").querySelectorAll("input[data-field]").forEach((input) => {
      input.addEventListener("input", () => {
        const row = rows.find((r) => r.id === input.closest("tr").dataset.id);
        row[input.dataset.field] = input.dataset.field === "qty" ? input.valueAsNumber : input.value;
        renderCounts();
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

  function renderCounts() {
    $("lineCount").textContent = rows.filter((r) => String(r.description || "").trim()).length;
    $("qtyCount").textContent = rows.reduce((sum, r) => sum + (Number(r.qty) || 0), 0);
  }

  function saveDraft(showToast) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(readForm()));
    status = "Draft";
    $("bomStatus").textContent = status;
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
    localStorage.setItem(STORAGE_KEY, JSON.stringify(readForm()));
    $("bomStatus").textContent = status;
    CIQ.toast("Material list marked ready for review", "success");
  }

  async function priceWithSonoran() {
    const error = validate();
    if (error) { CIQ.toast(error, "error"); return; }
    status = "Pricing requested";
    const payload = readForm();
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    const summary = [
      "CORRIDORIQ MATERIAL PRICING REQUEST",
      "Contractor: " + payload.companyName,
      "Project: " + payload.projectName,
      "Needed by: " + (payload.neededBy || "Not specified"),
      "Delivery: " + payload.deliveryPreference,
      "",
      ...payload.rows.filter((r) => r.qty > 0 && r.description)
        .map((r, i) => (i + 1) + ". " + r.qty + " " + (r.unit || "each") + " — " + r.description +
          (r.manufacturer ? " — " + r.manufacturer : "")),
      "", "Notes: " + (payload.requestNotes || "None"),
    ].join("\n");
    try {
      await navigator.clipboard.writeText(summary);
      CIQ.toast("Pricing request copied to clipboard", "success");
    } catch (e) {
      CIQ.toast("Pricing request saved; clipboard access was unavailable", "info");
    }
    $("bomStatus").textContent = status;
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("projects.view_assigned", {
      title: "Material List Intake",
      subtitle: "Build a reviewable contractor bill of materials",
      active: "opportunity-board.html",
    });
    if (!user) return;

    let draft = null;
    try { draft = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch (e) {}
    writeForm(draft || { rows: [blankRow()] });

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
      localStorage.removeItem(STORAGE_KEY); writeForm({ rows: [blankRow()] });
      $("sourceFile").value = ""; $("intakeStatus").innerHTML = "";
    });
    CIQ.guardUnsaved(() => false);
  });
})();