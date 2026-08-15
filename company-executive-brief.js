/* Evidence-based, printable company outreach brief. No inferred contact data. */
(function () {
  const companyId = Number(CIQ.qs("id"));
  let detail = null, projects = [], permits = [], activities = [], tasks = [];

  const esc = (value) => CIQ.esc(value == null ? "" : value);
  const value = (v) => (v == null || v === "" ? "—" : esc(v));
  const money = (v) => (v == null || v === "" ? "—" : CIQ.money(v));
  const date = (v) => (v ? CIQ.fmtDate(v) : "—");
  const title = (v) => v ? CIQ.titleCase(String(v).replaceAll("_", " ")) : "—";

  async function load() {
    if (!Number.isInteger(companyId) || companyId < 1) throw new Error("Open this report from a company profile.");
    const [d, pj, pm, ac, ts] = await Promise.all([
      CIQ.api.get(`/api/sales/companies/${companyId}`),
      CIQ.api.get(`/api/sales/companies/${companyId}/projects`).catch(() => ({ items: [] })),
      CIQ.api.get(`/api/sales/companies/${companyId}/permits`).catch(() => ({ items: [] })),
      CIQ.api.get(`/api/sales/companies/${companyId}/activities`).catch(() => ({ items: [] })),
      CIQ.api.get("/api/sales/tasks").catch(() => ({ items: [] })),
    ]);
    detail = d;
    projects = pj.items || pj || [];
    permits = pm.items || pm || [];
    activities = ac.items || ac || [];
    tasks = (ts.items || ts || []).filter((item) => Number(item.company_id) === companyId);
  }

  function contactState() {
    const c = detail.company || {}, contacts = detail.contacts || [];
    const people = contacts.filter((p) => p.phone || p.mobile_phone || p.email);
    const channels = [c.main_phone && "phone", c.main_email && "email", c.website && "website"].filter(Boolean);
    return { ready: Boolean(channels.length || people.length), people, channels };
  }

  function qualification() {
    const c = detail.company || {}, r = detail.relationship || {};
    const leadType = r.lead_type || c.lead_type || "unverified_permit_contact";
    const verification = r.lead_verification_status || c.lead_verification_status || "unverified";
    if (leadType === "verified_contractor" && verification === "verified") return { label: "Verified contractor", tone: "green", confidence: "High" };
    if (leadType === "verified_contractor") return { label: "Contractor classification", tone: "blue", confidence: "Moderate" };
    return { label: "Research required", tone: "amber", confidence: "Low" };
  }

  function summaryText() {
    const c = detail.company || {}, ci = detail.intelligence || {}, q = qualification(), contact = contactState();
    const location = [c.city, c.state].filter(Boolean).join(", ") || "an unspecified market";
    const activity = Number(ci.active_projects || 0);
    const score = ci.highest_opportunity_score == null ? null : Math.round(Number(ci.highest_opportunity_score));
    return `${c.display_name || c.legal_name || "This company"} is a ${q.confidence.toLowerCase()}-confidence ${q.label.toLowerCase()} lead in ${location}. ` +
      `CorridorIQ currently links ${projects.length} project record${projects.length === 1 ? "" : "s"} and ${permits.length} permit record${permits.length === 1 ? "" : "s"}; ${activity} are marked active. ` +
      (score == null ? "No validated opportunity score is available. " : `The highest recorded opportunity score is ${score}. `) +
      (contact.ready ? "At least one outreach channel is available." : "No verified outreach channel is currently available; enrichment is required before contact.");
  }

  function recommendedObjective() {
    const contact = contactState(), r = detail.relationship || {}, top = projects[0];
    if (!contact.ready) return "Verify the company identity and obtain a legitimate decision-maker phone number or email before outreach.";
    if (["qualified", "quote_requested", "quote_sent", "negotiating"].includes(r.relationship_status)) {
      return "Advance the active commercial conversation: confirm buying authority, material timing, quote requirements, and the next committed action.";
    }
    if (top) return `Confirm whether the company is involved with ${top.job_address || top.address || top.permit_number || "the highest-ranked project"}, identify the project manager or purchasing contact, and validate the material-buying window.`;
    return "Confirm the company’s current Arizona project activity, identify who controls material purchasing, and establish a dated follow-up.";
  }

  function contactBlock() {
    const c = detail.company || {}, contact = contactState();
    const primary = contact.people[0];
    const address = [c.address_line_1, c.address_line_2, c.city, c.state, c.postal_code].filter(Boolean).join(", ");
    const rows = [
      ["Primary person", primary && primary.full_name],
      ["Title / department", primary && [primary.job_title, primary.department].filter(Boolean).join(" · ")],
      ["Direct phone", primary && (primary.mobile_phone || primary.phone)],
      ["Direct email", primary && primary.email],
      ["Company phone", c.main_phone], ["Company email", c.main_email],
      ["Website", c.website], ["Business address", address], ["License", c.license_number],
    ];
    return `<div class="brief-facts">${rows.map(([k, v]) => `<div><span>${esc(k)}</span><strong>${value(v)}</strong></div>`).join("")}</div>`;
  }

  function pipelineTable() {
    if (!projects.length) return '<div class="brief-empty">No linked project records are available.</div>';
    return `<div class="table-wrap"><table class="tbl brief-table"><thead><tr><th>Project</th><th>Stage</th><th>Score</th><th>Timing</th><th>Estimated value</th></tr></thead><tbody>` +
      projects.slice(0, 8).map((p) => `<tr><td><strong>${value(p.job_address || p.address || p.permit_number || p.jurisdiction)}</strong><small>${value([p.jurisdiction, p.city].filter(Boolean).join(" · "))}</small></td><td>${esc(title(p.project_lifecycle || p.project_status))}</td><td>${p.opportunity_score == null ? "—" : Math.round(Number(p.opportunity_score))}</td><td>${esc(title(p.opportunity_timing))}<small>${date(p.opportunity_date)}</small></td><td>${money(p.estimated_material_value)}</td></tr>`).join("") +
      `</tbody></table></div>`;
  }

  function permitTable() {
    if (!permits.length) return '<div class="brief-empty">No raw permit evidence is available.</div>';
    return `<div class="table-wrap"><table class="tbl brief-table"><thead><tr><th>Permit</th><th>Status</th><th>Description</th><th>Filed / issued</th><th>Valuation</th></tr></thead><tbody>` +
      permits.slice(0, 10).map((p) => `<tr><td><strong>${value(p.permit_number)}</strong><small>${value(p.jurisdiction)}</small></td><td>${value(p.status)}</td><td>${value(p.description || p.permit_type)}</td><td>${date(p.filed_date)}<small>Issued ${date(p.issued_date)}</small></td><td>${money(p.valuation)}</td></tr>`).join("") +
      `</tbody></table></div>`;
  }

  function engagementBlock() {
    const r = detail.relationship || {}, last = activities[0];
    const open = tasks.filter((t) => ["open", "in_progress"].includes(t.status));
    const notes = activities.filter((a) => a.notes).slice(0, 3);
    return `<div class="brief-facts brief-facts-compact">
      <div><span>CRM stage</span><strong>${esc(title(r.relationship_status || "new"))}</strong></div>
      <div><span>Last contact</span><strong>${r.last_contact_at ? CIQ.fmtDateTime(r.last_contact_at) : "Never"}</strong></div>
      <div><span>Next follow-up</span><strong>${date(r.next_followup_at)}</strong></div>
      <div><span>Open tasks</span><strong>${open.length}</strong></div>
    </div>` + (last ? `<p class="brief-note"><strong>Latest activity:</strong> ${esc(CIQ.activityLabel(last.activity_type))}${last.activity_outcome ? ` · ${esc(CIQ.outcomeLabel(last.activity_outcome))}` : ""} · ${CIQ.fmtDateTime(last.activity_at)}</p>` : "") +
      (notes.length ? `<div class="brief-notes">${notes.map((n) => `<p>${esc(n.notes)}</p>`).join("")}</div>` : '<div class="brief-empty">No outreach notes have been logged.</div>');
  }

  function riskList() {
    const c = detail.company || {}, ci = detail.intelligence || {}, dq = detail.data_quality || {};
    const contact = contactState(), q = qualification(), risks = [];
    if (!contact.ready) risks.push("No verified phone, email, website, or reachable person is available.");
    if (q.confidence !== "High") risks.push(`Lead qualification confidence is ${q.confidence.toLowerCase()}; verify contractor role before pitching.`);
    if (!projects.length) risks.push("No linked projects support an immediate project-specific opening.");
    if (!activities.length) risks.push("No prior outreach history is recorded; avoid assuming an existing relationship.");
    if (Number(dq.potential_duplicates || 0) > 0) risks.push(`${dq.potential_duplicates} potential duplicate record${Number(dq.potential_duplicates) === 1 ? "" : "s"} require identity review.`);
    if (!c.last_seen_at && !ci.metrics_calculated_at) risks.push("No source-freshness timestamp is available.");
    if (!risks.length) risks.push("No critical data-quality blocker is currently flagged; validate details verbally before quoting.");
    return `<ul class="brief-risk-list">${risks.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`;
  }

  function actionsList() {
    const contact = contactState(), top = projects[0], r = detail.relationship || {};
    const actions = [];
    if (!contact.ready) actions.push("Research and verify a decision-maker contact before outreach.");
    else actions.push("Call the best available contact and confirm their role in project and material purchasing decisions.");
    if (top) actions.push(`Validate involvement in ${top.job_address || top.address || top.permit_number || "the leading project"} and confirm the material schedule.`);
    actions.push("Ask who prepares the bill of materials and whether alternates, delivery, or will-call support would remove friction.");
    actions.push(r.next_followup_at ? `Complete the scheduled follow-up on ${CIQ.fmtDate(r.next_followup_at)}.` : "End the interaction with a dated next action and log the outcome in CorridorIQ.");
    return `<ol class="brief-actions">${actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ol>`;
  }

  function render() {
    const c = detail.company || {}, ci = detail.intelligence || {}, r = detail.relationship || {};
    const name = c.display_name || c.legal_name || "Company";
    const q = qualification(), contact = contactState();
    document.title = `CorridorIQ — ${name} Executive Brief`;
    document.getElementById("backToCompany").href = `sales-company-profile.html?id=${companyId}`;
    document.getElementById("briefStatus").innerHTML = "";
    const brief = document.getElementById("executiveBrief");
    brief.innerHTML = `<header class="executive-brief-head">
      <div><div class="brief-brand"><span>CIQ</span> CorridorIQ</div><p>Company Executive Outreach Brief</p><h1>${esc(name)}</h1><div class="brief-subtitle">${esc([c.city, c.state, c.license_number && `License ${c.license_number}`].filter(Boolean).join(" · ") || "Location not available")}</div></div>
      <div class="brief-asof"><strong>Prepared ${esc(new Date().toLocaleDateString())}</strong><span>CRM stage: ${esc(title(r.relationship_status || "new"))}</span><span>Data last seen: ${date(c.last_seen_at || ci.metrics_calculated_at)}</span></div>
    </header>
    <section class="brief-decision-strip">
      <div><span>Qualification</span><strong class="brief-${q.tone}">${esc(q.label)}</strong><small>${esc(q.confidence)} confidence</small></div>
      <div><span>Contact readiness</span><strong class="brief-${contact.ready ? "green" : "amber"}">${contact.ready ? "Ready" : "Blocked"}</strong><small>${contact.ready ? "Outreach channel found" : "Enrichment required"}</small></div>
      <div><span>Priority</span><strong>${value(ci.company_priority_tier)}</strong><small>Score ${ci.company_priority_score == null ? "—" : Math.round(Number(ci.company_priority_score))}</small></div>
      <div><span>Active projects</span><strong>${Number(ci.active_projects || 0)}</strong><small>${projects.length} linked records</small></div>
    </section>
    <section class="brief-section"><h2>Executive summary</h2><p class="brief-lead">${esc(summaryText())}</p><div class="brief-objective"><span>Recommended call objective</span><strong>${esc(recommendedObjective())}</strong></div></section>
    <section class="brief-section"><h2>Who to contact</h2>${contactBlock()}</section>
    <section class="brief-section"><div class="brief-section-head"><h2>Project opportunity pipeline</h2><span>${projects.length} linked</span></div>${pipelineTable()}</section>
    <section class="brief-section"><div class="brief-section-head"><h2>Permit evidence</h2><span>${permits.length} records</span></div>${permitTable()}</section>
    <div class="brief-two-col"><section class="brief-section"><h2>Relationship position</h2>${engagementBlock()}</section><section class="brief-section"><h2>Risks and data gaps</h2>${riskList()}</section></div>
    <section class="brief-section"><h2>Next best actions</h2>${actionsList()}</section>
    <footer class="executive-brief-foot"><strong>Evidence standard</strong><span>This brief uses authenticated CorridorIQ CRM, company, project, and permit records. Missing information is shown as unavailable and is not inferred. Verify source data before committing pricing, credit, or delivery capacity.</span></footer>`;
    brief.hidden = false;
  }

  function plainText() {
    const c = detail.company || {}, ci = detail.intelligence || {}, r = detail.relationship || {};
    const contact = contactState(), primary = contact.people[0];
    return [`CORRIDORIQ COMPANY EXECUTIVE OUTREACH BRIEF`, c.display_name || c.legal_name || "Company", "",
      summaryText(), "", `CALL OBJECTIVE: ${recommendedObjective()}`, "",
      `CRM STAGE: ${title(r.relationship_status || "new")}`,
      `PRIORITY: ${ci.company_priority_tier || "—"} (${ci.company_priority_score == null ? "—" : Math.round(Number(ci.company_priority_score))})`,
      `CONTACT: ${primary ? [primary.full_name, primary.job_title, primary.phone || primary.mobile_phone, primary.email].filter(Boolean).join(" · ") : c.main_phone || c.main_email || "Enrichment required"}`,
      `PROJECTS: ${projects.length} · PERMITS: ${permits.length}`, "",
      "Evidence-based brief. Missing information is not inferred."].join("\n");
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const user = await CIQ.guard("crm.relationships.view", { title: "Executive Brief", subtitle: "Decision-ready company intelligence", active: "my-companies.html" });
    if (!user) return;
    document.getElementById("printBriefBtn").addEventListener("click", () => window.print());
    document.getElementById("copyBriefBtn").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(plainText()); CIQ.toast("Brief copied", "success"); }
      catch (error) { CIQ.toast("Copy failed; use Print / Save PDF", "error"); }
    });
    try { await load(); render(); }
    catch (error) { document.getElementById("briefStatus").innerHTML = CIQ.errorBanner(error.message || "Could not build executive brief."); }
  });
})();
