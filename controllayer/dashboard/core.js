// The employee panel (/me), rendered with Primer CSS markup: data loading, page composition and actions.
// The security console is app.js and views/*.js.
(function () {
  "use strict";
  if (!location.pathname.startsWith("/me")) return;
  const params = new URLSearchParams(location.search);
  const HOURS = [1, 8, 24, 168];
  const state = {
    as: params.get("as") || "",
    hours: 8,
    error: "",
  };

  // ---- formatting ---------------------------------------------------------------------

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const usd = (x) => "$" + Number(x || 0).toFixed(Number(x) >= 1 ? 2 : 4);
  const pct = (a, b) => (b ? Math.min(100, (100 * a) / b) : 0);
  const dur = (h) => (h % 24 ? h + "h" : h / 24 + "d");
  function ago(ts) {
    if (!ts) return muted("never");
    const s = ts - Date.now() / 1000, a = Math.abs(s);
    const v = a < 60 ? Math.round(a) + "s" : a < 3600 ? Math.round(a / 60) + "m" : a < 86400 ? Math.round(a / 3600) + "h" : Math.round(a / 86400) + "d";
    return `<span class="no-wrap" title="${esc(new Date(ts * 1000).toLocaleString())}">${s > 0 ? "in " + v : v + " ago"}</span>`;
  }
  const TONE = { allow: "ok", log: "neutral", warn: "warn", redact: "info", block: "bad",
    normal: "ok", watch: "warn", restricted: "bad", low: "ok", medium: "warn", high: "bad" };
  const tone = (x) => TONE[x] || "neutral";

  // ---- Primer building blocks ---------------------------------------------------------

  const LABEL = { ok: "Label--success", warn: "Label--attention", bad: "Label--danger", info: "Label--accent", neutral: "Label--secondary" };
  const FLASH = { ok: "flash-success", warn: "flash-warn", bad: "flash-error" };
  const FG = { ok: "color-fg-success", warn: "color-fg-attention", bad: "color-fg-danger", info: "color-fg-accent" };
  const BG = { ok: "color-bg-success-emphasis", warn: "color-bg-attention-emphasis", bad: "color-bg-danger-emphasis", info: "color-bg-accent-emphasis" };

  const muted = (html) => `<span class="color-fg-muted f6">${html}</span>`;
  const empty = (text) => `<div class="color-fg-muted f6">${esc(text)}</div>`;
  const badge = (t, text) => `<span class="Label ${LABEL[t] || LABEL.neutral}">${esc(text)}</span>`;
  const note = (html, t) => `<div class="flash ${FLASH[t] || ""} mb-3">${html}</div>`;
  const button = (label, attrs, kind) => `<button type="button" class="btn btn-sm ${kind ? "btn-" + kind : ""}" ${attrs}>${esc(label)}</button>`;
  const link = (label, href) => `<a class="btn btn-sm btn-invisible" href="${esc(href)}">${esc(label)}</a>`;
  const meter = (p, t) => `<span class="Progress acl-progress"><span class="Progress-item ${BG[t] || BG.info}" style="width:${p}%"></span></span>`;
  const card = (title, body, tools) => `<div class="Box">
      <div class="Box-header d-flex flex-items-center flex-wrap"><h3 class="Box-title flex-auto">${esc(title)}</h3><div class="acl-tools">${tools || ""}</div></div>
      <div class="Box-body">${body}</div></div>`;
  const table = (head, rows, num = []) => `<div class="markdown-body acl-scroll"><table>
      ${head ? `<thead><tr>${head.map((h, i) => `<th class="${num.includes(i) ? "acl-num" : ""}">${esc(h)}</th>`).join("")}</tr></thead>` : ""}
      <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${num.includes(i) ? "acl-num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;

  // One click picks an option: [{label, sub?, active, attrs}].
  const segmented = (items, cls = "") => `<div class="BtnGroup ${cls}" role="group">${items.map((o) =>
    `<button type="button" class="btn btn-sm BtnGroup-item${o.active ? " btn-primary" : ""}" aria-selected="${!!o.active}" ${o.attrs}>${esc(o.label)}${o.sub ? `<span class="acl-sub">${esc(o.sub)}</span>` : ""}</button>`).join("")}</div>`;
  // Segmented for a handful of choices, a select beyond that.
  const choice = (act, values, current, label = (v) => v) => values.length <= 6
    ? segmented(values.map((v) => ({ label: label(v) || "all", active: v === current, attrs: `data-act="${act}" data-v="${esc(v)}"` })))
    : `<select class="form-select select-sm" data-act="${act}">${values.map((v) => `<option value="${esc(v)}"${v === current ? " selected" : ""}>${esc(label(v))}</option>`).join("")}</select>`;
  const stats = (items) => `<div class="acl-stats">${items.map((k) => `<div>
      <div class="f6 color-fg-muted">${esc(k.label)}</div>
      <div class="acl-big ${FG[k.tone] || ""}">${esc(k.value)}</div>
      ${k.meter != null ? meter(k.meter, k.meter > 80 ? "bad" : "info") : ""}${k.sub ? `<div class="f6 color-fg-muted">${esc(k.sub)}</div>` : ""}</div>`).join("")}</div>`;
  const grid = (items) => `<div class="acl-grid">${items.map(([span, html]) => `<div class="acl-s${span}">${html}</div>`).join("")}</div>`;
  const shell = (p) => `
    <div class="Header">
      <div class="Header-item"><span class="Header-link f4 text-bold">AI Control Layer</span></div>
      <div class="Header-item"><a class="Header-link" href="/security">Security console</a></div>
      <div class="Header-item"><a class="Header-link text-underline" href="${"/me" + location.search}">Employee panel</a></div>
      <div class="Header-item Header-item--full"></div>
      <div class="Header-item acl-tools">${p.tools || ""}</div>
    </div>
    <div class="container-xl px-3 py-4">
      <div class="Subhead"><h2 class="Subhead-heading">${esc(p.title)}</h2><div class="Subhead-description text-mono f6">${p.meta}</div></div>
      ${p.notice || ""}${p.body}</div>`;

  // ---- data ---------------------------------------------------------------------------

  async function call(path, opts = {}) {
    const headers = { "content-type": "application/json" };
    if (state.as) headers["x-acl-as"] = state.as;
    const r = await fetch(path, { ...opts, headers });
    const body = await r.json().catch(() => ({}));
    if (!r.ok && !opts.allowError) throw new Error(body.error?.message || body.error || body.detail || "HTTP " + r.status);
    return { status: r.status, body };
  }
  const get = async (path) => (await call(path)).body;
  const send = (path, method, data, allowError) => call(path, { method, body: JSON.stringify(data || {}), allowError });

  // ---- employee panel -----------------------------------------------------------------

  async function employeeData() {
    const people = (await get("/me/api/people")).people;
    if (!people.some((p) => p.principal === state.as)) state.as = people[0]?.principal || "";
    const [me, res, act] = await Promise.all([get("/me/api/profile"), get("/me/api/resources"), get("/me/api/activity")]);
    return { people, me, res, act };
  }

  function employeePage(d) {
    const me = d.me;
    const mine = me.budgets.find((b) => b.scope === "principal") || { requests: 0, tokens: 0, usd: 0 };
    const team = me.budgets.find((b) => b.scope === "team");
    const usage = stats([
      { label: "Requests today", value: mine.requests },
      { label: "Tokens today", value: mine.tokens.toLocaleString() },
      { label: "Spend today", value: usd(mine.usd) },
      ...(team && team.usd_limit ? [{ label: "Team budget used", value: pct(team.usd, team.usd_limit).toFixed(1) + "%", meter: pct(team.usd, team.usd_limit), sub: `${usd(team.usd)} of $${team.usd_limit}` }] : []),
    ]);

    // Each scope is one click: add it to the agent's grant, or take it away (the last one revokes).
    // Scope edits keep the grant's expiry; an inactive grant is only renewed or revoked, explicitly.
    const cell = (r, agent) => {
      const g = r.grants[agent];
      const at = `data-agent="${esc(agent)}" data-rid="${esc(r.id)}"`;
      if (!g && r.suspended) return muted("suspended by security");
      if (g && !g.active) return `${g.scopes.map((sc) => badge("neutral", sc)).join(" ")}
        <div class="f6 mt-1">${badge("bad", "inactive")} ${muted(r.suspended ? "suspended by security" : g.expires_at ? "expired " + ago(g.expires_at) : "")}
        ${r.suspended ? "" : button("Renew " + dur(hoursFor(r)), `data-act="renew" ${at}`, "invisible")}${button("Revoke", `data-act="revoke" ${at}`, "invisible")}</div>`;
      const has = new Set(g ? g.scopes : []);
      const scopes = segmented(r.scopes.map((sc) => ({ label: sc, active: has.has(sc),
        attrs: `data-act="scope" ${at} data-v="${esc(sc)}" title="${has.has(sc) ? "remove " + esc(sc) : g ? "add " + esc(sc) : "grant " + esc(sc) + " for " + dur(hoursFor(r))}"` })));
      return scopes + (g ? `<div class="f6 mt-1">${muted(g.expires_at ? "expires " + ago(g.expires_at) : "no expiry")}</div>` : "");
    };
    const resources = d.res.resources.length ? table(["Resource", ...d.res.agents], d.res.resources.map((r) => [
      `<b>${esc(r.name)}</b> ${badge(tone(r.sensitivity), r.sensitivity)}<br>${muted(esc(r.type) + " · " + esc(r.description) + (r.max_grant_hours ? ` · max ${dur(r.max_grant_hours)}` : ""))}`,
      ...d.res.agents.map((a) => cell(r, a)),
    ])) : empty("No company resources are assigned to you.");

    const activity = d.act.length ? table(null, d.act.map((e) => [
      ago(e.ts), badge(tone(e.action), e.action), e.principal === me.principal ? muted("me") : esc(e.principal),
      `${esc(e.channel)}/${esc(e.direction)}${e.tool ? " " + muted(esc(e.tool)) : ""}`, muted(esc(e.reason))])) : empty("No activity yet.");

    return shell({
      title: "My AI Workspace",
      meta: `${esc(me.principal)} · ${esc(me.team)} / ${esc(me.role)}${me.pii_override_allowed ? " · may override PII masking (audited)" : ""}`,
      tools: d.people.length > 1 ? `<span class="f6">viewing as</span> ${choice("as", d.people.map((p) => p.principal), state.as)}` : "",
      notice: note(esc(me.monitoring_notice), "info") + (state.error ? note(esc(state.error), "bad") : ""),
      body: grid([
        [12, card("Today", usage)],
        [12, card("Company resources my agents can use", muted("Agents never receive credentials: the gateway makes each call. Grants expire on their own and stop at once if your access or the resource is suspended.") + `<div class="mt-2">${resources}</div>`,
          `${muted("new grants last")} ${choice("hours", HOURS.map(String), String(state.hours), (h) => dur(+h))}`)],
        [12, card("Recent activity: me and my agents", activity)],
      ]),
    });
  }

  function hoursFor(r) {
    return r.max_grant_hours ? Math.min(state.hours, r.max_grant_hours) : state.hours;
  }

  // ---- rendering and actions ----------------------------------------------------------

  let last = null;
  async function render(force) {
    const a = document.activeElement;
    if (!force && a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName)) return;
    try {
      const data = await employeeData();
      last = data;
      document.getElementById("acl-root").innerHTML = employeePage(data);
    } catch (e) {
      document.getElementById("acl-root").innerHTML = shell({ title: "My AI Workspace", meta: "", body: note("Cannot load: " + esc(e.message), "bad") });
    }
  }

  async function act(el) {
    const d = el.dataset;
    state.error = "";
    try {
      switch (d.act) {
        case "as": state.as = d.v; break;
        case "hours": state.hours = Number(d.v); break;
        case "scope": {
          const r = last.res.resources.find((x) => x.id === d.rid), g = r.grants[d.agent];
          const path = `/me/api/grants/${encodeURIComponent(d.agent)}/${encodeURIComponent(d.rid)}`;
          if (!g) { await send("/me/api/grants", "POST", { agent: d.agent, resource: d.rid, scopes: [d.v], hours: hoursFor(r) }); break; }
          const scopes = new Set(g.scopes);
          if (scopes.has(d.v)) scopes.delete(d.v); else scopes.add(d.v);
          if (scopes.size) await send(path, "PATCH", { scopes: [...scopes] });
          else await send(path, "DELETE");
          break;
        }
        case "renew": {
          const r = last.res.resources.find((x) => x.id === d.rid);
          await send("/me/api/grants", "POST", { agent: d.agent, resource: d.rid, scopes: r.grants[d.agent].scopes, hours: hoursFor(r) });
          break;
        }
        case "revoke": await send(`/me/api/grants/${encodeURIComponent(d.agent)}/${encodeURIComponent(d.rid)}`, "DELETE"); break;
        default: return;
      }
    } catch (e) { state.error = e.message; }
    render(true);
  }

  document.addEventListener("click", (ev) => {
    const el = ev.target.closest && ev.target.closest("[data-act]");
    if (el && el.tagName === "BUTTON") { ev.preventDefault(); act(el); }
  });
  document.addEventListener("change", (ev) => {
    const el = ev.target;
    if (el.tagName === "SELECT" && el.dataset.act) act({ dataset: { act: el.dataset.act, v: el.value } });
  });

  render(true);
  setInterval(() => render(false), 4000);
})();
