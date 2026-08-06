/* Contact enrichment admin — safe CSV preview and transactional import. */
(function () {
  const MAX_BYTES = 5 * 1024 * 1024;
  let uploadName = null;
  let uploadB64 = null;
  let previewCounts = null;

  function setStatus(message, kind) {
    const host = document.getElementById("statusMsg");
    host.innerHTML = message
      ? `<div class="status-msg ${kind || "info"}">${CIQ.esc(message)}</div>`
      : "";
  }

  function setBusy(button, busy, busyLabel, normalLabel) {
    button.disabled = busy;
    button.textContent = busy ? busyLabel : normalLabel;
  }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(",")[1]);
      reader.onerror = () => reject(new Error("The file could not be read."));
      reader.readAsDataURL(file);
    });
  }

  function resetPreview() {
    previewCounts = null;
    document.getElementById("previewPanel").hidden = true;
    document.getElementById("importResult").hidden = true;
    document.getElementById("importResult").innerHTML = "";
    document.getElementById("importBtn").disabled = true;
  }

  function renderRejections(items) {
    const host = document.getElementById("rejectionArea");
    if (!items.length) {
      host.innerHTML = "";
      return;
    }
    host.innerHTML = `
      <div class="reject-wrap">
        <h3 style="font-size:14px;margin:0 0 8px">Rows that must be fixed</h3>
        <table class="reject-table">
          <thead><tr><th>CSV row</th><th>Company ID</th><th>Company</th><th>Reason</th></tr></thead>
          <tbody>${items.map((item) => `<tr>
            <td>${Number(item.row_number) || ""}</td>
            <td>${CIQ.esc(item.company_id)}</td>
            <td>${CIQ.esc(item.company_name)}</td>
            <td>${CIQ.esc((item.reasons || []).join(" · "))}</td>
          </tr>`).join("")}</tbody>
        </table>
      </div>`;
  }

  function renderPreview(data) {
    const counts = data.counts;
    previewCounts = counts;
    document.getElementById("countInput").textContent = counts.input_rows;
    document.getElementById("countImportable").textContent = counts.importable;
    document.getElementById("countActionable").textContent = counts.actionable;
    document.getElementById("countSkipped").textContent = counts.skipped;
    document.getElementById("countRejected").textContent = counts.rejected;
    document.getElementById("previewPanel").hidden = false;
    renderRejections(data.rejections || []);

    const note = document.getElementById("previewNote");
    const importBtn = document.getElementById("importBtn");
    if (counts.rejected > 0) {
      note.textContent = `${counts.rejected} row(s) must be corrected or removed. Nothing can be imported until the file passes validation.`;
      importBtn.disabled = true;
      setStatus("Preview finished, but the import is blocked by rejected rows.", "err");
    } else if (counts.importable === 0) {
      note.textContent = "The file is valid, but it contains no contact rows that can be imported.";
      importBtn.disabled = true;
      setStatus("Preview finished. There is nothing to import.", "info");
    } else {
      note.textContent = `${counts.importable} company row(s) are ready. ${counts.skipped} Not Found/Ambiguous row(s) will be retained in the CSV but skipped safely.`;
      importBtn.disabled = false;
      setStatus("Preview passed. Review the counts, then approve the backup and import.", "ok");
    }
  }

  async function preview() {
    if (!uploadB64) {
      setStatus("Choose a completed CSV first.", "err");
      return;
    }
    const button = document.getElementById("previewBtn");
    setBusy(button, true, "Checking…", "1. Preview validation");
    document.getElementById("importBtn").disabled = true;
    setStatus("Checking company identities, contact formats, and source URLs…", "info");
    try {
      const data = await CIQ.api.post("/api/admin/contact-enrichment/preview", {
        filename: uploadName,
        content_base64: uploadB64,
      });
      renderPreview(data);
    } catch (error) {
      resetPreview();
      setStatus(error.message || "The CSV could not be validated.", "err");
    } finally {
      setBusy(button, false, "Checking…", "1. Preview validation");
      button.disabled = !uploadB64;
    }
  }

  async function applyImport() {
    if (!previewCounts || previewCounts.rejected || !previewCounts.importable) return;
    const approved = await CIQ.confirm(
      `Create a database backup, then import ${previewCounts.importable} validated company row(s)? Existing company contact fields will be preserved.`,
      { confirmLabel: "Back up & import" }
    );
    if (!approved) return;

    const button = document.getElementById("importBtn");
    setBusy(button, true, "Importing…", "2. Back up & import");
    document.getElementById("previewBtn").disabled = true;
    setStatus("Creating the backup and applying validated contacts…", "info");
    try {
      const data = await CIQ.api.post("/api/admin/contact-enrichment/import", {
        filename: uploadName,
        content_base64: uploadB64,
      });
      const counts = data.counts;
      const result = document.getElementById("importResult");
      result.hidden = false;
      result.innerHTML = `<div class="status-msg ok" style="margin-top:18px">
        <strong>Import complete.</strong><br>
        ${counts.company_profiles_updated} company profile(s) updated ·
        ${counts.contacts_created} named contact(s) created ·
        ${counts.contacts_updated} named contact(s) updated ·
        ${counts.rows_already_current} row(s) already current.<br>
        <span style="font-size:12px">Backup: ${CIQ.esc(data.backup_file)}</span>
      </div>`;
      setStatus("Contacts are now available in company profiles and contact-ready companies remain prioritized.", "ok");
      previewCounts = null;
      button.disabled = true;
      button.textContent = "Imported";
      CIQ.toast("Contact import complete", "success");
    } catch (error) {
      setStatus(error.message || "The import failed. No database changes were committed.", "err");
      button.disabled = false;
    } finally {
      document.getElementById("previewBtn").disabled = !uploadB64;
      if (!button.disabled) button.textContent = "2. Back up & import";
    }
  }

  async function chooseFile(event) {
    const file = event.target.files[0];
    uploadName = null;
    uploadB64 = null;
    resetPreview();
    setStatus("", "info");
    const previewBtn = document.getElementById("previewBtn");
    previewBtn.disabled = true;
    if (!file) {
      document.getElementById("fileMeta").textContent = "CSV only · maximum 5 MB · up to 5,000 companies";
      return;
    }
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setStatus("Choose a .csv file.", "err");
      event.target.value = "";
      return;
    }
    if (file.size > MAX_BYTES) {
      setStatus("The CSV is larger than the 5 MB upload limit.", "err");
      event.target.value = "";
      return;
    }
    try {
      uploadName = file.name;
      uploadB64 = await readFile(file);
      document.getElementById("fileMeta").textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
      previewBtn.disabled = false;
      setStatus("File loaded. Preview it before importing.", "ok");
    } catch (error) {
      setStatus(error.message, "err");
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("admin.system", {
      title: "Contact Import",
      subtitle: "Preview, protect, and apply researched contacts",
      active: "contact-enrichment-admin.html",
    });
    if (!user) return;
    document.getElementById("contactFile").addEventListener("change", chooseFile);
    document.getElementById("previewBtn").addEventListener("click", preview);
    document.getElementById("importBtn").addEventListener("click", applyImport);
  });
})();
