/* Organization-wide administrator dashboard — never assignment-scoped. */
(function () {
  let data = null;

  const PRIMARY_KPIS = [
    ["new_submitted_opportunities", "New submitted today", "◎", "opportunities.html?lifecycle=submitted&age_days=1", "good", "Fresh permit applications worth reviewing"],
    ["new_issued_permits", "New permits issued", "✓", "opportunities.html?lifecycle=Permit%20Issued&age_days=1", "good", "Projects moving into purchasing windows"],
    ["high_priority_opportunities", "High-priority targets", "⚡", "opportunity-board.html?score_min=80", "warn", "Best active revenue opportunities"],
    ["contact_ready_contractors", "Ready to contact", "☎", "my-companies.html?view=contact", "good", "Verified contractors with a reachable contact channel"],
  ];

  const SECONDARY_KPIS = [
    ["total_companies", "Companies", "⌂", "my-companies.html"],
    ["total_projects", "Projects", "▦", "opportunities.html"],
    ["total_permits", "Permit records", "☰", "opportunities.html"],
    ["estimates_awaiting_review", "Estimate queue", "$", "estimator-work-queue.html"],
    ["estimates_approved_for_supplier", "Material lines", "⬡", "product-search.html"],
    ["active_employees", "Active users", "⦿", "user-management.html"],
    ["overdue_team_tasks", "Overdue tasks", "⚠", "team-dashboard.html"],
    ["contractors_needing_enrichment", "Need contact research", "⌕", "my-companies.html?view=enrichment"],
    ["companies_awaiting_assignment", "Need assignment", "⇄", "assignments.html"],
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
      contact_ready_contractors: "No verified contractors have contact information yet",
      estimates_awaiting_review: "No estimates awaiting review",
      overdue_team_tasks: "No overdue team tasks",
    };
    return hints[key] || "";
  }

  function renderKpis() {
    const k = data.kpis || {};
    document.getElementById("primaryKpis").innerHTML = PRIMARY_KPIS.map(
      ([key, label, ico, href, cls, context]) => {
        const value = Number(k[key] || 0);
        const zero = value === 0 ? " zero" : "";
        return `<a class="priority-card ${cls}${zero}" href="${href}">
          <div class="priority-top"><span class="priority-ico">${ico}</span>
            <span class="priority-arrow">→</span></div>
          <div class="priority-value">${value.toLocaleString()}</div>
          <div class="priority-label">${CIQ.esc(label)}</div>
          <div class="priority-context">${CIQ.esc(value ? context : kpiEmptyHint(key, value))}</div>
        </a>`;
      }).join("");

    document.getElementById("secondaryKpis").innerHTML = SECONDARY_KPIS.map(
      ([key, label, ico, href]) => {
        const value = Number(k[key] || 0);
        return `<a class="compact-kpi" href="${href}">
          <span class="compact-ico">${ico}</span>
          <span><strong>${value.toLocaleString()}</strong><small>${CIQ.esc(label)}</small></span>
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
      const btn = document.getElementById("runRefreshBtn");
      if (btn) { btn.disabled = true; btn.textContent = "Refreshing…"; }
      setTimeout(load, 1200);
      setTimeout(load, 7000);
    } catch (e) {
      CIQ.toast(e.message || "Could not start refresh", "error");
    }
  }

  function renderRefresh() {
    const el = document.getElementById("refreshCard");
    const mr = data.morning_refresh || {};
    const items = data.jurisdiction_freshness || [];
    const stale = items.filter((j) => String(j.status || "").toLowerCase() !== "current");
    const when = mr.last_completed ? CIQ.relTime(mr.last_completed) : "never";
    const running = Boolean(mr.running);
    const healthy = !running && stale.length === 0 && mr.status === "succeeded";
    const stateClass = running ? "running" : healthy ? "healthy" : "attention";
    const title = running ? "Refreshing permit sources…" :
      healthy ? "All connected sources are current" :
      stale.length ? stale.length + " source" + (stale.length === 1 ? "" : "s") + " need attention" :
      "Data refresh needs attention";
    const detail = running ? "New records will appear as each jurisdiction completes." :
      "Last pipeline completion: " + when + (stale.length ? " · review stale sources below" : "");
    el.innerHTML = `<div class="freshness-banner ${stateClass}">
      <span class="freshness-pulse"></span>
      <div><strong>${CIQ.esc(title)}</strong><span>${CIQ.esc(detail)}</span></div>
      <button class="btn btn-sm" type="button" data-refresh-now ${running ? "disabled" : ""}>
        ${running ? "Running…" : "Run refresh now"}
      </button>
    </div>`;
    const button = el.querySelector("[data-refresh-now]");
    if (button && !running) button.addEventListener("click", runMorningRefresh);
  }

  function renderVerification() {
    const v = data.data_verification || {};
    const latest = v.latest_source_refresh ? CIQ.fmtDateTime(v.latest_source_refresh) : "No completed refresh recorded";
    const metric = (label, number, sourceClass, sourceLabel) => `<div class="verification-metric">
      <span>${CIQ.esc(label)}</span><strong>${Number(number || 0).toLocaleString()}</strong>
      <small class="source-tag ${sourceClass}">${CIQ.esc(sourceLabel)}</small></div>`;
    document.getElementById("verificationPanel").innerHTML = `<div class="verification-head">
      <div><span class="live-proof"><i></i> LIVE TEST PROOF</span><h3>Release 14 Executive Briefs: ACTIVE</h3>
        <p>This panel is rendered by the running server and counted from its connected database—not from a spreadsheet or static mockup.</p></div>
      <div class="verification-build"><span>RUNNING BUILD</span><strong>${CIQ.esc(v.build_id || "Build unavailable")}</strong>
        <small>Database: ${CIQ.esc(v.database_status || "unknown")} · ${CIQ.esc(latest)}</small></div>
    </div><div class="verification-grid">
      ${metric("Permit records", v.public_permit_records, "source-public", "Municipal source")}
      ${metric("Project records", v.corridoriq_project_records, "source-derived", "CorridorIQ derived")}
      ${metric("Imported contacts", v.imported_contact_records, "source-imported", "Research import")}
      ${metric("CRM activities", v.crm_activity_records, "source-crm", "Entered in CRM")}
      ${metric("CRM relationships", v.crm_relationship_records, "source-crm", "Entered in CRM")}
      ${metric("Jurisdictions", v.jurisdiction_sources, "source-public", "Municipal source")}
    </div><div class="verification-legend"><strong>What is real:</strong>
      <span><i class="source-public"></i> public permit source</span>
      <span><i class="source-imported"></i> imported contact research</span>
      <span><i class="source-crm"></i> your CRM activity</span>
      <span><i class="source-derived"></i> CorridorIQ calculation</span></div>`;
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
    document.getElementById("primaryKpis").innerHTML = CIQ.skeletonRows
      ? CIQ.skeletonRows(2) : `<div class="muted">Loading…</div>`;
    try {
      data = await CIQ.api.get("/api/admin/dashboard");
    } catch (e) {
      const msg = e.message || "Could not load admin dashboard";
      const hint = /unknown route|404/i.test(msg)
        ? " The CorridorIQ server needs a restart to load the admin dashboard API."
        : "";
      const el = document.getElementById("primaryKpis");
      if (el) el.innerHTML = CIQ.errorBanner(msg + hint);
      return;
    }
    renderRefresh();
    renderVerification();
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
