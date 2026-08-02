// dashboard.js — fetches data/exports/*.json and renders the live dashboard.
// Requires the site to be served over http(s), not opened as a file:// URL
// (browsers block fetch() of local files under file://). See README.md.

const EXPORTS_BASE = "data/exports";

async function fetchJSON(filename) {
  const response = await fetch(`${EXPORTS_BASE}/${filename}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${filename}: HTTP ${response.status}`);
  }
  return response.json();
}

function formatMoney(value) {
  if (value === null || value === undefined) return "—";
  return "$" + Math.round(value).toLocaleString("en-US");
}

function formatCompactMoney(value, { plus = false } = {}) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  // Shortest possible display so totals fit inside KPI tiles.
  let core;
  if (abs >= 1e9) core = "$" + Math.round(n / 1e9) + "B";
  else if (abs >= 1e6) core = "$" + Math.round(n / 1e6) + "M";
  else if (abs >= 1e3) core = "$" + Math.round(n / 1e3) + "K";
  else core = "$" + Math.round(n).toLocaleString("en-US");
  return plus && abs >= 1e6 ? core + "+" : core;
}

function formatPct(value) {
  if (value === null || value === undefined) return "—";
  return Math.round(value) + "%";
}

function escapeHtml(value) {
  if (value === null || value === undefined) return "—";
  const div = document.createElement("div");
  div.textContent = String(value);
  return div.innerHTML;
}

/**
 * Clearport-style KPI readout: big number + short supporting sentence.
 * Material / profit are scoped to scored opportunities — never the same
 * duplicate dollar shown twice under different labels.
 */
function renderStatTiles(summary) {
  const grid = document.getElementById("statTileGrid");
  const floor = summary.pipeline_score_floor ?? 60;
  const tracked =
    summary.jurisdictions_tracked_count ??
    summary.jurisdictions_connected_count + summary.jurisdictions_pending_count;
  const material = summary.totals.estimated_material_value_sum;
  const profit = summary.totals.potential_gross_profit_sum;

  const tiles = [
    {
      value: (summary.high_opportunity_count ?? 0).toLocaleString("en-US"),
      label: "High-confidence targets",
      detail: "Projects scoring 85+ across connected Arizona markets",
    },
    {
      value: formatCompactMoney(material, { plus: true }),
      label: "Estimated material value",
      detail: `Plumbing materials on opportunities scoring ${floor}+`,
      title: formatMoney(material),
      money: true,
    },
    {
      value: formatCompactMoney(profit, { plus: true }),
      label: "Potential gross profit",
      detail: "Supplier margin on that same scored pipeline",
      title: formatMoney(profit),
      money: true,
    },
    {
      value: `${summary.jurisdictions_connected_count} / ${tracked}`,
      label: "Markets live",
      detail: "Arizona jurisdictions connected vs tracked",
    },
  ];

  grid.innerHTML = tiles
    .map(
      (t) => `
      <div class="stat-tile${t.money ? " stat-tile-money" : ""}" ${t.title ? `title="${escapeHtml(t.title)}"` : ""}>
        <p class="stat-tile-value">${escapeHtml(t.value)}</p>
        <p class="stat-tile-label">${escapeHtml(t.label)}</p>
        <p class="stat-tile-detail">${escapeHtml(t.detail)}</p>
      </div>`
    )
    .join("");
}

function renderJurisdictions(statusData) {
  const grid = document.getElementById("jurisdictionGrid");
  grid.innerHTML = statusData.jurisdictions
    .map((j) => {
      const chipClass = j.status === "connected" ? "status-chip-connected" : "status-chip-pending";
      const chipLabel = j.status === "connected" ? "Connected" : "Pending";
      return `
        <div class="jurisdiction-card">
          <span class="status-chip ${chipClass}">${chipLabel}</span>
          <span class="jurisdiction-name">${escapeHtml(j.name)}</span>
          <span class="jurisdiction-notes">${escapeHtml(j.notes)}</span>
        </div>`;
    })
    .join("");
}

