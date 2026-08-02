/* Activity — the current user's CRM activity feed. */
(function () {
  let page = 1; const filters = {};

  async function load() {
    const feed = document.getElementById("feed");
    feed.innerHTML = CIQ.skeletonRows(5);
    const params = new URLSearchParams();
    if (filters.activity_type) params.set("activity_type", filters.activity_type);
    params.set("page", page); params.set("page_size", 30);
    let data;
    try { data = await CIQ.api.get("/api/sales/activity?" + params.toString()); }
    catch (e) { feed.innerHTML = CIQ.errorBanner(e.message); return; }
    if (!data.items.length) {
      feed.innerHTML = CIQ.emptyState({ icon: "≣", title: "No activity yet",
        text: "Your logged calls, emails, notes, and meetings will appear here." });
      document.getElementById("pager").innerHTML = ""; return;
    }
    feed.innerHTML = `<div class="card card-pad"><div class="timeline">${data.items.map((a) => `
      <div class="tl-item ${CIQ.esc(a.activity_type)}">
        <div class="tl-head">
          <span class="tl-type">${CIQ.esc(CIQ.activityLabel(a.activity_type))}</span>
          ${a.activity_outcome ? `<span class="badge slate">${CIQ.esc(CIQ.outcomeLabel(a.activity_outcome))}</span>` : ""}
          <span class="tl-when">${CIQ.fmtDateTime(a.activity_at)}</span>
        </div>
        <div style="font-size:13px"><a href="sales-company-profile.html?id=${a.company_id}">${CIQ.esc(a.display_name)}</a>
          ${a.subject ? " · " + CIQ.esc(a.subject) : ""}</div>
        ${a.notes ? `<div class="tl-notes">${CIQ.esc(a.notes)}</div>` : ""}
        ${a.next_followup_at ? `<div class="muted" style="font-size:12px;margin-top:3px">Next follow-up: ${CIQ.fmtDate(a.next_followup_at)}</div>` : ""}
      </div>`).join("")}</div></div>`;
    CIQ.pager(document.getElementById("pager"), { page: data.page, pages: data.pages, total: data.total,
      onPage: (p) => { page = p; load(); window.scrollTo(0, 0); } });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("crm.activities.view",
      { title: "Activity", subtitle: "Everything you've logged", active: "activity.html" });
    if (!user) return;
    const ft = document.getElementById("ftype");
    [["call", "Calls"], ["email", "Emails"], ["meeting", "Meetings"], ["note", "Notes"],
     ["quote_request", "Quote requests"], ["follow_up", "Follow-ups"], ["status_change", "Status changes"]]
      .forEach(([v, l]) => { const o = document.createElement("option"); o.value = v; o.textContent = l; ft.appendChild(o); });
    ft.addEventListener("change", () => { filters.activity_type = ft.value; page = 1; load(); });
    load();
  });
})();
