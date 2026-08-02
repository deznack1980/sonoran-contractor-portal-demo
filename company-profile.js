/* CorridorIQ — Company profile. API-first; requires the company API for full
   detail. No AI-generated summaries — every field is derived from real data. */

const API_BASE = "http://127.0.0.1:8770";

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function num(v) { return v === null || v === undefined ? "—" : Number(v).toLocaleString(); }
function score(v) { return v === null || v === undefined ? "—" : Number(v).toFixed(0); }
function money(v) { return v === null || v === undefined ? "—" : "$" + Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }); }

function getId() {
  const p = new URLSearchParams(location.search);
  return p.get("id");
}

async function getJSON(path) {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function recommendedAction(lifecycle) {
  const map = {
    "Application Submitted": "Initial introduction — get on the radar before buyout.",
    "Plan Review": "Share capabilities and indicative pricing.",
    "Permit Issued": "Prepare a material quote immediately.",
    "Construction Active": "Follow up for change orders and additional needs.",
    "Inspection": "Confirm final material pulls.",
    "Finaled": "Archive — retain relationship history.",
    "Closed": "Archive.",
  };
  return map[lifecycle] || "Verify current status before outreach.";
}

function renderMetrics(intel) {
  const grid = document.getElementById("metricGrid");
  if (!intel) { grid.innerHTML = "<p>No metrics calculated yet.</p>"; return; }
  const cards = [
    ["Priority", `${score(intel.company_priority_score)} <span class="tier-badge tier-${esc(intel.company_priority_tier || "Low")}">${esc(intel.company_priority_tier || "Low")}</span>`],
    ["Total projects", num(intel.total_projects)],
    ["Active projects", num(intel.active_projects)],
    ["Projects (30d)", num(intel.projects_last_30_days)],
    ["Permits", num(intel.total_permits)],
    ["Avg opportunity score", score(intel.average_opportunity_score)],
    ["Highest score", score(intel.highest_opportunity_score)],
    ["Est. opportunity total", money(intel.estimated_opportunity_total)],
    ["Municipalities", num(intel.municipality_count)],
    ["Commercial / Residential", `${num(intel.commercial_project_count)} / ${num(intel.residential_project_count)}`],
    ["Activity trend", esc(intel.activity_trend || "—")],
    ["90d growth", intel.permit_growth_90d == null ? "—" : `${intel.permit_growth_90d}×`],
  ];
  grid.innerHTML = cards.map(([lbl, val]) => `
    <div class="metric-card"><div class="val">${val}</div><div class="lbl">${esc(lbl)}</div></div>`).join("");
}

function renderOverview(data) {
  const c = data.company;
  const kv = document.getElementById("overviewKv");
  const rows = [
    ["Canonical name", c.display_name || c.legal_name || "—"],
    ["Normalized", c.normalized_name],
    ["City / State", `${c.city || "—"}, ${c.state || "—"}`],
    ["License", c.license_number ? `${c.license_number} (${c.license_state || "?"})` : "—"],
    ["Phone", c.main_phone || "—"],
    ["Email", c.main_email || "—"],
    ["Website", c.website || "—"],
    ["First seen", c.first_seen_at || "—"],
    ["Last seen", c.last_seen_at || "—"],
  ];
  kv.innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");

  document.getElementById("rolesBox").innerHTML =
    (data.roles || []).map((r) => `<span class="role-chip">${esc(r.role_type)}${r.is_primary ? " ★" : ""}</span>`).join("") || "—";
  document.getElementById("aliasesBox").innerHTML =
    (data.aliases || []).map((a) => `<span class="alias-chip">${esc(a.alias_name)}</span>`).join("") || "—";
  document.getElementById("contactsBox").innerHTML =
    (data.contacts || []).length
      ? data.contacts.map((ct) => `<span class="role-chip">${esc(ct.full_name || "?")}${ct.job_title ? " · " + esc(ct.job_title) : ""}</span>`).join("")
      : "<span style='color:#94a3b8;'>None in source</span>";
}

function renderDataQuality(data) {
  const dq = data.data_quality || {};
  const kv = document.getElementById("dqKv");
  const missing = (dq.missing_fields || []);
  const rows = [
    ["Source records", num(dq.source_record_count)],
    ["Potential duplicates", missing.length ? num(dq.potential_duplicates) : num(dq.potential_duplicates)],
    ["Missing fields", missing.length ? `<span class="dq-flag">${missing.map(esc).join(", ")}</span>` : "none"],
    ["Metrics model", esc(dq.metrics_model_version || "—")],
  ];
  kv.innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("");
}

function renderOpportunities(items) {
  const tbody = document.querySelector("#opportunitiesTable tbody");
  const rows = (items || []).filter((p) => p.opportunity_score != null).slice(0, 100);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:16px;">No scored opportunities.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map((p) => `<tr title="${esc(recommendedAction(p.project_lifecycle))}">
    <td>${esc(p.permit_number || "—")}</td>
    <td>${esc(p.city || p.jurisdiction || "—")}</td>
    <td>${esc(p.project_category || "—")}</td>
    <td>${esc(p.project_lifecycle || "—")}</td>
    <td>${score(p.opportunity_score)}</td>
    <td>${esc(p.opportunity_date || "—")}</td>
    <td>${esc(p.opportunity_timing || "—")}</td>
  </tr>`).join("");
}

function renderTimeline(items) {
  const ul = document.getElementById("timelineList");
  if (!items || !items.length) { ul.innerHTML = "<li>No timeline activity.</li>"; return; }
  ul.innerHTML = items.slice(0, 200).map((a) => `
    <li><span class="date">${esc(a.activity_date || "")}</span>
        <span><strong>${esc(a.activity_type)}</strong> — ${esc(a.title || a.description || "")}</span></li>`).join("");
}

async function init() {
  const id = getId();
  const statusEl = document.getElementById("apiStatus");
  if (!id) { showError("No company id provided."); return; }
  try {
    await getJSON("/api/health");
    statusEl.textContent = "Live";
  } catch {
    showError("Company API offline. Start it with: python -m pipeline.company_resolution.api");
    return;
  }
  try {
    const [detail, projects, timeline] = await Promise.all([
      getJSON(`/api/companies/${id}`),
      getJSON(`/api/companies/${id}/projects`),
      getJSON(`/api/companies/${id}/timeline`),
    ]);
    if (detail.error) { showError("Company not found."); return; }
    document.getElementById("companyName").textContent = detail.company.display_name || "Company";
    const intel = detail.intelligence;
    document.getElementById("companySub").textContent =
      `${(detail.roles || []).map((r) => r.role_type).join(", ") || "company"} · ${detail.company.city || ""} ${detail.company.state || ""}`;
    renderMetrics(intel);
    renderOverview(detail);
    renderDataQuality(detail);
    renderOpportunities(projects.items);
    renderTimeline(timeline.items);
    document.getElementById("profileContent").style.display = "block";
  } catch (e) {
    showError("Failed to load company: " + e.message);
  }
}

function showError(msg) {
  const el = document.getElementById("errorState");
  el.style.display = "block";
  el.textContent = msg;
}

document.addEventListener("DOMContentLoaded", init);
