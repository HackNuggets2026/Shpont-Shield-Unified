// The Redteam sidecar (redteam/, `make redteam`), proxied by the gateway at /api/admin/redteam/*.
// It attacks the gateway with a corpus of known attacks and normal requests, and scores the posture.

import { get, post } from "./api";

export interface CaseRow {
  id: string;
  kind: string;
  category: string;
  owasp: string;
  direction: string;
  principal: string;
  technique: string | null;
  text: string;
  action: string;
  caught_by: string[];
  error: string;
  was?: string;
  was_caught_by?: string[];
}
export interface CaseGroup { name: string; attacks: number; stopped: number; benign: number; false_positives: number; protection: number | null }
export interface PostureReport {
  fingerprint: string;
  started: number;
  seconds: number;
  cases: number;
  errors: number;
  posture: number | null;
  protection: number | null;
  friction: number | null;
  evasion: number | null;
  attacks: { total: number; stopped: number };
  benign: { total: number; stopped: number };
  mutants: { total: number; stopped: number };
  by_category: CaseGroup[];
  by_owasp: CaseGroup[];
  by_technique: { name: string; total: number; stopped: number }[];
  latency_ms: { p50: number | null; p95: number | null };
  open_attacks: CaseRow[];
  false_positives: CaseRow[];
  evaded: CaseRow[];
}
/** What one configuration change did, case by case. */
export interface PostureChange {
  ts: number;
  from: string;
  to: string;
  cause: string[];
  controls: { id: string; what: string }[];
  posture_before: number | null;
  posture_after: number | null;
  opened: CaseRow[];
  closed: CaseRow[];
  new_false_positives: CaseRow[];
  fixed_false_positives: CaseRow[];
  opened_by_category: Record<string, number>;
  changed: boolean;
}
export interface FeedCheck {
  feed_version: string;
  signatures: number;
  broken: number;
  self_tested: number;
  error: string;
  rows: { id: string; ok: boolean; vectors: number; problems: string[] }[];
}
export interface RedteamState {
  now: number;
  target: { name: string; error?: string; policy?: { name?: string; version?: string }; feed?: { version?: string; signatures?: number } };
  poligon: {
    report: PostureReport | null;
    runs: number;
    error: string;
    history: { ts: number; posture: number | null; protection: number | null; friction: number | null; evasion: number | null; fingerprint: string }[];
    changes: PostureChange[];
    corpus: { cases: number; mutants: number; attacks: number; benign: number };
  };
  feedcheck: FeedCheck;
}
export interface StageOutcome {
  id: string;
  attack: boolean;
  stopped: boolean;
  category: string;
  direction: string;
  ring: number;
  seg: string;
  rewrite: boolean;
  action: string;
  text: string;
}
export interface RedteamStage {
  target: { name: string; error: string; policy: string | null; feed: string | null };
  rings: { id: string; label: string; segments: { id: string; label: string; state: string; mode: string }[] }[];
  run: number;
  outcomes: StageOutcome[];
}

const R = "/api/admin/redteam";

export const redteam = {
  state: () => get<RedteamState>(`${R}/state`),
  stage: () => get<RedteamStage>(`${R}/stage`),
  run: () => post<PostureReport>(`${R}/run`, {}),
  reportPath: `${R}/report.md`,
};
