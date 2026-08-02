/**
 * Municipal Knowledge review dashboard (Sprint 2.1).
 * Prioritized triage, impact preview, confidence/lifecycle, batch review, audit.
 * Prefers live API. Falls back to data/exports/knowledge_queue.json (read-only).
 */

const API_BASE = "http://127.0.0.1:8765";
const CANONICAL_STATUSES = [
  "issued", "in review", "applied", "finaled",
  "expired", "cancelled", "withdrawn", "unknown",
];
const TRADES = [
  "commercial", "residential", "plumbing", "mechanical",
  "electrical", "building", "gas", "water heater", "pool", "other",
];
const MAPPING_SOURCES = [
  "Source documentation",
  "Municipality-specific rule",
  "Description analysis",
  "Historical pattern",
  "Human judgment",
  "Bootstrap rule",
];

let apiLive = false;
let currentKind = "status";
let snapshot = null;
const selected = new Set();

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function confTier(pct) {
  if (pct == null) return "—";
  if (pct >= 95) return "Verified";
  if (pct >= 80) return "High";
  if (pct >= 60) return "Moderate";
  return "Low";
}

async function probeApi() {
  const el = document.getElementById("apiStatus");
  try {
    const res = await fetch(`${API_BASE}/api/health`, { cache: "no-store" });
    apiLive = res.ok;
    el.textContent = apiLive
      ? "API online — preview / approve / reject / batch enabled."
      : "API offline — showing export snapshot (read-only).";
    el.className = "api-status " + (apiLive ? "ok" : "bad");
  } catch {
    apiLive = false;
    el.textContent =
      "API offline — showing export snapshot (read-only). Start: python -m pipeline.knowledge.review_api";
    el.className = "api-status bad";
  }
}

async function loadSnapshot() {
  if (apiLive) {
    const res = await fetch(`${API_BASE}/api/snapshot`, { cache: "no-store" });
    if (!res.ok) throw new Error(`API snapshot failed (${res.status})`);
    return res.json();
  }
  const res = await fetch("data/exports/knowledge_queue.json", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(
      "No knowledge_queue.json yet. Run: python -m pipeline.analysis.run_analysis (or full pipeline)."
    );
  }
  return res.json();
}

function renderStats(counts, extra) {
  const grid = document.getElementById("statGrid");
  const tiers = (extra && extra.priority_tiers) || {};
  const items = [
    ["Pending total", counts.pending_total],
    ["Critical", tiers.Critical || 0],
    ["High", tiers.High || 0],
    ["Medium", tiers.Medium || 0],
    ["Low", tiers.Low || 0],
    ["Status dictionary", counts.status_dictionary],
    ["Code dictionary", counts.permit_code_dictionary],
    ["KB version", (extra && extra.kb_version) || "—"],
  ];
  grid.innerHTML = items
    .map(
      ([label, value]) =>
        `<div class="knowledge-stat"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(
          label
        )}</span></div>`
    )
    .join("");
}

function populateMuniFilter() {
  const sel = document.getElementById("filterMuni");
  const munis = [...new Set((snapshot?.queue || []).map((i) => i.municipality_slug || ""))]
    .filter(Boolean)
    .sort();
  const current = sel.value;
  sel.innerHTML =
    `<option value="">All municipalities</option>` +
    munis.map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
  sel.value = current;
}

function populateSourceSelects() {
  const opts = MAPPING_SOURCES.map((s) => `<option value="${s}">${s}</option>`).join("");
  const bs = document.getElementById("batchSource");
  bs.innerHTML = opts;
  bs.value = "Human judgment";
}

function fieldsFor(kind, item) {
  if (kind === "status") {
    const suggested = item.suggested_interpretation || "issued";
    const options = CANONICAL_STATUSES.map(
      (s) => `<option value="${s}" ${s === suggested ? "selected" : ""}>${s}</option>`
    ).join("");
    return `<label>Canonical status<select data-field="canonical_status" data-proposed>${options}</select></label>`;
  }
  if (kind === "permit_code") {
    const suggested = (item.suggested_interpretation || "other").toLowerCase();
    const options = TRADES.map(
      (t) => `<option value="${t}" ${t === suggested ? "selected" : ""}>${t}</option>`
    ).join("");
    return `
      <label>Trade<select data-field="trade" data-proposed>${options}</select></label>
      <label>Project category<input data-field="project_category" placeholder="auto" /></label>`;
  }
  return `
    <label>Category<input data-field="category" data-proposed value="${escapeHtml(
      item.suggested_interpretation || "Other"
    )}" /></label>
    <label>Suggested products<input data-field="suggested_products" placeholder="PEX, Valves" /></label>`;
}

