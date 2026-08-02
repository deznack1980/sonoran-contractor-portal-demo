/* Tasks — Due Today / Overdue / Upcoming / Completed with inline completion. */
(function () {
  let all = [];
  let tab = "today";
  const PRIORITY = { urgent: "red", high: "amber", normal: "slate", low: "slate" };

  function today() { return new Date().toISOString().slice(0, 10); }
  function bucket(t) {
    if (t.status === "completed") return "completed";
    if (t.status === "cancelled") return "cancelled";
    const d = t.due_at ? String(t.due_at).slice(0, 10) : null;
    if (!d) return "upcoming";
    if (d < today()) return "overdue";
    if (d === today()) return "today";
    return "upcoming";
  }

  function counts() {
    const c = { today: 0, overdue: 0, upcoming: 0, completed: 0 };
    all.forEach((t) => { const b = bucket(t); if (c[b] != null) c[b]++; });
    return c;
  }

  function renderTabs() {
    const c = counts();
    const defs = [["today", "Due Today"], ["overdue", "Overdue"], ["upcoming", "Upcoming"], ["completed", "Completed"]];
    document.getElementById("tabs").innerHTML = defs.map(([k, l]) =>
      `<div class="tab ${tab === k ? "active" : ""}" data-tab="${k}">${l}<span class="count">${c[k] || 0}</span></div>`).join("");
    document.querySelectorAll("#tabs .tab").forEach((el) => el.addEventListener("click", () => { tab = el.dataset.tab; render(); }));
  }

  function taskRow(t) {
    const done = t.status === "completed";
    return `<tr>
      <td data-label="Task">
        <div style="font-weight:600">${CIQ.esc(t.title)}</div>
        ${t.description ? `<div class="muted" style="font-size:12px">${CIQ.esc(t.description)}</div>` : ""}
      </td>
      <td data-label="Company">${t.company_id ? `<a href="sales-company-profile.html?id=${t.company_id}">${CIQ.esc(t.company_name || "Company")}</a>` : '<span class="muted">—</span>'}</td>
      <td data-label="Due">${t.due_at ? CIQ.fmtDateTime(t.due_at) : '<span class="muted">—</span>'}</td>
      <td data-label="Priority"><span class="badge ${PRIORITY[t.priority] || "slate"}">${CIQ.esc(CIQ.titleCase(t.priority))}</span></td>
      <td data-label="Owner">${CIQ.esc(t.assigned_to || "—")}</td>
      <td data-label="">${done ? `<span class="badge green">Completed</span>` :
        `<div class="row"><button class="btn btn-sm btn-primary" data-a="done" data-id="${t.id}">Complete</button>
         <button class="btn btn-sm" data-a="resched" data-id="${t.id}">Reschedule</button></div>`}</td>
    </tr>`;
  }

  function render() {
    renderTabs();
    const items = all.filter((t) => bucket(t) === tab);
    const el = document.getElementById("tasks");
    if (!items.length) {
      el.innerHTML = CIQ.emptyState({ icon: "✓", title: "Nothing here",
        text: tab === "overdue" ? "No overdue tasks — nice work." : "No tasks in this view." });
      return;
    }
    el.innerHTML = `<div class="table-wrap"><table class="tbl responsive"><thead><tr>
      <th>Task</th><th>Company</th><th>Due</th><th>Priority</th><th>Owner</th><th></th></tr></thead>
      <tbody>${items.map(taskRow).join("")}</tbody></table></div>`;
    el.querySelectorAll('[data-a="done"]').forEach((b) => b.addEventListener("click", () => complete(Number(b.dataset.id), b)));
    el.querySelectorAll('[data-a="resched"]').forEach((b) => b.addEventListener("click", () => reschedule(Number(b.dataset.id))));
  }

  async function complete(id, btn) {
    await CIQ.busy(btn, async () => {
      try {
        const updated = await CIQ.api.patch(`/api/sales/tasks/${id}`, { status: "completed" });
        const i = all.findIndex((t) => t.id === id);
        if (i >= 0) all[i] = { ...all[i], ...updated };
        CIQ.toast("Task completed", "success"); render();
      } catch (e) { CIQ.toast(e.message, "error"); }
    });
  }

  function reschedule(id) {
    const t = all.find((x) => x.id === id);
    const body = document.createElement("div");
    body.innerHTML = `<label class="fld">New due date/time<input type="datetime-local" id="rs"
      value="${t.due_at ? String(t.due_at).slice(0, 16) : ""}" /></label>`;
    const foot = document.createElement("div");
    foot.innerHTML = `<button class="btn" data-a="cancel">Cancel</button><button class="btn btn-primary" data-a="save">Save</button>`;
    const m = CIQ.modal({ title: "Reschedule task", body, footer: foot });
    foot.querySelector('[data-a="cancel"]').addEventListener("click", m.close);
    foot.querySelector('[data-a="save"]').addEventListener("click", async (e) => {
      const val = body.querySelector("#rs").value;
      await CIQ.busy(e.target, async () => {
        try {
          const updated = await CIQ.api.patch(`/api/sales/tasks/${id}`, { due_at: val ? val + ":00" : null });
          const i = all.findIndex((x) => x.id === id); if (i >= 0) all[i] = { ...all[i], ...updated };
          CIQ.toast("Task rescheduled", "success"); m.close(); render();
        } catch (err) { CIQ.toast(err.message, "error"); }
      });
    });
  }

  function newTask() {
    const body = document.createElement("form");
    body.className = "form-grid";
    body.innerHTML = `<label class="fld full">Title<input name="title" required /></label>
      <label class="fld">Priority<select name="priority"><option value="normal">Normal</option>
        <option value="high">High</option><option value="urgent">Urgent</option><option value="low">Low</option></select></label>
      <label class="fld">Due<input type="datetime-local" name="due_at" /></label>
      <label class="fld full">Description<textarea name="description"></textarea></label>`;
    const foot = document.createElement("div");
    foot.innerHTML = `<button type="button" class="btn" data-a="cancel">Cancel</button>
      <button type="button" class="btn btn-primary" data-a="save">Create</button>`;
    const m = CIQ.modal({ title: "New task", body, footer: foot });
    foot.querySelector('[data-a="cancel"]').addEventListener("click", m.close);
    foot.querySelector('[data-a="save"]').addEventListener("click", async (e) => {
      const fd = new FormData(body);
      if (!(fd.get("title") || "").trim()) { CIQ.toast("Title is required", "error"); return; }
      await CIQ.busy(e.target, async () => {
        try {
          await CIQ.api.post("/api/sales/tasks", { title: fd.get("title").trim(),
            priority: fd.get("priority"), description: fd.get("description") || null,
            due_at: fd.get("due_at") ? fd.get("due_at") + ":00" : null });
          CIQ.toast("Task created", "success"); m.close(); await load();
        } catch (err) { CIQ.toast(err.message, "error"); }
      });
    });
  }

  async function load() {
    document.getElementById("tasks").innerHTML = CIQ.skeletonRows(4);
    try { all = (await CIQ.api.get("/api/sales/tasks")).items || []; }
    catch (e) { document.getElementById("tasks").innerHTML = CIQ.errorBanner(e.message); return; }
    CIQ.setTaskCount(all.filter((t) => bucket(t) === "today" || bucket(t) === "overdue").length);
    render();
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("crm.tasks.view", { title: "Tasks", subtitle: "Your work queue", active: "my-tasks.html" });
    if (!user) return;
    document.getElementById("newTaskBtn").addEventListener("click", newTask);
    if (!CIQ.hasPerm("crm.tasks.create")) document.getElementById("newTaskBtn").style.display = "none";
    load();
  });
})();
