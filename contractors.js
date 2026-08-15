/* Verified Contractor Directory — secured API only. */
(function () {
  let page = 1;
  const filters = { sort: "priority" };
  const money = (value) => value == null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(Number(value));
  const number = (value) => value == null ? "—" : Number(value).toLocaleString("en-US");

  function renderStats(data) {
    const s = data.stats || {};
    document.getElementById("contractorStats").innerHTML = [
      ["Verified contractors", number(s.total)], ["Contact ready", number(s.contact_ready)],
      ["Multi-jurisdiction", number(s.multi_jurisdiction)], ["Material opportunity", money(s.material_opportunity)],
    ].map(([label, value]) => `<article class="metric-card"><span>${CIQ.esc(label)}</span><strong>${CIQ.esc(value)}</strong></article>`).join("");
  }

  function card(c) {
    const jurisdictions = (c.jurisdictions_worked || []).map((name) => `<span class="badge slate">${CIQ.esc(name)} · ${number((c.jurisdiction_breakdown || {})[name] || 0)}</span>`).join("");
    return `<article class="contractor-card">
      <div class="contractor-card-head"><div><a class="contractor-name" href="sales-company-profile.html?id=${Number(c.company_id)}">${CIQ.esc(c.display_name || c.name)}</a><div class="muted">${c.license_number ? `License ${CIQ.esc(c.license_number)} · ` : ""}${CIQ.esc(CIQ.titleCase(c.contractor_type || "contractor"))}</div></div>
      <div class="contractor-badges"><span class="badge green">Verified</span><span class="badge ${c.has_contact_info ? "green" : "amber"}">${c.has_contact_info ? "Contact ready" : "Needs contact"}</span></div></div>
      <div class="contractor-metrics">
        <div><span>Opportunity</span><strong>${number(c.opportunity_rating)}</strong></div><div><span>Permits</span><strong>${number(c.permit_count)}</strong></div>
        <div><span>Commercial</span><strong>${number(c.commercial_pct)}%</strong></div><div><span>Avg project</span><strong>${money(c.avg_project_value)}</strong></div>
        <div><span>Annual volume</span><strong>${money(c.estimated_annual_volume)}</strong></div><div><span>Material opportunity</span><strong>${money(c.estimated_material_opportunity)}</strong></div>
        <div><span>Growth</span><strong>${CIQ.esc(CIQ.titleCase(c.growth_trend || "—"))}</strong></div><div><span>Last permit</span><strong>${CIQ.fmtDate(c.last_permit_date)}</strong></div>
      </div><div class="contractor-jurisdictions">${jurisdictions || '<span class="muted">No jurisdiction metrics</span>'}</div>
      <details class="contractor-proof"><summary>Verification evidence</summary><p><strong>Source:</strong> ${CIQ.esc(c.classification_source || "—")}</p><p><strong>Rule:</strong> ${CIQ.esc(c.classification_rule || "—")}</p><p><strong>Evidence permits:</strong> ${number(c.contractor_evidence_count)} · <strong>Model:</strong> ${CIQ.esc(c.classification_version || "—")}</p></details>
      <div class="contractor-actions"><a class="btn btn-primary btn-sm" href="sales-company-profile.html?id=${Number(c.company_id)}">Open company</a></div></article>`;
  }

  async function load() {
    const results = document.getElementById("contractorResults"); results.innerHTML = CIQ.skeletonRows(4);
    const params = new URLSearchParams({ page: String(page), page_size: "24", ...filters });
    try {
      const data = await CIQ.api.get("/api/contractors?" + params.toString()); renderStats(data);
      results.className = data.items.length ? "contractor-grid" : "";
      results.innerHTML = data.items.length ? data.items.map(card).join("") : CIQ.emptyState({ icon: "▤", title: "No verified contractors match", text: "Adjust the filters or complete the contractor rebuild after production audit approval." });
      CIQ.pager(document.getElementById("contractorPager"), { page: data.page, pages: data.pages, total: data.total, onPage: (next) => { page = next; load(); window.scrollTo(0, 0); } });
    } catch (error) { results.className = ""; results.innerHTML = CIQ.errorBanner(error.message); }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("companies.view", { title: "Verified Contractor Directory", subtitle: "Accurate companies, complete metrics, traceable evidence", active: "contractors.html" });
    if (!user) return;
    const search = document.getElementById("contractorSearch");
    search.addEventListener("input", CIQ.debounce(() => { filters.q = search.value.trim(); page = 1; load(); }, 300));
    document.getElementById("contractorContact").addEventListener("change", (event) => { filters.contact = event.target.value; page = 1; load(); });
    document.getElementById("contractorSort").addEventListener("change", (event) => { filters.sort = event.target.value; page = 1; load(); });
    document.getElementById("contractorMulti").addEventListener("change", (event) => { filters.multi = event.target.checked ? "1" : ""; page = 1; load(); }); load();
  });
})();
