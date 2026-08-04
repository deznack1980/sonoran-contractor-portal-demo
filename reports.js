/* Interactive Company Opportunity Report + export library. */
(function () {
  let catalogData = { reports: [], files: [] };
  let reportData = { items: [], summary: {} };

  function fileForPrefixes(files, prefixes) {
    return files.find((f) => prefixes.some((p) => f.name.toLowerCase().startsWith(p)));
  }

  function money(value) {
    if (value == null) return "—";
    const n = Number(value) || 0;
    return n >= 1000000 ? "$" + (n / 1000000).toFixed(1) + "M" :
      n >= 1000 ? "$" + Math.round(n / 1000) + "K" : "$" + Math.round(n).toLocaleString();
  }

  function tierClass(tier) {
    return ({ Critical: "red", High: "amber", Medium: "blue", Low: "slate" })[tier] || "slate";
  }

  function renderExportButton() {
    const company = (catalogData.reports || []).find((r) => r.key === "company_opportunity");
    const latest = company && fileForPrefixes(catalogData.files || [], company.file_prefixes || []);
    document.getElementById("reportExport").innerHTML = latest
      ? `<a class="btn btn-primary" href="/api/reports/download?name=${encodeURIComponent(latest.name)}">
          Export XLSX</a>`
      : '<button class="btn" disabled>Export not generated</button>';
  }

  function renderSummary() {
    const s = reportData.summary || {};
    const defs = [
      [Number(s.companies || 0).toLocaleString(), "Ranked companies"],
      [Number(s.active_projects || 0).toLocaleString(), "Active projects"],
      [money(s.estimated_opportunity_total || 0), "Estimated opportunity"],
      [Number(s.unassigned || 0).toLocaleString(), "Companies unassigned"],
    ];
    document.getElementById("reportSummary").innerHTML = defs.map(([value, label]) =>
      `<div class="report-metric"><strong>${value}</strong><span>${label}</span></div>`
    ).join("");
  }

  function filteredItems() {
    const q = document.getElementById("reportSearch").value.trim().toLowerCase();
    const tier = document.getElementById("reportTier").value;
    const owner = document.getElementById("reportOwner").value;
    const sort = document.getElementById("reportSort").value;
    const items = (reportData.items || []).filter((item) => {
      const haystack = [item.display_name, item.city, item.state, item.owner_name,
        item.license_number, item.company_type_primary].filter(Boolean).join(" ").toLowerCase();
      const itemTier = item.company_priority_tier || "Unscored";
      const ownerMatch = !owner || (owner === "assigned" ? item.assigned_user_id : !item.assigned_user_id);
      return (!q || haystack.includes(q)) && (!tier || itemTier === tier) && ownerMatch;
    });
    const num = (v) => Number(v) || 0;
    items.sort((a, b) => {
      if (sort === "value") return num(b.estimated_opportunity_total) - num(a.estimated_opportunity_total);
      if (sort === "projects") return num(b.active_projects) - num(a.active_projects);
      if (sort === "activity") return String(b.latest_activity_date || "").localeCompare(String(a.latest_activity_date || ""));
      return num(b.company_priority_score) - num(a.company_priority_score);
    });
    return items;
  }

  function renderCompanyReport() {
    const items = filteredItems();
    const el = document.getElementById("companyReport");
    if (!items.length) {
      el.innerHTML = CIQ.emptyState({ icon: "⌕", title: "No companies match these filters",
        text: "Clear a filter or search for another company." });
      return;
    }
    el.innerHTML = `<div class="report-result-head">
      <span><strong>${items.length.toLocaleString()}</strong> companies shown</span>
      <span>Updated ${CIQ.fmtDateTime(reportData.generated_at)}</span>
    </div>
    <div class="table-wrap"><table class="tbl responsive opportunity-report-table"><thead><tr>
      <th>Rank</th><th>Company</th><th>Priority</th><th>Projects</th>
      <th>Opportunity</th><th>Recent activity</th><th>Owner</th><th></th>
    </tr></thead><tbody>${items.map((item, index) => {
      const tier = item.company_priority_tier || "Unscored";
      return `<tr>
        <td data-label="Rank"><span class="report-rank">${index + 1}</span></td>
        <td data-label="Company"><a class="report-company" href="sales-company-profile.html?id=${item.company_id}">
          <strong>${CIQ.esc(item.display_name || "Unnamed company")}</strong>
          <small>${CIQ.esc([item.city, item.state].filter(Boolean).join(", ") || "Location unavailable")}
          ${item.license_number ? " · " + CIQ.esc(item.license_number) : ""}</small></a></td>
        <td data-label="Priority"><span class="badge ${tierClass(tier)}">${CIQ.esc(tier)}</span>
          <small class="score-line">Score ${item.company_priority_score != null ? Math.round(item.company_priority_score) : "—"}</small></td>
        <td data-label="Projects"><strong>${Number(item.active_projects || 0).toLocaleString()}</strong>
          <small class="score-line">${Number(item.projects_last_30_days || 0)} in 30 days</small></td>
        <td data-label="Opportunity"><strong>${money(item.estimated_opportunity_total)}</strong>
          <small class="score-line">Avg score ${item.average_opportunity_score != null ? Math.round(item.average_opportunity_score) : "—"}</small></td>
        <td data-label="Recent activity">${item.latest_activity_date ? CIQ.fmtDate(item.latest_activity_date) : "—"}
          <small class="score-line">${CIQ.esc(CIQ.titleCase(item.activity_trend || "no trend"))}</small></td>
        <td data-label="Owner">${item.owner_name ? CIQ.esc(item.owner_name) :
          '<span class="badge amber">Unassigned</span>'}</td>
        <td data-label=""><a class="btn btn-sm" href="sales-company-profile.html?id=${item.company_id}">Open</a></td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
  }

  function renderCatalog() {
    const files = catalogData.files || [];
    document.getElementById("cards").innerHTML = (catalogData.reports || []).map((r) => {
      const latest = fileForPrefixes(files, r.file_prefixes || []);
      const interactive = r.key === "company_opportunity";
      return `<div class="company-card">
        <div class="cc-name" style="font-size:15px">${CIQ.esc(r.title)}</div>
        <div class="cc-reason">${CIQ.esc(r.description)}</div>
        <div class="cc-meta"><div><span>For</span>${CIQ.esc(r.audience)}</div>
          <div><span>Latest</span>${latest ? CIQ.fmtDate(latest.generated_at) : "Not generated"}</div></div>
        <div class="cc-quick">${interactive ? '<a class="btn btn-sm btn-dark" href="#content">View online</a>' : ""}
          ${latest ? `<a class="btn btn-sm" href="/api/reports/download?name=${encodeURIComponent(latest.name)}">Download ${CIQ.esc(latest.type)}</a>`
          : '<button class="btn btn-sm" disabled>No export yet</button>'}</div>
      </div>`;
    }).join("");

    const el = document.getElementById("files");
    if (!files.length) {
      el.innerHTML = CIQ.emptyState({ icon: "⊟", title: "No generated exports",
        text: "Exports appear here after report generation." });
      return;
    }
    el.innerHTML = `<div class="table-wrap"><table class="tbl responsive"><thead><tr>
      <th>Report</th><th>Type</th><th>Generated</th><th>Size</th><th></th></tr></thead>
      <tbody>${files.map((file) => `<tr>
        <td data-label="Report">${CIQ.esc(file.name)}</td>
        <td data-label="Type"><span class="badge slate">${CIQ.esc(file.type)}</span></td>
        <td data-label="Generated">${CIQ.fmtDateTime(file.generated_at)}</td>
        <td data-label="Size">${file.size_kb} KB</td>
        <td data-label=""><a class="btn btn-sm" href="/api/reports/download?name=${encodeURIComponent(file.name)}">Download</a></td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("reports.view", {
      title: "Company Opportunities", subtitle: "Interactive sales intelligence", active: "reports.html"
    });
    if (!user) return;
    document.getElementById("companyReport").innerHTML = CIQ.skeletonRows(5);
    try {
      [catalogData, reportData] = await Promise.all([
        CIQ.api.get("/api/reports/catalog"),
        CIQ.api.get("/api/reports/company-opportunities"),
      ]);
      renderExportButton(); renderSummary(); renderCompanyReport(); renderCatalog();
      ["reportTier", "reportOwner", "reportSort"].forEach((id) =>
        document.getElementById(id).addEventListener("change", renderCompanyReport));
      document.getElementById("reportSearch").addEventListener("input", CIQ.debounce(renderCompanyReport, 180));
    } catch (e) {
      document.getElementById("companyReport").innerHTML = CIQ.errorBanner(e.message || "Could not load report");
    }
  });
})();