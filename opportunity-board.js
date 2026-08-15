/* Contractor CRM opportunity board — live data from the existing sales API. */
(function () {
  const STAGES = [
    { key: "new", label: "New Opportunity" },
    { key: "contacted", label: "Contacted" },
    { key: "qualified", label: "Qualified" },
    { key: "materials", label: "Material List" },
    { key: "quotes", label: "Supplier Quotes" },
    { key: "won", label: "Won" },
  ];

  let items = [];
  let totalAvailable = 0;
  let canEdit = false;
  let canUpdate = false;
  let activeStage = "all";

  function normalizeStage(p) {
    const status = String(p.relationship_status || "new").toLowerCase();
    if (status === "won") return "won";
    if (Number(p.quote_request_count || 0) > 0 || /quote|negotiat/.test(status)) return "quotes";
    if (Number(p.material_list_count || 0) > 0) return "materials";
    if (status === "qualified") return "qualified";
    if (/contact|attempt|follow_up/.test(status)) return "contacted";
    return "new";
  }

  const STATUS_OPTIONS = [
    ["new", "New"], ["assigned", "Assigned"], ["researching", "Researching"],
    ["attempted_contact", "Attempted contact"],
    ["contacted", "Contacted"], ["follow_up", "Follow-up"], ["qualified", "Qualified"],
    ["quote_requested", "Quote requested"], ["quote_sent", "Quote sent"],
    ["negotiating", "Negotiating"], ["won", "Won"], ["lost", "Lost"],
    ["do_not_contact", "Do not contact"],
  ];

  function statusSelect(p) {
    if (!canUpdate) return "";
    return `<select class="op-stage-select" data-status-company="${p.company_id}" aria-label="Update relationship status">
      ${STATUS_OPTIONS.map(([value, label]) => `<option value="${value}" ${p.relationship_status === value ? "selected" : ""}>${label}</option>`).join("")}
    </select>`;
  }

  function card(p) {
    const title = p.job_address || p.permit_number || "Project";
    const company = p.display_name || "Unknown contractor";
    const value = p.estimated_material_value != null ? CIQ.money(p.estimated_material_value) : "—";
    const stage = normalizeStage(p);
    return `<article class="op-card pipeline-opportunity" data-id="${p.project_id || ""}" data-stage="${stage}">
      <div class="pipeline-score" aria-label="Opportunity score ${p.opportunity_score != null ? Math.round(p.opportunity_score) : "not available"}">
        <strong>${p.opportunity_score != null ? Math.round(p.opportunity_score) : "—"}</strong><span>Priority</span>
      </div>
      <div class="pipeline-identity">
        <div class="pipeline-company-line">
          <a href="sales-company-profile.html?id=${p.company_id}">${CIQ.esc(company)}</a>
          <span class="badge ${p.has_contact_info ? "green" : "amber"}">${p.has_contact_info ? "Contact ready" : "Needs enrichment"}</span>
        </div>
        <div class="op-card-title">${CIQ.esc(title)}</div>
        <div class="op-card-sub">${CIQ.esc(p.jurisdiction || p.city || "Location unavailable")} · ${CIQ.esc(CIQ.titleCase(p.project_category || "Unclassified"))}</div>
        <div class="pipeline-badges">
          <span class="badge blue">${CIQ.esc(CIQ.titleCase(p.project_lifecycle || "Stage unknown"))}</span>
          <span class="badge slate">${CIQ.esc(CIQ.statusLabel(p.relationship_status || "new"))}</span>
          <span class="badge slate">${CIQ.esc(p.assigned_to || "Unassigned")}</span>
        </div>
      </div>
      <div class="pipeline-commercial">
        <span>Estimated material opportunity</span>
        <strong>${value}</strong>
        <small>${p.estimated_plumbing_scope ? CIQ.esc(p.estimated_plumbing_scope) : "Scope estimate not yet available"}</small>
      </div>
      <div class="pipeline-progress">
        <div><span>Opportunity date</span><strong>${p.opportunity_date ? CIQ.fmtDate(p.opportunity_date) : "—"}</strong></div>
        <div><span>Material lists</span><strong>${Number(p.material_list_count || 0)}</strong></div>
        <div><span>Supplier RFQs</span><strong>${Number(p.quote_request_count || 0)}</strong></div>
      </div>
      <div class="pipeline-actions">
        ${statusSelect(p)}
        <a class="btn btn-sm btn-primary" href="material-list-intake.html?company_id=${p.company_id}&project_id=${p.project_id || ""}">Build Material List</a>
        <div class="pipeline-secondary-actions">
          <a class="btn btn-sm btn-ghost" href="sales-company-profile.html?id=${p.company_id}">Company Profile</a>
          ${canEdit ? `<button class="btn btn-sm btn-ghost" data-log="1" data-cid="${p.company_id}" data-pid="${p.project_id || ""}" data-name="${CIQ.esc(company)}" data-title="${CIQ.esc(title)}">Log Activity</button>` : ""}
        </div>
      </div>
    </article>`;
  }

  function renderSummary(filtered) {
    const totalValue = filtered.reduce((sum, p) => sum + (Number(p.estimated_material_value) || 0), 0);
    const contactReady = filtered.filter((p) => p.has_contact_info).length;
    const materialReady = filtered.filter((p) => Number(p.material_list_count || 0) > 0).length;
    const defs = [
      [totalAvailable, "Verified opportunities", "Live CRM inventory"],
      [contactReady, "Contact-ready in view", "Ready for outreach"],
      [materialReady, "Material lists started", "Commercial workflow"],
      [CIQ.money(totalValue), "Estimated materials in view", "Preliminary opportunity"],
    ];
    document.getElementById("boardSummary").innerHTML = defs.map(([v, l, context]) =>
      `<div class="pipeline-kpi"><span>${l}</span><strong>${v}</strong><small>${context}</small></div>`
    ).join("");
  }

  function renderStageNav(grouped) {
    const allStages = [{ key: "all", label: "All Active" }, ...STAGES];
    const counts = { all: items.length };
    STAGES.forEach((stage) => { counts[stage.key] = grouped[stage.key].length; });
    document.getElementById("boardStageNav").innerHTML = allStages.map((stage) =>
      `<button type="button" role="tab" class="pipeline-stage-tab ${activeStage === stage.key ? "active" : ""}" data-stage-filter="${stage.key}" aria-selected="${activeStage === stage.key}">
        <span>${stage.label}</span><strong>${counts[stage.key] || 0}</strong>
      </button>`).join("");
    document.querySelectorAll("[data-stage-filter]").forEach((button) => button.addEventListener("click", () => {
      activeStage = button.dataset.stageFilter;
      render();
    }));
  }

  function render() {
    const grouped = Object.fromEntries(STAGES.map((s) => [s.key, []]));
    items.forEach((p) => grouped[normalizeStage(p)].push(p));
    const filtered = activeStage === "all" ? items : grouped[activeStage] || [];
    const activeDefinition = [{ key: "all", label: "All active opportunities" }, ...STAGES].find((stage) => stage.key === activeStage);

    renderSummary(items);
    renderStageNav(grouped);
    document.getElementById("queueTitle").textContent = activeDefinition?.label || "All active opportunities";
    document.getElementById("boardNotice").textContent = totalAvailable > items.length
      ? `${filtered.length.toLocaleString()} shown · top ${items.length.toLocaleString()} of ${totalAvailable.toLocaleString()} matches loaded`
      : `${filtered.length.toLocaleString()} opportunit${filtered.length === 1 ? "y" : "ies"}`;
    document.getElementById("board").innerHTML = filtered.length
      ? filtered.map(card).join("")
      : '<div class="pipeline-empty"><strong>No opportunities in this stage</strong><span>Choose another stage or reset the filters.</span></div>';

    document.querySelectorAll("button[data-log]").forEach((button) => {
      button.addEventListener("click", () => {
        CIQ.logActivity({
          companyId: Number(button.dataset.cid),
          companyName: button.dataset.name,
          projects: [{ project_id: button.dataset.pid, job_address: button.dataset.title }],
          prefill: { subject: button.dataset.title },
          onSaved: load,
        });
      });
    });
    document.querySelectorAll("select[data-status-company]").forEach((select) => {
      select.addEventListener("change", async () => {
        const companyId = Number(select.dataset.statusCompany);
        const previous = items.find((item) => Number(item.company_id) === companyId)?.relationship_status || "new";
        const next = select.value;
        if (["lost", "do_not_contact"].includes(next)) {
          const confirmed = await CIQ.confirm(
            next === "lost" ? "Mark this company as lost?" : "Set this company to Do Not Contact?",
            { danger: true, confirmLabel: "Confirm" });
          if (!confirmed) { select.value = previous; return; }
        }
        select.disabled = true;
        try {
          await CIQ.api.patch(`/api/sales/companies/${companyId}/relationship`, { relationship_status: next });
          CIQ.toast("Pipeline status updated", "success");
          await load();
        } catch (error) {
          select.value = previous;
          select.disabled = false;
          CIQ.toast(error.message || "Could not update pipeline status", "error");
        }
      });
    });
  }

  async function load() {
    const board = document.getElementById("board");
    board.innerHTML = CIQ.skeletonRows(5);
    try {
      const params = new URLSearchParams({ page: "1", page_size: "200", active_only: "1" });
      const q = document.getElementById("boardSearch").value.trim();
      const score = document.getElementById("boardScore").value;
      const owner = document.getElementById("boardOwner").value;
      const contact = document.getElementById("boardContact").value;
      if (q) params.set("q", q);
      if (score) params.set("score_min", score);
      if (owner) params.set("owner", owner);
      if (contact) params.set("contact_info", contact);
      const data = await CIQ.api.get("/api/sales/opportunities?" + params.toString());
      items = data.items || [];
      totalAvailable = Number(data.total || 0);
      render();
    } catch (e) {
      board.innerHTML = CIQ.errorBanner(e.message || "Could not load opportunity pipeline");
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("projects.view_assigned", {
      title: "Opportunity Pipeline",
      subtitle: "Turn permit intelligence into contractor revenue",
      active: "opportunity-board.html",
    });
    if (!user) return;
    canEdit = CIQ.hasPerm("crm.activities.create");
    canUpdate = CIQ.hasPerm("crm.relationships.update");
    const requestedScore = new URLSearchParams(location.search).get("score_min");
    if (requestedScore && [...document.getElementById("boardScore").options].some((option) => option.value === requestedScore)) {
      document.getElementById("boardScore").value = requestedScore;
    }
    const requestedOwner = new URLSearchParams(location.search).get("owner");
    if (requestedOwner && [...document.getElementById("boardOwner").options].some((option) => option.value === requestedOwner)) {
      document.getElementById("boardOwner").value = requestedOwner;
    }
    document.getElementById("boardSearch").addEventListener("input", CIQ.debounce(load, 300));
    document.getElementById("boardScore").addEventListener("change", load);
    document.getElementById("boardOwner").addEventListener("change", load);
    document.getElementById("boardContact").addEventListener("change", load);
    document.getElementById("boardReset").addEventListener("click", () => {
      document.getElementById("boardSearch").value = "";
      document.getElementById("boardScore").value = "60";
      document.getElementById("boardOwner").value = "";
      document.getElementById("boardContact").value = "";
      activeStage = "all";
      load();
    });
    load();
  });
})();