function sourceSelect() {
  const opts = MAPPING_SOURCES.map(
    (s) => `<option value="${s}" ${s === "Human judgment" ? "selected" : ""}>${s}</option>`
  ).join("");
  return `<label>Source<select data-field="mapping_source">${opts}</select></label>`;
}

function filteredSortedItems() {
  const tier = document.getElementById("filterTier").value;
  const muni = document.getElementById("filterMuni").value;
  const q = document.getElementById("filterSearch").value.trim().toLowerCase();
  const sort = document.getElementById("filterSort").value;
  let items = (snapshot?.queue || []).filter((i) => i.kind === currentKind);
  if (tier) items = items.filter((i) => (i.priority_tier || "") === tier);
  if (muni) items = items.filter((i) => (i.municipality_slug || "") === muni);
  if (q) {
    items = items.filter(
      (i) =>
        (i.raw_value || "").toLowerCase().includes(q) ||
        (i.description || "").toLowerCase().includes(q)
    );
  }
  const key = { priority: "priority_score", occurrence: "occurrence_count", recent: "affected_recent_count" }[sort];
  items = [...items].sort((a, b) => (Number(b[key]) || 0) - (Number(a[key]) || 0));
  return items;
}

function renderQueue() {
  const list = document.getElementById("queueList");
  const empty = document.getElementById("emptyState");
  const items = filteredSortedItems();
  if (!items.length) {
    list.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  list.innerHTML = items
    .map((item) => {
      const tier = item.priority_tier || "Low";
      const pscore = item.priority_score == null ? "—" : Math.round(Number(item.priority_score));
      const isSel = selected.has(item.id);
      return `
      <article class="knowledge-card ${isSel ? "is-selected" : ""}" data-id="${item.id}">
        <div class="card-top">
          <div>
            <h3>${escapeHtml(item.raw_value)}
              <span class="tier-badge tier-${tier}">${escapeHtml(tier)} · ${pscore}</span>
            </h3>
            <div class="knowledge-meta">
              Municipality: <strong>${escapeHtml(item.municipality_slug || "—")}</strong>
              · Occurrences: <strong>${escapeHtml(item.occurrence_count)}</strong>
              · Affected permits: <strong>${escapeHtml(item.affected_permit_count ?? "—")}</strong>
              · Recent(30d): <strong>${escapeHtml(item.affected_recent_count ?? "—")}</strong>
              · Active: <strong>${escapeHtml(item.affected_active_count ?? "—")}</strong>
              · Avg score: <strong>${escapeHtml(item.affected_avg_score ?? "—")}</strong>
            </div>
          </div>
          <label class="card-select">
            <input type="checkbox" data-action="select" ${isSel ? "checked" : ""} ${apiLive ? "" : "disabled"} /> batch
          </label>
        </div>
        <p><strong>Suggested:</strong> ${escapeHtml(item.suggested_interpretation || "—")}
           · <strong>Description:</strong> ${escapeHtml(item.description || "—")}</p>
        <div class="knowledge-actions">
          ${fieldsFor(currentKind, item)}
          <label>Confidence
            <input data-field="mapping_confidence" type="number" min="0" max="100" value="90" />
          </label>
          ${sourceSelect()}
          <label>Notes<input data-field="notes" placeholder="optional" /></label>
          <button type="button" class="btn-preview" data-action="preview" ${apiLive ? "" : "disabled"}>Preview impact</button>
          <button type="button" class="btn btn-approve" data-action="approve" ${apiLive ? "" : "disabled"}>Approve</button>
          <button type="button" class="btn btn-reject" data-action="reject" ${apiLive ? "" : "disabled"}>Reject</button>
        </div>
        <div class="impact-panel" data-role="impact" hidden></div>
      </article>`;
    })
    .join("");

  list.querySelectorAll("[data-action]").forEach((el) => {
    const action = el.dataset.action;
    if (action === "select") {
      el.addEventListener("change", () => toggleSelect(el));
    } else {
      el.addEventListener("click", () => handleAction(el, action));
    }
  });
}

function toggleSelect(el) {
  const card = el.closest(".knowledge-card");
  const id = Number(card.dataset.id);
  if (el.checked) {
    selected.add(id);
    card.classList.add("is-selected");
  } else {
    selected.delete(id);
    card.classList.remove("is-selected");
  }
  updateBatchToolbar();
}

function updateBatchToolbar() {
  const bar = document.getElementById("batchToolbar");
  document.getElementById("batchCount").textContent = selected.size;
  bar.classList.toggle("is-visible", selected.size > 0);
}

function readFields(card) {
  const payload = {};
  card.querySelectorAll("[data-field]").forEach((el) => {
    if (el.value !== "") payload[el.dataset.field] = el.value;
  });
  if (payload.mapping_confidence != null) {
    payload.mapping_confidence = Number(payload.mapping_confidence);
  }
  return payload;
}

function proposedFrom(card) {
  const el = card.querySelector("[data-proposed]");
  return el ? el.value : null;
}

function renderImpact(panel, data) {
  const warn = data.material_change
    ? `<div class="impact-warning">⚠ Material change: ${data.entering_queue} entering / ${data.leaving_queue} leaving the active sales queue.</div>`
    : "";
  const rows = (data.examples || [])
    .map(
      (e) => `<tr>
        <td>${escapeHtml(e.permit_number || "—")}</td>
        <td>${escapeHtml(e.city || e.jurisdiction || "—")}</td>
        <td>${escapeHtml(e.score_before)}</td>
        <td>${escapeHtml(e.score_after)}</td>
        <td>${e.delta >= 0 ? "+" : ""}${escapeHtml(e.delta)}</td>
      </tr>`
    )
    .join("");
  panel.innerHTML = `
    <strong>Impact preview</strong> — ${escapeHtml(data.raw_value)} →
    <em>${escapeHtml(data.proposed_mapping)}</em>
    (current fallback: ${escapeHtml(data.current_fallback)})${data.sampled ? " · sampled" : ""}
    <div class="impact-grid">
      <div><span>Affected permits</span><strong>${escapeHtml(data.affected_permit_count)}</strong></div>
      <div><span>Recent (30d)</span><strong>${escapeHtml(data.affected_recent_count)}</strong></div>
      <div><span>Active</span><strong>${escapeHtml(data.affected_active_count)}</strong></div>
      <div><span>Avg score</span><strong>${escapeHtml(data.avg_score_before)} → ${escapeHtml(data.avg_score_after)}</strong></div>
      <div><span>Above ${escapeHtml(data.threshold)}</span><strong>${escapeHtml(data.above_before)} → ${escapeHtml(data.above_after)}</strong></div>
      <div><span>Net queue change</span><strong>${data.net_queue_change >= 0 ? "+" : ""}${escapeHtml(data.net_queue_change)}</strong></div>
    </div>
    ${warn}
    ${rows ? `<table class="impact-examples"><thead><tr><th>Permit</th><th>City</th><th>Before</th><th>After</th><th>Δ</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
  `;
  panel.hidden = false;
}

async function handleAction(btn, action) {
  const card = btn.closest(".knowledge-card");
  const id = card.dataset.id;
  const error = document.getElementById("errorState");
  error.style.display = "none";

  try {
    if (action === "preview") {
      const proposed = proposedFrom(card);
      const res = await fetch(`${API_BASE}/api/queue/${id}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proposed_mapping: proposed }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      renderImpact(card.querySelector('[data-role="impact"]'), data);
      return;
    }

    if (action === "approve") {
      const panel = card.querySelector('[data-role="impact"]');
      if (panel.hidden) {
        // Enforce "reviewer must see preview before approving".
        const proposed = proposedFrom(card);
        const res = await fetch(`${API_BASE}/api/queue/${id}/preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ proposed_mapping: proposed }),
        });
        const data = await res.json();
        if (res.ok) renderImpact(panel, data);
        if (!confirm("Review the impact preview above, then confirm approval?")) return;
      }
    }

    const payload = readFields(card);
    const res = await fetch(`${API_BASE}/api/queue/${id}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    selected.delete(Number(id));
    await refresh();
  } catch (err) {
    error.style.display = "block";
    error.textContent = err.message || String(err);
  }
}

async function batchApprove() {
  const error = document.getElementById("errorState");
  error.style.display = "none";
  const mapping = document.getElementById("batchMapping").value.trim();
  if (!mapping) {
    error.style.display = "block";
    error.textContent = "Enter a proposed mapping for the batch.";
    return;
  }
  const payload = {
    ids: [...selected],
    proposed_mapping: mapping,
    mapping_confidence: Number(document.getElementById("batchConfidence").value),
    mapping_source: document.getElementById("batchSource").value,
    notes: document.getElementById("batchNotes").value || null,
    activate: true,
  };
  try {
    const res = await fetch(`${API_BASE}/api/queue/batch/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    selected.clear();
    updateBatchToolbar();
    await refresh();
  } catch (err) {
    error.style.display = "block";
    error.textContent = err.message || String(err);
  }
}

async function loadAudit() {
  const box = document.getElementById("auditContainer");
  if (!apiLive) {
    box.innerHTML = `<p class="knowledge-empty">Audit history requires the live API.</p>`;
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/audit?limit=100`, { cache: "no-store" });
    const data = await res.json();
    const rows = (data.audit || [])
      .map(
        (a) => `<tr>
          <td>${escapeHtml((a.created_at || "").slice(0, 19))}</td>
          <td>${escapeHtml(a.action)}</td>
          <td>${escapeHtml(a.kind)}</td>
          <td>${escapeHtml(a.municipality_slug || "—")}</td>
          <td>${escapeHtml(a.raw_value)}</td>
          <td>${escapeHtml(a.previous_mapping || "—")} → ${escapeHtml(a.new_mapping || "—")}</td>
          <td>${escapeHtml(a.mapping_confidence ?? "—")}</td>
          <td>${escapeHtml(a.affected_permit_count ?? "—")}</td>
          <td>${escapeHtml(a.score_impact_summary || "—")}</td>
        </tr>`
      )
      .join("");
    box.innerHTML = rows
      ? `<table class="audit-table"><thead><tr><th>When</th><th>Action</th><th>Kind</th><th>Muni</th><th>Raw</th><th>Mapping</th><th>Conf</th><th>Affected</th><th>Impact</th></tr></thead><tbody>${rows}</tbody></table>`
      : `<p class="knowledge-empty">No mapping changes recorded yet.</p>`;
  } catch (err) {
    box.innerHTML = `<p class="knowledge-empty">Audit load failed: ${escapeHtml(err.message)}</p>`;
  }
}

async function refresh() {
  snapshot = await loadSnapshot();
  renderStats(snapshot.counts || {}, snapshot);
  populateMuniFilter();
  renderQueue();
  updateBatchToolbar();
}

function wireControls() {
  document.querySelectorAll(".knowledge-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".knowledge-tab").forEach((t) => t.classList.remove("is-active"));
      tab.classList.add("is-active");
      currentKind = tab.dataset.kind;
      renderQueue();
    });
  });
  ["filterTier", "filterMuni", "filterSort"].forEach((id) =>
    document.getElementById(id).addEventListener("change", renderQueue)
  );
  document.getElementById("filterSearch").addEventListener("input", renderQueue);
  document.getElementById("batchApproveBtn").addEventListener("click", batchApprove);
  document.getElementById("batchClearBtn").addEventListener("click", () => {
    selected.clear();
    updateBatchToolbar();
    renderQueue();
  });
  document.getElementById("recomputeBtn").addEventListener("click", async () => {
    if (!apiLive) return;
    await fetch(`${API_BASE}/api/priority/recompute`, { method: "POST" });
    await refresh();
  });
  const auditWrap = document.querySelector("details.audit-wrap");
  auditWrap.addEventListener("toggle", () => {
    if (auditWrap.open) loadAudit();
  });
}

async function init() {
  wireControls();
  populateSourceSelects();
  const error = document.getElementById("errorState");
  try {
    if (window.location.protocol === "file:") {
      throw new Error("Open via local server (not file://). Use CorridorIQ HQ or python -m http.server.");
    }
    await probeApi();
    await refresh();
  } catch (err) {
    error.style.display = "block";
    error.textContent = err.message || String(err);
  }
}

init();
