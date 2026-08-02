/* Organization-wide administrator dashboard — never assignment-scoped. */
(function () {
  let data = null;

  const KPI_DEFS = [
    ["total_companies", "Total companies", "⌂", "my-companies.html", ""],
    ["total_projects", "Total projects", "▦", "opportunities.html", ""],
    ["total_permits", "Total permits", "☰", "opportunities.html", ""],
    ["new_submitted_opportunities", "New submitted", "◎", "opportunities.html", "good"],
    ["new_issued_permits", "New issued", "✓", "opportunities.html", "good"],
    ["high_priority_opportunities", "High-priority", "⚡", "my-companies.html?tier=Critical", "warn"],
    ["companies_awaiting_assignment", "Awaiting assignment", "⇄", "assignments.html", "warn"],
    ["estimates_awaiting_review", "Estimates awaiting review", "$", "estimator-work-queue.html", ""],
    ["estimates_approved_for_supplier", "Approved for supplier", "⬡", "product-search.html", ""],
    ["active_employees", "Active employees", "⦿", "user-management.html", ""],
    ["overdue_team_tasks", "Overdue team tasks", "⚠", "team-dashboard.html", "alert"],
  ];

  const ACTIONS = [
    ["opportunities.html", "View Projects"],
    ["my-companies.html", "View Companies"],
    ["opportunities.html", "Review Opportunities"],
    ["assignments.html", "Assign Work"],
    ["estimator-work-queue.html", "Review Estimates"],
    ["user-management.html", "Manage Users"],
    ["#run-refresh", "Run Morning Refresh"],
    ["catalog-admin.html", "Open Administration"],
  ];

  function kpiEmptyHint(key, v) {
    if (v > 0) return "";
    const hints = {
      new_submitted_opportunities: "No new submitted opportunities today",
      new_issued_permits: "No new issued permits today",
      high_priority_opportunities: "No high-priority opportunities",
      companies_awaiting_assignment: "No unassigned companies",
      estimates_awaiting_review: "No estimates awaiting review",
      overdue_team_tasks: "No overdue team tasks",
    };
    return hints[key] || "";
  }

  function renderKpis() {
    const k = data.kpis || {};
    document.getElementById("kpis").innerHTML = KPI_DEFS.map(([key, label, ico, href, cls]) => {
      const v = k[key] || 0;
      const hint = kpiEmptyHint(key, v);
      const alertCls = (key === "overdue_team_tasks" && v > 0) ? "alert"
        : (key === "companies_awaiting_assignment" && v > 0) ? "warn" : cls;
      return `<a class="kpi ${alertCls}" href="${href}" title="${CIQ.esc(hint || label)}">
        <span class="kpi-ico">${ico}</span>
        <div class="kpi-val">${v}</div>
        <div class="kpi-label">${label}</div>
        ${hint ? `<div class="muted" style="font-size:11px;margin-top:4px">${CIQ.esc(hint)}</div>` : ""}
      </a>`;
    }).join("");
  }

  function renderActions() {
    document.getElementById("actions").innerHTML = ACTIONS.map(([href, label]) => {
      if (href === "#run-refresh") {
        return `<button class="btn btn-sm" type="button" data-a="refresh">${CIQ.esc(label)}</button>`;
      }
      return `<a class="btn btn-sm" href="${href}">${CIQ.esc(label)}</a>`;
    }).join("");
    document.querySelectorAll('[data-a="refresh"]').forEach((b) =>
      b.addEventListener("click", runMorningRefresh));
  }

  async function runMorningRefresh() {
    if (!CIQ.hasPerm("pipeline.run")) {
      CIQ.toast("You do not have permission to run the morning refresh.", "error");
      return;
    }
    try {
      await CIQ.api.post("/api/admin/morning-refresh/run", {});
      CIQ.toast("Morning refresh started", "success");
      setTimeout(load, 1200);
    } catch (e) {
      CIQ.toast(e.message || "Could not start refresh", "error");
    }
  }

  function renderRefresh() {
    const el = document.getElementById("refreshCard");
    const mr = data.morning_refresh || {};
    const status = mr.status || "none";
    const cls = ({ succeeded: "green", partial: "amber", failed: "red", running: "" })[status] || "";
    const when = mr.last_completed ? CIQ.relTime(mr.last_completed) : "—";
    const running = mr.running ? " · in progress" : "";
    el.innerHTML = `<div class="company-card"><div class="cc-top">
      <div><div class="cc-name" style="font-size:14.5px">Morning refresh status</div>
      <div class="cc-loc">Last completed ${when}${running}</div></div>
      <span class="badge ${cls}">${CIQ.esc(mr.label || "No refresh yet")}</span></div></div>`;
  }

  function renderFreshness() {
    const el = document.getElementById("freshness");
    const items = data.jurisdiction_freshness || [];
    if (!items.length) {
      el.innerHTML = CIQ.emptyState({ title: "All jurisdictions are current",
        text: "Freshness will appear after the next morning refresh." });
      return;
    }
    const bad = items.filter((j) => j.status && j.status !== "Current");
    if (!bad.length) {
      el.innerHTML = CIQ.emptyState({ icon: "✓", title: "All jurisdictions are current",
        text: `${items.length} jurisdiction${items.length === 1 ? "" : "s"} reporting.` });
    }
    const rows = (bad.length ? bad : items).map((j) => {
      const cls = j.status === "Current" ? "green" : j.status === "Delayed" ? "amber" : "red";
      return `<tr><td data-label="Jurisdiction">${CIQ.esc(j.name || j.slug || "—")}</td>
        <td data-label="Status"><span class="badge ${cls}">${CIQ.esc(j.status || "—")}</span></td>
        <td data-label="Synced">${j.newest_source_date ? CIQ.fmtDate(j.newest_source_date) : "—"}</td>
        <td data-label="Today">${j.records_received_today || 0}</td></tr>`;
    }).join("");
    el.innerHTML = (bad.length ? "" : el.innerHTML) +
      `<div class="table-wrap"><table class="tbl responsive"><thead><tr>
      <th>Jurisdiction</th><th>Status</th><th>Newest source</th><th>Received today</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderPipeline() {
    const el = document.getElementById("pipeline");
    const items = data.recent_pipeline_activity || [];
    if (!items.length) {
      el.innerHTML = CIQ.emptyState({ title: "No pipeline runs yet",
        text: "Run a morning refresh to populate pipeline history." });
      return;
    }
    el.innerHTML = `<div class="table-wrap"><table class="tbl responsive"><thead><tr>
      <th>Started</th><th>Status</th><th>Source</th><th>Succeeded</th><th>Failed</th><th>Created</th><th>Updated</th>
      </tr></thead><tbody>${items.map((r) => `<tr>
        <td data-label="Started">${r.started_at ? CIQ.relTime(r.started_at) : "—"}</td>
        <td data-label="Status"><span class="badge">${CIQ.esc(r.status || "—")}</span></td>
        <td data-label="Source">${CIQ.esc(r.trigger_source || "—")}</td>
        <td data-label="Succeeded">${r.jurisdictions_succeeded ?? "—"}</td>
        <td data-label="Failed">${r.jurisdictions_failed ?? "—"}</td>
        <td data-label="Created">${r.records_created ?? "—"}</td>
        <td data-label="Updated">${r.records_updated ?? "—"}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  function renderOpps() {
    const el = document.getElementById("opps");
    const items = data.recent_opportunity_activity || [];
    if (!items.length) {
      el.innerHTML = CIQ.emptyState({ title: "No new submitted opportunities today",
        text: "Organization-wide opportunity activity will appear here." });
      return;
    }
    el.innerHTML = `<div class="table-wrap"><table class="tbl responsive"><thead><tr>
      <th>Company</th><th>Jurisdiction</th><th>Lifecycle</th><th>Score</th><th>Date</th>
      </tr></thead><tbody>${items.map((o) => `<tr>
        <td data-label="Company">${CIQ.esc(o.display_name || "—")}</td>
        <td data-label="Jurisdiction">${CIQ.esc(o.jurisdiction || "—")}</td>
        <td data-label="Lifecycle">${CIQ.esc(o.project_lifecycle || "—")}</td>
        <td data-label="Score">${o.opportunity_score != null ? Math.round(o.opportunity_score) : "—"}</td>
        <td data-label="Date">${o.opportunity_date ? CIQ.fmtDate(o.opportunity_date) : "—"}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  async function load() {
    document.getElementById("kpis").innerHTML = CIQ.skeletonRows
      ? CIQ.skeletonRows(2) : `<div class="muted">Loading…</div>`;
    try {
      data = await CIQ.api.get("/api/admin/dashboard");
    } catch (e) {
      const msg = e.message || "Could not load admin dashboard";
      const hint = /unknown route|404/i.test(msg)
        ? " The CorridorIQ server needs a restart to load the admin dashboard API."
        : "";
      const el = document.getElementById("kpis");
      if (el) el.innerHTML = CIQ.errorBanner(msg + hint);
      return;
    }
    renderRefresh();
    renderKpis();
    renderActions();
    renderFreshness();
    renderPipeline();
    renderOpps();
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("admin.system", {
      title: "Admin Dashboard",
      subtitle: "Organization-wide pipeline and CRM",
      active: "admin-dashboard.html",
    });
    if (!user) return;
    const btn = document.getElementById("runRefreshBtn");
    if (btn) {
      if (!CIQ.hasPerm("pipeline.run")) btn.style.display = "none";
      else btn.addEventListener("click", runMorningRefresh);
    }
    load();
  });
})();
