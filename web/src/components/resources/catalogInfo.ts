// Human wording for catalog entries: prices, usage volumes, and which workflows reach a resource.
import type { CatalogItem, Workflow } from "../../api";
import { num, tokens, usd } from "../../lib/format";

export type ResourceClass = "consumable" | "leasable" | "access_grant";
export const CLASSES: ResourceClass[] = ["consumable", "leasable", "access_grant"];

export const CLASS_INFO: Record<ResourceClass, { title: string; short: string; long: string }> = {
  consumable: {
    title: "Consumable",
    short: "Used up and billed per token, minute or dollar.",
    long: "Models, Claude Code usage, CI minutes, the cloud bill. Metered per unit and limited by budgets per person, team and workflow.",
  },
  leasable: {
    title: "Leasable",
    short: "Held for a while, billed per minute held.",
    long: "Simulators and sandbox VMs an agent starts and stops. Concurrency caps apply; a lease idle past its limit becomes a zombie and is reclaimed.",
  },
  access_grant: {
    title: "Access grant",
    short: "Needs time-boxed permission from an admin.",
    long: "Production data, deploys, external email. An agent may act only under a live grant (RFC 9396-style: resource, actions, expiry) or, where allowed, inside a workflow that includes it.",
  },
};

const unitWord = (u: string) => (u === "minute" ? "minute" : u === "use" ? "use" : u);

/** A unit price: "$0.008", "$0.0004", "$2.50" (no padded zeros on sub-cent prices). */
export function unitUsd(v: number): string {
  if (v >= 0.01) return usd(v);
  return "$" + v.toFixed(6).replace(/0+$/, "");
}

/** "$2.50 / 1M input · $10.00 / 1M output tokens", "$0.01 / minute", "billed at cost". */
export function priceLabel(r: CatalogItem): string {
  const p = r.price;
  const parts: string[] = [];
  if (p.usd_per_1m_input || p.usd_per_1m_output) {
    parts.push(`${usd(p.usd_per_1m_input)} / 1M input · ${usd(p.usd_per_1m_output)} / 1M output tokens`);
  }
  if (p.usd_per_compute_second) parts.push(`${unitUsd(p.usd_per_compute_second)} / GPU-second`);
  if (p.usd_per_unit) {
    if (r.unit === "usd") parts.push(r.meter === "billing_export" ? "billed at cost (FOCUS import)" : "billed at cost");
    else parts.push(`${unitUsd(p.usd_per_unit)} / ${unitWord(r.unit)}`);
  }
  if (parts.length) return parts.join(" · ");
  if (r.class === "access_grant") return "no metered cost";
  if (r.meter === "telemetry") return "subscription · cost as reported by telemetry";
  return "free";
}

export const METER_LABEL: Record<string, string> = {
  gateway: "gateway",
  mcp_tools: "MCP tools",
  telemetry: "telemetry",
  billing_export: "billing export",
  report_api: "report API",
};

/** The 30-day volume in the resource's own unit. */
export function usageLabel(r: CatalogItem): string {
  const u = r.usage;
  const bits: string[] = [];
  if (u.tokens) bits.push(`${tokens(u.tokens)} tokens`);
  if (u.minutes) bits.push(`${num(u.minutes)} min${r.class === "leasable" ? " held" : ""}`);
  if (u.requests) bits.push(`${num(u.requests)} ${r.class === "access_grant" ? "uses" : "calls"}`);
  if (!bits.length && u.usd > 0) return r.meter === "billing_export" ? "imported from the bill" : "cost only";
  return bits.join(" · ") || "no usage";
}

function globRe(glob: string): RegExp {
  return new RegExp("^" + glob.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$");
}
/** Two glob patterns overlap if either one matches the other literally (good enough for catalog patterns). */
function overlaps(a: string, b: string): boolean {
  return a === b || globRe(a).test(b) || globRe(b).test(a);
}

/**
 * Workflows that reach this resource: the server lists the ones that name it under `resources`; a workflow that
 * allows one of its models or tools reaches it too (the server does not count those yet).
 */
export function workflowsFor(r: CatalogItem, menu: Workflow[] | undefined): { name: string; via: "resource" | "model" | "tool" }[] {
  const out = new Map<string, "resource" | "model" | "tool">();
  for (const w of r.workflows) out.set(w, "resource");
  for (const w of menu ?? []) {
    if (out.has(w.name)) continue;
    if (r.models.length && w.models.some((m) => r.models.some((rm) => overlaps(m, rm)))) out.set(w.name, "model");
    else if (r.tools.length && w.tools.some((t) => r.tools.some((rt) => overlaps(t, rt)))) out.set(w.name, "tool");
  }
  return [...out].map(([name, via]) => ({ name, via }));
}

export function grantLength(m: number | undefined): string {
  if (!m) return "—";
  return m >= 60 ? `${+(m / 60).toFixed(1)} h` : `${m} min`;
}
