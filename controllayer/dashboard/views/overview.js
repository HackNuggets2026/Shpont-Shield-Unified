// Overview: spend, budgets and risk across the organisation. Summaries and a top few only; every number
// and mark links to the full, filtered list.
(function () {
  "use strict";
  const ACL = window.ACL;
  const { h, charts: c } = ACL;
  const people = (patch) => ACL.href({ view: "people", ...patch });
  const audit = (patch) => ACL.href({ view: "audit", ...patch });
  const more = (label, href) => `<div class="f6 mt-2">${h.link(label + " →", href)}</div>`;

  function spendChart(o) {
    const s = o.series, n = s.models.length;
    const day = (i) => new Date((s.start + i * 86400) * 1000).toISOString().slice(0, 10);
    return c.columns({
      title: "Spend over time",
      width: 780,
      labels: c.bucketLabels(s, n),
      series: [
        { label: "Models", color: c.KIND.model, values: s.models },
        { label: "Company services", color: c.KIND.service, values: s.services },
      ],
      ref: s.unit === "day" && o.daily_budget ? { value: o.daily_budget, label: "daily caps " + h.usd(o.daily_budget) } : null,
      href: s.unit === "day" ? (i) => people({ period: 1, end: day(i) }) : null,
    });
  }

  function risk(o) {
    const r = o.risk;
    const head = r.attention
      ? `<div class="f4 text-bold"><span class="acl-t-bad">${r.attention}</span> ${r.attention === 1 ? "person needs" : "people need"} attention</div>`
      : `<div class="f4 text-bold acl-t-ok">Nobody needs attention</div>`;
    const sub = `<div class="f6 color-fg-muted mb-2">${r.people.restricted} restricted · ${r.people.watch} on watch · ${r.overrides} manual override${r.overrides === 1 ? "" : "s"} · ${r.signals} external signal${r.signals === 1 ? "" : "s"}</div>`;
    const rows = r.top.map((p) => `<li class="acl-risk-row">
        <span class="acl-dot" style="background:${c.STATUS[p.level]}"></span>
        <div class="acl-risk-who">${h.person(p.id, p.name)} ${h.muted(h.esc(p.team))} ${h.badge(h.tone(p.level), p.level)} ${h.muted("score " + p.score)}
          <div class="f6 color-fg-muted acl-ellipsis" title="${h.esc(p.reason)}">${h.esc(p.reason)}</div></div>
        <a class="btn btn-sm" href="${h.esc(ACL.href({ person: p.id }))}" data-nav>View profile</a></li>`).join("");
    const actions = `<div class="acl-tools mt-2">
        <a class="btn btn-sm btn-primary" href="${h.esc(people({ risk: "elevated", sort: "-score" }))}" data-nav>Review watch list (${r.attention})</a>
        <a class="btn btn-sm" href="${h.esc(ACL.href({ view: "risk" }))}" data-nav>Open risk triage</a></div>`;
    return head + sub + (rows ? `<ul class="list-style-none">${rows}</ul>` : h.empty("No risk findings in this period.")) + actions;
  }

  function costByService(o) {
    const top = o.items.slice(0, 8), rest = o.items.slice(8);
    const rows = top.map((i) => ({
      label: i.name, value: i.usd, color: c.KIND[i.kind], icon: ACL.logo(i.key),
      sub: i.kind === "service" ? h.num(i.requests) + " calls" : h.num(i.tokens) + " tok",
      href: people({ item: i.key, sort: "-item" }),
    }));
    if (rest.length) rows.push({ label: `${rest.length} more`, value: rest.reduce((a, i) => a + i.usd, 0), color: c.OTHER, href: ACL.href({ view: "resources" }) });
    return rows.length ? c.hbars(rows) + c.legend([{ label: "Model", color: c.KIND.model }, { label: "Company service", color: c.KIND.service }]) : h.empty("No spend in this period.");
  }

  function perPerson(o) {
    const hist = o.spend.histogram;
    if (!hist.length) return h.empty("Nobody spent anything in this period.");
    const at = (v) => {
      const i = hist.findIndex((b) => v < b.hi), k = i < 0 ? hist.length - 1 : i, b = hist[k];
      return k + Math.min(1, (v - b.lo) / (b.hi - b.lo));
    };
    const bins = hist.map((b) => ({
      count: b.count, edge: h.usd(b.lo), end: h.usd(b.hi), tip: `${h.usd(b.lo)} - ${h.usd(b.hi)}: ${b.count} people`,
      href: people({ min_usd: b.lo, max_usd: b.hi, sort: "-usd" }),
    }));
    const marks = [["p50", o.spend.p50], ["p90", o.spend.p90]].map(([k, v]) => ({ at: at(v), label: `${k} ${h.usd(v)}` }));
    const top = o.spend.top.slice(0, 5).map((t) => ({ label: t.name, value: t.usd, sub: t.team, href: ACL.href({ person: t.id }) }));
    return `<div class="f6 color-fg-muted">People by spend, agents included (p99 ${h.usd(o.spend.p99)})</div>${c.histogram(bins, marks, { title: "Spend per person", width: 400, height: 130 })}
      <div class="f6 text-bold mt-2 mb-1">Top spenders</div>${c.hbars(top)}${more("All people by spend", people({ sort: "-usd" }))}`;
  }

  function budgets(o) {
    const b = o.budget_bands;
    const split = c.split([
      { label: "under 50%", value: b.under, color: c.STATUS.under, href: people({ budget: "under" }) },
      { label: "50-80%", value: b.half, color: c.STATUS.half, href: people({ budget: "half" }) },
      { label: "80-100%", value: b.near, color: c.STATUS.near, href: people({ budget: "near", sort: "-used" }) },
      { label: "over", value: b.over, color: c.STATUS.over, href: people({ budget: "over", sort: "-used" }) },
      { label: "no cap", value: b.nocap, color: c.STATUS.nocap, href: people({ budget: "nocap" }) },
    ]);
    const teams = o.teams.slice(0, 5).map((t) => [
      h.link(t.team, people({ team: t.team })), h.usd(t.usd),
      `${h.meter(t.budget ? t.usd / t.budget : null)} ${h.pct(t.budget ? t.usd / t.budget : null)}`]);
    return `<div class="f6 color-fg-muted mb-2">People by spend against their allowance (own and agents' daily caps)</div>${split}
      <div class="f6 text-bold mt-3 mb-1">Top teams by spend</div>${h.table([{ label: "Team" }, { label: "Spend", num: true }, { label: "Used" }], teams)}
      ${o.teams.length > 5 ? more(`All ${o.teams.length} teams in People`, people({ sort: "team" })) : ""}`;
  }

  ACL.register({
    id: "overview",
    title: "Overview",
    tab: true,
    order: 10,
    load: (ctx) => ACL.get(`/admin/analytics/overview?period=${ctx.period}`),
    render(o) {
      const t = o.totals, delta = t.prev_usd ? (t.usd - t.prev_usd) / t.prev_usd : null;
      const tiles = h.tiles([
        { label: `Spend, ${h.periodLabel(o.period)}`, value: h.usd(t.usd), meter: t.budget ? t.usd / t.budget : null,
          sub: `${h.pct(t.budget ? t.usd / t.budget : null)} of ${h.usd(t.budget)}` + (delta != null ? ` · ${delta >= 0 ? "+" : ""}${h.pct(delta)} vs prev.` : ""),
          href: people({ sort: "-usd" }) },
        { label: "Models", value: h.usd(t.usd_models), sub: h.pct(t.usd ? t.usd_models / t.usd : null) + " of spend" },
        { label: "Company services", value: h.usd(t.usd_services), sub: h.pct(t.usd ? t.usd_services / t.usd : null) + " of spend", href: ACL.href({ view: "resources" }) },
        { label: "Active people", value: t.active.toLocaleString("en-US"), sub: `of ${t.people.toLocaleString("en-US")} · ${t.agents.toLocaleString("en-US")} agents`, href: people({}) },
        { label: "Over budget", value: String(o.budget_bands.over), tone: o.budget_bands.over ? "bad" : "", sub: `${o.budget_bands.near} at 80-100%`, href: people({ budget: "over", sort: "-used" }) },
        { label: "Blocked", value: h.num(t.block), tone: t.block ? "bad" : "", sub: `of ${h.num(t.decisions)} decisions`, href: audit({ action: "block" }) },
        { label: "Redacted", value: h.num(t.redact), sub: `${h.num(t.warn)} warned`, href: audit({ action: "redact" }) },
        { label: "On watch", value: String(t.watched), tone: t.watched ? "warn" : "", sub: "watch or restricted", href: people({ risk: "elevated", sort: "-score" }) },
      ]);
      const unit = o.series.unit === "hour" ? "per hour, last 24 h (UTC)" : "per day (UTC); click a day for who spent it";
      return tiles + `<div class="acl-cols">
        <div class="acl-c8">${h.card("Spend over time", `<div class="f6 color-fg-muted mb-1">${unit}</div>` + spendChart(o))}</div>
        <div class="acl-c4">${h.card("Insider risk", risk(o))}</div>
        <div class="acl-c4">${h.card("Cost by service", costByService(o))}</div>
        <div class="acl-c4">${h.card("Spend per person", perPerson(o))}</div>
        <div class="acl-c4">${h.card("Budget used", budgets(o))}</div>
      </div>`;
    },
  });
})();
