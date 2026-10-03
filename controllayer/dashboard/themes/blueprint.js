// Blueprint (Palantir): dark, dense operations console.
(function () {
  const e = ACL.esc;
  document.body.className = "bp5-dark";
  ACL.css(["https://cdn.jsdelivr.net/npm/@blueprintjs/core@5.19.1/lib/css/blueprint.css"], `
    body { background: #1c2127; margin: 0; }
    .acl-main { padding: 18px 22px 40px; }
    .acl-meta { font-size: 12px; margin: 2px 0 14px; }
    .bp5-navbar .bp5-html-select select { min-width: 110px; }
    .acl-kv { font-size: 11px; letter-spacing: .06em; text-transform: uppercase; }
    .acl-big { font-size: 24px; margin-top: 2px; }`);
  const INTENT = { ok: "bp5-intent-success", warn: "bp5-intent-warning", bad: "bp5-intent-danger", info: "bp5-intent-primary", neutral: "" };
  const COLOR = { ok: "#72CA9B", warn: "#FBB360", bad: "#FA999C", info: "#8ABBFF" };
  window.ACL_THEME = {
    shell: (p) => `
      <nav class="bp5-navbar bp5-dark" style="position:sticky;top:0;z-index:20">
        <div class="bp5-navbar-group bp5-align-left">
          <div class="bp5-navbar-heading"><b>AI Control Layer</b></div><span class="bp5-navbar-divider"></span>
          ${ACL.nav().map((n) => `<a class="bp5-button bp5-minimal${n.active ? " bp5-active" : ""}" href="${n.href}">${e(n.label)}</a>`).join("")}
        </div>
        <div class="bp5-navbar-group bp5-align-right acl-tools">${p.tools || ""}<span class="bp5-navbar-divider"></span>${ACL.themePicker("blueprint")}</div>
      </nav>
      <main class="acl-main"><h3 class="bp5-heading">${e(p.title)}</h3>
        <div class="acl-meta bp5-text-muted bp5-monospace-text">${p.meta}</div>${p.notice || ""}${p.body}</main>`,
    card: (title, body, o = {}) => `<div class="bp5-card bp5-elevation-1" style="padding:14px 16px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px">
          <h5 class="bp5-heading" style="margin:0">${e(title)}</h5><div class="acl-tools">${o.tools || ""}</div></div>${body}</div>`,
    kpis: (items) => `<div class="acl-kpis">${items.map((k) => `<div><div class="acl-kv bp5-text-muted">${e(k.label)}</div>
        <div class="acl-big bp5-monospace-text" style="color:${COLOR[k.tone] || "inherit"}">${e(k.value)}</div></div>`).join("")}</div>`,
    table: (head, rows, o = {}) => `<div class="acl-scroll"><table class="bp5-html-table bp5-compact${o.bare ? "" : " bp5-html-table-striped"}" style="width:100%">
        ${o.bare ? "" : `<thead><tr>${head.map((h, i) => `<th class="${(o.num || []).includes(i) ? "acl-num" : ""}">${e(h)}</th>`).join("")}</tr></thead>`}
        <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${(o.num || []).includes(i) ? "acl-num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`,
    badge: (t, text) => `<span class="bp5-tag bp5-minimal ${INTENT[t] || ""}">${e(text)}</span>`,
    button: (label, attrs, o = {}) => `<button type="button" class="bp5-button bp5-small ${o.kind === "primary" ? "bp5-intent-primary" : o.kind === "danger" ? "bp5-intent-danger bp5-minimal" : ""}" ${attrs}>${e(label)}</button>`,
    link: (label, href) => `<a class="bp5-button bp5-small bp5-minimal" href="${e(href)}">${e(label)}</a>`,
    select: (attrs, opts) => `<div class="bp5-html-select bp5-minimal"><select ${attrs}>${opts.map((o) => `<option value="${e(o.value)}"${o.selected ? " selected" : ""}>${e(o.label)}</option>`).join("")}</select></div>`,
    input: (attrs) => `<input class="bp5-input bp5-small" ${attrs}>`,
    textarea: (attrs, v) => `<textarea class="bp5-input bp5-fill bp5-monospace-text" ${attrs}>${e(v)}</textarea>`,
    checkbox: (attrs, label) => `<label class="bp5-control bp5-checkbox bp5-inline" style="margin:0 8px 0 0"><input type="checkbox" ${attrs}><span class="bp5-control-indicator"></span>${e(label)}</label>`,
    note: (html, t) => `<div class="bp5-callout ${INTENT[t] || ""}" style="margin-bottom:14px">${html}</div>`,
    meter: (p, t) => `<div class="acl-meter bp5-progress-bar bp5-no-stripes bp5-no-animation ${INTENT[t] || ""}"><div class="bp5-progress-meter" style="width:${p}%"></div></div>`,
    pre: (text) => `<pre class="bp5-code-block" style="margin-top:10px;max-height:340px;overflow:auto">${e(text)}</pre>`,
    muted: (html) => `<span class="bp5-text-muted" style="font-size:12px">${html}</span>`,
    empty: (text) => `<div class="bp5-text-muted">${e(text)}</div>`,
  };
})();
