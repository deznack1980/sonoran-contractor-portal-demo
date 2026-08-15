/* CorridorIQ Sales Workspace — shared client runtime.
 * Renders the app shell (sidebar + topbar), navigation gated by permission,
 * and reusable components (toasts, modals, the Log Activity form, badges,
 * pagination, date helpers). Security note: hiding a link is never a
 * substitute for authorization — the backend enforces every permission and
 * record-level access rule. The session lives in an HTTP-only cookie only. */
(function () {
  const CIQ = {};
  CIQ.user = null;
  let authed = false;

  const CART_KEY = "ciq_cart_v1";
  function readCart() { try { const v = JSON.parse(localStorage.getItem(CART_KEY) || "[]"); return Array.isArray(v) ? v : []; } catch (e) { return []; } }
  function cartKey(item) { return String(item.key || `${item.productId}:${item.supplierId || "any"}`); }
  function writeCart(items) { localStorage.setItem(CART_KEY, JSON.stringify(items)); document.dispatchEvent(new CustomEvent("ciq:cart-change")); }
  CIQ.cart = {
    items: readCart,
    add(item) { const items = readCart(), key = cartKey(item), old = items.find((i) => cartKey(i) === key), qty = Math.max(1, Number(item.quantity) || 1); if (old) old.quantity = Math.max(1, Number(old.quantity) || 1) + qty; else items.push({ ...item, key, quantity: qty }); writeCart(items); },
    update(key, qty) { const items = readCart(), item = items.find((i) => cartKey(i) === String(key)); if (item) item.quantity = Math.max(1, Number(qty) || 1); writeCart(items); },
    remove(key) { writeCart(readCart().filter((i) => cartKey(i) !== String(key))); },
    clear() { writeCart([]); },
    total(items = readCart()) { return items.reduce((sum, i) => sum + (Number(i.unitPrice) || 0) * (Number(i.quantity) || 0), 0); },
  };

  /* ---- API ---------------------------------------------------------- */
  async function request(method, path, body) {
    const opts = { method, credentials: "include", headers: { "Content-Type": "application/json" } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      throw new Error("Network error — check your connection.");
    }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (res.status === 401 && authed && !location.pathname.endsWith("login.html")) {
      location.href = "login.html?expired=1";
      throw new Error("Session expired");
    }
    if (!res.ok) {
      const err = new Error((data && data.error) || res.statusText || "Request failed");
      err.status = res.status; err.data = data;
      throw err;
    }
    return data;
  }
  CIQ.api = {
    get: (p) => request("GET", p),
    post: (p, b) => request("POST", p, b),
    patch: (p, b) => request("PATCH", p, b),
  };

  CIQ.hasPerm = (key) => {
    if (!CIQ.user) return false;
    let perms = CIQ.user.permissions;
    if (typeof perms === "string") {
      try { perms = JSON.parse(perms); } catch (e) { perms = []; }
    }
    if (!Array.isArray(perms)) perms = perms ? Array.from(perms) : [];
    return perms.includes("admin.system") || perms.includes(key);
  };
  CIQ.logout = async function () {
    try { await CIQ.api.post("/api/auth/logout", {}); } catch (e) {}
    location.href = "login.html";
  };

  /* ---- Navigation config (role-aware) ------------------------------- */
  function navForUser() {
    const roles = (CIQ.user && CIQ.user.roles) || [];
    const landing = (CIQ.user && CIQ.user.default_landing_page) || "sales-dashboard.html";
    const isAdmin = roles.includes("admin") || CIQ.hasPerm("admin.system");
    const isManager = roles.includes("sales_manager") || CIQ.hasPerm("companies.assign");
    const isEstimator = roles.includes("estimator");
    const isReadOnly = roles.includes("read_only");

    if (isAdmin) {
      return [
        { label: null, items: [
          { href: "admin-dashboard.html", label: "Dashboard", icon: "▦" },
          { href: "my-companies.html", label: "Companies", icon: "⌂", perm: "companies.view" },
          { href: "opportunities.html", label: "Project Records", icon: "◎", perm: "projects.view" },
          { href: "opportunity-board.html", label: "Opportunity Pipeline", icon: "⚡", perm: "projects.view" },
          { href: "estimator-work-queue.html", label: "Estimates", icon: "$", perm: "projects.view" },
          { href: "assignments.html", label: "Assignments", icon: "⇄", perm: "companies.assign" },
          { href: "team-dashboard.html", label: "Team", icon: "☰", perm: "users.view" },
          { href: "reports.html", label: "Reports", icon: "⊟", perm: "reports.view" },
          { href: "user-management.html", label: "Users", icon: "⦿", perm: "users.create" },
          { href: "contact-enrichment-admin.html", label: "Contact Import", icon: "⇩", perm: "admin.system" },
          { href: "intelligence-import.html", label: "Intelligence Import", icon: "◇", perm: "admin.system" },
          { href: "catalog-admin.html", label: "Administration", icon: "⚙", perm: "admin.system" },
        ] },
      ];
    }
    if (isManager) {
      return [
        { label: null, items: [
          { href: "team-dashboard.html", label: "Dashboard", icon: "▦" },
          { href: "my-companies.html", label: "Companies", icon: "⌂", perm: "crm.relationships.view" },
          { href: "my-tasks.html", label: "Tasks", icon: "✓", perm: "crm.tasks.view" },
          { href: "activity.html", label: "Activity", icon: "≣", perm: "crm.activities.view" },
          { href: "opportunities.html", label: "Project Records", icon: "◎", perm: "projects.view_assigned" },
          { href: "opportunity-board.html", label: "Opportunity Pipeline", icon: "⚡", perm: "projects.view_assigned" },
          { href: "assignments.html", label: "Assignments", icon: "⇄", perm: "companies.assign" },
          { href: "reports.html", label: "Reports", icon: "⊟", perm: "reports.view" },
        ] },
        { label: "Catalog", items: [
          { href: "product-search.html", label: "Products", icon: "⬡", perm: "products.view" },
        ] },
      ];
    }
    if (isEstimator) {
      return [
        { label: null, items: [
          { href: "estimator-work-queue.html", label: "Work Queue", icon: "▦" },
          { href: "estimator-work-queue.html#mine", label: "My Estimates", icon: "$" },
          { href: "estimator-work-queue.html#submitted", label: "Submitted Estimates", icon: "✓" },
        ] },
      ];
    }
    if (isReadOnly) {
      return [
        { label: null, items: [
          { href: landing, label: "Dashboard", icon: "▦" },
          { href: "my-companies.html", label: "My Companies", icon: "⌂", perm: "crm.relationships.view" },
          { href: "activity.html", label: "Activity", icon: "≣", perm: "crm.activities.view" },
          { href: "reports.html", label: "Reports", icon: "⊟", perm: "reports.view" },
        ] },
      ];
    }
    // Sales representative (default)
    return [
      { label: null, items: [
        { href: "sales-dashboard.html", label: "Dashboard", icon: "▦" },
        { href: "my-companies.html", label: "My Companies", icon: "⌂", perm: "crm.relationships.view" },
        { href: "my-tasks.html", label: "Tasks", icon: "✓", perm: "crm.tasks.view" },
        { href: "activity.html", label: "Activity", icon: "≣", perm: "crm.activities.view" },
        { href: "opportunities.html", label: "Project Records", icon: "◎", perm: "projects.view_assigned" },
        { href: "opportunity-board.html", label: "Opportunity Pipeline", icon: "⚡", perm: "projects.view_assigned" },
      ] },
      { label: "More", items: [
        { href: "reports.html", label: "Reports", icon: "⊟", perm: "reports.view" },
        { href: "product-search.html", label: "Products", icon: "⬡", perm: "products.view" },
      ] },
    ];
  }

  function navHtml(active) {
    let out = "";
    for (const group of navForUser()) {
      const items = group.items.filter((i) => !i.perm || CIQ.hasPerm(i.perm));
      if (!items.length) continue;
      if (group.label) out += `<div class="nav-group-label">${group.label}</div>`;
      out += items.map((i) => {
        const hrefBase = (i.href || "").split("#")[0];
        const on = active === i.href || active === hrefBase ? " active" : "";
        return `<a class="nav-item${on}" href="${i.href}">
          <span class="ico" aria-hidden="true">${i.icon}</span>${CIQ.esc(i.label)}</a>`;
      }).join("");
    }
    return out;
  }

  function initials(name) {
    const parts = (name || "?").trim().split(/\s+/);
    return ((parts[0] || "")[0] || "" + ((parts[1] || "")[0] || "")).toUpperCase() +
      (parts[1] ? parts[1][0].toUpperCase() : "");
  }

  function primaryRole() {
    const r = (CIQ.user && CIQ.user.roles) || [];
    const map = { admin: "Administrator", sales_manager: "Sales Manager",
      sales_representative: "Sales Rep", estimator: "Estimator",
      read_only: "Read Only", fulfillment_user: "Fulfillment" };
    return map[r[0]] || (r[0] || "Employee");
  }

  CIQ.renderShell = function (opts) {
    opts = opts || {};
    const app = document.createElement("div");
    app.className = "app"; app.id = "ciqApp";
    app.innerHTML = `
      <div class="scrim" id="ciqScrim"></div>
      <aside class="sidebar" id="ciqSidebar">
        <div class="sidebar-brand">
          <div class="brand-mark">CIQ</div>
          <div class="brand-text"><div class="t">CorridorIQ</div><div class="s">Sales Workspace</div></div>
        </div>
        <nav class="nav">${navHtml(opts.active)}</nav>
        <div class="sidebar-foot">
          <a class="nav-item" href="#" id="ciqSignout"><span class="ico">⇥</span>Sign out</a>
        </div>
      </aside>
      <div class="main">
        <header class="topbar">
          <button class="icon-btn mobile-only" id="ciqBurger" aria-label="Menu">☰</button>
          <div class="stack">
            <h1>${CIQ.esc(opts.title || "")}</h1>
            ${opts.subtitle ? `<div class="subtitle">${CIQ.esc(opts.subtitle)}</div>` : ""}
          </div>
          <form class="topbar-search" id="ciqSearchForm">
            <span class="si">⌕</span>
            <input type="search" id="ciqSearch" placeholder="Search companies…" autocomplete="off" />
          </form>
          <div class="topbar-actions">
            <div class="cart-wrap" id="ciqCartWrap">
              <button class="icon-btn" id="ciqCartBtn" type="button" aria-label="Cart" aria-haspopup="dialog" aria-expanded="false">🛒<span class="count" id="ciqCartCount" hidden>0</span></button>
              <section class="cart-dropdown" id="ciqCartDropdown" role="dialog" aria-label="Shopping cart" hidden><div class="cart-head"><strong>Your cart</strong><button class="icon-btn cart-close" id="ciqCartClose" type="button" aria-label="Close cart">×</button></div><div class="cart-items" id="ciqCartItems"></div><div class="cart-foot"><div class="cart-total"><span>Subtotal</span><strong id="ciqCartSubtotal">$0.00</strong></div><div class="cart-total grand"><span>Total</span><strong id="ciqCartTotal">$0.00</strong></div><a class="btn btn-primary btn-block" id="ciqCheckoutBtn" href="checkout.html">Checkout</a></div></section>
            </div>
            <button class="icon-btn" id="ciqTasksBtn" title="Tasks" aria-label="Tasks">✓</button>
            <div class="profile" id="ciqProfile" tabindex="0">
              <div class="avatar">${CIQ.esc(initials(CIQ.user.display_name || CIQ.user.email))}</div>
              <div class="stack">
                <span class="profile-name">${CIQ.esc(CIQ.user.display_name || CIQ.user.email)}</span>
                <span class="profile-role">${CIQ.esc(primaryRole())}</span>
              </div>
              <div class="menu" id="ciqMenu">
                <a href="login.html?change=1">Change password</a>
                <button type="button" id="ciqMenuSignout">Sign out</button>
              </div>
            </div>
          </div>
        </header>
        <div class="content" id="content"></div>
      </div>`;
    // Move page content (from <template id="pageContent">) into #content.
    const tpl = document.getElementById("pageContent");
    document.body.innerHTML = "";
    document.body.appendChild(app);
    let host = document.getElementById("toastHost");
    if (!host) { host = document.createElement("div"); host.id = "toastHost"; document.body.appendChild(host); }
    if (tpl) app.querySelector("#content").appendChild(tpl.content.cloneNode(true));

    // Wire shell interactions.
    const go = (e) => { e.preventDefault(); CIQ.logout(); };
    app.querySelector("#ciqSignout").addEventListener("click", go);
    app.querySelector("#ciqMenuSignout").addEventListener("click", go);
    app.querySelector("#ciqTasksBtn").addEventListener("click", () => location.href = "my-tasks.html");
    const burger = app.querySelector("#ciqBurger");
    burger.addEventListener("click", () => app.classList.toggle("nav-open"));
    app.querySelector("#ciqScrim").addEventListener("click", () => app.classList.remove("nav-open"));
    const cartWrap = app.querySelector("#ciqCartWrap"), cartButton = app.querySelector("#ciqCartBtn"), cartPanel = app.querySelector("#ciqCartDropdown");
    const closeCart = () => { cartPanel.hidden = true; cartButton.setAttribute("aria-expanded", "false"); };
    const money = (v) => "$" + Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const renderCart = () => { const items = CIQ.cart.items(), count = items.reduce((n, i) => n + (Number(i.quantity) || 0), 0), badge = app.querySelector("#ciqCartCount"); badge.textContent = count; badge.hidden = !count; app.querySelector("#ciqCartItems").innerHTML = items.length ? items.map((i) => { const qty = Math.max(1, Number(i.quantity) || 1); return `<article class="cart-item" data-cart-key="${CIQ.esc(cartKey(i))}"><div class="cart-item-copy"><strong>${CIQ.esc(i.name || i.sku || "Product")}</strong><span>${CIQ.esc(i.supplier || "Supplier not selected")}</span><small>${CIQ.esc(i.sku || "")} · ${money(i.unitPrice)} each</small></div><div class="cart-item-actions"><div class="qty-control"><button type="button" data-cart-action="decrease" aria-label="Decrease quantity">−</button><output>${qty}</output><button type="button" data-cart-action="increase" aria-label="Increase quantity">+</button></div><strong>${money((Number(i.unitPrice) || 0) * qty)}</strong><button class="cart-remove" type="button" data-cart-action="remove">Remove</button></div></article>`; }).join("") : `<div class="cart-empty"><strong>Your cart is empty</strong><span>Add a supplier offer to begin checkout.</span></div>`; const total = CIQ.cart.total(items); app.querySelector("#ciqCartSubtotal").textContent = money(total); app.querySelector("#ciqCartTotal").textContent = money(total); const checkout = app.querySelector("#ciqCheckoutBtn"); checkout.classList.toggle("disabled", !items.length); checkout.setAttribute("aria-disabled", String(!items.length)); };
    const openCart = () => { cartPanel.hidden = false; cartButton.setAttribute("aria-expanded", "true"); renderCart(); };
    cartButton.addEventListener("click", (e) => { e.stopPropagation(); cartPanel.hidden ? openCart() : closeCart(); });
    app.querySelector("#ciqCartClose").addEventListener("click", closeCart);
    cartPanel.addEventListener("click", (e) => { e.stopPropagation(); const control = e.target.closest("[data-cart-action]"); if (!control) return; const row = control.closest("[data-cart-key]"), item = CIQ.cart.items().find((i) => cartKey(i) === row.dataset.cartKey); if (!item) return; if (control.dataset.cartAction === "remove") CIQ.cart.remove(row.dataset.cartKey); else CIQ.cart.update(row.dataset.cartKey, Number(item.quantity) + (control.dataset.cartAction === "increase" ? 1 : -1)); });
    app.querySelector("#ciqCheckoutBtn").addEventListener("click", (e) => { if (!CIQ.cart.items().length) e.preventDefault(); });
    document.addEventListener("ciq:cart-change", renderCart);
    document.addEventListener("click", (e) => { if (!cartWrap.contains(e.target)) closeCart(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCart(); });
    renderCart();
    const prof = app.querySelector("#ciqProfile"), menu = app.querySelector("#ciqMenu");
    prof.addEventListener("click", (e) => { if (e.target.closest(".menu")) return; menu.classList.toggle("open"); });
    document.addEventListener("click", (e) => { if (!prof.contains(e.target)) menu.classList.remove("open"); });
    const sform = app.querySelector("#ciqSearchForm");
    sform.addEventListener("submit", (e) => {
      e.preventDefault();
      const q = app.querySelector("#ciqSearch").value.trim();
      location.href = "my-companies.html" + (q ? "?q=" + encodeURIComponent(q) : "");
    });
    return app.querySelector("#content");
  };

  CIQ.setTaskCount = function (n) {
    const b = document.getElementById("ciqTasksBtn");
    if (!b) return;
    let c = b.querySelector(".count");
    if (n > 0) {
      if (!c) { c = document.createElement("span"); c.className = "count"; b.appendChild(c); }
      c.textContent = n;
    } else if (c) { c.remove(); }
  };

  /* ---- Guard -------------------------------------------------------- */
  CIQ.landingPage = function () {
    return (CIQ.user && CIQ.user.default_landing_page) || "sales-dashboard.html";
  };

  CIQ.guard = async function (requiredPerm, opts) {
    opts = opts || {};
    let me;
    try { me = await CIQ.api.get("/api/auth/me"); }
    catch (e) { location.href = "login.html"; return null; }
    CIQ.user = me.user; authed = true;
    if (CIQ.user.must_change_password && !location.pathname.endsWith("login.html")) {
      location.href = "login.html?change=1"; return null;
    }
    const anyPerm = opts.anyPerm;
    let allowed = true;
    if (anyPerm && anyPerm.length) {
      allowed = anyPerm.some((p) => CIQ.hasPerm(p));
    } else if (requiredPerm) {
      allowed = CIQ.hasPerm(requiredPerm);
    }
    // Role fallback: administrators always reach admin-only pages.
    if (!allowed && requiredPerm === "admin.system") {
      const roles = (CIQ.user && CIQ.user.roles) || [];
      allowed = roles.includes("admin");
    }
    if (!allowed) {
      location.href = CIQ.landingPage();
      return null;
    }
    CIQ.content = CIQ.renderShell(opts);
    return CIQ.user;
  };

  /* ---- Toast -------------------------------------------------------- */
  CIQ.toast = function (msg, type = "info", ms = 3200) {
    const host = document.getElementById("toastHost");
    if (!host) return;
    const t = document.createElement("div");
    t.className = "toast " + type;
    t.innerHTML = `<span>${CIQ.esc(msg)}</span>`;
    host.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 200); }, ms);
  };

  /* ---- Modal / confirm --------------------------------------------- */
  CIQ.modal = function ({ title, body, footer, onMount, width }) {
    const back = document.createElement("div");
    back.className = "modal-backdrop";
    back.innerHTML = `<div class="modal" role="dialog" aria-modal="true" ${width ? `style="max-width:${width}px"` : ""}>
      <div class="modal-head"><h3>${CIQ.esc(title || "")}</h3>
        <button class="modal-close" aria-label="Close">&times;</button></div>
      <div class="modal-body"></div>
      ${footer !== undefined ? `<div class="modal-foot"></div>` : ""}</div>`;
    back.querySelector(".modal-body").innerHTML = typeof body === "string" ? body : "";
    if (typeof body !== "string" && body) back.querySelector(".modal-body").appendChild(body);
    if (footer && footer instanceof Node) back.querySelector(".modal-foot").appendChild(footer);
    else if (typeof footer === "string") back.querySelector(".modal-foot").innerHTML = footer;
    const close = () => back.remove();
    back.querySelector(".modal-close").addEventListener("click", close);
    back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
    document.addEventListener("keydown", function esc(e) {
      if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
    });
    document.body.appendChild(back);
    if (onMount) onMount(back, close);
    return { el: back, close };
  };

  CIQ.confirm = function (message, { danger = false, confirmLabel = "Confirm" } = {}) {
    return new Promise((resolve) => {
      const foot = document.createElement("div");
      foot.innerHTML = `<button class="btn" data-a="cancel">Cancel</button>
        <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-a="ok">${CIQ.esc(confirmLabel)}</button>`;
      const m = CIQ.modal({ title: "Please confirm", body: `<p>${CIQ.esc(message)}</p>`, footer: foot });
      foot.querySelector('[data-a="cancel"]').addEventListener("click", () => { m.close(); resolve(false); });
      foot.querySelector('[data-a="ok"]').addEventListener("click", () => { m.close(); resolve(true); });
    });
  };

  /* ---- Log Activity (reusable, Phase 6) ---------------------------- */
  const OUTCOMES = [
    ["", "—"], ["no_answer", "No Answer"], ["left_voicemail", "Left Voicemail"],
    ["spoke_with_contact", "Spoke With Contact"], ["interested", "Interested"],
    ["follow_up_requested", "Follow-Up Requested"], ["appointment_set", "Appointment Set"],
    ["quote_requested", "Quote Requested"], ["not_interested", "Not Interested"],
    ["wrong_number", "Wrong Number"], ["do_not_contact", "Do Not Contact"],
  ];
  const TYPES = [
    ["call", "Call"], ["voicemail", "Voicemail"], ["email", "Email"],
    ["text_message", "Text"], ["meeting", "Meeting"], ["site_visit", "Site Visit"],
    ["note", "Note"], ["quote_request", "Quote Request"], ["follow_up", "Follow-Up"],
  ];
  const STATUS_OPTS = [
    ["", "No change"], ["contacted", "Contacted"], ["qualified", "Qualified"],
    ["follow_up", "Follow-Up"], ["quote_requested", "Quote Requested"],
    ["quote_sent", "Quote Sent"], ["negotiating", "Negotiating"], ["won", "Won"],
    ["lost", "Lost"], ["do_not_contact", "Do Not Contact"],
  ];

  CIQ.logActivity = function ({ companyId, companyName, prefill = {}, projects = [], onSaved }) {
    const nowLocal = new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
      .toISOString().slice(0, 16);
    const projOpts = ['<option value="">— none —</option>']
      .concat(projects.map((p) => `<option value="${p.project_id || p.id}">${CIQ.esc(
        (p.job_address || p.address || p.permit_number || ("Project #" + (p.project_id || p.id))))}</option>`)).join("");
    const body = document.createElement("form");
    body.className = "form-grid";
    body.innerHTML = `
      <label class="fld">Activity type
        <select name="activity_type">${TYPES.map(([v, l]) =>
          `<option value="${v}" ${prefill.activity_type === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="fld">Outcome
        <select name="activity_outcome">${OUTCOMES.map(([v, l]) =>
          `<option value="${v}" ${prefill.activity_outcome === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="fld">Contact person
        <input name="subject" placeholder="Who did you speak with?" value="${CIQ.esc(prefill.subject || "")}" /></label>
      <label class="fld">When
        <input type="datetime-local" name="activity_at" value="${nowLocal}" /></label>
      <label class="fld full">Notes
        <textarea name="notes" placeholder="What happened?">${CIQ.esc(prefill.notes || "")}</textarea></label>
      <label class="fld">Related project
        <select name="project_id">${projOpts}</select></label>
      <label class="fld">Next follow-up
        <input type="datetime-local" name="next_followup_at" /></label>
      <label class="fld full">Update relationship status (optional)
        <select name="relationship_status">${STATUS_OPTS.map(([v, l]) =>
          `<option value="${v}">${l}</option>`).join("")}</select></label>`;

    const foot = document.createElement("div");
    foot.innerHTML = `<button type="button" class="btn" data-a="cancel">Cancel</button>
      <button type="button" class="btn btn-primary" data-a="save">Save activity</button>`;

    let dirty = false;
    body.addEventListener("input", () => { dirty = true; });

    const m = CIQ.modal({
      title: companyName ? `Log activity · ${companyName}` : "Log activity",
      body, footer: foot,
    });

    foot.querySelector('[data-a="cancel"]').addEventListener("click", async () => {
      if (dirty && body.querySelector('[name="notes"]').value.trim()) {
        if (!(await CIQ.confirm("Discard your unsaved notes?", { danger: true, confirmLabel: "Discard" }))) return;
      }
      m.close();
    });

    const saveBtn = foot.querySelector('[data-a="save"]');
    saveBtn.addEventListener("click", async () => {
      const fd = new FormData(body);
      const payload = {
        activity_type: fd.get("activity_type"),
        activity_outcome: fd.get("activity_outcome") || null,
        subject: (fd.get("subject") || "").trim() || null,
        notes: (fd.get("notes") || "").trim() || null,
        activity_at: fd.get("activity_at") ? fd.get("activity_at") + ":00" : null,
        project_id: fd.get("project_id") ? Number(fd.get("project_id")) : null,
        next_followup_at: fd.get("next_followup_at") ? fd.get("next_followup_at") + ":00" : null,
      };
      const newStatus = fd.get("relationship_status");
      // Duplicate-submit prevention + disabled state.
      saveBtn.disabled = true; saveBtn.textContent = "Saving…";
      try {
        if ((newStatus === "lost" || newStatus === "do_not_contact")) {
          const ok = await CIQ.confirm(
            newStatus === "lost" ? "Mark this company as Lost?" : "Set this company to Do Not Contact?",
            { danger: true, confirmLabel: "Yes, continue" });
          if (!ok) { saveBtn.disabled = false; saveBtn.textContent = "Save activity"; return; }
        }
        const act = await CIQ.api.post(`/api/sales/companies/${companyId}/activities`, payload);
        if (newStatus) {
          await CIQ.api.patch(`/api/sales/companies/${companyId}/relationship`, { relationship_status: newStatus });
        }
        CIQ.toast("Activity saved", "success");
        m.close();
        if (onSaved) onSaved(act, newStatus || null);
      } catch (e) {
        CIQ.toast(e.message || "Could not save activity", "error");
        saveBtn.disabled = false; saveBtn.textContent = "Save activity";
      }
    });
    return m;
  };

  /* ---- Badges & formatting ----------------------------------------- */
  const STATUS_LABEL = {
    new: "New", assigned: "Assigned", researching: "Researching",
    attempted_contact: "Attempted", contacted: "Contacted", qualified: "Qualified",
    follow_up: "Follow-Up", quote_requested: "Quote Requested", quote_sent: "Quote Sent",
    negotiating: "Negotiating", won: "Won", lost: "Lost",
    do_not_contact: "Do Not Contact", inactive: "Inactive",
  };
  const STATUS_COLOR = {
    won: "green", lost: "red", do_not_contact: "red", quote_requested: "gold",
    quote_sent: "gold", qualified: "blue", negotiating: "blue", contacted: "amber",
    follow_up: "amber", researching: "slate", attempted_contact: "amber",
    new: "slate", assigned: "slate", inactive: "slate",
  };
  const TIER_COLOR = { Critical: "red", High: "amber", Medium: "blue", Low: "slate" };

  CIQ.statusLabel = (s) => STATUS_LABEL[s] || CIQ.titleCase(s || "");
  CIQ.statusBadge = (s) =>
    `<span class="badge ${STATUS_COLOR[s] || "slate"}"><span class="dot"></span>${CIQ.esc(CIQ.statusLabel(s))}</span>`;
  CIQ.tierBadge = (t) => t ? `<span class="badge ${TIER_COLOR[t] || "slate"}">${CIQ.esc(t)}</span>` : "";
  CIQ.scoreChip = (v) => v == null ? '<span class="muted">—</span>' :
    `<span class="score-chip">${Math.round(v)}</span>`;
  CIQ.priorityBadge = (tier, score) =>
    `${CIQ.tierBadge(tier)} ${score != null ? `<span class="score-chip muted">${Math.round(score)}</span>` : ""}`;

  CIQ.activityLabel = (t) => ({
    call: "Call", voicemail: "Voicemail", email: "Email", text_message: "Text",
    meeting: "Meeting", site_visit: "Site Visit", note: "Note",
    quote_request: "Quote Request", quote_sent: "Quote Sent", follow_up: "Follow-Up",
    status_change: "Status Change", assignment: "Assignment",
  }[t] || CIQ.titleCase(t || ""));
  CIQ.outcomeLabel = (o) => (OUTCOMES.find((x) => x[0] === o) || [o, CIQ.titleCase(o || "")])[1];

  CIQ.titleCase = (s) => String(s || "").replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

  /* ---- Dates -------------------------------------------------------- */
  CIQ.fmtDate = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso); if (isNaN(d)) return String(iso).slice(0, 10);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  };
  CIQ.fmtDateTime = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso); if (isNaN(d)) return String(iso);
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  };
  CIQ.relTime = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso); if (isNaN(d)) return String(iso).slice(0, 10);
    const s = (Date.now() - d.getTime()) / 1000;
    if (s < 0) { // future
      const days = Math.round(-s / 86400);
      if (days === 0) return "today";
      if (days === 1) return "tomorrow";
      return "in " + days + "d";
    }
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    const days = Math.floor(s / 86400);
    if (days < 30) return days + "d ago";
    return CIQ.fmtDate(iso);
  };
  CIQ.money = (v) => v == null ? "—" :
    "$" + Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });

  /* ---- Utilities ---------------------------------------------------- */
  CIQ.esc = function (s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  };
  CIQ.qs = (name) => new URLSearchParams(location.search).get(name);
  CIQ.debounce = function (fn, ms = 300) {
    let t; return function (...a) { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), ms); };
  };
  CIQ.busy = async function (btn, fn) {
    if (btn.disabled) return;
    const label = btn.textContent; btn.disabled = true; btn.dataset.busy = "1";
    try { return await fn(); }
    finally { btn.disabled = false; btn.textContent = label; delete btn.dataset.busy; }
  };
  CIQ.skeletonRows = (n = 5) =>
    Array.from({ length: n }).map(() => `<div class="skel skel-row"></div>`).join("");
  CIQ.emptyState = ({ icon = "◎", title = "Nothing here yet", text = "", action = "" }) =>
    `<div class="empty-state"><div class="ico">${icon}</div><h3>${CIQ.esc(title)}</h3>
     <p>${CIQ.esc(text)}</p>${action}</div>`;
  CIQ.errorBanner = (msg) => `<div class="error-banner">${CIQ.esc(msg)}</div>`;

  CIQ.pager = function (el, { page, pages, total, onPage }) {
    if (!pages || pages <= 1) { el.innerHTML = total != null ? `<span class="muted">${total} total</span>` : ""; return; }
    el.innerHTML = `<button class="btn btn-sm" ${page <= 1 ? "disabled" : ""} data-a="prev">← Prev</button>
      <span>Page ${page} of ${pages}${total != null ? ` · ${total} total` : ""}</span>
      <button class="btn btn-sm" ${page >= pages ? "disabled" : ""} data-a="next">Next →</button>`;
    const p = el.querySelector('[data-a="prev"]'), n = el.querySelector('[data-a="next"]');
    if (p) p.addEventListener("click", () => onPage(page - 1));
    if (n) n.addEventListener("click", () => onPage(page + 1));
  };

  /* Warn before leaving with unsaved input. */
  CIQ.guardUnsaved = function (isDirty) {
    window.addEventListener("beforeunload", (e) => {
      if (isDirty()) { e.preventDefault(); e.returnValue = ""; }
    });
  };

  window.CIQ = CIQ;
})();
