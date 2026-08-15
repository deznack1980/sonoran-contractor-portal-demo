/* Secure source-backed external intelligence import. */
(function () {
  const MAX_BYTES = 8 * 1024 * 1024;
  const COLUMNS = ["company_id", "company_name", "source_type", "evidence_type", "source_record_id",
    "title", "status", "summary", "amount", "effective_date", "expiration_date", "source_url",
    "retrieved_at", "confidence", "match_method"];
  let uploadName = null, uploadB64 = null, previewCounts = null;
  let rocName = null, rocB64 = null, rocCounts = null;

  const escCsv = (value) => `"${String(value == null ? "" : value).replaceAll('"', '""')}"`;
  const status = (message, kind) => {
    document.getElementById("intelligenceStatus").innerHTML = message
      ? `<div class="status-msg ${kind || "info"}">${CIQ.esc(message)}</div>` : "";
  };
  const rocStatus = (message, kind) => {
    document.getElementById("rocStatus").innerHTML = message
      ? `<div class="status-msg ${kind || "info"}">${CIQ.esc(message)}</div>` : "";
  };

  function templateCsv() {
    const retrieved = new Date().toISOString();
    const rows = [
      ["123", "Example Contractor LLC", "az_roc", "contractor_license", "ROC-123456", "Arizona contractor license", "Active", "Classification CR-37", "", "2024-01-01", "2026-12-31", "https://roc.az.gov/contractor-search", retrieved, "95", "license_number"],
      ["123", "Example Contractor LLC", "azcc", "business_entity", "AZCC-1234567", "Arizona business entity", "Active", "Domestic LLC", "", "2018-05-10", "", "https://arizonabusinesscenter.azcc.gov/businesssearch", retrieved, "90", "legal_name"],
      ["123", "Example Contractor LLC", "az_ucc", "ucc_filing", "202600000001", "UCC financing statement", "Filed", "Secured party and filing status only; no distress inference", "", "2026-01-15", "2031-01-15", "https://azsos.gov/business/ucc", retrieved, "85", "legal_name"],
      ["123", "Example Contractor LLC", "adot", "plan_holder", "F000001C", "ADOT plan holder", "Registered", "Registered plan holder for advertised project", "", "2026-08-15", "", "https://cnsads.azdot.gov/current", retrieved, "85", "manual_verified"],
    ];
    return [COLUMNS, ...rows].map((row) => row.map(escCsv).join(",")).join("\r\n") + "\r\n";
  }

  function downloadTemplate() {
    const blob = new Blob([templateCsv()], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "corridoriq_external_intelligence_template.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
  }

  function downloadCsv(filename, columns, rows) {
    const csv = [columns, ...rows].map((row) => row.map(escCsv).join(",")).join("\r\n") + "\r\n";
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 500);
  }

  async function downloadResearchQueue(event) {
    await CIQ.busy(event.currentTarget, async () => {
      try {
        const data = await CIQ.api.get("/api/admin/external-intelligence/research-queue?limit=500");
        const columns = ["company_id", "company_name", "legal_name", "license_number", "city", "state",
          "has_contact_info", "priority_tier", "priority_score", "needed_sources"];
        const rows = (data.items || []).map((item) => [item.company_id, item.company_name, item.legal_name,
          item.license_number, item.city, item.state, item.has_contact_info ? "yes" : "no",
          item.company_priority_tier, item.company_priority_score, (item.needed_sources || []).join("|")]);
        downloadCsv("corridoriq_external_research_queue.csv", columns, rows);
        CIQ.toast(`Downloaded ${rows.length} companies for source research`, "success");
      } catch (error) { CIQ.toast(error.message || "Could not build research queue", "error"); }
    });
  }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(",")[1]);
      reader.onerror = () => reject(new Error("The file could not be read."));
      reader.readAsDataURL(file);
    });
  }

  function renderCoverage(data) {
    const total = Number(data.total_crm_companies || 0);
    const bids = data.public_bids || {};
    document.getElementById("sourceCoverage").innerHTML = (data.sources || []).slice(0, 4).map((source) => {
      const covered = Number(source.companies || 0), pct = total ? Math.round(covered / total * 100) : 0;
      return `<article class="source-coverage-card"><span>${CIQ.esc(source.category)}</span>
        <strong>${CIQ.esc(source.label)}</strong><div><b>${covered.toLocaleString()}</b> / ${total.toLocaleString()} companies</div>
        <div class="progress"><i style="width:${pct}%"></i></div><small>${Number(source.records || 0).toLocaleString()} evidence records · ${pct}% coverage${source.source_type === "adot" ? ` · ${Number(bids.opportunities || 0)} live bids / ${Number(bids.planholders || 0)} planholders / ${Number(bids.matched_planholders || 0)} matched` : ""}</small></article>`;
    }).join("");
  }

  async function loadCoverage() {
    try { renderCoverage(await CIQ.api.get("/api/admin/external-intelligence/coverage")); }
    catch (error) { document.getElementById("sourceCoverage").innerHTML = CIQ.errorBanner(error.message); }
  }

  async function refreshSources(event) {
    const approved = await CIQ.confirm("Fetch current ADOT advertisements and planholder contact records now?", { confirmLabel: "Run source refresh" });
    if (!approved) return;
    await CIQ.busy(event.currentTarget, async () => {
      rocStatus("", "info"); status("Fetching current ADOT projects and planholder records…", "info");
      try {
        const data = await CIQ.api.post("/api/admin/external-intelligence/refresh", {});
        const c = data.counts || {};
        status(`ADOT refresh complete: ${c.opportunities || 0} live projects, ${c.planholders || 0} planholders, ${c.matched_companies || 0} company matches, and ${c.contact_updates || 0} new phone numbers.`, "ok");
        await loadCoverage();
      } catch (error) { status(error.message || "Public-source refresh failed.", "err"); }
    });
  }

  function renderRejections(items) {
    if (!items.length) return "";
    return `<div class="table-wrap"><table class="tbl responsive"><thead><tr><th>Row</th><th>Company</th><th>Source</th><th>Reason</th></tr></thead><tbody>${items.map((item) => `<tr>
      <td data-label="Row">${item.row_number}</td><td data-label="Company">${CIQ.esc(item.company_name)}<small>${CIQ.esc(item.company_id)}</small></td>
      <td data-label="Source">${CIQ.esc(item.source_type)}</td><td data-label="Reason">${CIQ.esc((item.reasons || []).join(" · "))}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function renderPreview(data) {
    previewCounts = data.counts;
    const blocked = previewCounts.rejected > 0 || !previewCounts.importable;
    document.getElementById("intelligencePreview").hidden = false;
    document.getElementById("intelligencePreview").innerHTML = `<div class="intelligence-counts">
      <div><strong>${previewCounts.input_rows}</strong><span>Input rows</span></div>
      <div class="good"><strong>${previewCounts.importable}</strong><span>Ready</span></div>
      <div class="bad"><strong>${previewCounts.rejected}</strong><span>Rejected</span></div>
    </div><p class="muted">Sources in file: ${CIQ.esc((data.source_types || []).join(", ") || "—")}</p>${renderRejections(data.rejections || [])}`;
    document.getElementById("intelligenceImportBtn").disabled = blocked;
    status(blocked ? "Preview complete. Fix all rejected rows before importing." : "Preview passed. Approve the backup and import when ready.", blocked ? "err" : "ok");
  }

  async function preview() {
    if (!uploadB64) return;
    const button = document.getElementById("intelligencePreviewBtn");
    button.disabled = true; button.textContent = "Checking…";
    status("Validating identities, evidence keys, source URLs, dates, and confidence…", "info");
    try {
      renderPreview(await CIQ.api.post("/api/admin/external-intelligence/preview", { filename: uploadName, content_base64: uploadB64 }));
    } catch (error) { status(error.message || "Preview failed.", "err"); }
    finally { button.disabled = false; button.textContent = "1. Preview validation"; }
  }

  async function applyImport() {
    if (!previewCounts || previewCounts.rejected || !previewCounts.importable) return;
    const approved = await CIQ.confirm(`Back up the complete database and import ${previewCounts.importable} verified evidence row(s)?`, { confirmLabel: "Back up & import" });
    if (!approved) return;
    const button = document.getElementById("intelligenceImportBtn");
    button.disabled = true; button.textContent = "Importing…";
    try {
      const data = await CIQ.api.post("/api/admin/external-intelligence/import", { filename: uploadName, content_base64: uploadB64 });
      status(`Import complete: ${data.counts.created_rows} created and ${data.counts.updated_rows} updated. Backup: ${data.backup_file}`, "ok");
      CIQ.toast("External intelligence imported", "success");
      previewCounts = null; await loadCoverage();
    } catch (error) { status(error.message || "Import failed; no changes were committed.", "err"); button.disabled = false; }
    finally { button.textContent = "2. Back up & import"; }
  }

  async function chooseFile(event) {
    const file = event.target.files[0];
    uploadName = uploadB64 = previewCounts = null;
    document.getElementById("intelligencePreview").hidden = true;
    document.getElementById("intelligenceImportBtn").disabled = true;
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv") || file.size > MAX_BYTES) {
      status("Choose a CSV no larger than 8 MB.", "err"); event.target.value = ""; return;
    }
    try {
      uploadName = file.name; uploadB64 = await readFile(file);
      document.getElementById("intelligenceFileMeta").textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
      document.getElementById("intelligencePreviewBtn").disabled = false;
      status("File loaded. Preview it before importing.", "ok");
    } catch (error) { status(error.message, "err"); }
  }

  function renderRocPreview(data) {
    rocCounts = data.counts || {};
    const matched = Number(rocCounts.exact_matches || 0);
    document.getElementById("rocPreview").hidden = false;
    document.getElementById("rocPreview").innerHTML = `<div class="intelligence-counts roc-counts">
      <div><strong>${Number(rocCounts.source_rows || 0).toLocaleString()}</strong><span>ROC license rows</span></div>
      <div class="good"><strong>${matched.toLocaleString()}</strong><span>Exact CorridorIQ matches</span></div>
      <div><strong>${Number(rocCounts.matched_companies || 0).toLocaleString()}</strong><span>Companies verified</span></div>
      <div><strong>${Number(rocCounts.unmatched || 0).toLocaleString()}</strong><span>Not in CRM</span></div>
      <div class="bad"><strong>${Number(rocCounts.ambiguous || 0).toLocaleString()}</strong><span>Held for review</span></div>
    </div><p class="muted">${CIQ.esc(data.matching_policy || "Exact matches only.")}</p>`;
    document.getElementById("rocImportBtn").disabled = matched < 1;
    rocStatus(matched ? `${matched.toLocaleString()} license rows are ready to verify and reclassify.` : "No safe CorridorIQ matches were found.", matched ? "ok" : "err");
  }

  async function previewRoc() {
    if (!rocB64) return;
    const button = document.getElementById("rocPreviewBtn");
    button.disabled = true; button.textContent = "Matching…";
    rocStatus("Matching license numbers, legal names, DBAs, and aliases…", "info");
    try { renderRocPreview(await CIQ.api.post("/api/admin/external-intelligence/roc-preview", { filename: rocName, content_base64: rocB64 })); }
    catch (error) { rocStatus(error.message || "ROC preview failed.", "err"); }
    finally { button.disabled = false; button.textContent = "1. Preview ROC matches"; }
  }

  async function importRoc() {
    if (!rocCounts || !Number(rocCounts.exact_matches || 0)) return;
    const approved = await CIQ.confirm(`Back up the database, verify ${Number(rocCounts.exact_matches).toLocaleString()} ROC license matches, and reclassify those companies?`, { confirmLabel: "Verify & reclassify" });
    if (!approved) return;
    const button = document.getElementById("rocImportBtn");
    button.disabled = true; button.textContent = "Verifying…";
    try {
      const data = await CIQ.api.post("/api/admin/external-intelligence/roc-import", { filename: rocName, content_base64: rocB64 });
      const c = data.counts || {};
      rocStatus(`ROC import complete: ${Number(c.matched_companies || 0).toLocaleString()} companies verified; ${Number(c.created_rows || 0).toLocaleString()} evidence records created and ${Number(c.updated_rows || 0).toLocaleString()} updated. Backup: ${data.backup_file}`, "ok");
      CIQ.toast("ROC verification complete", "success"); rocCounts = null; await loadCoverage();
    } catch (error) { rocStatus(error.message || "ROC import failed.", "err"); button.disabled = false; }
    finally { button.textContent = "2. Back up, verify & reclassify"; }
  }

  async function chooseRoc(event) {
    const file = event.target.files[0]; rocName = rocB64 = rocCounts = null;
    document.getElementById("rocPreview").hidden = true; document.getElementById("rocImportBtn").disabled = true;
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv") || file.size > 35 * 1024 * 1024) {
      rocStatus("Choose the unedited Arizona ROC CSV, no larger than 35 MB.", "err"); event.target.value = ""; return;
    }
    try {
      rocName = file.name; rocB64 = await readFile(file);
      document.getElementById("rocFileMeta").textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
      document.getElementById("rocPreviewBtn").disabled = false;
      rocStatus("Official file loaded. Preview exact matches before changing the database.", "ok");
    } catch (error) { rocStatus(error.message, "err"); }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("admin.system", { title: "Intelligence Import", subtitle: "Source-backed company evidence", active: "intelligence-import.html" });
    if (!user) return;
    document.getElementById("downloadTemplateBtn").addEventListener("click", downloadTemplate);
    document.getElementById("refreshSourcesBtn").addEventListener("click", refreshSources);
    document.getElementById("downloadQueueBtn").addEventListener("click", downloadResearchQueue);
    document.getElementById("intelligenceFile").addEventListener("change", chooseFile);
    document.getElementById("intelligencePreviewBtn").addEventListener("click", preview);
    document.getElementById("intelligenceImportBtn").addEventListener("click", applyImport);
    document.getElementById("rocFile").addEventListener("change", chooseRoc);
    document.getElementById("rocPreviewBtn").addEventListener("click", previewRoc);
    document.getElementById("rocImportBtn").addEventListener("click", importRoc);
    await loadCoverage();
  });
})();
