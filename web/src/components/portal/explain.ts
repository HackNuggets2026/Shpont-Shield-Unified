// Plain-language explanations for the employee portal: who decided, what a policy reason means, what an
// admin action was. Employees should never have to decode "access_grant/grant_required" or "auto:detections".
import type { AdminAction } from "../../api";
import { minutes, titleCase } from "../../lib/format";

/** "auto:detections" -> "automatic detection"; "dana" -> "dana (admin)". */
export function actorLabel(by: string | null | undefined): string {
  if (!by) return "an administrator";
  if (by === "auto:detections") return "automatic detection";
  if (by.startsWith("auto:")) return `automatic rule (${by.slice(5)})`;
  if (by === "system" || by === "gateway") return "the gateway";
  return `${by} (admin)`;
}

export const isAutomatic = (by: string | null | undefined) => !!by && (by.startsWith("auto:") || by === "system");

/** "risk score 118.9 reached quarantine (80)" -> friendlier sentence; other reasons pass through. */
export function statusReason(reason: string | null | undefined): string {
  if (!reason) return "No reason was recorded.";
  const m = /^risk score ([\d.]+) reached (\w+) \(([\d.]+)\)$/.exec(reason.trim());
  if (m) {
    const [, score, level, threshold] = m;
    const what = level === "quarantine" ? "pauses access automatically" : level === "tighten" ? "reduces the daily budget automatically" : "alerts security";
    return `Automatic detections raised your risk score to ${Math.round(Number(score))}. At ${Math.round(Number(threshold))} the policy ${what} until someone reviews it.`;
  }
  return reason;
}

const NAMES: Record<string, string> = {
  github_token: "a GitHub token",
  aws_access_key: "an AWS access key",
  aws_secret_key: "an AWS secret key",
  private_key: "a private key",
  slack_token: "a Slack token",
  openai_key: "an API key",
  anthropic_key: "an API key",
  jwt: "a login token (JWT)",
  password: "a password",
  email: "an email address",
  phone: "a phone number",
  iban: "a bank account number",
  credit_card: "a card number",
  card: "a card number",
  ssn: "a national ID number",
  ip_address: "an IP address",
};
const name = (n: string) => NAMES[n] ?? n.replace(/_/g, " ");
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

export interface Explained {
  /** One short sentence an employee understands. */
  title: string;
  /** What to do about it, if anything. */
  hint?: string;
  /** The raw policy code, for when they ask IT. */
  code: string;
  /** Any extra detail the policy attached. */
  extra?: string;
}

/**
 * Turns a policy reason like "secrets/github_token", "pii/email:redact" or
 * "access_grant/grant_required: prod_db grant is for workflow 'bugfix'" into plain language.
 */
