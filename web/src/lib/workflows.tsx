// Human names and fixed colors for the priced workflow menu, so "bugfix" reads as "Bug fixing" and keeps
// one hue on every chart (menu order decides the slot; unattributed traffic is always gray).
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { admin } from "../api";
import { titleCase } from "./format";

const LABELS: Record<string, string> = {
  chat_assist: "Chat assist",
  pr_review: "PR review",
  bugfix: "Bug fixing",
  ui_qa: "UI QA",
  data_analysis: "Data analysis",
  load_test: "Load testing",
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
