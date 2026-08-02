// contractors.js — fetches data/exports/contractor_matching.json and renders
// the contractor matching page (searchable, filterable table).
// Requires a local server, same as dashboard.js — see README.md.

const EXPORTS_BASE = "data/exports";

let allContractors = [];
let jurisdictionNameLookup = {};

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

function formatPct(value) {
  if (value === null || value === undefined) return "—";
  return Math.round(value) + "%";
}

function escapeHtml(value) {
  if (value === null || value === undefined || value === "") return "—";
  const div = document.createElement("div");
  div.textContent = String(value);
  return div.innerHTML;
}

function jurisdictionLabel(slug) {
  return jurisdictionNameLookup[slug] || slug;
}

function renderStatTiles(data) {
  const grid = document.getElementById("statTileGrid");
  const tiles = [
    { value: data.total_contractors, label: "Contractors in directory" },
    { value: data.multi_jurisdiction_count, label: "Matched across jurisdictions" },
    { value: data.connected_jurisdictions.join(", ") || "None yet", label: "Connected jurisdictions" },
  ];
  grid.innerHTML = tiles
    .map(
      (t) => `
      <div class="stat-tile">
        <p class="stat-tile-value">${escapeHtml(t.value)}</p>
        <p class="stat-tile-label">${escapeHtml(t.label)}</p>
      </div>`
    )
    .join("");
}

function renderTable(contractors) {
  const tbody = document.querySelector("#contractorsTable tbody");
  document.getElementById("resultCount").textContent = `${contractors.length} contractor(s) shown`;

  if (!contractors.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="dashboard-empty">No contractors match your filters.</td></tr>';
    return;
  }

  tbody.innerHTML = contractors
    .map((c) => {
      const badges = c.jurisdictions_worked
        .map((slug) => {
          const count = c.jurisdiction_breakdown[slug] || 0;
          return `<span class="status-chip status-chip-connected jurisdiction-badge">${escapeHtml(jurisdictionLabel(slug))} (${escapeHtml(count)})</span>`;
        })
        .join(" ");

      return `
      <tr>
        <td>${escapeHtml(c.name)}${c.is_multi_jurisdiction ? ' <span class="status-chip status-chip-connected">Multi-market</span>' : ""}</td>
        <td>${badges}</td>
        <td>${escapeHtml(c.permit_count)}</td>
        <td>${formatPct(c.commercial_pct)}</td>
        <td>${formatMoney(c.avg_project_value)}</td>
        <td>${escapeHtml(c.growth_trend)}</td>
        <td class="score-cell">${escapeHtml(c.opportunity_rating)}</td>
        <td>${escapeHtml((c.last_permit_date || "").slice(0, 10))}</td>
      </tr>`;
    })
    .join("");
}

function applyFilters() {
  const query = document.getElementById("searchInput").value.trim().toLowerCase();
  const multiOnly = document.getElementById("multiOnlyToggle").checked;

  let filtered = allContractors;
  if (multiOnly) {
    filtered = filtered.filter((c) => c.is_multi_jurisdiction);
  }
  if (query) {
    filtered = filtered.filter((c) => c.name.toLowerCase().includes(query));
  }
  renderTable(filtered);
}

async function loadContractorMatching() {
  const loadingEl = document.getElementById("loadingState");
  const errorEl = document.getElementById("errorState");
  const contentEl = document.getElementById("matchingContent");

  try {
    const [matching, jurisdictions] = await Promise.all([
      fetchJSON("contractor_matching.json"),
      fetchJSON("jurisdictions_status.json"),
    ]);

    jurisdictionNameLookup = Object.fromEntries(jurisdictions.jurisdictions.map((j) => [j.slug, j.name]));
    allContractors = matching.contractors;

    renderStatTiles(matching);
    applyFilters();

    document.getElementById("generatedAt").textContent = `Data as of ${new Date(matching.generated_at).toLocaleString()}`;
    document.getElementById("searchInput").addEventListener("input", applyFilters);
    document.getElementById("multiOnlyToggle").addEventListener("change", applyFilters);

    loadingEl.style.display = "none";
    contentEl.style.display = "block";
  } catch (err) {
    loadingEl.style.display = "none";
    errorEl.style.display = "block";
    errorEl.textContent =
      "Couldn't load live data. If you opened this file directly in your browser, run a local server instead " +
      "(python -m http.server 8000, then visit http://localhost:8000/contractors.html) — browsers block fetching " +
      "local files over file://. Details: " + err.message;
  }
}

document.addEventListener("DOMContentLoaded", loadContractorMatching);
