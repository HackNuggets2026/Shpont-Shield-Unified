// Records what the console shows into static files for the GitHub Pages preview.
//
//   node preview/snapshot.mjs --gateway http://127.0.0.1:8787 --out preview/snapshot
//
// It signs in as each demo identity in a real browser (system Chrome), walks the pages, clicks every tab
// and a couple of "Next" buttons, follows drill-down links up to a cap, and saves every GET /api/*
// response. Responses are keyed by an FNV-1a hash of "<secret>\n<path>", the same hash src/lib/preview.ts uses.
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const arg = (name, dflt) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : dflt;
};
const GATEWAY = arg("gateway", "http://127.0.0.1:8787");
const OUT = arg("out", "preview/snapshot");
const MAX_PAGES = Number(arg("max-pages", 160));
const WORKERS = Number(arg("workers", 6));

const IDENTITIES = [
  { secret: "demo-admin-token", label: "Admin", start: ["/console", "/console/org", "/console/people", "/console/workflows", "/console/security", "/console/resources", "/console/requests", "/console/activity"] },
  { secret: "dev-alice-key", label: "alice (dev)", start: ["/portal", "/portal/menu", "/portal/access", "/portal/activity", "/portal/privacy"] },
  { secret: "dev-frank-key", label: "frank (quarantined)", start: ["/portal", "/portal/menu", "/portal/access", "/portal/activity", "/portal/privacy"] },
  { secret: "fin-bob-key", label: "bob (finance)", start: ["/portal", "/portal/menu", "/portal/access", "/portal/activity", "/portal/privacy"] },
  { secret: "intern-key", label: "carol (intern)", start: ["/portal", "/portal/menu", "/portal/access", "/portal/activity", "/portal/privacy"] },
];

// How many drill-down pages of each kind to visit; the rest fall back to "not in the preview".
const CAPS = [
  [/^\/console\/people\/[^/]+$/, 25],
  [/^\/console\/incidents\/[^/]+$/, 30],
  [/^\/console\/org\/team\/[^/]+$/, 41],
];

function fnv(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

const settle = async (page, ms = 900) => {
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
  await page.waitForTimeout(ms);
};

async function crawl(browser, id, files) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const exact = new Map();
  const anyByBase = new Map();
  page.on("response", async (r) => {
    const u = new URL(r.url());
    if (!u.pathname.startsWith("/api/") || r.request().method() !== "GET" || r.status() !== 200) return;
    try {
      const body = await r.text();
      JSON.parse(body);
      const path = u.pathname + u.search;
      exact.set(path, body);
      if (!anyByBase.has(u.pathname)) anyByBase.set(u.pathname, body);
    } catch {
      /* not JSON or the page moved on */
    }
  });

  await page.goto(`${GATEWAY}/login`);
  await page.getByRole("button", { name: id.label, exact: true }).click();
  await page.waitForURL(/\/(console|portal)/, { timeout: 15_000 });
  await settle(page);

  const queue = [...id.start];
  const seen = new Set(queue);
  const used = CAPS.map(() => 0);
  let visited = 0;
  let busy = 0;
  const worker = async (page) => {
    for (;;) {
      if (visited >= MAX_PAGES) return;
      const path = queue.shift();
      if (!path) {
        if (!busy) return;
        await page.waitForTimeout(300);
        continue;
      }
      busy++;
      visited++;
      try {
        await page.goto(GATEWAY + path).catch(() => {});
        await settle(page, 900);
        const tabs = await page.locator('[role="tab"]').count();
        for (let i = 0; i < tabs; i++) {
          await page.locator('[role="tab"]').nth(i).click({ timeout: 2_000 }).catch(() => {});
          await settle(page, 400);
        }
        for (let i = 0; i < 2; i++) {
          const next = page.getByRole("button", { name: /^Next/ });
          if (!(await next.count()) || !(await next.first().isEnabled())) break;
          await next.first().click({ timeout: 2_000 }).catch(() => {});
          await settle(page, 400);
        }
        if (path.startsWith("/console")) {
          const links = await page.$$eval('a[href^="/console/"]', (as) => as.map((a) => a.getAttribute("href")));
          for (const href of links) {
            const clean = href.split("#")[0];
            if (seen.has(clean)) continue;
            const cap = CAPS.findIndex(([re]) => re.test(clean.split("?")[0]));
            if (cap >= 0) {
              if (used[cap] >= CAPS[cap][1]) continue;
              used[cap]++;
            }
            seen.add(clean);
            queue.push(clean);
          }
        }
      } finally {
        busy--;
      }
      process.stdout.write(`${id.label}: ${visited} pages, ${exact.size} responses, ${queue.length} queued\n`);
    }
  };
  const pages = [page];
  for (let i = 1; i < WORKERS; i++) {
    const p = await ctx.newPage();
    p.on("response", page.listeners("response")[0]);
    pages.push(p);
  }
  await Promise.all(pages.map(worker));
  process.stdout.write("\n");
  for (const [path, body] of exact) files.set(fnv(`${id.secret}\n${path}`), body);
  for (const [base, body] of anyByBase) files.set(fnv(`${id.secret}\n${base}?*`), body);
  await ctx.close();
}

const browser = await chromium.launch({ channel: "chrome", headless: true });
const files = new Map();
const takenAt = Math.floor(Date.now() / 1000);
for (const id of IDENTITIES) await crawl(browser, id, files);
await browser.close();

// 16 shards keyed by the first hex digit, so the static host serves a handful of files.
rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });
let bytes = 0;
for (const c of "0123456789abcdef") {
  const parts = [...files].filter(([name]) => name[0] === c).map(([name, body]) => `${JSON.stringify(name)}:${body}`);
  const text = `{${parts.join(",")}}`;
  writeFileSync(join(OUT, `${c}.json`), text);
  bytes += text.length;
}
writeFileSync(join(OUT, "meta.json"), JSON.stringify({ taken_at: takenAt, files: files.size, bytes }));
console.log(`wrote ${files.size} files (${(bytes / 1e6).toFixed(1)} MB) to ${OUT}, taken at ${takenAt}`);
