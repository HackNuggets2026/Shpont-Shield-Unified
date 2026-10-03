// Overview: spend, budgets and risk across the organisation; every number links to the filtered list.
(function () {
  "use strict";
  const { h, charts: c } = window.ACL;
  const people = (patch) => window.ACL.href({ view: "people", ...patch });
  const audit = (patch) => window.ACL.href({ view: "audit", ...patch });

  function spendChart(o) {
    const s = o.series, n = s.models.length;
    const labels = c.bucketLabels(s, n);
    const day = (i) => new Date((s.start + i * 86400) * 1000).toISOString().slice(0, 10);
    return c.columns({
      title: "Spend over time",
      labels,
      series: [
        { label: "Models", color: c.KIND.model, values: s.models },
        { label: "Company services", color: c.KIND.service, values: s.services },
      ],
      ref: s.unit === "day" && o.daily_budget ? { value: o.daily_budget, label: "daily caps " + h.usd(o.daily_budget) } : null,
      href: s.unit === "day" ? (i) => people({ period: 1, end: day(i) }) : null,
    });
  }

  function costByService(o) {
    const top = o.items.slice(0, 12), rest = o.items.slice(12);
    const rows = top.map((i) => ({
      label: i.name, value: i.usd, color: c.KIND[i.kind], sub: i.kind === "service" ? h.num(i.requests) + " calls" : h.num(i.tokens) + " tok",
      href: people({ item: i.key, sort: "-item" }),
    }));
    if (rest.length) rows.push({ label: `Other (${rest.length})`, value: rest.reduce((a, i) => a + i.usd, 0), color: c.OTHER });
    return rows.length ? c.hbars(rows) + c.legend([{ label: "Model", color: c.KIND.model }, { label: "Company service", color: c.KIND.service }]) : h.empty("No spend in this period.");
  }

  function perPerson(o) {
    const hist = o.spend.histogram;
    if (!hist.length) return h.empty("Nobody spent anything in this period.");
    const at = (v) => {
      const i = hist.findIndex((b) => v < b.hi);
      const k = i < 0 ? hist.length - 1 : i, b = hist[k];
      return k + Math.min(1, (v - b.lo) / (b.hi - b.lo));
    };
    const bins = hist.map((b) => ({
      count: b.count, edge: h.usd(b.lo), end: h.usd(b.hi),
      tip: `${h.usd(b.lo)} - ${h.usd(b.hi)}: ${b.count} people`,
      href: people({ min_usd: b.lo, max_usd: b.hi, sort: "-usd" }),
    }));
    const marks = [["p50", o.spend.p50], ["p90", o.spend.p90], ["p99", o.spend.p99]].map(([k, v]) => ({ at: at(v), label: `${k} ${h.usd(v)}` }));
    const top = o.spend.top.map((t) => ({ label: t.name, value: t.usd, sub: t.team, href: window.ACL.href({ person: t.id }) }));
    return `<div class="f6 color-fg-muted mb-1">People by spend (agents included), log-scaled buckets</div>${c.histogram(bins, marks, { title: "Spend per person" })}
      <div class="f6 color-fg-muted mt-3 mb-1">Top spenders</div>${c.hbars(top)}`;
  }

  function budgets(o) {
    const b = o.budget_bands;
    return c.split([
      { label: "under 50%", value: b.under, color: c.STATUS.under, href: people({ budget: "under" }) },
      { label: "50-80%", value: b.half, color: c.STATUS.half, href: people({ budget: "half" }) },
      { label: "80-100%", value: b.near, color: c.STATUS.near, href: people({ budget: "near", sort: "-used" }) },
      { label: "over", value: b.over, color: c.STATUS.over, href: people({ budget: "over", sort: "-used" }) },
      { label: "no cap", value: b.nocap, color: c.STATUS.nocap, href: people({ budget: "nocap" }) },
    ]);
  }

  function risk(o) {
    const r = o.risk;
    const split = c.split(["normal", "watch", "restricted"].map((l) => ({ label: l, value: r.people[l], color: c.STATUS[l], href: people({ risk: l === "normal" ? "" : l, sort: "-score" }) })));
    const agents = r.agents.watch + r.agents.restricted;
    const facts = `<div class="f6 color-fg-muted mt-2">${h.link(`${r.overrides} manual override${r.overrides === 1 ? "" : "s"}`, people({ risk: "flagged", sort: "-score" }), "Link--secondary")} · ${r.signals} with external signals · ${agents} agent${agents === 1 ? "" : "s"} on watch or restricted</div>`;
    const alerts = o.alerts.length ? `<ul class="acl-alerts list-style-none mt-2">${o.alerts.map((a) =>
      `<li class="py-1 border-top f6">${h.badge(h.tone(a.level), a.level)} ${h.person(a.principal, a.name)} ${h.muted(h.ago(a.ts))}<div class="color-fg-muted">${h.esc(a.reason)}</div></li>`).join("")}</ul>` : h.empty("No alerts.");
    return split + facts + `<div class="f6 text-bold mt-3">Recent alerts</div>` + alerts;
  }

  function teams(o) {
    const rows = o.teams.map((t) => [
      h.link(t.team, people({ team: t.team })),
      `${t.people.toLocaleString("en-US")}`,
      `${t.active.toLocaleString("en-US")}`,
      h.usd(t.usd),
      h.usd(t.usd / Math.max(1, t.people)),
      h.usd(t.budget),
      `${h.meter(t.budget ? t.usd / t.budget : null)} ${h.pct(t.budget ? t.usd / t.budget : null)}`,
      t.blocks ? h.link(String(Math.round(t.blocks)), people({ team: t.team, sort: "-blocks" }), "Link--secondary") : h.muted("0"),
    ]);
    return h.table([{ label: "Team" }, { label: "People", num: true }, { label: "Active", num: true }, { label: "Spend", num: true },
      { label: "Per person", num: true }, { label: "Allowance", num: true }, { label: "Used" }, { label: "Blocked", num: true }], rows);
  }

  window.ACL.register({
    id: "overview",
    title: "Overview",
    tab: true,
    order: 10,
    load: (ctx) => window.ACL.get(`/admin/analytics/overview?period=${ctx.period}`),
    render(o) {
      const t = o.totals, delta = t.prev_usd ? (t.usd - t.prev_usd) / t.prev_usd : null;
      const tiles = h.tiles([
        { label: `Spend, ${h.periodLabel(o.period)}`, value: h.usd(t.usd), meter: t.budget ? t.usd / t.budget : null,
          sub: `${h.pct(t.budget ? t.usd / t.budget : null)} of ${h.usd(t.budget)} allowance` + (delta != null ? ` · ${delta >= 0 ? "+" : ""}${h.pct(delta)} vs previous` : ""),
          href: people({ sort: "-usd" }) },
        { label: "Models", value: h.usd(t.usd_models), sub: h.pct(t.usd ? t.usd_models / t.usd : null) + " of spend" },
        { label: "Company services", value: h.usd(t.usd_services), sub: h.pct(t.usd ? t.usd_services / t.usd : null) + " of spend" },
        { label: "Active people", value: t.active.toLocaleString("en-US"), sub: `of ${t.people.toLocaleString("en-US")} · ${t.agents.toLocaleString("en-US")} agents`, href: people({}) },
        { label: "Over budget", value: String(o.budget_bands.over), tone: o.budget_bands.over ? "bad" : "", sub: `${o.budget_bands.near} at 80-100%`, href: people({ budget: "over", sort: "-used" }) },
        { label: "Blocked", value: h.num(t.block), tone: t.block ? "bad" : "", sub: `of ${h.num(t.decisions)} decisions`, href: audit({ action: "block" }) },
        { label: "Redacted", value: h.num(t.redact), sub: `${h.num(t.warn)} warned`, href: audit({ action: "redact" }) },
        { label: "On watch", value: String(t.watched), tone: t.watched ? "warn" : "", sub: "watch or restricted", href: people({ risk: "elevated", sort: "-score" }) },
      ]);
      const unit = o.series.unit === "hour" ? "per hour, last 24 h (UTC)" : "per day (UTC)";
      return tiles + `<div class="acl-cols">
        <div class="acl-c8">${h.card("Spend over time", `<div class="f6 color-fg-muted mb-1">${unit}; click a day for who spent it</div>` + spendChart(o))}</div>
        <div class="acl-c4">${h.card("Budget used", `<div class="f6 color-fg-muted mb-2">People by spend against their allowance (own and agents' daily caps)</div>` + budgets(o))}
          <div class="mt-3">${h.card("Insider risk", risk(o))}</div></div>
        <div class="acl-c6">${h.card("Cost by service", costByService(o))}</div>
        <div class="acl-c6">${h.card("Spend per person", perPerson(o))}</div>
        <div class="acl-c12">${h.card("Teams", teams(o))}</div>
      </div>`;
    },
  });
})();