export function explainReason(reason: string | null | undefined, decision?: string | null): Explained {
  const raw = (reason ?? "").trim();
  if (!raw) return { title: decision === "redact" ? "Sensitive details were masked" : "Stopped by policy", code: "" };
  const m = /^([a-z_]+)\/([a-z0-9_]+)(?::(?:block|redact|warn|log|allow))?(?::\s*(.*))?$/i.exec(raw);
  if (!m) return { title: raw, code: "" };
  const [, cat, sub, extra] = m;
  const code = `${cat}/${sub}`;
  const out = (title: string, hint?: string): Explained => ({ title, hint, code, extra: cleanExtra(extra, sub) });
  switch (cat) {
    case "secrets":
      return out(`Looked like it contained ${name(sub)}, so it was not sent`, "If it was a real credential, rotate it. Use a secrets manager reference instead of pasting keys.");
    case "pii":
      return decision === "block"
        ? out(`Contained ${name(sub)}, so it was not sent`)
        : out(`${cap(name(sub))} was masked before reaching the model`, "Nothing to do; the rest of your request went through.");
    case "prompt_injection":
      return out("Looked like an instruction trying to override the AI's rules", "Often caused by pasting text from an untrusted web page or document.");
    case "data_exfiltration":
      return out("Looked like moving company data to an outside destination", "Use approved channels to share data outside the company.");
    case "confidential_output":
      return out("Asked for confidential customer data", "Customer records stay in the systems built for them.");
    case "harmful_request":
      return out(`Looked like a request for ${sub.replace(/_/g, " ")}, which is outside acceptable use`);
    case "access_grant":
      if (sub === "grant_required")
        return out("Needs time-boxed access you did not have at that moment", "Request access on the Access page; an admin approves it.");
      if (sub === "quarantined") return out("Access grants are suspended while your access is paused");
      if (sub === "revoked") return out("Your access was revoked");
      return out(titleCase(sub));
    case "tool_access":
      if (sub === "tool_not_allowed") return out("Your role cannot use this tool");
      if (sub === "quarantined") return out("Only read and search tools work while your access is paused");
      if (sub === "irreversible_action") return out("Irreversible action: needs an approved grant or a human sign-off");
      return out(titleCase(sub));
    case "model_allowlist":
      return out(sub === "model_missing" ? "The request did not name a model" : "This model is not on the approved list", "See the Workflow menu for the models you can use.");
    case "budget":
      if (sub === "run_budget") return out("This task went over its per-run limit", "Split the work or ask for a larger workflow.");
      if (sub === "rate_limit") return out("Too many requests per minute; slow down and retry");
      if (sub === "runaway_loop") return out("The same call repeated in a loop, so it was stopped", "Check your agent or script for a retry loop.");
      return out("Your daily budget was used up", "It resets at midnight UTC. You can request more on the Workflow menu.");
    case "resources":
    case "resource":
      if (sub === "budget_exhausted") return out("Your daily budget was used up", "It resets at midnight UTC.");
      if (sub === "concurrency_limit") return out("You already had the maximum number of these running", "Stop one you are done with on the Home page.");
      if (sub === "resource_not_in_workflow" || sub === "not_in_workflow") return out("That workflow does not include this resource");
      return out(titleCase(sub));
    case "workflow":
      if (sub === "unlabeled") return out("The call had no workflow label", "Send the x-acl-workflow header.");
      if (sub === "not_on_menu") return out("That workflow is not on the menu");
      if (sub === "needs_approval") return out("That workflow needs admin approval first", "Request it on the Workflow menu.");
      if (sub === "model_not_in_workflow") return out("That workflow does not use this model");
      if (sub === "tool_not_in_workflow") return out("That workflow does not use this tool");
      return out(titleCase(sub));
    case "auth":
      return out(sub === "revoked" ? "Your access was revoked" : "The API key was missing or unknown");
    case "signatures":
      return out("Matched a known attack pattern");
    default:
      return out(`${titleCase(cat)}: ${sub.replace(/_/g, " ")}`);
  }
}

/** Keeps the useful part of a policy's free-text detail; drops counters, scores and restatements. */
function cleanExtra(extra: string | undefined, sub: string): string | undefined {
  let x = (extra ?? "").trim();
  if (!x) return undefined;
  x = x.replace(new RegExp(`^${sub}\\s+`), "");
  const score = /\bp=([\d.]+)(?:\s+conf=[\d.]+)?/.exec(x);
  if (score) {
    const rest = x.replace(score[0], "").trim();
    const conf = `detector was ${Math.round(Number(score[1]) * 100)}% sure`;
    return rest ? `${rest} (${conf})` : cap(conf);
  }
  if (/^\d+ x \w+$/.test(x) || /is quarantined; only/.test(x)) return undefined;
  return x;
}

/** What a detection rule means, in a sentence. */
export const RULE_TEXT: Record<string, { title: string; text: string }> = {
  probing: { title: "Repeated blocked attempts", text: "Several requests in a short time were blocked, which can look like someone probing the limits." },
  secret_paste: { title: "Credentials sent to AI tools", text: "Secrets such as API keys were pasted into prompts more than once." },
  exfiltration: { title: "Possible data exfiltration", text: "Requests that looked like moving confidential data out, together with unusually heavy use." },
  usage_spike: { title: "Unusual spike in usage", text: "Far more tokens in a short window than your usual pattern." },
  new_client: { title: "Key used from a new place", text: "Your API key was used from a client or network not seen before, which can mean a leaked key." },
  tool_drift: { title: "First use of a sensitive tool", text: "A sensitive tool was used for the first time on your key." },
  off_hours: { title: "Activity outside working hours", text: "Heavy use outside the usual working hours." },
  zombie_resource: { title: "Forgotten running resource", text: "A simulator or VM kept running idle in your name." },
  unlabeled_resource: { title: "Resource without a task label", text: "A paid resource ran without saying which task it was for." },
  permission_bypass: { title: "Permission prompts bypassed", text: "Claude Code ran with permission prompts turned off." },
  unapproved_mcp_server: { title: "Unapproved MCP server", text: "A tool server not on the approved list was used." },
  rejected_edit_storm: { title: "Many rejected edits", text: "Lots of AI-proposed edits were rejected in a row." },
};
export const ruleTitle = (r: string) => RULE_TEXT[r]?.title ?? titleCase(r);

