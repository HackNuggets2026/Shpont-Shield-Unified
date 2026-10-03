// People: everyone (or every agent) with spend, budget used, top service and risk; filtered, sorted and
// paged by the server. Every filter, the sort and the page live in the URL, so overview links land filtered.
(function () {
  "use strict";
  const ACL = window.ACL;
  const { h, charts: c } = ACL;
  const PARAMS = ["q", "team", "risk", "budget", "item", "kind", "min_usd", "max_usd", "sort", "page", "per_page", "end"];
  const FILTERS = ["q", "team", "risk", "budget", "item", "min_usd", "max_usd", "end"];
  const RISKS = [
    { label: "Any", value: "" },
    { label: "Flagged", value: "flagged", color: c.STATUS.watch, title: "Any risk score, a manual override or an external signal" },
    { label: "Elevated", value: "elevated", color: c.STATUS.watch, title: "Watch or restricted" },
    { label: "Watch", value: "watch", color: c.STATUS.watch },
    { label: "Restricted", value: "restricted", color: c.STATUS.restricted },
  ];
  const BANDS = [
    { label: "Any", value: "" },
    { label: "<50%", value: "under", color: c.STATUS.under, title: "Under half of the allowance" },
    { label: "50-80%", value: "half", color: c.STATUS.half },
    { label: "80-100%", value: "near", color: c.STATUS.near },
    { label: "Over", value: "over", color: c.STATUS.over, title: "Spent more than the allowance" },
    { label: "No cap", value: "nocap", color: c.STATUS.nocap, title: "They or one of their agents has no daily cap" },
  ];
  const KINDS = [{ label: "People", value: "" }, { label: "Agents", value: "agent" }];
  const PER_PAGE = [25, 50, 100].map((n) => ({ label: n + " / page", value: n }));

  const here = (patch) => ACL.href({ ...patch, page: null }, false);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const day = (s) => MONTHS[Number(s.slice(5, 7)) - 1] + " " + Number(s.slice(8, 10)); // "2026-10-03" -> "Oct 3"

  // Links that set one URL param; the current one is highlighted, a colour swatch marks the level or band.
  // counts[value], when known, is how many rows picking it would give; an empty option is greyed and inert.
  const chips = (key, options, current, counts = {}) => `<div class="BtnGroup" role="group">${options.map((o) => {
    const on = String(o.value) === String(current || ""), n = counts[o.value];
    const sw = o.color ? `<i class="acl-swatch" style="background:${o.color}"></i>` : "";
    const inner = sw + h.esc(o.label) + (n != null ? ` <span class="${on ? "" : "color-fg-muted"}">${h.num(n)}</span>` : "");
    const title = o.title ? ` title="${h.esc(o.title)}"` : "";
    if (n === 0 && !on) return `<span class="btn btn-sm BtnGroup-item" aria-disabled="true"${title}>${inner}</span>`;
    return `<a class="btn btn-sm BtnGroup-item${on ? " btn-primary" : ""}" aria-current="${on}" href="${h.esc(here({ [key]: o.value }))}" data-nav${title}>${inner}</a>`;
  }).join("")}</div>`;

  function itemSelect(items, current) {
    const opt = (i) => `<option value="${h.esc(i.key)}"${i.key === current ? " selected" : ""}>${h.esc(i.name)}${i.usd != null ? " · " + h.usd(i.usd) : ""}</option>`;
    const known = items.some((i) => i.key === current);
    const extra = current && !known ? opt({ key: current, name: current.replace(/^[a-z]+:/, "") }) : "";
    const group = (kind, label) => {
      const list = items.filter((i) => i.kind === kind);
      return list.length ? `<optgroup label="${label}">${list.map(opt).join("")}</optgroup>` : "";
    };
    return `<select class="form-select select-sm" data-act="item" aria-label="Service or model">
      <option value="">All services and models</option>${extra}${group("model", "Models")}${group("service", "Company services")}</select>`;
  }

  const sum = (o) => Object.values(o).reduce((a, n) => a + n, 0);
  const riskCounts = (l) => ({ "": sum(l), elevated: l.watch + l.restricted, watch: l.watch, restricted: l.restricted });
  const bandCounts = (b) => ({ "": sum(b), ...b });

  // The matched set at a glance: every number covers all pages, and links narrow the list to it.
  function summary(r, p) {
    const s = r.summary, l = s.levels, elevated = l.watch + l.restricted;
    const who = p.kind === "agent" ? "agent" : "person";
    return h.tiles([
      { label: "Spend", value: h.usd(s.usd), meter: s.budget ? s.usd / s.budget : null,
        sub: s.budget ? `${h.pct(s.usd / s.budget)} of ${h.usd(s.budget)} allowance` : "no capped allowance" },
      { label: `Per ${who}`, value: h.usd(r.total ? s.usd / r.total : 0), sub: `over ${r.total.toLocaleString("en-US")} matched` },
      { label: "Over budget", value: h.num(s.bands.over), tone: s.bands.over ? "bad" : "", sub: `${h.num(s.bands.near)} at 80-100%`,
        href: here({ budget: "over", sort: "-used" }) },
      { label: "Watch or restricted", value: h.num(elevated), tone: l.restricted ? "bad" : elevated ? "warn" : "",
        sub: `${h.num(l.restricted)} restricted`, href: here({ risk: "elevated", sort: "-score" }) },
      { label: "Blocked requests", value: h.num(s.blocks), tone: s.blocks ? "bad" : "", sub: "in this period", href: here({ sort: "-blocks" }) },
    ]);
  }

  function filters(r, p) {
    const teams = [{ label: "All teams", value: "" }, ...r.teams.map((t) => ({ label: t, value: t }))];
    const q = `<input class="form-control input-sm" type="search" data-input="q" value="${h.esc(p.q || "")}" placeholder="Name, id or team" aria-label="Search" style="width:180px">`;
    const label = (t) => `<span class="color-fg-muted f6 ml-2">${t}</span>`;
    return `<div class="acl-tools mb-2">${q}${h.navSelect("team", teams, p.team || "", "Team")}${itemSelect(r.items, p.item || "")}
      ${label("Risk")}${chips("risk", RISKS, p.risk, riskCounts(r.summary.levels))}${label("Budget used")}${chips("budget", BANDS, p.budget, bandCounts(r.summary.bands))}
      <span class="flex-auto"></span>${chips("kind", KINDS, p.kind === "agent" ? "agent" : "")}</div>`;
  }

  // Filters with no control of their own (from overview drill-downs), each removable; plus clear all.
  function active(p) {
    const tags = [];
    const rm = (patch, text) => tags.push(`<a class="Label Label--accent" href="${h.esc(here(patch))}" data-nav title="Remove this filter">${h.esc(text)} ×</a>`);
    if (p.min_usd || p.max_usd) {
      const lo = p.min_usd ? h.usd(p.min_usd) : "$0", hi = p.max_usd ? h.usd(p.max_usd) : "any";
      rm({ min_usd: null, max_usd: null }, `spend ${lo} - ${hi}`);
    }
    if (p.end) rm({ end: null }, `period ending ${day(p.end)}`);
    const any = FILTERS.some((k) => p[k]);
    if (!any) return "";
    const clear = `<a class="Link--secondary f6" href="${h.esc(ACL.href({ view: "people", kind: p.kind || null, per_page: p.per_page || null }))}" data-nav>Clear all filters</a>`;
    return `<div class="acl-tools mb-2">${tags.join("")}${clear}</div>`;
  }

  function riskCell(x) {
    const parts = [h.badge(h.tone(x.level), x.level)];
    if (x.score > 0) parts.push(`<span class="acl-num">${x.score.toFixed(0)}</span>`);
    if (x.manual) parts.push(h.badge("info", "manual", `Set to ${x.manual.level}${x.manual.reason ? ": " + x.manual.reason : ""}`));
    if (x.signals) parts.push(h.badge("neutral", `${x.signals} signal${x.signals === 1 ? "" : "s"}`));
    return `<span class="no-wrap">${parts.join(" ")}</span>`;
  }

  function usedCell(x) {
    if (x.used == null) return h.muted("no cap");
    const cls = x.used >= 1 ? "color-fg-danger text-bold" : x.used >= 0.8 ? "color-fg-attention" : "";
    return `<span class="no-wrap">${h.meter(x.used, `${h.usd(x.usd)} of ${h.usd(x.budget)}`)} <span class="acl-num ${cls}">${h.pct(x.used)}</span></span>`;
  }

  function topCell(x) {
    const t = x.top_item;
    if (!t) return h.muted("-");
    const share = x.usd ? t.usd / x.usd : null;
    return `<a class="Link--secondary no-wrap" href="${h.esc(here({ item: t.key, sort: "-item" }))}" data-nav title="${h.esc(`${t.name}: ${h.usd(t.usd)}; show everyone using it`)}">${ACL.logo(t.key)} ${h.esc(t.name)}</a> ${h.muted(h.pct(share))}`;
  }

  function personCell(x, agents) {
    const sub = agents
      ? (x.owner ? `of ${h.person(x.owner, x.owner)}` : "")
      : [x.role, x.agents ? `${x.agents} agent${x.agents === 1 ? "" : "s"}` : ""].filter(Boolean).map(h.esc).join(" · ");
    return `${h.person(x.id, x.name)}${sub ? " " + h.muted(sub) : ""}`;
  }

  function table(r, p) {
    const agents = p.kind === "agent";
    const item = p.item ? r.items.find((i) => i.key === p.item) || { name: p.item.replace(/^[a-z]+:/, "") } : null;
    const cols = [
      { label: agents ? "Agent" : "Person", sort: "name" },
      { label: "Team", sort: "team" },
      { label: "Spend", num: true, sort: "usd" },
      ...(item ? [{ label: item.name, num: true, sort: "item" }] : []),
      { label: "Budget used", sort: "used" },
      { label: "Top service or model" },
      { label: "Risk", sort: "risk" },
      { label: "Blocked", num: true, sort: "blocks" },
      { label: "Last active", sort: "last" },
    ];
    const rows = r.rows.map((x) => [
      personCell(x, agents),
      `<a class="Link--secondary" href="${h.esc(here({ team: x.team }))}" data-nav>${h.esc(x.team)}</a>`,
      `<span title="${h.esc(h.num(x.requests) + " requests")}">${h.usd(x.usd)}</span>`,
      ...(item ? [h.usd(x.item_usd)] : []),
      usedCell(x),
      topCell(x),
      riskCell(x),
      x.blocks ? `<span class="color-fg-danger" title="${h.esc(h.num(x.redacts) + " redacted")}">${h.num(x.blocks)}</span>` : h.muted("0"),
      h.ago(x.last_seen),
    ]);
    // Rows without spend in the period are greyed; a click anywhere on a row opens the person.
    const attrs = (i) => `data-href="${h.esc(ACL.href({ person: r.rows[i].id }))}" style="cursor:pointer"${r.rows[i].usd ? "" : ' class="acl-greyed" title="No spend in this period"'}`;
    return rows.length ? h.table(cols, rows, { sort: r.sort, rowAttrs: attrs }) : h.empty(`No ${agents ? "agents" : "people"} match these filters.`);
  }

  ACL.register({
    id: "people",
    title: "People",
    tab: true,
    order: 20,
    inspection: true,
    async load(ctx) {
      const p = ctx.params;
      const qs = PARAMS.filter((k) => p[k]).map((k) => `${k}=${encodeURIComponent(p[k])}`);
      qs.push("period=" + ctx.period);
      try {
        return { r: await ACL.get("/admin/analytics/people?" + qs.join("&")) };
      } catch (e) {
        return { error: e.message };
      }
    },
    render(d, ctx) {
      const p = ctx.params;
      if (d.error) {
        return h.note(`Cannot list people: ${h.esc(d.error)}. ${h.link("Clear all filters", ACL.href({ view: "people" }))}`, "bad");
      }
      const r = d.r, noun = p.kind === "agent" ? "agent" : "person";
      const count = `${r.total.toLocaleString("en-US")} ${r.total === 1 ? noun : noun === "agent" ? "agents" : "people"}`;
      const span = r.first === r.last ? day(r.last) : `${day(r.first)} - ${day(r.last)}`;
      const tools = `${h.muted(`spend ${h.esc(span)} (UTC)${p.kind === "agent" ? "" : ", agents included"}`)}${h.navSelect("per_page", PER_PAGE, r.per_page, "Rows per page")}`;
      return summary(r, p) + h.card(count, filters(r, p) + active(p) + table(r, p) + (r.total ? h.pager(r) : ""), tools);
    },
    actions: {
      // Picking a service sorts by spend on it; clearing it drops that sort.
      item(el, ctx) {
        const sort = ctx.params.sort || "";
        const next = el.value ? "-item" : sort.replace(/^-/, "") === "item" ? null : sort || null;
        history.pushState(null, "", here({ item: el.value || null, sort: next }));
      },
    },
    inputs: {
      q: (value) => ACL.go({ q: value.trim() || null, page: null }, { replace: true }),
    },
  });
})();