function renderCategoryBreakdown(summary) {
  const grid = document.getElementById("categoryGrid");
  const entries = Object.entries(summary.category_breakdown || {}).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) {
    grid.innerHTML = '<p class="dashboard-empty">No categorized projects yet.</p>';
    return;
  }
  grid.innerHTML = entries
    .map(
      ([category, count]) => `
      <div class="category-chip">
        <span>${escapeHtml(category)}</span>
        <span class="category-count">${escapeHtml(count)}</span>
      </div>`
    )
    .join("");
}

function renderHighOpportunityTable(data) {
  const tbody = document.querySelector("#highOpportunityTable tbody");
  if (!data.projects.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="dashboard-empty">No high-opportunity projects yet.</td></tr>';
    return;
  }
  tbody.innerHTML = data.projects
    .map(
      (p) => `
      <tr>
        <td class="score-cell">${escapeHtml(p.opportunity_score?.toFixed?.(0) ?? p.opportunity_score)}</td>
        <td>${escapeHtml(p.permit_number)}</td>
        <td>${escapeHtml(p.city)}</td>
        <td>${escapeHtml(p.project_category)}</td>
        <td>${escapeHtml(p.general_contractor_name)}</td>
        <td>${formatMoney(p.estimated_material_value)}</td>
        <td>${escapeHtml(p.estimated_plumbing_scope)}</td>
      </tr>`
    )
    .join("");
}

