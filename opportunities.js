/* Opportunities — high-scoring projects across assigned companies. */
(function () {
  let page = 1;
  const filters = { age_days: "365", lead_type: "verified_contractor" };
  let canEdit = false;

  function truncate(s, n) { s = s || ""; return s.length > n ? s.slice(0, n) + "…" : s; }

  function leadMeta(value) {
    const leadType = value || "unverified_permit_contact";
    const labels = {
      verified_contractor: ["Verified contractor", "green"],
      specifier_architect_engineer: ["Architect / Engineer", "blue"],
      owner_developer: ["Owner / Developer", "amber"],
      unverified_permit_contact: ["Unverified contact", "slate"],
    };
    return labels[leadType] || [CIQ.titleCase(leadType.replaceAll("_", " ")), "slate"];
  }

  function ageMeta(p) {
    const raw = p.opportunity_date || p.last_updated_at || p.issued_date || p.first_seen_at;
    if (!raw) return { label: "Date unknown", cls: "slate", days: null };
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return { label: "Date unknown", cls: "slate", days: null };
    const days = Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000));
    if (days <= 30) return { label: "Fresh · " + days + "d", cls: "green", days };
    if (days <= 90) return { label: "Recent · " + days + "d", cls: "blue", days };
    if (days <= 365) return { label: "Aged · " + days + "d", cls: "amber", days };
    return { label: "Historical · " + Math.floor(days / 365) + "y", cls: "slate", days };
  }

  function card(p) {
    const age = ageMeta(p);
    return `<div class="company-card">
      <div class="cc-top">
        <div><div class="cc-name" style="font-size:15px">${CIQ.esc(p.job_address || p.permit_number || "Project")}</div>
          <div class="cc-loc"><a href="sales-company-profile.html?id=${p.company_id}">${CIQ.esc(p.display_name)}</a> · ${CIQ.esc(p.jurisdiction || p.city || "")}</div></div>
        <div class="stack" style="align-items:flex-end;gap:5px">
          <span class="score-chip">${p.opportunity_score != null ? Math.round(p.opportunity_score) : "—"}</span>
          <span class="badge ${leadMeta(p.lead_type)[1]}">${CIQ.esc(leadMeta(p.lead_type)[0])}</span>
          <span class="badge ${age.cls}">${CIQ.esc(age.label)}</span>
          <span class="badge ${p.has_contact_info ? "green" : "amber"}">${p.has_contact_info ? "Contact available" : "No contact data"}</span>
        </div>
      </div>
      <div class="cc-meta">
        <div><span>Stage</span>${CIQ.esc(CIQ.titleCase(p.project_lifecycle || "—"))}</div>
        <div><span>Timing</span>${CIQ.esc(CIQ.titleCase(p.opportunity_timing || "—"))}</div>
        <div><span>Category</span>${CIQ.esc(CIQ.titleCase(p.project_category || "—"))}</div>
        <div><span>Opportunity</span>${p.opportunity_date ? CIQ.fmtDate(p.opportunity_date) : "—"}</div>
        <div><span>Company permit history</span>${p.company_permit_count != null ? p.company_permit_count : "—"}</div>
        <div><span>Est. value</span>${p.estimated_material_value != null ? CIQ.money(p.estimated_material_value) : "—"}</div>
      </div>
      ${p.why_this_lead ? `<div class="cc-reason"><strong>Why this lead:</strong> ${CIQ.esc(truncate(p.why_this_lead, 160))}</div>` : ""}
      ${p.description ? `<div class="cc-reason">${CIQ.esc(truncate(p.description, 120))}</div>` : ""}
      <div class="cc-quick">
        <a class="btn btn-sm btn-ghost" href="sales-company-profile.html?id=${p.company_id}">View company</a>
        <a class="btn btn-sm btn-primary" href="material-list-intake.html?company_id=${p.company_id}&project_id=${p.project_id}">Build material list</a>
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
    if (!data.classification_ready && filters.lead_type !== "all") {
      list.innerHTML = CIQ.emptyState({ icon: "⚠", title: "Lead classification required",
        text: "Run ApplyCorridorIQLeadIntegrity.bat once, then reopen Project Records. Unverified permit professionals are intentionally blocked from the contractor queue." });
      document.getElementById("pager").innerHTML = "";
      return;
    }
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
      { title: "Project Records", subtitle: "Permit-backed projects with clear source age", active: "opportunities.html" });
    if (!user) return;
    canEdit = CIQ.hasPerm("crm.activities.create");
    const lc = document.getElementById("flifecycle");
    ["Application Submitted", "Plan Review", "Permit Issued", "Construction Active", "Inspection", "Completed"].forEach((v) => {
      const o = document.createElement("option"); o.value = v; o.textContent = v; lc.appendChild(o);
    });
    const fq = document.getElementById("fq");
    const lead = document.getElementById("flead");
    const requestedLead = new URLSearchParams(location.search).get("lead_type");
    const allowedLeads = [...lead.options].map((option) => option.value);
    if (requestedLead && allowedLeads.includes(requestedLead)) {
      filters.lead_type = requestedLead;
      lead.value = requestedLead;
    }
    fq.addEventListener("input", CIQ.debounce(() => { filters.q = fq.value.trim(); page = 1; load(); }, 350));
    lead.addEventListener("change", () => { filters.lead_type = lead.value; page = 1; load(); });
    lc.addEventListener("change", () => { filters.lifecycle = lc.value; page = 1; load(); });
    document.getElementById("fscore").addEventListener("change", (e) => { filters.score_min = e.target.value; page = 1; load(); });
    document.getElementById("fcontact").addEventListener("change", (e) => { filters.contact_info = e.target.value; page = 1; load(); });
    document.getElementById("fage").addEventListener("change", (e) => { filters.age_days = e.target.value; page = 1; load(); });
    load();
  });
})();
