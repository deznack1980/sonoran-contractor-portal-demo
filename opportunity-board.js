/* Contractor CRM opportunity board — live data from the existing sales API. */
(function () {
  const STAGES = [
    { key: "new", label: "New Opportunity" },
    { key: "contacted", label: "Contacted" },
    { key: "materials", label: "Material List" },
    { key: "estimating", label: "Estimating" },
    { key: "quotes", label: "Supplier Quotes" },
    { key: "review", label: "Customer Review" },
  ];

  let items = [];
  let canEdit = false;

  function normalizeStage(p) {
    const status = String(p.relationship_status || p.sales_stage || "").toLowerCase();
    if (/won|lost|customer.review|proposal|quote.returned/.test(status)) return "review";
    if (/quote|rfq|supplier/.test(status)) return "quotes";
    if (/estim/.test(status)) return "estimating";
    if (/material|bom/.test(status)) return "materials";
    if (/contact|attempt|conversation|appointment/.test(status)) return "contacted";
    return "new";
  }

  function card(p) {
    const title = p.job_address || p.permit_number || "Project";
    const company = p.display_name || "Unknown contractor";
    const value = p.estimated_material_value != null ? CIQ.money(p.estimated_material_value) : "—";
    return `<article class="op-card" draggable="false" data-id="${p.project_id || ""}">
      <div class="cc-top">
        <div>
          <div class="op-card-title">${CIQ.esc(title)}</div>
          <div class="op-card-sub">${CIQ.esc(company)} · ${CIQ.esc(p.jurisdiction || p.city || "")}</div>
        </div>
        <span class="score-chip">${p.opportunity_score != null ? Math.round(p.opportunity_score) : "—"}</span>
      </div>
      <div class="op-card-meta">
        <div><span>Lifecycle</span>${CIQ.esc(CIQ.titleCase(p.project_lifecycle || "—"))}</div>
        <div><span>Est. materials</span>${value}</div>
        <div><span>Opportunity date</span>${p.opportunity_date ? CIQ.fmtDate(p.opportunity_date) : "—"}</div>
        <div><span>Category</span>${CIQ.esc(CIQ.titleCase(p.project_category || "—"))}</div>
      </div>
      <div class="op-card-actions">
        <a class="btn btn-sm btn-ghost" href="sales-company-profile.html?id=${p.company_id}">Open</a>
        <a class="btn btn-sm" href="material-list-intake.html?company_id=${p.company_id}&project_id=${p.project_id || ""}">Material List</a>
        ${canEdit ? `<button class="btn btn-sm" data-log="1" data-cid="${p.company_id}" data-pid="${p.project_id || ""}" data-name="${CIQ.esc(company)}" data-title="${CIQ.esc(title)}">Log Activity</button>` : ""}
      </div>
    </article>`;
  }

  function renderSummary(filtered) {
    const totalValue = filtered.reduce((sum, p) => sum + (Number(p.estimated_material_value) || 0), 0);
    const high = filtered.filter((p) => Number(p.opportunity_score) >= 80).length;
    const submitted = filtered.filter((p) => /submitted|preconstruction|permitting/i.test(p.project_lifecycle || "")).length;
    const defs = [
      [filtered.length, "Active opportunities"],
      [high, "Score 80+"],
      [submitted, "Early-stage leads"],
      [CIQ.money(totalValue), "Estimated material value"],
    ];
    document.getElementById("boardSummary").innerHTML = defs.map(([v, l]) =>
      `<div class="kpi" style="cursor:default"><div class="kpi-val">${v}</div><div class="kpi-label">${l}</div></div>`
    ).join("");
  }

  function render() {
    const q = document.getElementById("boardSearch").value.trim().toLowerCase();
    const minScore = Number(document.getElementById("boardScore").value || 60);
    const owner = document.getElementById("boardOwner").value;
    const userId = CIQ.user && (CIQ.user.id || CIQ.user.user_id);
    const filtered = items.filter((p) => {
      const haystack = [p.display_name, p.job_address, p.permit_number, p.jurisdiction, p.city]
        .filter(Boolean).join(" ").toLowerCase();
      const assignedId = p.assigned_user_id || p.assigned_to_user_id || p.owner_user_id || p.user_id;
      const isMine = !owner || (userId != null && String(assignedId) === String(userId));
      return Number(p.opportunity_score || 0) >= minScore && (!q || haystack.includes(q)) && isMine;
    });

    renderSummary(filtered);
    const grouped = Object.fromEntries(STAGES.map((s) => [s.key, []]));
    filtered.forEach((p) => grouped[normalizeStage(p)].push(p));

    document.getElementById("board").innerHTML = STAGES.map((stage) => {
      const stageItems = grouped[stage.key];
      return `<section class="crm-column" data-stage="${stage.key}">
        <div class="crm-column-head"><div class="crm-column-title">
          <span>${stage.label}</span><span class="crm-column-count">${stageItems.length}</span>
        </div></div>
        <div class="crm-column-body">
          ${stageItems.length ? stageItems.map(card).join("") : '<div class="pipeline-empty">No opportunities</div>'}
        </div>
      </section>`;
    }).join("");

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
  }

  async function load() {
    const board = document.getElementById("board");
    board.innerHTML = CIQ.skeletonRows(5);
    try {
      const data = await CIQ.api.get("/api/sales/opportunities?page=1&page_size=200&score_min=60");
      items = data.items || [];
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
    document.getElementById("boardSearch").addEventListener("input", CIQ.debounce(render, 250));
    document.getElementById("boardScore").addEventListener("change", render);
    document.getElementById("boardOwner").addEventListener("change", render);
    load();
  });
})();
