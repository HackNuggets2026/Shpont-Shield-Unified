// Minimal browser surface for running the dashboard scripts in QuickJS (tests/jsconsole.py).
var window = globalThis;
var __handlers = {}, __timers = [], __errors = [];
var console = { log: function () {}, warn: function () {}, error: function (m) { __errors.push(String(m)); } };
function El(tag) {
  this.tagName = String(tag).toUpperCase(); this.dataset = {}; this.style = {}; this.children = [];
  this.hidden = false; this.textContent = ""; this.className = ""; this._html = "";
  var set = {};
  this.classList = { add: function (c) { set[c] = 1; }, remove: function (c) { delete set[c]; }, contains: function (c) { return !!set[c]; } };
}
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.querySelector = function () { return null; };
El.prototype.getBoundingClientRect = function () { return { left: 0, top: 0, width: 10, height: 10 }; };
Object.defineProperty(El.prototype, "innerHTML", { get: function () { return this._html; }, set: function (v) { this._html = String(v); } });
var document = {
  body: new El("body"), _root: new El("div"), activeElement: null, hidden: false, title: "",
  getElementById: function (id) { return id === "acl-root" ? this._root : null; },
  addEventListener: function (t, f) { (__handlers[t] = __handlers[t] || []).push(f); },
  createElement: function (t) { return new El(t); },
};
window.innerWidth = 1280;
window.addEventListener = function (t, f) { document.addEventListener("window:" + t, f); };
var location = { pathname: "/", search: "" };
function __go(url) { url = String(url); var i = url.indexOf("?"); location.pathname = i < 0 ? url : url.slice(0, i); location.search = i < 0 ? "" : url.slice(i); }
var history = { pushState: function (s, t, u) { __go(u); }, replaceState: function (s, t, u) { __go(u); } };
var setInterval = function () { return 0; };
var setTimeout = function (f) { __timers.push(f); return __timers.length; };
var clearTimeout = function () {};
function URLSearchParams(s) {
  this.list = []; s = String(s || "").replace(/^\?/, "");
  if (s) s.split("&").forEach(function (part) {
    var i = part.indexOf("="), k = i < 0 ? part : part.slice(0, i), v = i < 0 ? "" : part.slice(i + 1);
    this.list.push([decodeURIComponent(k.replace(/\+/g, " ")), decodeURIComponent(v.replace(/\+/g, " "))]);
  }, this);
}
URLSearchParams.prototype.get = function (k) { var e = this.list.find(function (x) { return x[0] === k; }); return e ? e[1] : null; };
URLSearchParams.prototype[Symbol.iterator] = function () { return this.list[Symbol.iterator](); };
function fetch(path, opts) {
  opts = opts || {};
  var r = JSON.parse(__fetch(opts.method || "GET", String(path), opts.body || "", JSON.stringify(opts.headers || {})));
  return Promise.resolve({ ok: r.status < 400, status: r.status, json: function () {
    try { return Promise.resolve(JSON.parse(r.body)); } catch (e) { return Promise.reject(e); } } });
}
function __fire(type, ev) { (__handlers[type] || []).forEach(function (f) { f(ev); }); }
function __el(tag, attrs, dataset) {
  return { tagName: tag, disabled: false, dataset: dataset || {}, value: (attrs || {}).value,
    getAttribute: function (k) { return (attrs || {})[k] === undefined ? null : attrs[k]; } };
}
function __target(match) { return { closest: function (sel) { return match(sel); } }; }
