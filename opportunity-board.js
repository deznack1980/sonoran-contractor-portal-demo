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
    return `<article class="op-card" data-id="${p.project_id || ""}">
      <div class="cc-top">
        <div>
          <div class="op-card-title">${CIQ.esc(title)}</div>
          <div class="op-card-sub">${CIQ.esc(company)} · ${CIQ.esc(p.jurisdiction || p.city || "")}</div>
        </div>
        <span class="score-chip">${p.opportunity_score != null ? Math.round(p.opportunity_score) : "—"}</span>
      </div>
      <div class="op-card-badges">
        <span class="badge ${p.has_contact_info ? "green" : "amber"}">${p.has_contact_info ? "Contact ready" : "Needs contact"}</span>
        <span class="badge slate">${CIQ.esc(CIQ.statusLabel(p.relationship_status || "new"))}</span>
      </div>
      <div class="op-card-meta">
        <div><span>Lifecycle</span>${CIQ.esc(CIQ.titleCase(p.project_lifecycle || "—"))}</div>
        <div><span>Est. materials</span>${value}</div>
        <div><span>Opportunity date</span>${p.opportunity_date ? CIQ.fmtDate(p.opportunity_date) : "—"}</div>
        <div><span>Category</span>${CIQ.esc(CIQ.titleCase(p.project_category || "—"))}</div>
        <div><span>Assigned to</span>${CIQ.esc(p.assigned_to || "Unassigned")}</div>
        <div><span>Material lists / RFQs</span>${Number(p.material_list_count || 0)} / ${Number(p.quote_request_count || 0)}</div>
      </div>
      ${statusSelect(p)}
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
      [totalAvailable, "Matching opportunities"],
      [high, "Score 80+"],
      [submitted, "Early-stage leads"],
      [CIQ.money(totalValue), "Visible estimated value"],
    ];
    document.getElementById("boardSummary").innerHTML = defs.map(([v, l]) =>
      `<div class="kpi" style="cursor:default"><div class="kpi-val">${v}</div><div class="kpi-label">${l}</div></div>`
    ).join("");
  }

  function render() {
    const filtered = items;

    renderSummary(filtered);
    document.getElementById("boardNotice").textContent = totalAvailable > filtered.length
      ? `Showing the top ${filtered.length.toLocaleString()} of ${totalAvailable.toLocaleString()} matching opportunities. Refine the filters to narrow the queue.`
      : `${totalAvailable.toLocaleString()} matching contractor opportunit${totalAvailable === 1 ? "y" : "ies"}.`;
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
      if (q) params.set("q", q);
      if (score) params.set("score_min", score);
      if (owner) params.set("owner", owner);
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
    load();
  });
})();
