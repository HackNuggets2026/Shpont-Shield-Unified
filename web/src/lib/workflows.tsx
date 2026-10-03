// Human names and fixed colors for the priced workflow menu, so "bugfix" reads as "Bug fixing" and keeps
// one hue on every chart (menu order decides the slot; unattributed traffic is always gray).
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { admin } from "../api";
import { titleCase } from "./format";

const LABELS: Record<string, string> = {
  chat_assist: "Quick questions",
  pr_review: "PR review",
  bugfix: "Bug fixing",
  ui_qa: "UI QA on simulators",
  data_analysis: "Data analysis",
  load_test: "Load tests",
  "(none)": "Unattributed",
  unlabeled: "Unlabeled",
};

/** "bugfix" -> "Bug fixing"; unknown ids are title-cased. */
export function wfLabel(id: string | null | undefined): string {
  if (!id) return "Unattributed";
  return LABELS[id] ?? titleCase(id);
}

/** Label first, id second: "Bug fixing · bugfix". */
export function WfName({ id, className }: { id: string; className?: string }) {
  const label = wfLabel(id);
  const plain = id === "(none)" || id === "unlabeled" || label.toLowerCase() === id.toLowerCase();
  return (
    <span className={className}>
      <span className="font-medium text-ink">{label}</span>
      {!plain && <span className="ml-1.5 font-mono text-[11px] text-muted">{id}</span>}
    </span>
  );
}

const SLOTS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)"];

export function workflowColors(names: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  names.forEach((n, i) => (out[n] = SLOTS[i] ?? "var(--s-other)"));
  out["(none)"] = "var(--s-other)";
  out.unlabeled = "var(--s-other)";
  return out;
}

/** One hue per workflow, in menu order, shared by every chart. */
export function useWorkflowColors(): Record<string, string> {
  const menu = useQuery({ queryKey: ["admin", "menu"], queryFn: admin.menu, staleTime: 60_000 });
  const names = menu.data?.workflows.map((w) => w.name).join("|") ?? "";
  return useMemo(() => workflowColors(names ? names.split("|") : []), [names]);
}

// ---- the menu, in plain language ---------------------------------------------------------------

export interface WfMeta {
  /** One sentence a non-engineer understands. */
  pitch: string;
  /** What a typical run looks like. */
  example: string;
  icon: "bug" | "pr" | "phone" | "chart" | "chat" | "bolt" | "other";
}

const META: Record<string, WfMeta> = {
  bugfix: { pitch: "An agent reproduces a defect, finds the cause and proposes a fix.", example: "“Checkout fails for EUR cards” → patch + test", icon: "bug" },
  pr_review: { pitch: "An agent reads a pull request, runs the tests and leaves review comments.", example: "PR #4812 → 6 comments, tests green", icon: "pr" },
  ui_qa: { pitch: "An agent drives the app on a phone simulator and checks every screen.", example: "Release 7.3 smoke test on iPhone 16", icon: "phone" },
  data_analysis: { pitch: "Questions about data answered with queries, charts and a short write-up.", example: "“Why did card churn rise in August?”", icon: "chart" },
  chat_assist: { pitch: "Quick questions, explanations and writing help on small, cheap models.", example: "“Rewrite this email to the regulator”", icon: "chat" },
  load_test: { pitch: "An agent spins up VMs and hammers a service to find its breaking point.", example: "Payments API at 3× peak for 20 min", icon: "bolt" },
};

export function wfMeta(id: string, description?: string): WfMeta {
  return META[id] ?? { pitch: description ? description[0].toUpperCase() + description.slice(1) + "." : "", example: "", icon: "other" };
}

/** Catalog resource ids in plain words. */
const RESOURCE_WORDS: Record<string, string> = {
  claude_code: "Claude Code",
  "gpt-4o": "GPT-4o",
  "gpt-4o-mini": "GPT-4o mini",
  llama: "Llama (on-prem)",
  qwen: "Qwen (on-prem)",
  mock: "Mock model",
  ci_minutes: "CI minutes",
  cloud: "Cloud bill",
  vm: "Sandbox VMs",
  simulator: "Phone simulators",
  prod_db: "Production database",
  prod_deploy: "Production deploy",
  external_email: "Email outside the company",
  "(none)": "Not attributed",
};
export const resourceWord = (r: string) => RESOURCE_WORDS[r] ?? titleCase(r);
