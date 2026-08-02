/* Executive sales dashboard — answers "what requires attention today?" */
(function () {
  let data = null;

  const KPI_DEFS = [
    ["calls_due_today", "Calls due today", "☎", "my-companies.html?view=followup", "warn"],
    ["overdue_followups", "Overdue follow-ups", "⚠", "my-companies.html?view=overdue", "alert"],
    ["new_assigned_companies", "New assigned", "＋", "my-companies.html?status=assigned", ""],
    ["high_priority_opportunities", "High-priority", "◎", "my-companies.html?tier=Critical", "good"],
    ["appointments_scheduled", "Appointments", "◷", "activity.html", ""],
    ["quotes_requested", "Quotes requested", "$", "my-companies.html?status=quote_requested", ""],
  ];

  function renderKpis() {
    const k = data.kpis || {};
    document.getElementById("kpis").innerHTML = KPI_DEFS.map(([key, label, ico, href, cls]) => {
      const v = k[key] || 0;
      const alertCls = (key === "overdue_followups" && v > 0) ? "alert"
        : (key === "calls_due_today" && v > 0) ? "warn" : cls;
      return `<a class="kpi ${alertCls}" href="${href}">
        <span class="kpi-ico">${ico}</span>
        <div class="kpi-val">${v}</div>
        <div class="kpi-label">${label}</div></a>`;
    }).join("");
  }

  function companyCard(c) {
    const overdue = c.followup_overdue
      ? `<span class="badge red">Overdue</span>` : "";
    return `<div class="company-card">
      <div class="cc-top">
        <div>
          <div class="cc-name"><a href="sales-company-profile.html?id=${c.company_id}">${CIQ.esc(c.display_name)}</a></div>
          <div class="cc-loc">${CIQ.esc([c.city, c.state].filter(Boolean).join(", ") || "—")}</div>
        </div>
        <div class="stack" style="align-items:flex-end;gap:6px">
          ${CIQ.tierBadge(c.company_priority_tier)}
          ${CIQ.statusBadge(c.relationship_status)}
        </div>
      </div>
      <div class="cc-reason">${CIQ.esc(c.reason)}</div>
      <div class="cc-meta">
        <div><span>Active projects</span>${c.active_projects || 0}</div>
        <div><span>Top project score</span>${c.highest_opportunity_score != null ? Math.round(c.highest_opportunity_score) : "—"}</div>
        <div><span>Last contact</span>${c.last_contact_at ? CIQ.relTime(c.last_contact_at) : "Never"}</div>
        <div><span>Next follow-up</span>${c.next_followup_at ? CIQ.fmtDate(c.next_followup_at) : "—"} ${overdue}</div>
      </div>
      <div class="cc-action"><span>Next:</span> <span class="next">${CIQ.esc(c.recommended_action)}</span></div>
      <div class="cc-quick">
        <button class="btn btn-sm" data-a="call" data-id="${c.company_id}" data-name="${CIQ.esc(c.display_name)}">Call</button>
        <button class="btn btn-sm" data-a="log" data-id="${c.company_id}" data-name="${CIQ.esc(c.display_name)}">Log activity</button>
        <button class="btn btn-sm" data-a="followup" data-id="${c.company_id}" data-name="${CIQ.esc(c.display_name)}">Follow-up</button>
        <a class="btn btn-sm btn-ghost" href="sales-company-profile.html?id=${c.company_id}">Open</a>
      </div>
    </div>`;
  }

  function renderPriority() {
    const el = document.getElementById("priority");
    const items = data.priority_companies || [];
    if (!items.length) {
      const assignmentScoped = data.assignment_scoped !== false
        && !(CIQ.user && CIQ.user.dashboard_mode === "organization");
      if (assignmentScoped) {
        el.innerHTML = CIQ.emptyState({ title: "No companies assigned to you",
          text: "When companies are assigned to you they'll appear here, prioritized by opportunity." });
      } else {
        el.innerHTML = CIQ.emptyState({ title: "No priority companies right now",
          text: "Organization relationships will appear here when CRM assignments exist." });
      }
      return;
    }
    el.className = "grid-cards";
    el.innerHTML = items.map(companyCard).join("");
    el.querySelectorAll("button[data-a]").forEach((b) => b.addEventListener("click", () => {
      const id = Number(b.dataset.id), name = b.dataset.name;
      const prefill = b.dataset.a === "call" ? { activity_type: "call" }
        : b.dataset.a === "followup" ? { activity_type: "follow_up" } : {};
      CIQ.logActivity({ companyId: id, companyName: name, prefill, onSaved: load });
    }));
  }

  function renderFollowups() {
    const el = document.getElementById("followups");
    const items = data.followups_due || [];
    if (!items.length) {
      el.innerHTML = CIQ.emptyState({ icon: "✓", title: "No follow-ups due", text: "You're all caught up." });
      return;
    }
    el.innerHTML = `<div class="table-wrap"><table class="tbl responsive"><thead><tr>
      <th>Company</th><th>Owner</th><th>Due</th><th>Last outcome</th><th>Required action</th><th></th>
      </tr></thead><tbody>${items.map((f) => `<tr>
        <td data-label="Company"><a href="sales-company-profile.html?id=${f.company_id}">${CIQ.esc(f.display_name)}</a></td>
        <td data-label="Owner">${CIQ.esc(f.assigned_to || "—")}</td>
        <td data-label="Due">${CIQ.fmtDate(f.next_followup_at)} ${f.overdue ? '<span class="badge red">Overdue</span>' : ""}</td>
        <td data-label="Last outcome">${f.last_outcome ? CIQ.esc(CIQ.outcomeLabel(f.last_outcome)) : '<span class="muted">—</span>'}</td>
        <td data-label="Action">${CIQ.esc(f.recommended_action)}</td>
        <td data-label=""><button class="btn btn-sm" data-id="${f.company_id}" data-name="${CIQ.esc(f.display_name)}">Log</button></td>
      </tr>`).join("")}</tbody></table></div>`;
    el.querySelectorAll("button[data-id]").forEach((b) => b.addEventListener("click", () =>
      CIQ.logActivity({ companyId: Number(b.dataset.id), companyName: b.dataset.name, onSaved: load })));
  }

  function renderOpps() {
    const el = document.getElementById("opps");
    const items = data.recent_opportunity_activity || [];
    if (!items.length) {
      el.innerHTML = CIQ.emptyState({ title: "No recent project activity", text: "New permits and projects tied to your companies will show here." });
      return;
    }
    el.className = "grid-cards";
    el.innerHTML = items.map((p) => `<div class="company-card">
      <div class="cc-top">
        <div><div class="cc-name" style="font-size:14.5px">${CIQ.esc(p.job_address || p.jurisdiction || "Project")}</div>
          <div class="cc-loc">${CIQ.esc(p.display_name)} · ${CIQ.esc(p.jurisdiction || p.city || "")}</div></div>
        <span class="score-chip">${p.opportunity_score != null ? Math.round(p.opportunity_score) : "—"}</span>
      </div>
      <div class="cc-meta">
        <div><span>Stage</span>${CIQ.esc(CIQ.titleCase(p.project_lifecycle || "—"))}</div>
        <div><span>Timing</span>${CIQ.esc(CIQ.titleCase(p.opportunity_timing || "—"))}</div>
        <div><span>Category</span>${CIQ.esc(CIQ.titleCase(p.project_category || "—"))}</div>
        <div><span>Opportunity</span>${p.opportunity_date ? CIQ.fmtDate(p.opportunity_date) : "—"}</div>
      </div>
      <div class="cc-quick"><a class="btn btn-sm btn-ghost" href="sales-company-profile.html?id=${p.company_id}">Open company</a></div>
    </div>`).join("");
  }

  const REFRESH_CLASS = { succeeded: "green", partial: "amber", failed: "red", running: "" };

  function freshnessRows(freshness) {
    if (!freshness || !freshness.length) return "";
    const bad = freshness.filter((j) => j.status !== "Current");
    const rows = (bad.length ? bad : freshness).map((j) => {
      const cls = j.status === "Current" ? "green" : j.status === "Delayed" ? "amber" : "red";
      return `<tr><td data-label="Jurisdiction">${CIQ.esc(j.name)}</td>
        <td data-label="Status"><span class="badge ${cls}">${CIQ.esc(j.status)}</span></td>
        <td data-label="Newest source">${j.newest_source_date ? CIQ.fmtDate(j.newest_source_date) : "—"}</td>
        <td data-label="Received today">${j.records_received_today || 0}</td></tr>`;
    }).join("");
    const heading = bad.length ? "Jurisdictions needing attention" : "All jurisdictions current";
    return `<div class="section-title" style="margin-top:14px">${heading}</div>
      <div class="table-wrap"><table class="tbl responsive"><thead><tr>
      <th>Jurisdiction</th><th>Status</th><th>Newest source</th><th>Received today</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  async function renderRefresh() {
    const el = document.getElementById("refreshCard");
    const canMonitor = CIQ.hasPerm("pipeline.monitor");
    let simple;
    try { simple = await CIQ.api.get("/api/status/refresh"); }
    catch (e) { el.innerHTML = ""; return; }
    const status = simple.status || "none";
    const cls = REFRESH_CLASS[status] || "";
    const when = simple.last_completed ? CIQ.relTime(simple.last_completed) : "—";

    if (!canMonitor) {
      // Employees see a single, non-technical line only.
      el.innerHTML = `<div class="company-card"><div class="cc-top">
        <div><div class="cc-name" style="font-size:14.5px">Data status</div>
        <div class="cc-loc">Last refresh ${when}</div></div>
        <span class="badge ${cls}">${CIQ.esc(simple.label || "—")}</span></div></div>`;
      return;
    }

    let detail = null;
    try { detail = await CIQ.api.get("/api/admin/morning-refresh"); } catch (e) { detail = null; }
    const s = (detail && detail.summary) || {};
    const running = detail && detail.running;
    const canRun = CIQ.hasPerm("pipeline.run");
    const stats = [
      ["New submitted", s.new_submitted_opportunities],
      ["New issued", s.new_issued_permits],
      ["Projects updated", s.updated_projects],
      ["Estimator ready", s.estimator_ready],
    ].map(([l, v]) => `<div><span>${l}</span>${v != null ? v : "—"}</div>`).join("");

    el.innerHTML = `<div class="company-card">
      <div class="cc-top">
        <div><div class="cc-name" style="font-size:14.5px">Morning refresh</div>
        <div class="cc-loc">Last completed ${when}${s.duration_seconds != null ? " · " + s.duration_seconds + "s" : ""}</div></div>
        <div class="stack" style="align-items:flex-end;gap:6px">
          <span class="badge ${running ? "" : cls}">${running ? "Running…" : CIQ.esc(simple.label || "—")}</span>
          ${canRun ? `<button class="btn btn-sm" id="runRefreshBtn" ${running ? "disabled" : ""}>Run refresh now</button>` : ""}
        </div>
      </div>
      <div class="cc-meta">${stats}</div>
      ${freshnessRows(detail && detail.freshness)}
    </div>`;

    const btn = document.getElementById("runRefreshBtn");
    if (btn) btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await CIQ.api.post("/api/admin/morning-refresh/run", {});
        CIQ.toast("Morning refresh started.", "info");
        setTimeout(renderRefresh, 2500);
      } catch (e) {
        CIQ.toast(e.message || "Could not start refresh.", "error");
        btn.disabled = false;
      }
    });
  }

  function renderSummary() {
    const s = data.activity_summary || {};
    const defs = [
      ["calls_logged", "Calls logged"], ["conversations", "Conversations"],
      ["appointments", "Appointments"], ["quote_requests", "Quote requests"],
      ["followups_completed", "Follow-ups done"], ["companies_contacted", "Companies contacted"],
    ];
    document.getElementById("summary").innerHTML = defs.map(([k, l]) =>
      `<div class="kpi" style="cursor:default"><div class="kpi-val">${s[k] || 0}</div>
       <div class="kpi-label">${l}</div></div>`).join("");
  }

  async function load() {
    try {
      data = await CIQ.api.get("/api/sales/dashboard");
    } catch (e) {
      CIQ.content.innerHTML = CIQ.errorBanner(e.message || "Could not load dashboard");
      return;
    }
    CIQ.setTaskCount((data.kpis && data.kpis.tasks_due_today) || 0);
    renderKpis(); renderPriority(); renderFollowups(); renderOpps(); renderSummary();
    renderRefresh();
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard(null, { title: "Dashboard", subtitle: "What needs attention today", active: "sales-dashboard.html" });
    if (!user) return;
    // Admins / managers belong on their role dashboards, not the rep view.
    if (user.dashboard_mode === "organization" || user.dashboard_mode === "team"
        || user.dashboard_mode === "estimator" || user.dashboard_mode === "read_only") {
      const dest = user.default_landing_page || CIQ.landingPage();
      if (dest && !location.pathname.endsWith(dest)) {
        location.replace(dest);
        return;
      }
    }
    document.getElementById("kpis").innerHTML = CIQ.skeletonRows(1);
    document.getElementById("priority").innerHTML = CIQ.skeletonRows(3);
    load();
  });
})();
