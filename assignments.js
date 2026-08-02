/* Assignments — review and (re)assign company ownership. */
(function () {
  let rows = [], team = [], filterQ = "";

  function render() {
    const el = document.getElementById("list");
    const items = rows.filter((r) => !filterQ || (r.company_name || "").toLowerCase().includes(filterQ));
    if (!items.length) { el.innerHTML = CIQ.emptyState({ icon: "⇄", title: "No assignments" }); return; }
    el.innerHTML = `<div class="table-wrap"><table class="tbl responsive"><thead><tr>
      <th>Company</th><th>Status</th><th>Assigned to</th><th>Assigned</th><th>Next follow-up</th><th></th></tr></thead>
      <tbody>${items.map((r) => `<tr>
        <td data-label="Company"><a href="sales-company-profile.html?id=${r.company_id}">${CIQ.esc(r.company_name)}</a></td>
        <td data-label="Status">${CIQ.statusBadge(r.relationship_status || "new")}</td>
        <td data-label="Assigned to">${CIQ.esc(r.assigned_to || "Unassigned")}</td>
        <td data-label="Assigned">${r.assigned_at ? CIQ.fmtDate(r.assigned_at) : "—"}</td>
        <td data-label="Next follow-up">${r.next_followup_at ? CIQ.fmtDate(r.next_followup_at) : "—"}</td>
        <td data-label=""><button class="btn btn-sm" data-cid="${r.company_id}" data-name="${CIQ.esc(r.company_name)}">Reassign</button></td>
      </tr>`).join("")}</tbody></table></div>`;
    el.querySelectorAll("button[data-cid]").forEach((b) => b.addEventListener("click", () =>
      assignModal({ id: Number(b.dataset.cid), name: b.dataset.name })));
  }

  function assignModal(company) {
    const body = document.createElement("div");
    body.innerHTML = `${company ? `<p>Reassign <b>${CIQ.esc(company.name)}</b></p>`
      : `<label class="fld">Find company<input id="coSearch" placeholder="Company name…" /></label>
         <div id="coResults" style="max-height:170px;overflow:auto;margin:6px 0"></div>`}
      <label class="fld">Assign to<select id="repSel">${team.map((m) =>
        `<option value="${m.user_id}">${CIQ.esc(m.display_name)}</option>`).join("")}</select></label>
      <label class="fld" style="margin-top:10px">Reason (optional)<input id="reason" /></label>`;
    const foot = document.createElement("div");
    foot.innerHTML = `<button class="btn" data-a="cancel">Cancel</button>
      <button class="btn btn-primary" data-a="save" ${company ? "" : "disabled"}>Assign</button>`;
    const m = CIQ.modal({ title: company ? "Reassign company" : "Assign company", body, footer: foot });
    let chosen = company || null;
    const saveBtn = foot.querySelector('[data-a="save"]');
    if (!company) {
      const results = body.querySelector("#coResults");
      body.querySelector("#coSearch").addEventListener("input", CIQ.debounce(async (e) => {
        const q = e.target.value.trim(); if (!q) { results.innerHTML = ""; return; }
        const data = await CIQ.api.get("/api/sales/companies?page_size=8&q=" + encodeURIComponent(q));
        results.innerHTML = data.items.map((c) => `<div class="chip" data-cid="${c.company_id}" data-name="${CIQ.esc(c.display_name)}"
          style="display:block;margin-bottom:4px">${CIQ.esc(c.display_name)}</div>`).join("") || '<div class="muted">No matches</div>';
        results.querySelectorAll("[data-cid]").forEach((r) => r.addEventListener("click", () => {
          chosen = { id: Number(r.dataset.cid), name: r.dataset.name }; saveBtn.disabled = false;
          results.querySelectorAll(".chip").forEach((c) => c.classList.remove("active")); r.classList.add("active");
        }));
      }, 300));
    }
    foot.querySelector('[data-a="cancel"]').addEventListener("click", m.close);
    saveBtn.addEventListener("click", async () => {
      if (!chosen) return;
      await CIQ.busy(saveBtn, async () => {
        try {
          await CIQ.api.post("/api/manager/assignments", { company_id: chosen.id,
            new_user_id: Number(body.querySelector("#repSel").value),
            reason: body.querySelector("#reason").value.trim() || null });
          CIQ.toast("Assignment saved", "success"); m.close(); await load();
        } catch (e) { CIQ.toast(e.message, "error"); }
      });
    });
  }

  async function load() {
    document.getElementById("list").innerHTML = CIQ.skeletonRows(5);
    try {
      [rows, team] = await Promise.all([
        CIQ.api.get("/api/manager/assignments").then((d) => d.items || []),
        CIQ.api.get("/api/manager/team").then((d) => d.items || []),
      ]);
    } catch (e) { document.getElementById("list").innerHTML = CIQ.errorBanner(e.message); return; }
    render();
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("companies.assign", { title: "Assignments", subtitle: "Company ownership", active: "assignments.html" });
    if (!user) return;
    document.getElementById("assignBtn").addEventListener("click", () => assignModal(null));
    const fq = document.getElementById("fq");
    fq.addEventListener("input", CIQ.debounce(() => { filterQ = fq.value.trim().toLowerCase(); render(); }, 250));
    load();
  });
})();
