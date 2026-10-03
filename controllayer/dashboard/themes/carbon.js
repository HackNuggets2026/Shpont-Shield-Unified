// Carbon (IBM): flat enterprise grid, IBM Plex, square corners.
(function () {
  const e = ACL.esc;
  ACL.css([
    "https://cdn.jsdelivr.net/npm/carbon-components@10.58.15/css/carbon-components.min.css",
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;600&display=swap",
  ], `
    body { background: #f4f4f4; margin: 0; font-family: "IBM Plex Sans", sans-serif; }
    .acl-main { padding: 72px 32px 48px; }
    .acl-title { font-size: 32px; font-weight: 300; margin: 0; }
    .acl-meta { font: 12px "IBM Plex Mono", monospace; color: #525252; margin: 6px 0 20px; }
    .acl-kv { font-size: 12px; color: #525252; }
    .acl-big { font: 300 28px "IBM Plex Mono", monospace; }
    .acl-muted { font-size: 12px; color: #6f6f6f; }
    .acl-grid { --acl-gap: 2px; }
    .bx--header .bx--select-input { background: #262626; color: #f4f4f4; border-bottom-color: #6f6f6f; }
    .bx--header .acl-tools { margin-left: auto; padding-right: 16px; color: #c6c6c6; }
    .acl-meter { height: 8px; background: #e0e0e0; }
    .acl-meter > i { display: block; height: 100%; }
    .acl-pre { font: 12px "IBM Plex Mono", monospace; background: #f4f4f4; padding: 12px; margin-top: 12px; max-height: 340px; overflow: auto; }
    .bx--data-table td { vertical-align: top; }`);
  const TAG = { ok: "bx--tag--green", warn: "bx--tag--magenta", bad: "bx--tag--red", info: "bx--tag--blue", neutral: "bx--tag--cool-gray" };
  const NOTE = { ok: "success", warn: "warning", bad: "error", info: "info", neutral: "info" };
  const BAR = { ok: "#24a148", warn: "#f1c21b", bad: "#da1e28", info: "#0f62fe" };
  window.ACL_THEME = {
    shell: (p) => `
      <header class="bx--header" aria-label="AI Control Layer">
        <a class="bx--header__name" href="#"><span class="bx--header__name--prefix">ACME</span>&nbsp;[AI Control Layer]</a>
        <nav class="bx--header__nav" aria-label="Panels"><ul class="bx--header__menu-bar">
          ${ACL.nav().map((n) => `<li><a class="bx--header__menu-item" href="${n.href}"${n.active ? ' aria-current="page"' : ""}><span class="bx--text-truncate--end">${e(n.label)}</span></a></li>`).join("")}
        </ul></nav>
        <div class="acl-tools">${p.tools || ""}${ACL.themePicker("carbon")}</div>
      </header>
      <main class="acl-main"><h1 class="acl-title">${e(p.title)}</h1><div class="acl-meta">${p.meta}</div>${p.notice || ""}${p.body}</main>`,
    card: (title, body, o = {}) => `<div class="bx--tile" style="padding:16px 16px 20px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:12px">
          <h4 style="font-size:16px;font-weight:600;margin:0">${e(title)}</h4><div class="acl-tools">${o.tools || ""}</div></div>${body}</div>`,
    kpis: (items) => `<div class="acl-kpis">${items.map((k) => `<div style="border-left:3px solid ${BAR[k.tone] || "#8d8d8d"};padding-left:12px">
        <div class="acl-kv">${e(k.label)}</div><div class="acl-big">${e(k.value)}</div></div>`).join("")}</div>`,
    table: (head, rows, o = {}) => `<div class="bx--data-table-container acl-scroll" style="padding:0">
        <table class="bx--data-table bx--data-table--compact${o.bare ? "" : " bx--data-table--zebra"}">
        ${o.bare ? "" : `<thead><tr>${head.map((h, i) => `<th class="${(o.num || []).includes(i) ? "acl-num" : ""}"><span class="bx--table-header-label">${e(h)}</span></th>`).join("")}</tr></thead>`}
        <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${(o.num || []).includes(i) ? "acl-num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`,
    badge: (t, text) => `<span class="bx--tag ${TAG[t] || TAG.neutral}" style="margin:0 2px 0 0">${e(text)}</span>`,
    button: (label, attrs, o = {}) => `<button type="button" class="bx--btn bx--btn--sm ${o.kind === "primary" ? "bx--btn--primary" : o.kind === "danger" ? "bx--btn--danger--ghost" : "bx--btn--tertiary"}" ${attrs}>${e(label)}</button>`,
    link: (label, href) => `<a class="bx--btn bx--btn--ghost bx--btn--sm" href="${e(href)}">${e(label)}</a>`,
    select: (attrs, opts) => `<div class="bx--select bx--select--inline" style="display:inline-block"><div class="bx--select-input__wrapper">
        <select class="bx--select-input" ${attrs}>${opts.map((o) => `<option value="${e(o.value)}"${o.selected ? " selected" : ""}>${e(o.label)}</option>`).join("")}</select></div></div>`,
    input: (attrs) => `<input class="bx--text-input bx--text-input--sm" ${attrs}>`,
    textarea: (attrs, v) => `<textarea class="bx--text-area" style="width:100%;font-family:'IBM Plex Mono',monospace" ${attrs}>${e(v)}</textarea>`,
    checkbox: (attrs, label) => { const id = ACL.uid(); return `<span class="bx--checkbox-wrapper" style="display:inline-flex;margin-right:12px">
        <input type="checkbox" class="bx--checkbox" id="${id}" ${attrs}><label for="${id}" class="bx--checkbox-label">${e(label)}</label></span>`; },
    note: (html, t) => `<div class="bx--inline-notification bx--inline-notification--${NOTE[t] || "info"} bx--inline-notification--low-contrast" style="max-width:none;margin:0 0 16px">
        <div class="bx--inline-notification__details"><div class="bx--inline-notification__text-wrapper"><div class="bx--inline-notification__subtitle">${html}</div></div></div></div>`,
    meter: (p, t) => `<div class="acl-meter"><i style="width:${p}%;background:${BAR[t] || BAR.info}"></i></div>`,
    pre: (text) => `<pre class="acl-pre">${e(text)}</pre>`,
    muted: (html) => `<span class="acl-muted">${html}</span>`,
    empty: (text) => `<div class="acl-muted">${e(text)}</div>`,
  };
})();
