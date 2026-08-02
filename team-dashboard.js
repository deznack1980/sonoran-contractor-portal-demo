/* Team dashboard — workload overview + company assignment (managers/admins). */
(function () {
  let team = [];
  let canAssign = false;

  function renderKpis() {
    const sum = (k) => team.reduce((a, m) => a + (m[k] || 0), 0);
    const defs = [["assigned_companies", "Assigned companies", ""], ["followups_due", "Follow-ups due", "warn"],
      ["overdue_tasks", "Overdue tasks", "alert"], ["appointments", "Appointments", "good"],
      ["quote_requests", "Quote requests", ""], ["companies_no_activity", "No activity yet", "warn"]];
    document.getElementById("kpis").innerHTML = defs.map(([k, l, cls]) => {
      const v = sum(k);
      const c = (k === "overdue_tasks" && v) ? "alert" : (k === "companies_no_activity" && v) ? "warn" : cls;
      return `<div class="kpi ${c}" style="cursor:default"><div class="kpi-val">${v}</div><div class="kpi-label">${l}</div></div>`;
    }).join("");
  }

  function renderTeam() {
    const el = document.getElementById("team");
    if (!team.length) { el.innerHTML = CIQ.emptyState({ title: "No team members" }); return; }
    el.innerHTML = `<div class="table-wrap"><table class="tbl responsive"><thead><tr>
      <th>Representative</th><th>Companies</th><th>Calls</th><th>Follow-ups due</th>
      <th>Overdue tasks</th><th>Appts</th><th>Quotes</th><th>No activity</th>${canAssign ? "<th></th>" : ""}</tr></thead>
      <tbody>${team.map((m) => `<tr>
        <td data-label="Rep"><div style="font-weight:600">${CIQ.esc(m.display_name)}</div>
          <div class="muted" style="font-size:12px">${(m.roles || []).map((r) => CIQ.titleCase(r)).join(", ")}</div></td>
        <td data-label="Companies">${m.assigned_companies}</td>
        <td data-label="Calls">${m.calls_completed}</td>
        <td data-label="Follow-ups due">${m.followups_due ? `<span class="badge amber">${m.followups_due}</span>` : 0}</td>
        <td data-label="Overdue">${m.overdue_tasks ? `<span class="badge red">${m.overdue_tasks}</span>` : 0}</td>
        <td data-label="Appts">${m.appointments}</td>
        <td data-label="Quotes">${m.quote_requests}</td>
        <td data-label="No activity">${m.companies_no_activity}</td>
        ${canAssign ? `<td data-label=""><button class="btn btn-sm" data-uid="${m.user_id}" data-name="${CIQ.esc(m.display_name)}">Assign</button></td>` : ""}
      </tr>`).join("")}</tbody></table></div>`;
    el.querySelectorAll("button[data-uid]").forEach((b) => b.addEventListener("click", () =>
      assignModal({ user_id: Number(b.dataset.uid), name: b.dataset.name })));
  }

  function assignModal(preset) {
    const body = document.createElement("div");
    body.innerHTML = `
      <label class="fld">Find company
        <input id="coSearch" placeholder="Type a company name…" autocomplete="off" /></label>
      <div id="coResults" style="max-height:180px;overflow:auto;margin:6px 0"></div>
      <label class="fld">Assign to
        <select id="repSel">${team.map((m) => `<option value="${m.user_id}" ${preset && preset.user_id === m.user_id ? "selected" : ""}>${CIQ.esc(m.display_name)}</option>`).join("")}</select></label>
      <label class="fld" style="margin-top:10px">Reason (optional)<input id="reason" /></label>
      <div id="coChosen" class="muted" style="margin-top:8px;font-size:13px"></div>`;
    const foot = document.createElement("div");
    foot.innerHTML = `<button class="btn" data-a="cancel">Cancel</button>
      <button class="btn btn-primary" data-a="save" disabled>Assign</button>`;
    const m = CIQ.modal({ title: "Assign company", body, footer: foot });
    let chosen = null;
    const results = body.querySelector("#coResults");
    const saveBtn = foot.querySelector('[data-a="save"]');
    const search = CIQ.debounce(async () => {
      const q = body.querySelector("#coSearch").value.trim();
      if (!q) { results.innerHTML = ""; return; }
      try {
        const data = await CIQ.api.get("/api/sales/companies?page_size=8&q=" + encodeURIComponent(q));
        results.innerHTML = data.items.map((c) => `<div class="chip" data-cid="${c.company_id}" data-name="${CIQ.esc(c.display_name)}"
          style="display:block;margin-bottom:4px">${CIQ.esc(c.display_name)} <span class="muted">· ${CIQ.esc(c.assigned_to || "unassigned")}</span></div>`).join("")
          || '<div class="muted" style="font-size:13px">No matches</div>';
        results.querySelectorAll("[data-cid]").forEach((r) => r.addEventListener("click", () => {
          chosen = { id: Number(r.dataset.cid), name: r.dataset.name };
          body.querySelector("#coChosen").textContent = "Selected: " + chosen.name;
          saveBtn.disabled = false;
        }));
      } catch (e) { results.innerHTML = CIQ.errorBanner(e.message); }
    }, 300);
    body.querySelector("#coSearch").addEventListener("input", search);
    foot.querySelector('[data-a="cancel"]').addEventListener("click", m.close);
    saveBtn.addEventListener("click", async () => {
      if (!chosen) return;
      await CIQ.busy(saveBtn, async () => {
        try {
          await CIQ.api.post("/api/manager/assignments", {
            company_id: chosen.id, new_user_id: Number(body.querySelector("#repSel").value),
            reason: body.querySelector("#reason").value.trim() || null });
          CIQ.toast("Company assigned", "success"); m.close(); await load();
        } catch (e) { CIQ.toast(e.message, "error"); }
      });
    });
  }

  async function load() {
    document.getElementById("team").innerHTML = CIQ.skeletonRows(4);
    try { team = (await CIQ.api.get("/api/manager/team")).items || []; }
    catch (e) { document.getElementById("team").innerHTML = CIQ.errorBanner(e.message); return; }
    renderKpis(); renderTeam();
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("users.view", { title: "Team", subtitle: "Workload and assignments", active: "team-dashboard.html" });
    if (!user) return;
    canAssign = CIQ.hasPerm("companies.assign");
    const ab = document.getElementById("assignBtn");
    if (canAssign) ab.addEventListener("click", () => assignModal(null)); else ab.style.display = "none";
    load();
  });
})();