function renderRecentPermitsTable(data) {
  const tbody = document.querySelector("#recentPermitsTable tbody");
  if (!data.permits.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="dashboard-empty">No permits issued in the last ${data.window_days} days in connected jurisdictions.</td></tr>`;
    return;
  }
  tbody.innerHTML = data.permits
    .slice(0, 30)
    .map(
      (p) => `
      <tr>
        <td>${escapeHtml((p.issued_date || "").slice(0, 10))}</td>
        <td>${escapeHtml(p.permit_number)}</td>
        <td>${escapeHtml(p.city)}</td>
        <td>${escapeHtml(p.project_category)}</td>
        <td class="score-cell">${escapeHtml(p.opportunity_score)}</td>
        <td>${escapeHtml((p.description || "").slice(0, 80))}</td>
      </tr>`
    )
    .join("");
}

function renderContractorsTable(data) {
  const tbody = document.querySelector("#contractorsTable tbody");
  if (!data.contractors.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="dashboard-empty">No contractor activity yet.</td></tr>';
    return;
  }
  tbody.innerHTML = data.contractors
    .slice(0, 30)
    .map(
      (c) => `
      <tr>
        <td>${escapeHtml(c.name)}</td>
        <td>${escapeHtml(c.permit_count)}</td>
        <td>${formatPct(c.commercial_pct)}</td>
        <td>${formatMoney(c.avg_project_value)}</td>
        <td>${escapeHtml(c.growth_trend)}</td>
        <td class="score-cell">${escapeHtml(c.opportunity_rating)}</td>
        <td>${escapeHtml((c.last_permit_date || "").slice(0, 10))}</td>
      </tr>`
    )
    .join("");
}

// ---------------------------------------------------------------
// Project lifecycle pipeline (Sprint 3) — filters + saved views.
// ---------------------------------------------------------------
const LIFECYCLE_FILTERS = [
  ["Submitted", "Application Submitted"],
  ["In Review", "Plan Review"],
  ["Issued", "Permit Issued"],
  ["Construction", "Construction Active"],
  ["Inspection", "Inspection"],
  ["Final", "Finaled"],
  ["Closed", "Closed"],
];
let lifecycleProjects = [];
let lifecycleView = "all";
const activeStages = new Set();

function renderLifecycleFilters() {
  const wrap = document.getElementById("lifecycleFilters");
  if (!wrap) return;
  wrap.innerHTML = LIFECYCLE_FILTERS.map(
    ([label, stage]) =>
      `<span class="lifecycle-chip${activeStages.has(stage) ? " is-on" : ""}" data-stage="${stage}">${label}</span>`
  ).join("");
  wrap.querySelectorAll(".lifecycle-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const stage = chip.dataset.stage;
      if (activeStages.has(stage)) activeStages.delete(stage);
      else activeStages.add(stage);
      renderLifecycleFilters();
      renderLifecycleTable();
    });
  });
}

function applyLifecycleView(list) {
  switch (lifecycleView) {
    case "earliest":
      return list.filter((p) =>
        ["Application Submitted", "Plan Review"].includes(p.project_lifecycle)
      );
    case "issued_today":
      return list.filter((p) => p.issued_today);
    case "construction":
      return list.filter((p) => p.project_lifecycle === "Construction Active");
    case "followup":
      return list.filter((p) =>
        ["Permit Issued", "Construction Active"].includes(p.project_lifecycle)
      );
    default:
      return list;
  }
}

function renderLifecycleTable() {
  const tbody = document.querySelector("#lifecycleTable tbody");
  if (!tbody) return;
  let list = applyLifecycleView(lifecycleProjects);
  if (activeStages.size) {
    list = list.filter((p) => activeStages.has(p.project_lifecycle));
  }
  if (!list.length) {
    tbody.innerHTML =
      '<tr><td colspan="9" class="dashboard-empty">No projects match this lifecycle view.</td></tr>';
    return;
  }
  tbody.innerHTML = list
    .slice(0, 60)
    .map((p) => {
      const timing = p.opportunity_timing || "—";
      const age =
        p.days_since_opportunity === null || p.days_since_opportunity === undefined
          ? "—"
          : `${p.days_since_opportunity}d`;
      const permitCell = p.job_url
        ? `<a href="${escapeHtml(p.job_url)}">${escapeHtml(p.permit_number)}</a>`
        : escapeHtml(p.permit_number);
      return `
      <tr>
        <td>${escapeHtml(p.project_lifecycle || "—")}</td>
        <td><span class="timing-badge timing-${escapeHtml(timing)}">${escapeHtml(timing)}</span></td>
        <td>${escapeHtml((p.opportunity_date || "").slice(0, 10) || "—")}<br><span style="color:#888;font-size:0.75rem;">${escapeHtml(p.opportunity_date_basis || "")}</span></td>
        <td>${escapeHtml(age)}</td>
        <td>${permitCell}</td>
        <td>${escapeHtml(p.city)}</td>
        <td>${escapeHtml(p.project_category)}</td>
        <td>${escapeHtml(p.status)}</td>
        <td class="score-cell">${escapeHtml(p.opportunity_score ?? "—")}</td>
      </tr>`;
    })
    .join("");
}

function wireLifecycleViews() {
  document.querySelectorAll(".lifecycle-view").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".lifecycle-view").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      lifecycleView = btn.dataset.view;
      renderLifecycleTable();
    });
  });
}

async function loadDashboard() {
  const loadingEl = document.getElementById("loadingState");
  const errorEl = document.getElementById("errorState");
  const contentEl = document.getElementById("dashboardContent");

  try {
    const [summary, jurisdictions, highOpportunity, recentPermits, contractors, lifecycle] =
      await Promise.all([
        fetchJSON("dashboard_summary.json"),
        fetchJSON("jurisdictions_status.json"),
        fetchJSON("high_opportunity_projects.json"),
        fetchJSON("permits_recent.json"),
        fetchJSON("contractors.json"),
        fetchJSON("lifecycle_pipeline.json").catch(() => ({ projects: [] })),
      ]);

    renderStatTiles(summary);
    renderJurisdictions(jurisdictions);
    renderCategoryBreakdown(summary);
    renderHighOpportunityTable(highOpportunity);
    renderRecentPermitsTable(recentPermits);
    renderContractorsTable(contractors);

    lifecycleProjects = lifecycle.projects || [];
    wireLifecycleViews();
    renderLifecycleFilters();
    renderLifecycleTable();

    document.getElementById("generatedAt").textContent = `Data as of ${new Date(summary.generated_at).toLocaleString()}`;
    loadingEl.style.display = "none";
    contentEl.style.display = "block";
  } catch (err) {
    loadingEl.style.display = "none";
    errorEl.style.display = "block";
    errorEl.textContent =
      "Couldn't load live data. If you opened this file directly in your browser, run a local server instead " +
      "(python -m http.server 8000, then visit http://localhost:8000/dashboard.html) — browsers block fetching " +
      "local files over file://. Details: " + err.message;
  }
}

document.addEventListener("DOMContentLoaded", loadDashboard);
