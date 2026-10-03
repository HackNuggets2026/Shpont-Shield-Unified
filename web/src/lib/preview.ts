// The static preview build (VITE_PREVIEW=1): the SPA runs without a gateway, reading GET responses that
// web/preview/snapshot.mjs recorded from a seeded gateway. Writes are switched off and the clock is set
// back to the moment the snapshot was taken, so "5 min ago" and "today" read as they did then.

export const PREVIEW = import.meta.env.VITE_PREVIEW === "1";
export const SNAPSHOT_AT = Number(import.meta.env.VITE_SNAPSHOT_AT || 0);

/** FNV-1a, 32 bit, hex. The snapshot script names its files with the same function. */
export function snapshotHash(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

// The snapshot is packed into 16 shards by the first hex digit of the hash; each is fetched once.
const shards = new Map<string, Promise<Record<string, unknown>>>();

function shard(c: string): Promise<Record<string, unknown>> {
  let p = shards.get(c);
  if (!p) {
    p = fetch(`${import.meta.env.BASE_URL}snapshot/${c}.json`)
      .then((r) => (r.ok ? r.json() : {}))
      .catch(() => ({}));
    shards.set(c, p);
  }
  return p;
}

async function load(secret: string, path: string): Promise<unknown | undefined> {
  const h = snapshotHash(`${secret}\n${path}`);
  return (await shard(h[0]))[h];
}

/**
 * A recorded response for this caller and path. A query the crawl never made falls back to the same
 * endpoint with other parameters (a later page, another filter), except for searches.
 */
export async function previewGet(secret: string, raw: string): Promise<{ ok: true; data: unknown } | { ok: false }> {
  // Normalized the way the browser reported the URL during the crawl.
  const u = new URL(raw, "http://x");
  const path = u.pathname + u.search;
  const exact = await load(secret, path);
  if (exact !== undefined) return { ok: true, data: exact };
  const [base, query = ""] = path.split("?");
  if (/(^|&)q=/.test(query)) return { ok: false };
  const any = await load(secret, `${base}?*`);
  return any === undefined ? { ok: false } : { ok: true, data: any };
}

/** Moves Date (now and new Date()) back to the snapshot time, keeping real elapsed time after load. */
export function freezeClock() {
  if (!SNAPSHOT_AT) return;
  const Real = Date;
  const offset = Real.now() - SNAPSHOT_AT * 1000;
  class PreviewDate extends Real {
    constructor(...args: unknown[]) {
      if (args.length === 0) super(Real.now() - offset);
      else super(...(args as [string | number | Date]));
    }
    static now() {
      return Real.now() - offset;
    }
  }
  globalThis.Date = PreviewDate as DateConstructor;
}