/** An admin or automatic action about me, in plain words. */
/**
 * The change an action made. Detections log the flat patch; admin writes log the whole overlay patch
 * ({principals: {pid: {...}}}), and grants carry the full grant list, so unwrap both.
 */
export function actionPatch(a: AdminAction): Record<string, unknown> {
  let d = (a.detail ?? {}) as Record<string, unknown>;
  const ps = d.principals as Record<string, unknown> | undefined;
  if (ps && typeof ps === "object") d = ((ps[a.target] ?? Object.values(ps)[0]) ?? {}) as Record<string, unknown>;
  const grants = d.grants as Record<string, unknown>[] | undefined;
  if (Array.isArray(grants) && grants.length && !d.resource) {
    const g = grants[grants.length - 1];
    const mins = typeof g.expires === "number" && typeof g.granted_at === "number" ? (g.expires - g.granted_at) / 60 : undefined;
    d = { ...d, resource: g.resource, minutes: mins };
  }
  return d;
}

export function myActionLabel(a: AdminAction): string {
  const d = actionPatch(a);
  switch (a.action) {
    case "recommend_quarantine":
      return "Detection suggested pausing your access (not applied)";
    case "recommend_tighten":
      return "Detection suggested a lower budget (not applied)";
    case "quarantine":
      return "Paused your AI access";
    case "tighten":
      return typeof d.budget_scale === "number" ? `Reduced your daily budget to ${Math.round(d.budget_scale * 100)}%` : "Reduced your daily budget";
    case "restrict":
      if (d.status === "quarantined") return "Paused your AI access";
      if (d.status === "revoked") return "Revoked your AI access";
      if (d.status === "active") return "Restored your access";
      if (typeof d.budget_scale === "number") return `Set your daily budget to ${Math.round(d.budget_scale * 100)}%`;
      if (d.approved_workflows) return "Changed your approved workflows";
      return "Changed your access";
    case "clear_restrictions":
      return "Lifted your restrictions";
    case "view_events":
      return "Opened the content of your events";
    case "grant":
      return `Granted ${String(d.resource ?? "access")}${typeof d.minutes === "number" ? ` for ${minutes(Math.round(d.minutes))}` : ""}`;
    case "revoke_grant":
      return "Ended a grant early";
    case "approve_request":
      if (typeof d.resource === "string") return `Approved your request: ${d.resource}${typeof d.minutes === "number" ? ` for ${minutes(Math.round(d.minutes))}` : ""}`;
      if (typeof d.budget_scale === "number") return `Approved more budget: ${Math.round(d.budget_scale * 100)}% of normal`;
      if (Array.isArray(d.approved_workflows)) return "Approved your workflow request";
      return "Approved your request";
    case "deny_request":
      return "Declined your request";
    case "incident_resolved":
      return "Closed an incident about you as resolved";
    case "incident_dismissed":
      return "Dismissed an incident about you";
    case "incident_acknowledged":
      return "Acknowledged an incident about you";
    case "release_lease":
      return "Stopped a resource running in your name";
    default:
      return titleCase(a.action);
  }
}

export type ActionKind = "restrict" | "view" | "positive" | "neutral";
export function myActionKind(a: AdminAction): ActionKind {
  if (a.action === "view_events") return "view";
  if (a.action.startsWith("recommend_")) return "neutral";
  const d = actionPatch(a);
  if (/quarantine|tighten|revoke|deny/.test(a.action) || (a.action === "restrict" && d.status !== "active")) return "restrict";
  if (/grant|approve|clear|resolved|dismissed/.test(a.action) || (a.action === "restrict" && d.status === "active")) return "positive";
  return "neutral";
}
