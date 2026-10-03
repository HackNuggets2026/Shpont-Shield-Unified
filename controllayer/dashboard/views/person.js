// Person drill-down (?person=<id>, agents too): spend, agents' access, insider risk, recent decisions.
(function () {
  "use strict";
  const ACL = window.ACL, { h, charts: c } = ACL;
  const LEVELS = ["normal", "watch", "restricted"];
  const TOP_ITEMS = 8, TOP_EVENTS = 10, TOP_ALERTS = 4;
  const enc = encodeURIComponent;
  const audit = (pid, patch) => ACL.href({ view: "audit", principal: pid, ...patch });
  const score = (x) => String(Math.round(Number(x || 0) * 10) / 10);
  const short = (name) => String(name).replace(/\s*\(.*\)$/, "");
  const LEVEL_STYLE = {
    normal: "background:var(--status-good);border-color:var(--status-good);color:#fff",
    watch: "background:var(--status-warning);border-color:var(--status-warning);color:#1f2328",
    restricted: "background:var(--status-critical);border-color:var(--status-critical);color:#fff",
  };
  const dot = (level) => `<span style="color:${c.STATUS[level]}">●</span>`;

  // Colour follows the item on every chart of the page: rank in the person's own spend, top five, then Other.
  function colours(d) {
    const m = { other: c.OTHER };
    d.items.forEach((it, i) => { m[it.key] = i < c.PALETTE.length ? c.PALETTE[i] : c.OTHER; });
    return m;
  }

  function header(d) {
    const p = d.person, r = d.risk;
    const who = p.kind === "agent" ? `agent of ${h.person(p.owner, p.owner_name)}` : h.esc(p.role);
    return `<div class="d-flex flex-items-center flex-wrap mb-2" style="gap:4px 10px">
      <h2 class="f3 text-semibold">${h.esc(p.name)}</h2>
      ${h.badge(h.tone(r.level), r.level + (r.manual ? " (override)" : ""))}
      <span class="f6 color-fg-muted">${h.esc(p.id)} · ${h.link(p.team, ACL.href({ view: "people", team: p.team }), "Link--secondary")} · ${who}</span>
      <span class="flex-auto"></span>
      <span class="f6">${h.link("Audit log for " + (p.kind === "agent" ? "this agent" : "this person"), audit(p.id), "Link--secondary")}</span>
    </div>`;
  }

  function tiles(d) {
    const s = d.spend, dec = d.decisions, r = d.risk, used = s.budget ? s.usd / s.budget : null;
    const left = s.budget == null ? "no cap" : s.usd <= s.budget ? `${h.usd(s.budget - s.usd)} left of ${h.usd(s.budget)}` : `${h.usd(s.usd - s.budget)} over ${h.usd(s.budget)}`;
    const agentUsd = d.person.kind === "agent" ? 0 : d.agents.reduce((a, x) => a + x.usd, 0);
    return h.tiles([
      { label: `Spend, ${h.periodLabel(d.period)}`, value: h.usd(s.usd), meter: used, tone: used >= 1 ? "bad" : "",
        sub: `${used == null ? "" : h.pct(used) + " · "}${left}` },
      { label: "Today", value: h.usd(s.today_usd), meter: s.daily_cap ? s.today_usd / s.daily_cap : null,
        sub: s.daily_cap ? `${h.usd(s.today_left)} left of ${h.usd(s.daily_cap)}/day` : "no daily cap" },
      d.person.kind === "agent"
        ? { label: "Requests", value: h.num(s.requests), sub: h.num(s.tokens) + " tokens" }
        : { label: "Agents", value: String(d.agents.length), sub: s.usd ? `${h.pct(agentUsd / s.usd)} of spend · ${h.num(s.requests)} requests` : `${h.num(s.requests)} requests` },
      { label: "Blocked", value: h.num(dec.block), tone: dec.block ? "bad" : "", sub: `${h.num(dec.redact)} redacted · ${h.num(dec.warn)} warned of ${h.num(dec.decisions)}`,
        href: audit(d.person.id, { action: "block" }) },
      { label: "Risk score", value: score(r.score), tone: r.level === "restricted" ? "bad" : r.level === "watch" ? "warn" : "",
        sub: `watch at ${score(r.levels.watch)} · restricted at ${score(r.levels.restricted)}` },
    ]);
  }

  function spendChart(d, col) {
    const s = d.series;
    if (!s.items.length) return h.empty("No spend in this period.");
    const n = s.values[0].length, cap = d.spend.daily_cap;
    const peak = Math.max(...Array.from({ length: n }, (_, i) => s.values.reduce((a, v) => a + (v[i] || 0), 0)));
    // The cap line only when spend comes near it: otherwise it flattens the bars it is drawn over.
    const ref = s.unit === "day" && cap && peak >= cap / 2 ? { value: cap, label: "daily cap " + h.usd(cap) } : null;
    const unit = s.unit === "hour" ? "per hour, last 24 h (UTC)" : "per day (UTC)";
    return `<div class="f6 color-fg-muted mb-1">${unit}${cap ? ` · daily cap ${h.usd(cap)}` : ""}</div>` + c.columns({
      title: "Spend over time",
      width: 780,
      labels: c.bucketLabels(s, n),
      series: s.items.map((it, k) => ({ label: it.name, color: col[it.key] || c.OTHER, values: s.values[k] })),
      ref,
    });
  }

  function byItem(d, col) {
    if (!d.items.length) return h.empty("No spend in this period.");
    const pid = d.person.id, viaAgents = d.person.kind !== "agent" && d.agents.length;
    const sub = (it) => {
      const agents = it.usd - (it.by_principal[pid] || 0);
      return it.kind + (viaAgents && agents > 0 ? ` · ${h.pct(agents / it.usd)} by agents` : "");
    };
    const top = d.items.slice(0, TOP_ITEMS), rest = d.items.slice(TOP_ITEMS);
    const rows = top.map((it) => ({ label: it.name, icon: ACL.logo(it.key), value: it.usd, color: col[it.key], sub: sub(it),
      href: ACL.href({ view: "people", item: it.key, sort: "-item" }) }));
    if (rest.length) rows.push({ label: `Other (${rest.length})`, value: rest.reduce((a, it) => a + it.usd, 0), color: c.OTHER });
    return c.hbars(rows) + `<div class="f6 color-fg-muted mt-1">Bars link to everyone using the item.</div>`;
  }

  // ---- access: one row of pills per agent ------------------------------------------------

  // A resource pill per entitled resource (and any grant left over from a lost entitlement); granted
  // ones carry a pill per scope and their expiry. Every pill is one click.
  function chip(a, id, r, g) {
    const at = `data-agent="${h.esc(a.id)}" data-rid="${h.esc(id)}"`;
    const name = r ? short(r.name) : id;
    // h.pill escapes its label: the logo goes in after.
    const pill = (on, attrs, opts) => h.pill(on, name, `${at} ${attrs}`, opts).replace(/>([^<]*)<\/button>$/, (m, text) => `>${ACL.logo(id)}${text}</button>`);
    const link = (label, attrs, title) => `<button type="button" class="btn-link f6" ${at} ${attrs} title="${h.esc(title)}">${label}</button>`;
    const revoke = link("revoke", `data-act="revoke"`, "Remove the grant");
    const wrap = (inner) => `<span class="d-inline-flex flex-items-center no-wrap mr-2 mb-1" style="gap:3px">${inner}</span>`;
    if (!r || r.suspended) {
      const why = r ? `${r.name} is suspended by security` : `the owner is no longer entitled to ${id}`;
      return wrap(pill(!!g, "", { disabled: true, title: why }) + (g ? revoke : ""));
    }
    const hours = r.max_grant_hours, forHours = hours ? `for ${h.dur(hours)}` : "with no expiry";
    const grantAttrs = (act, scopes) => `data-act="${act}" data-scopes="${h.esc(scopes)}" data-hours="${hours ?? ""}"`;
    if (!g) return wrap(pill(false, grantAttrs("grant", r.scopes[0]), { title: `${r.name} (${r.sensitivity} sensitivity): click to grant ${r.scopes[0]} ${forHours}` }));
    if (!g.active) {
      const scopes = g.scopes.filter((x) => r.scopes.includes(x)).join(",") || r.scopes[0];
      const title = `${r.name}: expired; click to renew ${scopes.replace(/,/g, ", ")} ${forHours}`;
      return wrap(pill(false, grantAttrs("renew", scopes), { title }) + `<span class="f6 color-fg-attention">expired ${h.ago(g.expires_at)}</span>`
        + link("renew", grantAttrs("renew", scopes), title) + revoke);
    }
    const exp = g.expires_at ? `expires ${new Date(g.expires_at * 1000).toISOString().replace("T", " ").slice(0, 16)} UTC` : "no expiry";
    let out = pill(true, `data-act="revoke"`, { title: `${r.name} (${r.sensitivity} sensitivity): ${g.scopes.join(", ")}; ${exp}; click to revoke` });
    if (r.scopes.length > 1) {
      out += r.scopes.map((sc) => {
        const on = g.scopes.includes(sc), next = on ? g.scopes.filter((x) => x !== sc) : [...g.scopes, sc];
        const act = next.length ? `data-act="scopes" data-scopes="${h.esc(next.join(","))}"` : `data-act="revoke"`;
        return h.pill(on, sc, `${at} ${act} style="font-size:10px;padding:0 5px"`, { title: on ? (next.length ? `Remove ${sc}` : `Remove ${sc}: revokes the grant`) : `Add ${sc}` });
      }).join("");
    }
    if (g.expires_at) out += `<span class="f6 color-fg-muted">${h.ago(g.expires_at)}</span>`;
    return wrap(out);
  }

  function access(d) {
    if (!d.agents.length) {
      return h.empty(`No agents. Entitled to ${d.resources.length} resource${d.resources.length === 1 ? "" : "s"}: `
        + d.resources.map((r) => short(r.name)).join(", ") + ".");
    }
    const res = Object.fromEntries(d.resources.map((r) => [r.id, r]));
    const self = d.person.kind === "agent";
    return d.agents.map((a, i) => {
      const ids = [...d.resources.map((r) => r.id), ...Object.keys(a.grants).filter((id) => !res[id])];
      const active = Object.values(a.grants).filter((g) => g.active).length;
      const expired = Object.values(a.grants).filter((g) => !g.active).length;
      const head = `<div class="d-flex flex-items-baseline flex-wrap f6 mb-1" style="gap:2px 8px">
        ${self ? `<span class="text-bold">${h.esc(a.name)}</span>` : h.person(a.id, a.name)}
        ${a.level !== "normal" ? h.badge(h.tone(a.level), a.level) : ""}
        <span class="color-fg-muted">${h.usd(a.usd)}${a.cap ? ` · cap ${h.usd(a.cap)}/day` : " · no cap"} · ${active} of ${d.resources.length} granted${expired ? ` · ${expired} inactive` : ""}</span></div>`;
      return `<div class="${i ? "pt-2 mt-2 border-top" : ""}">${head}<div class="d-flex flex-wrap">${ids.map((id) => chip(a, id, res[id], a.grants[id])).join("")}</div></div>`;
    }).join("");
  }

  // ---- insider risk ----------------------------------------------------------------------

  function levelControl(r) {
    const set = r.manual?.level, at = (l) => `data-act="level" data-v="${l}"`;
    const resolves = `${r.auto} · score ${score(r.score)}${r.auto !== r.computed ? " + signal" : ""}`;
    const auto = `<div class="BtnGroup acl-auto"><button type="button" class="btn btn-sm BtnGroup-item${set ? "" : " btn-primary"}" aria-pressed="${!set}" ${at("auto")} title="Follow the score and external signals">AUTO<span class="acl-sub">${dot(r.auto)} ${h.esc(resolves)}</span></button></div>`;
    const over = `<div><div class="acl-cap">override</div><div class="BtnGroup" role="group">${LEVELS.map((l) =>
      `<button type="button" class="btn btn-sm BtnGroup-item" aria-pressed="${set === l}" ${at(l)}${set === l ? ` style="${LEVEL_STYLE[l]}"` : ""}>${h.esc(l)}</button>`).join("")}</div></div>`;
    return `<div class="acl-level">${auto}${over}</div>`;
  }

  function risk(d) {
    const r = d.risk, lv = r.levels;
    const manual = r.manual ? `<div class="f6 mt-1">${dot(r.manual.level)} Override <b>${h.esc(r.manual.level)}</b> set ${h.ago(r.manual.at)}${r.manual.reason ? ": " + h.esc(r.manual.reason) : ""}${r.level !== r.manual.level ? h.muted(` (effective ${h.esc(r.level)} via the owner)`) : ""}</div>` : "";
    const peak = Math.max(...r.history, 0);
    let chart = h.empty("No risk score in this period.");
    if (peak > 0) {
      const day0 = Date.parse(d.first + "T00:00:00Z");
      const labels = r.history.map((_, i) => new Date(day0 + i * 86400000).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }));
      const thresholds = [{ value: lv.watch, label: "watch" }];
      if (peak >= lv.restricted / 2) thresholds.push({ value: lv.restricted, label: "restricted" });
      chart = `<div class="f6 color-fg-muted mt-2">Daily peak score</div>` + c.line({ title: "Risk score", width: 480, labels, values: r.history, color: c.STATUS[r.level], thresholds, height: 90, fmt: score });
    }
    const signals = r.signals.length ? `<div class="f6 text-bold mt-2">External signals${r.manual ? h.muted(" - the override wins") : ""}</div>` + r.signals.map((g) =>
      `<div class="d-flex flex-items-center f6 py-1 border-top" style="gap:6px">${h.badge(h.tone(g.level), g.level)}<b>${h.esc(g.source)}</b>
        <span class="flex-auto color-fg-muted">${h.esc(g.reason || "")} · ends ${h.ago(g.expires_at)}</span>
        ${h.button("Dismiss", `data-act="dismiss" data-src="${h.esc(g.source)}" title="Drop this signal"`)}</div>`).join("") : "";
    const alerts = d.alerts.length ? `<div class="f6 text-bold mt-2">Alerts</div>` + d.alerts.slice(0, TOP_ALERTS).map((a) =>
      `<div class="f6 py-1 border-top">${h.badge(h.tone(a.level), a.level)} ${a.principal !== d.person.id ? h.esc(short(a.name || a.principal)) + " · " : ""}${h.esc(a.reason)} ${h.muted(h.ago(a.ts))}</div>`).join("") : "";
    const reset = h.button("Reset score", `data-act="reset" title="Clear the score, e.g. after a review found nothing"${r.score > 0 ? "" : " disabled"}`);
    return `<div class="d-flex flex-items-end flex-wrap" style="gap:8px">${levelControl(r)}<span class="flex-auto"></span>${reset}</div>${manual}${chart}${signals}${alerts}`;
  }

  // ---- recent decisions ------------------------------------------------------------------

  function decisions(d) {
    const ev = d.events.slice(0, TOP_EVENTS);
    if (!ev.length) return h.empty("No recent decisions.");
    const pid = d.person.id;
    const rows = ev.map((e) => [
      h.ago(e.ts),
      e.principal === pid ? h.muted("self") : h.person(e.principal, e.principal.startsWith(pid + "-") ? e.principal.slice(pid.length + 1) : e.principal),
      h.esc(`${e.channel} · ${e.direction}`),
      e.model || e.tool ? ACL.logo(e.model ? "model:" + e.model : e.tool) + h.esc(e.model || e.tool) : h.muted("-"),
      h.badge(h.tone(e.action), e.action),
      (e.findings || []).length ? h.esc(e.findings.map((f) => (typeof f === "string" ? f : `${f.control}/${f.category}`)).join(", ")) : h.muted("-"),
    ]);
    return h.table([{ label: "When" }, { label: "Who" }, { label: "Channel" }, { label: "Model / tool" }, { label: "Decision" }, { label: "Findings" }], rows);
  }

  // ---- actions ---------------------------------------------------------------------------

  const scopesOf = (el) => String(el.dataset.scopes || "").split(",").filter(Boolean);
  const grant = (el) => ACL.send("/admin/grants", "POST", {
    agent: el.dataset.agent, resource: el.dataset.rid, scopes: scopesOf(el), hours: el.dataset.hours ? Number(el.dataset.hours) : null,
  });
  const grantPath = (el) => `/admin/grants/${enc(el.dataset.agent)}/${enc(el.dataset.rid)}`;

  window.ACL.register({
    id: "person",
    title: "Person",
    parent: "people",
    titleOf: (d) => (d.missing ? "Unknown person" : d.person.name),
    async load(ctx) {
      const pid = ctx.params.person;
      const end = ctx.params.end ? "&end=" + enc(ctx.params.end) : "";
      try {
        return await ACL.get(`/admin/analytics/person/${enc(pid)}?period=${ctx.period}${end}`);
      } catch (e) {
        if (/unknown principal/.test(e.message)) return { missing: pid };
        throw e;
      }
    },
    render(d) {
      if (d.missing) {
        return `<div class="Box p-4 text-center"><h2 class="f3 mb-1">No person or agent "${h.esc(d.missing)}"</h2>
          <p class="color-fg-muted f6 mb-2">The id is not in the directory.</p>${h.link("Search people", ACL.href({ view: "people", q: d.missing }))}</div>`;
      }
      const col = colours(d), agent = d.person.kind === "agent";
      return header(d) + tiles(d) + `<div class="acl-cols">
        ${h.card("Spend over time", spendChart(d, col), "", "acl-c8")}
        ${h.card("Spend by service and model", byItem(d, col), "", "acl-c4")}
        ${h.card(agent ? "Access" : "Agents and access", access(d), h.muted("click a pill to grant or revoke"), "acl-c7")}
        ${h.card("Insider risk", risk(d), "", "acl-c5")}
        ${h.card("Recent decisions", decisions(d), h.link(`Full audit log (${h.num(d.decisions.decisions)} in period)`, audit(d.person.id), "Link--secondary f6"), "acl-c12")}
      </div>`;
    },
    actions: {
      grant,
      renew: grant,
      scopes: (el) => ACL.send(grantPath(el), "PATCH", { scopes: scopesOf(el) }),
      revoke: (el) => ACL.send(grantPath(el), "DELETE"),
      level: (el, ctx) => ACL.send(`/admin/risk/${enc(ctx.params.person)}`, "POST", { level: el.dataset.v }),
      reset: (el, ctx) => ACL.send(`/admin/risk/${enc(ctx.params.person)}/reset`, "POST"),
      dismiss: (el, ctx) => ACL.send(`/admin/risk/${enc(ctx.params.person)}/signal/${el.dataset.src.split("/").map(enc).join("/")}`, "DELETE"),
    },
  });
})();
