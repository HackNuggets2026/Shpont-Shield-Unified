// Beer CSS (Material Design 3): soft tonal surfaces, pill controls.
(function () {
  const e = ACL.esc;
  document.body.className = "light";
  ACL.css(["https://cdn.jsdelivr.net/npm/beercss@3.13.3/dist/cdn/beer.min.css"], `
    .acl-main { padding: 20px 24px 48px; }
    .acl-kv { font-size: 12px; opacity: .75; }
    .acl-big { font-size: 26px; font-weight: 500; }
    .acl-muted { font-size: 12px; opacity: .7; }
    .acl-meter { height: 6px; border-radius: 3px; background: var(--surface-variant); overflow: hidden; }
    .acl-meter > i { display: block; height: 100%; border-radius: 3px; }
    table td { vertical-align: top; }
    .field.small select, .field.small input { height: 2.25rem; }
    header .field { margin: 0; }`);
  const TEXT = { ok: "green-text", warn: "amber-text", bad: "red-text", info: "blue-text", neutral: "" };
  const BAR = { ok: "#2e7d32", warn: "#ef6c00", bad: "#c62828", info: "#1565c0" };
  const CARD = { ok: "secondary-container", warn: "tertiary-container", bad: "error-container", info: "primary-container", neutral: "surface-container" };
  window.ACL_THEME = {
    shell: (p) => `
      <header class="primary-container"><nav>
        <h6 class="max">AI Control Layer</h6>
        ${ACL.nav().map((n) => `<a class="chip${n.active ? " fill" : ""}" href="${n.href}">${e(n.label)}</a>`).join("")}
        <div class="acl-tools">${p.tools || ""}${ACL.themePicker("beer")}</div>
      </nav></header>
      <main class="acl-main"><h4 style="margin:0">${e(p.title)}</h4><div class="acl-muted" style="margin:4px 0 16px">${p.meta}</div>
        ${p.notice || ""}${p.body}</main>`,
    card: (title, body, o = {}) => `<article class="round" style="margin:0">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px">
          <h6 style="margin:0">${e(title)}</h6><div class="acl-tools">${o.tools || ""}</div></div>${body}</article>`,
    kpis: (items) => `<div class="acl-kpis">${items.map((k) => `<div class="${CARD[k.tone] || CARD.neutral} round" style="padding:12px 14px">
        <div class="acl-kv">${e(k.label)}</div><div class="acl-big">${e(k.value)}</div></div>`).join("")}</div>`,
    table: (head, rows, o = {}) => `<div class="acl-scroll"><table class="${o.bare ? "" : "stripes"} small-text">
        ${o.bare ? "" : `<thead><tr>${head.map((h, i) => `<th class="${(o.num || []).includes(i) ? "acl-num" : ""}">${e(h)}</th>`).join("")}</tr></thead>`}
        <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${(o.num || []).includes(i) ? "acl-num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`,
    badge: (t, text) => `<span class="chip small border ${TEXT[t] || ""}" style="margin:0 2px 0 0">${e(text)}</span>`,
    button: (label, attrs, o = {}) => `<button type="button" class="small ${o.kind === "primary" ? "" : o.kind === "danger" ? "border red-text" : "border"}" ${attrs}>${e(label)}</button>`,
    link: (label, href) => `<a class="button small border" href="${e(href)}">${e(label)}</a>`,
    select: (attrs, opts) => `<div class="field border small round" style="display:inline-block;margin:0;min-width:120px"><select ${attrs}>${opts.map((o) => `<option value="${e(o.value)}"${o.selected ? " selected" : ""}>${e(o.label)}</option>`).join("")}</select></div>`,
    input: (attrs) => `<div class="field border small round" style="display:inline-block;margin:0"><input ${attrs}></div>`,
    textarea: (attrs, v) => `<div class="field border round" style="margin:0;height:auto"><textarea ${attrs} style="font-family:monospace">${e(v)}</textarea></div>`,
    checkbox: (attrs, label) => `<label class="checkbox small" style="margin-right:10px"><input type="checkbox" ${attrs}><span>${e(label)}</span></label>`,
    note: (html, t) => `<article class="${CARD[t] || CARD.info} round" style="margin:0 0 16px;padding:12px 16px">${html}</article>`,
    meter: (p, t) => `<div class="acl-meter"><i style="width:${p}%;background:${BAR[t] || BAR.info}"></i></div>`,
    pre: (text) => `<pre class="surface-container round" style="padding:12px;margin-top:10px;max-height:340px;overflow:auto;font-size:12px">${e(text)}</pre>`,
    muted: (html) => `<span class="acl-muted">${html}</span>`,
    empty: (text) => `<div class="acl-muted">${e(text)}</div>`,
  };
})();
