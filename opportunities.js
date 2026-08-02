/* Opportunities — high-scoring projects across assigned companies. */
(function () {
  let page = 1; const filters = {}; let canEdit = false;

  function truncate(s, n) { s = s || ""; return s.length > n ? s.slice(0, n) + "…" : s; }

  function card(p) {
    return `<div class="company-card">
      <div class="cc-top">
        <div><div class="cc-name" style="font-size:15px">${CIQ.esc(p.job_address || p.permit_number || "Project")}</div>
          <div class="cc-loc"><a href="sales-company-profile.html?id=${p.company_id}">${CIQ.esc(p.display_name)}</a> · ${CIQ.esc(p.jurisdiction || p.city || "")}</div></div>
        <span class="score-chip">${p.opportunity_score != null ? Math.round(p.opportunity_score) : "—"}</span>
      </div>
      <div class="cc-meta">
        <div><span>Stage</span>${CIQ.esc(CIQ.titleCase(p.project_lifecycle || "—"))}</div>
        <div><span>Timing</span>${CIQ.esc(CIQ.titleCase(p.opportunity_timing || "—"))}</div>
        <div><span>Category</span>${CIQ.esc(CIQ.titleCase(p.project_category || "—"))}</div>
        <div><span>Opportunity</span>${p.opportunity_date ? CIQ.fmtDate(p.opportunity_date) : "—"}</div>
        <div><span>Related permits</span>${p.permit_count != null ? p.permit_count : "—"}</div>
        <div><span>Est. value</span>${p.estimated_material_value != null ? CIQ.money(p.estimated_material_value) : "—"}</div>
      </div>
      ${p.description ? `<div class="cc-reason">${CIQ.esc(truncate(p.description, 120))}</div>` : ""}
      <div class="cc-quick">
        <a class="btn btn-sm btn-ghost" href="sales-company-profile.html?id=${p.company_id}">View company</a>
        ${canEdit ? `<button class="btn btn-sm" data-cid="${p.company_id}" data-pid="${p.project_id}" data-addr="${CIQ.esc(p.job_address || "")}" data-name="${CIQ.esc(p.display_name)}">Log activity</button>` : ""}
      </div>
    </div>`;
  }

  async function load() {
    const list = document.getElementById("list");
    list.className = ""; list.innerHTML = CIQ.skeletonRows(4);
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => { if (v) params.set(k, v); });
    params.set("page", page); params.set("page_size", 24);
    let data;
    try { data = await CIQ.api.get("/api/sales/opportunities?" + params.toString()); }
    catch (e) { list.innerHTML = CIQ.errorBanner(e.message); return; }
    if (!data.items.length) {
      list.innerHTML = CIQ.emptyState({ icon: "◎", title: "No opportunities match",
        text: "Projects tied to your assigned companies will appear here." });
      document.getElementById("pager").innerHTML = ""; return;
    }
    list.className = "grid-cards";
    list.innerHTML = data.items.map(card).join("");
    list.querySelectorAll("button[data-cid]").forEach((b) => b.addEventListener("click", () =>
      CIQ.logActivity({ companyId: Number(b.dataset.cid), companyName: b.dataset.name,
        projects: [{ project_id: b.dataset.pid, job_address: b.dataset.addr }],
        prefill: { subject: b.dataset.addr || "" }, onSaved: load })));
    CIQ.pager(document.getElementById("pager"), { page: data.page, pages: data.pages, total: data.total,
      onPage: (p) => { page = p; load(); window.scrollTo(0, 0); } });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("projects.view_assigned",
      { title: "Opportunities", subtitle: "Projects that created the opening", active: "opportunities.html" });
    if (!user) return;
    canEdit = CIQ.hasPerm("crm.activities.create");
    const lc = document.getElementById("flifecycle");
    ["preconstruction", "permitting", "under_construction", "inspection", "completed"].forEach((v) => {
      const o = document.createElement("option"); o.value = v; o.textContent = CIQ.titleCase(v); lc.appendChild(o);
    });
    const fq = document.getElementById("fq");
    fq.addEventListener("input", CIQ.debounce(() => { filters.q = fq.value.trim(); page = 1; load(); }, 350));
    lc.addEventListener("change", () => { filters.lifecycle = lc.value; page = 1; load(); });
    document.getElementById("fscore").addEventListener("change", (e) => { filters.score_min = e.target.value; page = 1; load(); });
    load();
  });
})();
