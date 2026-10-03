// Primer (GitHub): familiar developer UI, follows the OS light/dark setting.
(function () {
  const e = ACL.esc;
  const html = document.documentElement;
  html.setAttribute("data-color-mode", "auto");
  html.setAttribute("data-light-theme", "light");
  html.setAttribute("data-dark-theme", "dark_dimmed");
  document.body.className = "color-bg-default color-fg-default";
  ACL.css(["https://cdn.jsdelivr.net/npm/@primer/css@21.5.1/dist/primer.css"], `
    body { margin: 0; }
    .acl-kv { font-size: 12px; }
    .acl-big { font-size: 24px; font-weight: 600; }
    .Box-body table { width: 100%; }
    .markdown-body table { display: table; width: 100%; font-size: 12px; margin: 0; }
    .markdown-body td, .markdown-body th { vertical-align: top; }
    .Header .form-select { color: #fff; background-color: transparent; border-color: rgba(255,255,255,.25); }
    .Header .color-fg-muted { color: rgba(255,255,255,.7) !important; }`);
  const LABEL = { ok: "Label--success", warn: "Label--attention", bad: "Label--danger", info: "Label--accent", neutral: "Label--secondary" };
  const FLASH = { ok: "flash-success", warn: "flash-warn", bad: "flash-error", info: "", neutral: "" };
  const FG = { ok: "color-fg-success", warn: "color-fg-attention", bad: "color-fg-danger", info: "color-fg-accent" };
  const BG = { ok: "color-bg-success-emphasis", warn: "color-bg-attention-emphasis", bad: "color-bg-danger-emphasis", info: "color-bg-accent-emphasis" };
  window.ACL_THEME = {
    shell: (p) => `
      <div class="Header">
        <div class="Header-item"><span class="Header-link f4 text-bold">AI Control Layer</span></div>
        ${ACL.nav().map((n) => `<div class="Header-item"><a class="Header-link${n.active ? " text-underline" : ""}" href="${n.href}">${e(n.label)}</a></div>`).join("")}
        <div class="Header-item Header-item--full"></div>
        <div class="Header-item acl-tools">${p.tools || ""}${ACL.themePicker("primer")}</div>
      </div>
      <div class="container-xl px-3 py-4">
        <div class="Subhead"><h2 class="Subhead-heading">${e(p.title)}</h2><div class="Subhead-description text-mono f6">${p.meta}</div></div>
        ${p.notice || ""}${p.body}</div>`,
    card: (title, body, o = {}) => `<div class="Box">
        <div class="Box-header d-flex flex-items-center"><h3 class="Box-title flex-auto">${e(title)}</h3><div class="acl-tools">${o.tools || ""}</div></div>
        <div class="Box-body">${body}</div></div>`,
    kpis: (items) => `<div class="acl-kpis">${items.map((k) => `<div><div class="acl-kv color-fg-muted">${e(k.label)}</div>
        <div class="acl-big ${FG[k.tone] || ""}">${e(k.value)}</div></div>`).join("")}</div>`,
    table: (head, rows, o = {}) => `<div class="markdown-body acl-scroll"><table>
        ${o.bare ? "" : `<thead><tr>${head.map((h, i) => `<th class="${(o.num || []).includes(i) ? "acl-num" : ""}">${e(h)}</th>`).join("")}</tr></thead>`}
        <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${(o.num || []).includes(i) ? "acl-num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`,
    badge: (t, text) => `<span class="Label ${LABEL[t] || LABEL.neutral}">${e(text)}</span>`,
    button: (label, attrs, o = {}) => `<button type="button" class="btn btn-sm ${o.kind === "primary" ? "btn-primary" : o.kind === "danger" ? "btn-danger" : ""}" ${attrs}>${e(label)}</button>`,
    link: (label, href) => `<a class="btn btn-sm btn-invisible" href="${e(href)}">${e(label)}</a>`,
    select: (attrs, opts) => `<select class="form-select select-sm" ${attrs}>${opts.map((o) => `<option value="${e(o.value)}"${o.selected ? " selected" : ""}>${e(o.label)}</option>`).join("")}</select>`,
    input: (attrs) => `<input class="form-control input-sm" ${attrs}>`,
    textarea: (attrs, v) => `<textarea class="form-control width-full input-monospace" ${attrs}>${e(v)}</textarea>`,
    checkbox: (attrs, label) => `<label class="f6 mr-2"><input type="checkbox" ${attrs}> ${e(label)}</label>`,
    note: (html, t) => `<div class="flash ${FLASH[t] || ""} mb-3">${html}</div>`,
    meter: (p, t) => `<span class="Progress acl-meter"><span class="Progress-item ${BG[t] || BG.info}" style="width:${p}%"></span></span>`,
    pre: (text) => `<pre class="color-bg-subtle p-3 f6 text-mono mt-2" style="overflow:auto;max-height:340px;border-radius:6px">${e(text)}</pre>`,
    muted: (html) => `<span class="color-fg-muted f6">${html}</span>`,
    empty: (text) => `<div class="color-fg-muted f6">${e(text)}</div>`,
  };
})();
