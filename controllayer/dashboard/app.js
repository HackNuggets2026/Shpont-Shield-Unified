// Security console shell: router, data access, Primer building blocks and SVG charts.
// Views live in views/*.js and register themselves with ACL.register (see docs/console-contract.md).
(function () {
  "use strict";
  const ACL = (window.ACL = { views: {}, order: [] });
  const TOKEN = new URLSearchParams(location.search).get("token") || "";

  // ---- data -----------------------------------------------------------------------------

  async function call(path, opts = {}) {
    const headers = { "content-type": "application/json" };
    if (TOKEN) headers["x-admin-token"] = TOKEN;
    const r = await fetch(path, { method: opts.method || "GET", body: opts.body, headers });
    const body = await r.json().catch(() => ({}));
    if (!r.ok && !opts.allowError) throw new Error(body.error?.message || body.error || body.detail?.[0]?.msg || "HTTP " + r.status);
    return { status: r.status, body };
  }
  ACL.get = async (path) => (await call(path)).body;
  ACL.send = (path, method, data, allowError) => call(path, { method, body: JSON.stringify(data || {}), allowError });
  // A link to an admin download or JSON: carries the token only when the console was opened with one.
  ACL.withToken = (url) => (TOKEN ? url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN) : url);

  // ---- URL state ------------------------------------------------------------------------
  // The URL is the state: ?view=people&team=sales, ?person=alice. `period`, `token` and `theme` survive navigation.

  const KEEP = ["token", "period", "theme"];
  ACL.params = () => Object.fromEntries(new URLSearchParams(location.search));
  ACL.href = (patch, reset = true) => {
    const cur = ACL.params();
    const base = reset ? Object.fromEntries(KEEP.filter((k) => cur[k]).map((k) => [k, cur[k]])) : cur;
    const next = { ...base, ...patch };
    const q = Object.entries(next).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
    return location.pathname + (q.length ? "?" + q.join("&") : "");
  };
  ACL.go = (patch, { reset = false, replace = false } = {}) => {
    history[replace ? "replaceState" : "pushState"](null, "", ACL.href(patch, reset));
    return render(true);
  };
  ACL.period = () => Math.min(31, Math.max(1, Number(ACL.params().period) || 30));

  // ---- formatting -----------------------------------------------------------------------

  const h = (ACL.h = {});
  h.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  h.usd = (x) => {
    const v = Number(x || 0), a = Math.abs(v);
    if (a >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (a >= 1e4) return "$" + (v / 1e3).toFixed(1) + "k";
    if (a >= 100) return "$" + Math.round(v).toLocaleString("en-US");
    return "$" + v.toFixed(a >= 1 || a === 0 ? 2 : a >= 0.01 ? 3 : 4);
  };
  h.num = (x) => {
    const v = Number(x || 0);
    return v >= 1e9 ? (v / 1e9).toFixed(1) + "B" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e4 ? (v / 1e3).toFixed(1) + "k" : Math.round(v).toLocaleString("en-US");
  };
  h.pct = (f) => (f == null ? "-" : (100 * f).toFixed(f < 0.1 ? 1 : 0) + "%");
  h.dur = (hours) => (hours % 24 ? hours + "h" : hours / 24 + "d");
  h.ago = (ts) => {
    if (!ts) return h.muted("never");
    const s = ts - Date.now() / 1000, a = Math.abs(s);
    const v = a < 60 ? Math.round(a) + "s" : a < 3600 ? Math.round(a / 60) + "m" : a < 86400 ? Math.round(a / 3600) + "h" : Math.round(a / 86400) + "d";
    return `<span class="no-wrap" title="${h.esc(new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16))} UTC">${s > 0 ? "in " + v : v + " ago"}</span>`;
  };
  h.periodLabel = (days) => (days === 1 ? "today" : `last ${days} days`);

  // ---- Primer building blocks -----------------------------------------------------------

  const TONE = { allow: "ok", log: "neutral", warn: "warn", redact: "info", block: "bad",
    normal: "ok", watch: "warn", restricted: "bad", low: "ok", medium: "warn", high: "bad" };
  const LABEL = { ok: "Label--success", warn: "Label--attention", bad: "Label--danger", info: "Label--accent", neutral: "Label--secondary" };
  const FLASH = { ok: "flash-success", warn: "flash-warn", bad: "flash-error" };
  h.tone = (x) => TONE[x] || "neutral";
  h.muted = (html) => `<span class="color-fg-muted f6">${html}</span>`;
  h.empty = (text) => `<div class="color-fg-muted f6 py-2">${h.esc(text)}</div>`;
  h.badge = (t, text, title) => `<span class="Label ${LABEL[t] || LABEL.neutral}"${title ? ` title="${h.esc(title)}"` : ""}>${h.esc(text)}</span>`;
  h.note = (html, t) => `<div class="flash ${FLASH[t] || ""} mb-3">${html}</div>`;
  h.button = (label, attrs, kind) => `<button type="button" class="btn btn-sm ${kind ? "btn-" + kind : ""}" ${attrs}>${h.esc(label)}</button>`;
  h.link = (label, href, cls = "Link--primary") => `<a class="${cls}" href="${h.esc(href)}" data-nav>${h.esc(label)}</a>`;
  // A person's name linking to their page.
  h.person = (id, name) => `<a class="Link--primary text-bold" href="${h.esc(ACL.href({ person: id }))}" data-nav>${h.esc(name || id)}</a>`;
  h.card = (title, body, tools, cls = "") => `<section class="Box acl-card ${cls}">
      <div class="Box-header py-2 d-flex flex-items-center flex-wrap"><h3 class="Box-title flex-auto f5">${h.esc(title)}</h3><div class="acl-tools">${tools || ""}</div></div>
      <div class="Box-body p-3">${body}</div></section>`;
  // cols: [{label, num?, sort?: key}], rows: [[cell html...]], opts: {sort, rowAttrs: (i) => attrs, detail: (i) => html|null}
  h.table = (cols, rows, opts = {}) => `<div class="acl-scroll"><table class="acl-table">
      <thead><tr>${cols.map((c) => {
        const cls = c.num ? "acl-num" : "";
        if (!c.sort) return `<th class="${cls}">${h.esc(c.label)}</th>`;
        const cur = (opts.sort || "").replace(/^-/, ""), desc = (opts.sort || "").startsWith("-");
        const next = cur === c.sort && desc ? c.sort : "-" + c.sort;
        const arrow = cur === c.sort ? (desc ? " ↓" : " ↑") : "";
        return `<th class="${cls}" aria-sort="${cur === c.sort ? (desc ? "descending" : "ascending") : "none"}"><a class="Link--secondary" href="${h.esc(ACL.href({ sort: next, page: null }, false))}" data-nav>${h.esc(c.label)}${arrow}</a></th>`;
      }).join("")}</tr></thead>
      <tbody>${rows.map((r, i) => {
        const detail = opts.detail ? opts.detail(i) : null;
        return `<tr ${opts.rowAttrs ? opts.rowAttrs(i) : ""}>${r.map((cell, j) => `<td class="${cols[j]?.num ? "acl-num" : ""}">${cell}</td>`).join("")}</tr>`
          + (detail == null ? "" : `<tr class="acl-detail"><td colspan="${cols.length}">${detail}</td></tr>`);
      }).join("")}</tbody></table></div>`;
  // One click picks an option: [{label, active, attrs}].
  h.segmented = (items) => `<div class="BtnGroup" role="group">${items.map((o) =>
    `<button type="button" class="btn btn-sm BtnGroup-item${o.active ? " btn-primary" : ""}" aria-pressed="${!!o.active}" ${o.attrs}${o.title ? ` title="${h.esc(o.title)}"` : ""}>${h.esc(o.label)}</button>`).join("")}</div>`;
  // Segmented links that change URL params: [{label, value}] for param `key`.
  h.navSegmented = (key, options, current) => `<div class="BtnGroup" role="group">${options.map((o) =>
    `<a class="btn btn-sm BtnGroup-item${String(o.value) === String(current) ? " btn-primary" : ""}" href="${h.esc(ACL.href({ [key]: o.value, page: null }, false))}" data-nav>${h.esc(o.label)}</a>`).join("")}</div>`;
  // A select that sets URL param `key`.
  h.navSelect = (key, options, current, label) => `<select class="form-select select-sm" data-param="${h.esc(key)}" aria-label="${h.esc(label || key)}">${options.map((o) =>
    `<option value="${h.esc(o.value)}"${String(o.value) === String(current ?? "") ? " selected" : ""}>${h.esc(o.label)}</option>`).join("")}</select>`;
  // A compact on/off pill: green "on", grey "off"; disabled entries are greyed and not clickable.
  h.pill = (on, label, attrs, opts = {}) => `<button type="button" class="acl-pill ${on ? "acl-on" : "acl-off"}${opts.disabled ? " acl-disabled" : ""}" aria-pressed="${!!on}"${opts.disabled ? " disabled" : ""} ${attrs || ""}${opts.title ? ` title="${h.esc(opts.title)}"` : ""}>${h.esc(label)}</button>`;
  // A meter of used / budget: blue under 80%, attention to 100%, danger over.
  h.meter = (f, title) => {
    if (f == null) return h.muted("no cap");
    const cls = f >= 1 ? "acl-m-over" : f >= 0.8 ? "acl-m-near" : "acl-m-ok";
    return `<span class="acl-meter ${cls}" title="${h.esc(title || h.pct(f) + " of budget")}"><span style="width:${Math.min(100, 100 * f).toFixed(1)}%"></span></span>`;
  };
  // Stat tiles: [{label, value, sub?, tone?, href?, meter?}].
  h.tiles = (items) => `<div class="acl-tiles">${items.map((k) => {
    const inner = `<div class="acl-tile-label">${h.esc(k.label)}</div>
      <div class="acl-tile-value ${k.tone ? "acl-t-" + k.tone : ""}">${h.esc(k.value)}</div>
      ${k.meter !== undefined ? h.meter(k.meter) : ""}${k.sub ? `<div class="acl-tile-sub">${k.sub}</div>` : ""}`;
    return k.href ? `<a class="acl-tile acl-tile-link" href="${h.esc(k.href)}" data-nav>${inner}</a>` : `<div class="acl-tile">${inner}</div>`;
  }).join("")}</div>`;
  h.pager = (r) => {
    const from = r.total ? (r.page - 1) * r.per_page + 1 : 0, to = Math.min(r.total, r.page * r.per_page);
    const nav = (label, page, off) => off ? `<span class="btn btn-sm" aria-disabled="true">${label}</span>` : `<a class="btn btn-sm" href="${h.esc(ACL.href({ page }, false))}" data-nav>${label}</a>`;
    return `<div class="d-flex flex-items-center flex-justify-between mt-2 f6">
      <span class="color-fg-muted">${from.toLocaleString("en-US")}-${to.toLocaleString("en-US")} of ${r.total.toLocaleString("en-US")}</span>
      <span class="acl-tools">${nav("Previous", r.page - 1, r.page <= 1)}<span class="color-fg-muted">page ${r.page} of ${r.pages}</span>${nav("Next", r.page + 1, r.page >= r.pages)}</span></div>`;
  };

  // ---- brand logos ----------------------------------------------------------------------
  // Pinned, permissively licensed icon sets on jsDelivr: Simple Icons (CC0, one-colour, drawn as a mask in
  // the brand colour), devicon (MIT, full colour) where Simple Icons dropped the brand, and LobeHub icons
  // (MIT) for every model provider.

  const SI = (slug, hex, v = "16.33.0") => ({ mask: `https://cdn.jsdelivr.net/npm/simple-icons@${v}/icons/${slug}.svg`, hex });
  const DEV = (path) => ({ img: `https://cdn.jsdelivr.net/npm/devicon@2.17.0/icons/${path}.svg` });
  const LOBE = (name) => ({ img: `https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.95.1/icons/${name}.svg` });
  const BRANDS = {
    vercel: ["Vercel", SI("vercel", "000000")], heroku: ["Heroku", DEV("heroku/heroku-original")],
    stripe: ["Stripe", SI("stripe", "635BFF")], postgres: ["PostgreSQL", SI("postgresql", "4169E1")],
    supabase: ["Supabase", SI("supabase", "3FCF8E")], snowflake: ["Snowflake", SI("snowflake", "29B5E8")],
    slack: ["Slack", DEV("slack/slack-original")], github: ["GitHub", SI("github", "181717")],
    linear: ["Linear", SI("linear", "5E6AD2")], zendesk: ["Zendesk", SI("zendesk", "03363D")],
    notion: ["Notion", SI("notion", "000000")], gdrive: ["Google Drive", SI("googledrive", "4285F4")],
    datadog: ["Datadog", SI("datadog", "632CA6")], pagerduty: ["PagerDuty", SI("pagerduty", "06AC38")],
    zapier: ["Zapier", SI("zapier", "FF4F00")], sendgrid: ["SendGrid", SI("sendgrid", "1A82E2", "13.21.0")],
    salesforce: ["Salesforce", DEV("salesforce/salesforce-original")], hubspot: ["HubSpot", SI("hubspot", "FF7A59")],
    s3: ["AWS S3", DEV("amazonwebservices/amazonwebservices-original-wordmark")], upstash: ["Upstash", SI("upstash", "00E9A3")],
    claude: ["Claude (Anthropic)", LOBE("claude-color")], anthropic: ["Anthropic", LOBE("anthropic")],
    openai: ["OpenAI", LOBE("openai")], meta: ["Llama (Meta)", LOBE("meta-color")], qwen: ["Qwen", LOBE("qwen-color")],
    mistral: ["Mistral AI", LOBE("mistral-color")], gemini: ["Gemini (Google)", LOBE("gemini-color")],
    deepseek: ["DeepSeek", LOBE("deepseek-color")], ollama: ["Ollama", LOBE("ollama")],
  };
  const ALIASES = { "github-acme": "github", "postgres-prod": "postgres", "aws-s3": "s3", "salesforce-crm": "salesforce",
    "google-drive": "gdrive", "upstash-redis": "upstash", redis: "upstash" };
  const MODELS = [[/^claude/, "claude"], [/^(gpt|o\d|chatgpt|text-embedding)/, "openai"], [/^(llama|codellama)/, "meta"],
    [/^qwen/, "qwen"], [/^(mistral|mixtral|codestral)/, "mistral"], [/^gemini/, "gemini"], [/^deepseek/, "deepseek"]];
  // key: "service:stripe", "model:claude-sonnet-5", a service key, a resource id, a model name or a tool name.
  ACL.logo = (key, size = 14) => {
    const raw = String(key || "").replace(/^(service|model):/, "");
    let id = ALIASES[raw] || (BRANDS[raw] ? raw : null) || (BRANDS[raw.split("_")[0]] ? raw.split("_")[0] : null);
    if (!id) { const m = MODELS.find(([re]) => re.test(raw.toLowerCase())); id = m ? m[1] : null; }
    const style = `width:${size}px;height:${size}px`;
    if (!id) {
      const letter = (raw[0] || "?").toUpperCase();
      return `<span class="acl-logo acl-logo-text" role="img" aria-label="${h.esc(raw || "?")}" title="${h.esc(raw || "?")}" style="${style};font-size:${Math.round(size * 0.62)}px">${h.esc(letter)}</span>`;
    }
    const [name, src] = BRANDS[id];
    if (src.img) return `<img class="acl-logo" src="${src.img}" alt="${h.esc(name)}" title="${h.esc(name)}" style="${style}" loading="lazy">`;
    return `<span class="acl-logo" role="img" aria-label="${h.esc(name)}" title="${h.esc(name)}" style="${style};background:#${src.hex};-webkit-mask-image:url(${src.mask});mask-image:url(${src.mask})"></span>`;
  };

  // ---- charts (inline SVG; palette in core.css: --viz-1.., status colours) ----------------
  // Every mark carries data-tip (shown by the shared tooltip) and, when it drills down, sits in an
  // <a data-nav href>. Time series and distributions are smooth curves with a hover crosshair;
  // categories are bars (at most 24px thick, 4px rounded data end, 2px gaps).

  const c = (ACL.charts = {});
  const VBW = 640; // default viewBox width; pass opts.width close to the rendered width so text stays ~11px
  const ticks = (max, n = 4) => {
    if (max <= 0) return [0];
    const raw = max / n, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    const out = [];
    for (let v = 0; v <= max + step * 0.001; v += step) out.push(v);
    if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
    return out;
  };
  const wrap = (inner, href, tip) => {
    const t = tip ? ` data-tip="${h.esc(tip)}"` : "";
    return href ? `<a href="${h.esc(href)}" data-nav${t}>${inner}</a>` : `<g tabindex="0"${t}>${inner}</g>`;
  };
  c.legend = (series) => `<div class="acl-legend">${series.map((s) =>
    `<span><i class="acl-swatch" style="background:${s.color}"></i>${h.esc(s.label)}${s.value != null ? ` <b>${h.esc(s.value)}</b>` : ""}</span>`).join("")}</div>`;

  // Ranked horizontal bars. rows: [{label, value, color?, href?, sub?, icon? (html before the label)}], opts: {fmt, max}
  c.hbars = (rows, opts = {}) => {
    const fmt = opts.fmt || h.usd, max = opts.max || Math.max(...rows.map((r) => r.value), 0) || 1;
    return `<div class="acl-hbars">${rows.map((r) => {
      const w = Math.max(0.5, (100 * r.value) / max);
      const bar = `<span class="acl-hbar-label" title="${h.esc(r.label)}">${r.icon || ""}${h.esc(r.label)}</span>
        <span class="acl-hbar-track"><span class="acl-hbar" style="width:${w.toFixed(2)}%;background:${r.color || "var(--viz-1)"}"></span></span>
        <span class="acl-hbar-value">${h.esc(fmt(r.value))}${r.sub ? ` <span class="color-fg-muted">${h.esc(r.sub)}</span>` : ""}</span>`;
      const tip = `${r.label}: ${fmt(r.value)}${r.sub ? " (" + r.sub + ")" : ""}`;
      return r.href ? `<a class="acl-hbar-row" href="${h.esc(r.href)}" data-nav data-tip="${h.esc(tip)}">${bar}</a>` : `<div class="acl-hbar-row" tabindex="0" data-tip="${h.esc(tip)}">${bar}</div>`;
    }).join("")}</div>`;
  };

  // A 100% bar split into labelled parts: [{label, value, color, href?}]. Labels always show (count + name).
  c.split = (parts) => {
    const total = parts.reduce((a, p) => a + p.value, 0) || 1;
    const segs = parts.filter((p) => p.value > 0).map((p) => {
      const tip = `${p.label}: ${p.value.toLocaleString("en-US")} (${h.pct(p.value / total)})`;
      const seg = `<span class="acl-split-seg" style="flex:${p.value} 1 0;background:${p.color}"></span>`;
      return p.href ? `<a class="acl-split-a" style="flex:${p.value} 1 0" href="${h.esc(p.href)}" data-nav data-tip="${h.esc(tip)}">${seg}</a>` : `<span class="acl-split-a" style="flex:${p.value} 1 0" tabindex="0" data-tip="${h.esc(tip)}">${seg}</span>`;
    }).join("");
    const keys = parts.map((p) => {
      const inner = `<i class="acl-swatch" style="background:${p.color}"></i>${h.esc(p.label)} <b>${p.value.toLocaleString("en-US")}</b>`;
      return p.href ? `<a class="Link--secondary" href="${h.esc(p.href)}" data-nav>${inner}</a>` : `<span>${inner}</span>`;
    }).join("");
    return `<div class="acl-split">${segs}</div><div class="acl-legend">${keys}</div>`;
  };

  // A single-series line over time with optional horizontal thresholds. opts: {labels, values, color, thresholds: [{value, label}], fmt, height}
  c.line = (opts) => {
    const W = opts.width || VBW, H = opts.height || 120, L = 34, B = 20, T = 10, n = opts.labels.length;
    const yt = ticks(Math.max(...opts.values, ...(opts.thresholds || []).map((t) => t.value), 1), 3), max = yt[yt.length - 1];
    const ph = H - B - T, step = n > 1 ? (W - L - 8) / (n - 1) : 0, x = (i) => L + 4 + i * step, y = (v) => T + ph - (v / max) * ph;
    const fmt = opts.fmt || ((v) => h.num(v));
    let out = yt.map((v) => `<line class="acl-grid" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text class="acl-axis" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${h.esc(fmt(v))}</text>`).join("");
    (opts.thresholds || []).forEach((t) => {
      out += `<line class="acl-ref" x1="${L}" x2="${W}" y1="${y(t.value)}" y2="${y(t.value)}"/><text class="acl-axis acl-ref-label" x="${W - 2}" y="${y(t.value) - 3}" text-anchor="end">${h.esc(t.label)}</text>`;
    });
    const pts = opts.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
    out += `<polyline points="${pts}" fill="none" stroke="${opts.color || "var(--viz-1)"}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    const last = n - 1;
    if (n) out += `<circle cx="${x(last)}" cy="${y(opts.values[last])}" r="4" fill="${opts.color || "var(--viz-1)"}" stroke="var(--viz-surface)" stroke-width="2"/>`;
    opts.values.forEach((v, i) => {
      out += `<rect class="acl-hit" tabindex="0" x="${x(i) - step / 2}" y="${T}" width="${Math.max(step, 6)}" height="${ph}" data-tip="${h.esc(opts.labels[i] + ": " + fmt(v))}"/>`;
    });
    const every = Math.ceil(n / 6);
    opts.labels.forEach((lab, i) => {
      if (i % every === 0 || i === last) out += `<text class="acl-axis" x="${x(i)}" y="${H - 5}" text-anchor="${i === last ? "end" : "middle"}">${h.esc(lab)}</text>`;
    });
    return `<svg class="acl-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${h.esc(opts.title || "line chart")}">${out}</svg>`;
  };

  // Monotone cubic (Fritsch-Carlson) through [x, y] points: smooth, but never overshoots a peak or dips
  // below the baseline between data points. Returns path commands starting with M.
  const r1 = (v) => Math.round(v * 10) / 10;
  const smooth = (pts) => {
    const n = pts.length;
    if (n < 3) return pts.map(([x, y], i) => `${i ? "L" : "M"}${r1(x)},${r1(y)}`).join("");
    const dx = [], m = [], t = [];
    for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; m[i] = (pts[i + 1][1] - pts[i][1]) / dx[i]; }
    t[0] = m[0]; t[n - 1] = m[n - 2];
    for (let i = 1; i < n - 1; i++) {
      t[i] = m[i - 1] * m[i] <= 0 ? 0 : (3 * (dx[i - 1] + dx[i])) / ((2 * dx[i] + dx[i - 1]) / m[i - 1] + (dx[i] + 2 * dx[i - 1]) / m[i]);
    }
    let d = `M${r1(pts[0][0])},${r1(pts[0][1])}`;
    for (let i = 0; i < n - 1; i++) {
      const k = dx[i] / 3;
      d += `C${r1(pts[i][0] + k)},${r1(pts[i][1] + t[i] * k)} ${r1(pts[i + 1][0] - k)},${r1(pts[i + 1][1] - t[i + 1] * k)} ${r1(pts[i + 1][0])},${r1(pts[i + 1][1])}`;
    }
    return d;
  };
  // One hover column: hit area, hairline crosshair and a dot per series, revealed together on hover.
  const xcol = (x, x0, w, T, ph, dots, href, tip) => wrap(
    `<rect class="acl-hit" x="${r1(x0)}" y="${T}" width="${r1(w)}" height="${ph}"/>` +
    `<line class="acl-xhair" x1="${r1(x)}" x2="${r1(x)}" y1="${T}" y2="${T + ph}"/>` +
    dots.map(([y, col]) => `<circle class="acl-xdot" cx="${r1(x)}" cy="${r1(y)}" r="4" fill="${col}"/>`).join(""), href, tip);

  // Stacked smooth areas over time. Same options as columns: {labels, series: [{label, color, values}], fmt, ref, href, height}
  c.area = (opts) => {
    const W = opts.width || VBW, H = opts.height || 180, L = 48, B = 22, T = 8, n = opts.labels.length;
    const fmt = opts.fmt || h.usd;
    const cum = [];
    opts.series.forEach((s, k) => { cum[k] = opts.labels.map((_, i) => (k ? cum[k - 1][i] : 0) + (s.values[i] || 0)); });
    const totals = cum.length ? cum[cum.length - 1] : opts.labels.map(() => 0);
    const yt = ticks(Math.max(...totals, opts.ref?.value || 0, 0));
    const max = yt[yt.length - 1] || 1, ph = H - B - T, y = (v) => T + ph - (v / max) * ph;
    const step = n > 1 ? (W - L - 8) / (n - 1) : 0, x = (i) => L + 4 + i * step;
    let out = yt.map((v) => `<line class="acl-grid" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text class="acl-axis" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${h.esc(fmt(v))}</text>`).join("");
    opts.series.forEach((s, k) => {
      const top = cum[k].map((v, i) => [x(i), y(v)]);
      const bottom = (k ? cum[k - 1] : cum[k].map(() => 0)).map((v, i) => [x(i), y(v)]).reverse();
      out += `<path d="${smooth(top)}${smooth(bottom).replace(/^M/, "L")}Z" fill="${s.color}" fill-opacity=".16"/>`;
      out += `<path d="${smooth(top)}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    });
    out += `<line class="acl-base" x1="${L}" x2="${W}" y1="${y(0)}" y2="${y(0)}"/>`;
    if (opts.ref && opts.ref.value > 0) {
      out += `<line class="acl-ref" x1="${L}" x2="${W}" y1="${y(opts.ref.value)}" y2="${y(opts.ref.value)}"/><text class="acl-axis acl-ref-label" x="${W - 2}" y="${y(opts.ref.value) - 4}" text-anchor="end">${h.esc(opts.ref.label)}</text>`;
    }
    opts.labels.forEach((lab, i) => {
      const tip = `${lab}: ${fmt(totals[i])}` + (opts.series.length > 1 ? opts.series.map((s) => `\n${s.label}: ${fmt(s.values[i] || 0)}`).join("") : "");
      const dots = opts.series.map((s, k) => [y(cum[k][i]), s.color]);
      out += xcol(x(i), x(i) - step / 2, Math.max(step, 6), T, ph, dots, opts.href?.(i), tip);
    });
    if (n) out += `<circle cx="${r1(x(n - 1))}" cy="${r1(y(totals[n - 1]))}" r="3.5" fill="${opts.series[opts.series.length - 1]?.color || "var(--viz-1)"}" stroke="var(--viz-surface)" stroke-width="2"/>`;
    const every = Math.ceil(n / 8);
    opts.labels.forEach((lab, i) => {
      if (i % every === 0 || i === n - 1) out += `<text class="acl-axis" x="${x(i)}" y="${H - 6}" text-anchor="${i === n - 1 ? "end" : i === 0 ? "start" : "middle"}">${h.esc(lab)}</text>`;
    });
    return `<svg class="acl-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${h.esc(opts.title || "area chart")}">${out}</svg>` + (opts.series.length > 1 ? c.legend(opts.series) : "");
  };

  // A distribution as one smooth curve over histogram bins (equal-width bands, labelled at their edges).
  // bins: [{count, edge, end?, href?, tip?}], markers: [{at: fractional bin index, label}]
  c.density = (bins, markers = [], opts = {}) => {
    const W = opts.width || VBW, H = opts.height || 150, L = 34, B = 22, T = 16, n = bins.length || 1;
    const yt = ticks(Math.max(...bins.map((b) => b.count), 1), 3), max = yt[yt.length - 1];
    const ph = H - B - T, band = (W - L) / n, y = (v) => T + ph - (v / max) * ph, cx = (i) => L + (i + 0.5) * band;
    const color = opts.color || "var(--viz-1)";
    let out = yt.map((v) => `<line class="acl-grid" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text class="acl-axis" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${h.num(v)}</text>`).join("");
    const pts = [[L, y(0)], ...bins.map((b, i) => [cx(i), y(b.count)]), [W, y(0)]];
    out += `<path d="${smooth(pts)}Z" fill="${color}" fill-opacity=".14"/><path d="${smooth(pts)}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    out += `<line class="acl-base" x1="${L}" x2="${W}" y1="${y(0)}" y2="${y(0)}"/>`;
    bins.forEach((b, i) => {
      out += xcol(cx(i), L + i * band, band, T, ph, [[y(b.count), color]], b.href, b.tip || `${b.edge}: ${b.count}`);
      out += `<text class="acl-axis" x="${L + i * band}" y="${H - 6}" text-anchor="${i ? "middle" : "start"}">${h.esc(b.edge ?? "")}</text>`;
    });
    if (bins.length && bins[bins.length - 1].end != null) out += `<text class="acl-axis" x="${W - 2}" y="${H - 6}" text-anchor="end">${h.esc(bins[bins.length - 1].end)}</text>`;
    markers.forEach((m) => {
      const x = L + m.at * band;
      out += `<line class="acl-marker" x1="${x}" x2="${x}" y1="${T - 4}" y2="${y(0)}"/><text class="acl-axis acl-marker-label" x="${x + 3}" y="${T - 6}">${h.esc(m.label)}</text>`;
    });
    return `<svg class="acl-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${h.esc(opts.title || "distribution")}">${out}</svg>`;
  };

  c.PALETTE = ["var(--viz-1)", "var(--viz-2)", "var(--viz-3)", "var(--viz-4)", "var(--viz-5)"];
  c.OTHER = "var(--viz-other)";
  c.KIND = { model: "var(--viz-1)", service: "var(--viz-2)" };
  c.STATUS = { normal: "var(--status-good)", watch: "var(--status-warning)", restricted: "var(--status-critical)",
    under: "var(--viz-seq-1)", half: "var(--viz-seq-2)", near: "var(--status-warning)", over: "var(--status-critical)", nocap: "var(--viz-other)" };
  // Axis labels for a series: days as "Oct 3", hours as "14:00" (UTC).
  c.bucketLabels = (series, n) => Array.from({ length: n }, (_, i) => {
    const d = new Date((series.start + i * (series.unit === "hour" ? 3600 : 86400)) * 1000);
    return series.unit === "hour" ? String(d.getUTCHours()).padStart(2, "0") + ":00" : d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  });

  // ---- tooltip ----------------------------------------------------------------------------

  let tip = null;
  function showTip(el, ev) {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "acl-tip";
      document.body.appendChild(tip);
    }
    tip.textContent = "";
    el.getAttribute("data-tip").split("\n").forEach((line, i) => {
      const row = document.createElement("div");
      if (i === 0) row.className = "acl-tip-head";
      row.textContent = line;
      tip.appendChild(row);
    });
    const r = el.getBoundingClientRect();
    const x = ev && ev.clientX != null ? ev.clientX : r.left + r.width / 2, y = ev && ev.clientY != null ? ev.clientY : r.top;
    tip.style.left = Math.min(window.innerWidth - 220, x + 12) + "px";
    tip.style.top = y + 14 + "px";
    tip.hidden = false;
  }
  const hideTip = () => { if (tip) tip.hidden = true; };

  // ---- shell and router ---------------------------------------------------------------------

  ACL.register = (view) => {
    ACL.views[view.id] = view;
    ACL.order = Object.values(ACL.views).sort((a, b) => (a.order || 0) - (b.order || 0));
  };
  ACL.currentView = () => {
    const p = ACL.params();
    if (p.person && ACL.views.person) return ACL.views.person;
    return ACL.views[p.view] || ACL.views.overview || ACL.order[0];
  };
  ACL.state = { error: "", summary: null };

  const PERIODS = [{ label: "Today", value: 1 }, { label: "7 days", value: 7 }, { label: "30 days", value: 30 }];
  function shell(view, body) {
    const s = ACL.state.summary || {};
    const pol = s.policy || {};
    const tabs = ACL.order.filter((v) => v.tab && !v.hidden).map((v) =>
      `<a class="UnderlineNav-item" href="${h.esc(ACL.href({ view: v.id }))}" data-nav${v === view || (view.parent === v.id) ? ' aria-current="page"' : ""}>${h.esc(v.title)}</a>`).join("");
    return `<header class="Header py-2 px-3">
        <div class="Header-item"><a class="Header-link f4 text-bold" href="${h.esc(ACL.href({}))}" data-nav>AI Control Layer</a></div>
        <div class="Header-item Header-item--full f6 color-fg-on-emphasis acl-meta">${pol.name ? `${h.esc(pol.name)} · policy ${h.esc(pol.version)}` : ""}</div>
        ${s.demo_mode ? `<div class="Header-item mr-0"><span class="acl-demo" title="identity.demo_mode is on: no admin token or API keys are checked">Demo mode - no authentication</span></div>` : ""}
      </header>
      ${view.inspection ? `<div class="acl-inspection px-3">Database inspection: a full, paged listing</div>` : ""}
      <nav class="UnderlineNav px-3 acl-nav" aria-label="Console">
        <div class="UnderlineNav-body">${tabs}</div>
        <div class="UnderlineNav-actions acl-tools">${view.periodic === false ? "" : h.muted("period") + " " + h.navSegmented("period", PERIODS, ACL.period())}</div>
      </nav>
      <main class="acl-main px-3 py-3">
        ${pol.last_error ? h.note("Rejected policy edit: " + h.esc(String(pol.last_error).slice(0, 200)), "bad") : ""}
        ${ACL.state.error ? h.note(h.esc(ACL.state.error), "bad") : ""}
        ${body}
      </main>`;
  }

  let seq = 0;
  let data = null;
  ACL.ctx = () => ({ params: ACL.params(), period: ACL.period(), data });
  async function render(force) {
    const a = document.activeElement;
    if (!force && a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName)) return;
    const mine = ++seq;
    const view = ACL.currentView();
    const root = document.getElementById("acl-root");
    root.classList.add("acl-loading");
    try {
      const [summary, d] = await Promise.all([ACL.get("/admin/summary"), view.load ? view.load(ACL.ctx()) : null]);
      if (mine !== seq) return; // a newer navigation won
      ACL.state.summary = summary;
      data = d;
      const focus = a && a.dataset ? a.dataset.input : null, caret = focus ? a.selectionStart : null;
      root.innerHTML = shell(view, view.render(d, ACL.ctx()));
      if (focus) {
        const el = root.querySelector(`[data-input="${focus}"]`);
        if (el) { el.focus(); if (caret != null && el.setSelectionRange) el.setSelectionRange(caret, caret); }
      }
      document.title = (view.titleOf ? view.titleOf(d) : view.title) + " - AI Control Layer";
    } catch (e) {
      if (mine !== seq) return;
      root.innerHTML = shell(view, h.note("Cannot load: " + h.esc(e.message) + (TOKEN ? "" : " (if this deployment needs a token, open with ?token=...)"), "bad"));
    } finally {
      if (mine === seq) root.classList.remove("acl-loading");
    }
  }
  ACL.refresh = () => render(true);

  async function act(el) {
    const view = ACL.currentView();
    const name = el.dataset.act;
    const fn = (view.actions && view.actions[name]) || (ACL.actions && ACL.actions[name]);
    if (!fn) return;
    ACL.state.error = "";
    try { await fn(el, ACL.ctx()); } catch (e) { ACL.state.error = e.message; }
    render(true);
  }
  ACL.actions = {};

  let typing = null;
  ACL.start = () => {
    document.addEventListener("click", (ev) => {
      const t = ev.target;
      const nav = t.closest && t.closest("a[data-nav]");
      if (nav && !ev.ctrlKey && !ev.metaKey && !ev.shiftKey && ev.button === 0) {
        ev.preventDefault();
        history.pushState(null, "", nav.getAttribute("href"));
        hideTip();
        render(true);
        return;
      }
      const btn = t.closest && t.closest("[data-act]");
      if (btn && (btn.tagName === "BUTTON" || btn.tagName === "A") && !btn.disabled) { ev.preventDefault(); act(btn); }
      else if (!btn) {
        // A whole table row can link somewhere (<tr data-href>), unless the click was on a control in it.
        const row = t.closest && t.closest("tr[data-href]");
        if (row && !(t.closest("a, button, input, select, textarea, label"))) {
          if (ev.ctrlKey || ev.metaKey) window.open(row.getAttribute("data-href"), "_blank");
          else { history.pushState(null, "", row.getAttribute("data-href")); hideTip(); render(true); }
        }
      }
    });
    document.addEventListener("change", (ev) => {
      const el = ev.target;
      if (el.dataset && el.dataset.param) ACL.go({ [el.dataset.param]: el.value || null, page: null });
      else if (el.dataset && el.dataset.act && el.tagName === "SELECT") act(el);
    });
    document.addEventListener("input", (ev) => {
      const el = ev.target;
      if (!el.dataset || !el.dataset.input) return;
      const view = ACL.currentView();
      const handler = view.inputs && view.inputs[el.dataset.input];
      if (!handler) return;
      clearTimeout(typing);
      typing = setTimeout(() => handler(el.value, ACL.ctx(), el), el.dataset.debounce ? Number(el.dataset.debounce) : 250);
    });
    document.addEventListener("keydown", (ev) => {
      const view = ACL.currentView();
      if (view.onKey) view.onKey(ev, ACL.ctx());
    });
    document.addEventListener("mouseover", (ev) => {
      const el = ev.target.closest && ev.target.closest("[data-tip]");
      if (el) showTip(el, ev); else hideTip();
    });
    document.addEventListener("focusin", (ev) => {
      const el = ev.target.closest && ev.target.closest("[data-tip]");
      if (el) showTip(el); else hideTip();
    });
    window.addEventListener("popstate", () => render(true));
    render(true);
    setInterval(() => { if (!document.hidden) render(false); }, 15000);
  };
  if (!location.pathname.replace(/^\/legacy(?=\/)/, "").startsWith("/me")) document.addEventListener("DOMContentLoaded", ACL.start); // /me is core.js
})();
