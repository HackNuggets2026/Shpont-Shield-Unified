// terminal.css: monospace console for people who live in a shell.
(function () {
  const e = ACL.esc;
  document.body.className = "terminal";
  ACL.css([
    "https://cdn.jsdelivr.net/npm/terminal.css@0.7.5/dist/terminal.min.css",
    "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap",
  ], `
    :root { --global-font-size: 14px; --global-line-height: 1.5em; --global-space: 10px;
      --font-stack: "JetBrains Mono", Menlo, monospace; --mono-font-stack: "JetBrains Mono", Menlo, monospace;
      --background-color: #0d1014; --page-width: 100%; --font-color: #d3dae3; --invert-font-color: #0d1014;
      --primary-color: #46d39a; --secondary-color: #7d8896; --error-color: #ff6b6b; --progress-bar-background: #1e252e;
      --progress-bar-fill: #46d39a; --code-bg-color: #141a21; --input-style: solid; --display-h1-decoration: none; }
    body { margin: 0; }
    .acl-main { padding: 8px 24px 48px; }
    .terminal-card { margin: 0; }
    .terminal-card > header { text-transform: lowercase; }
    .acl-kv { color: var(--secondary-color); font-size: 12px; }
    .acl-big { font-size: 22px; font-weight: 600; }
    .acl-muted { color: var(--secondary-color); font-size: 12px; }
    table { font-size: 12px; } table td { vertical-align: top; }
    select, input, textarea { font-family: var(--font-stack); background: var(--code-bg-color); color: var(--font-color); }
    .terminal-nav { padding: 12px 24px; border-bottom: 1px solid #1e252e; margin-bottom: 16px; }
    .terminal-menu ul { display: flex; gap: 16px; align-items: center; margin: 0; }
    .acl-meter { height: 8px; background: var(--progress-bar-background); }
    .acl-meter > i { display: block; height: 100%; }`);
  const COLOR = { ok: "#46d39a", warn: "#f5c06f", bad: "#ff6b6b", info: "#6cb6ff", neutral: "#7d8896" };
  const ALERT = { bad: "terminal-alert-error", info: "terminal-alert-primary", ok: "terminal-alert-primary" };
  window.ACL_THEME = {
    shell: (p) => `
      <div class="terminal-nav">
        <div class="logo terminal-prompt"><span>acl@gateway:${e(ACL.PAGE)}</span></div>
        <nav class="terminal-menu"><ul>
          ${ACL.nav().map((n) => `<li><a class="${n.active ? "active" : ""}" href="${n.href}">${e(n.label.toLowerCase())}</a></li>`).join("")}
          <li class="acl-tools">${p.tools || ""}${ACL.themePicker("terminal")}</li>
        </ul></nav>
      </div>
      <main class="acl-main"><div class="acl-muted">$ ${p.meta}</div><br>${p.notice || ""}${p.body}</main>`,
    card: (title, body, o = {}) => `<div class="terminal-card"><header>${e(title)}</header>
        <div>${o.tools ? `<div class="acl-tools" style="margin-bottom:8px">${o.tools}</div>` : ""}${body}</div></div>`,
    kpis: (items) => `<div class="acl-kpis">${items.map((k) => `<div><div class="acl-kv">${e(k.label.toLowerCase())}</div>
        <div class="acl-big" style="color:${COLOR[k.tone] || "inherit"}">${e(k.value)}</div></div>`).join("")}</div>`,
    table: (head, rows, o = {}) => `<div class="acl-scroll"><table>
        ${o.bare ? "" : `<thead><tr>${head.map((h, i) => `<th class="${(o.num || []).includes(i) ? "acl-num" : ""}">${e(h)}</th>`).join("")}</tr></thead>`}
        <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${(o.num || []).includes(i) ? "acl-num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`,
    badge: (t, text) => `<span style="color:${COLOR[t] || COLOR.neutral};white-space:nowrap">[${e(text)}]</span>`,
    button: (label, attrs, o = {}) => `<button type="button" class="btn btn-small ${o.kind === "primary" ? "btn-primary" : o.kind === "danger" ? "btn-error btn-ghost" : "btn-default btn-ghost"}" ${attrs}>${e(label.toLowerCase())}</button>`,
    link: (label, href) => `<a href="${e(href)}">${e(label.toLowerCase())}</a>`,
    select: (attrs, opts) => `<select ${attrs} style="width:auto;display:inline-block">${opts.map((o) => `<option value="${e(o.value)}"${o.selected ? " selected" : ""}>${e(o.label)}</option>`).join("")}</select>`,
    input: (attrs) => `<input ${attrs}>`,
    textarea: (attrs, v) => `<textarea ${attrs} style="width:100%">${e(v)}</textarea>`,
    checkbox: (attrs, label) => `<label style="margin-right:10px;white-space:nowrap"><input type="checkbox" ${attrs}> ${e(label)}</label>`,
    note: (html, t) => `<div class="terminal-alert ${ALERT[t] || ""}">${html}</div>`,
    meter: (p, t) => `<div class="acl-meter"><i style="width:${p}%;background:${COLOR[t] || COLOR.info}"></i></div>`,
    pre: (text) => `<pre style="max-height:340px;overflow:auto">${e(text)}</pre>`,
    muted: (html) => `<span class="acl-muted">${html}</span>`,
    empty: (text) => `<div class="acl-muted"># ${e(text.toLowerCase())}</div>`,
  };
})();
