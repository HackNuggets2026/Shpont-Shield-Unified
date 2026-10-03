// Consistent pills for every enum the API returns. Color never carries meaning alone: each pill has a label.
import type { ReactNode } from "react";
import { Pill, type Tone } from "./ui";
import { IconActivity, IconGlobe, IconPlug, IconRadar, IconReceipt, IconTerminal, IconBox } from "./icons";
import { titleCase } from "../lib/format";

export const severityTone = (s: string | null | undefined): Tone =>
  s === "high" ? "bad" : s === "medium" ? "serious" : s === "low" ? "warn" : "neutral";

export function SeverityPill({ severity }: { severity: string | null | undefined }) {
  return (
    <Pill tone={severityTone(severity)} dot>
      {severity ?? "info"}
    </Pill>
  );
}

export const statusTone = (s: string): Tone =>
  s === "active" ? "good" : s === "quarantined" ? "bad" : s === "revoked" ? "bad" : s === "limited" ? "warn" : "neutral";

/** A person's access status, with budget scale folded in ("limited" when scaled below 100%). */
export function PersonStatusPill({ status, scale }: { status: string; scale?: number }) {
  if (status === "active" && scale !== undefined && scale < 1) {
    return (
      <Pill tone="warn" dot>
        limited · {Math.round(scale * 100)}%
      </Pill>
    );
  }
  return (
    <Pill tone={statusTone(status)} dot>
      {status}
    </Pill>
  );
}

export const levelTone = (l: string): Tone =>
  l === "quarantine" ? "bad" : l === "tighten" ? "serious" : l === "alert" ? "warn" : "neutral";

export function LevelPill({ level }: { level: string }) {
  return <Pill tone={levelTone(level)}>{level === "none" ? "normal" : level}</Pill>;
}

export const incidentTone = (s: string): Tone =>
  s === "open" ? "bad" : s === "acknowledged" ? "warn" : s === "resolved" ? "good" : "neutral";

export function IncidentStatusPill({ status }: { status: string }) {
  return (
    <Pill tone={incidentTone(status)} dot>
      {status}
    </Pill>
  );
}

export const decisionTone = (d: string | null | undefined): Tone =>
  d === "block" ? "bad" : d === "redact" ? "serious" : d === "warn" ? "warn" : d === "allow" || d === "log" ? "good" : "neutral";

export function DecisionPill({ decision }: { decision: string | null | undefined }) {
  if (!decision) return null;
  const known = ["allow", "log", "warn", "redact", "block"].includes(decision);
  return <Pill tone={known ? decisionTone(decision) : "neutral"}>{decision}</Pill>;
}

export function RequestStatusPill({ status }: { status: string }) {
  const tone: Tone = status === "pending" ? "warn" : status === "approved" ? "good" : status === "denied" ? "bad" : "neutral";
  return (
    <Pill tone={tone} dot>
      {status}
    </Pill>
  );
}

export const CLASS_LABEL: Record<string, string> = {
  consumable: "Consumable",
  leasable: "Leasable",
  access_grant: "Access grant",
};
export function ClassPill({ cls }: { cls: string }) {
  const tone: Tone = cls === "consumable" ? "info" : cls === "leasable" ? "accent" : "cc";
  return <Pill tone={tone}>{CLASS_LABEL[cls] ?? cls}</Pill>;
}

export function SensitivityPill({ s }: { s: string }) {
  const tone: Tone = s === "critical" ? "bad" : s === "high" ? "serious" : s === "medium" ? "warn" : "neutral";
  return <Pill tone={tone}>{s}</Pill>;
}

export function TierPill({ tier }: { tier: string }) {
  const tone: Tone = tier === "heavy" ? "serious" : tier === "standard" ? "info" : "neutral";
  return <Pill tone={tone}>{tier}</Pill>;
}

const SOURCES: Record<string, { label: string; icon: ReactNode; tone: Tone }> = {
  gateway: { label: "Gateway", icon: <IconGlobe size={12} />, tone: "info" },
  mcp: { label: "MCP", icon: <IconPlug size={12} />, tone: "accent" },
  claude_code: { label: "Claude Code", icon: <IconTerminal size={12} />, tone: "cc" },
  report: { label: "Report", icon: <IconReceipt size={12} />, tone: "neutral" },
  billing_export: { label: "Billing", icon: <IconReceipt size={12} />, tone: "neutral" },
  cloudevents: { label: "CloudEvents", icon: <IconBox size={12} />, tone: "neutral" },
  detections: { label: "Detection", icon: <IconRadar size={12} />, tone: "bad" },
  seed: { label: "Seed", icon: <IconActivity size={12} />, tone: "neutral" },
};

export function sourceLabel(s: string): string {
  return SOURCES[s]?.label ?? titleCase(s);
}

export function SourceBadge({ source }: { source: string }) {
  const s = SOURCES[source] ?? { label: titleCase(source), icon: <IconActivity size={12} />, tone: "neutral" as Tone };
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide ring-1 ring-inset ${
        {
          info: "bg-info/10 text-info ring-info/25",
          accent: "bg-accent/10 text-accent ring-accent/25",
          cc: "bg-cc/12 text-cc ring-cc/35",
          bad: "bg-bad/10 text-bad ring-bad/25",
          neutral: "bg-ink/[0.05] text-ink2 ring-ink/10",
          good: "",
          warn: "",
          serious: "",
        }[s.tone]
      }`}
    >
      {s.icon}
      {s.label}
    </span>
  );
}
