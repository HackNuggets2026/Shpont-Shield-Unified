"""Turn a run into numbers a security team and a manager can both read.

protection  share of attacks stopped (blocked or redacted)
friction    share of benign requests stopped: the price developers pay for the guardrails
evasion     share of mutated attacks still stopped, among attacks whose original is stopped
posture     protection x (1 - friction), 0-100. Blocking everything scores 0, so does blocking nothing.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from ..core.types import Outcome
from .runner import Run


def _rate(hit: int, total: int) -> float | None:
    return round(hit / total, 4) if total else None


def _group(outcomes: list[Outcome], key) -> list[dict[str, Any]]:
    buckets: dict[str, list[Outcome]] = defaultdict(list)
    for o in outcomes:
        buckets[key(o)].append(o)
    rows = []
    for name, items in sorted(buckets.items()):
        attacks = [o for o in items if o.case.must_stop]
        benign = [o for o in items if not o.case.must_stop]
        rows.append(
            {
                "name": name,
                "attacks": len(attacks),
                "stopped": sum(o.decision.stopped for o in attacks),
                "benign": len(benign),
                "false_positives": sum(o.decision.stopped for o in benign),
                "protection": _rate(sum(o.decision.stopped for o in attacks), len(attacks)),
            }
        )
    return rows


def _row(o: Outcome) -> dict[str, Any]:
    return {
        "id": o.case.id,
        "kind": o.case.kind,
        "category": o.case.category,
        "owasp": o.case.owasp,
        "direction": o.case.direction,
        "principal": o.case.principal,
        "technique": o.case.technique,
        "text": o.case.text if len(o.case.text) <= 220 else o.case.text[:217] + "...",
        "action": o.decision.action,
        "caught_by": o.caught_by,
        "error": o.decision.error,
    }


def report(run: Run) -> dict[str, Any]:
    base = [o for o in run.outcomes if not o.case.parent]
    mut = [o for o in run.outcomes if o.case.parent]
    attacks = [o for o in base if o.case.must_stop]
    benign = [o for o in base if not o.case.must_stop]
    stopped_ids = {o.case.id for o in attacks if o.decision.stopped}
    fair_mut = [o for o in mut if o.case.parent in stopped_ids]

    protection = _rate(sum(o.decision.stopped for o in attacks), len(attacks))
    friction = _rate(sum(o.decision.stopped for o in benign), len(benign))
    evasion = _rate(sum(o.decision.stopped for o in fair_mut), len(fair_mut))
    posture = None if protection is None else round(100 * protection * (1 - (friction or 0.0)), 1)

    techniques: dict[str, list[Outcome]] = defaultdict(list)
    for o in fair_mut:
        techniques[o.case.technique or "?"].append(o)
    lat = sorted(o.decision.latency_ms for o in run.outcomes if not o.decision.error)

    return {
        "fingerprint": run.fingerprint,
        "started": run.started,
        "seconds": round(run.seconds, 3),
        "cases": len(run.outcomes),
        "errors": sum(bool(o.decision.error) for o in run.outcomes),
        "posture": posture,
        "protection": protection,
        "friction": friction,
        "evasion": evasion,
        "attacks": {"total": len(attacks), "stopped": len(stopped_ids)},
        "benign": {"total": len(benign), "stopped": sum(o.decision.stopped for o in benign)},
        "mutants": {"total": len(fair_mut), "stopped": sum(o.decision.stopped for o in fair_mut)},
        "by_category": _group(base, lambda o: o.case.category),
        "by_owasp": _group([o for o in base if o.case.owasp], lambda o: o.case.owasp),
        "by_technique": [
            {"name": n, "total": len(v), "stopped": sum(o.decision.stopped for o in v)}
            for n, v in sorted(techniques.items())
        ],
        "latency_ms": {
            "p50": round(statistics.median(lat), 2) if lat else None,
            "p95": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 2) if lat else None,
        },
        "open_attacks": [_row(o) for o in attacks if not o.decision.stopped],
        "false_positives": [_row(o) for o in benign if o.decision.stopped],
        "evaded": [_row(o) for o in fair_mut if not o.decision.stopped],
    }


def _cause(old: str, new: str) -> list[str]:
    """Which parts of the configuration fingerprint differ, e.g. ["policy", "controls"]."""
    a = dict(seg.split("=", 1) for seg in old.split("|") if "=" in seg)
    b = dict(seg.split("=", 1) for seg in new.split("|") if "=" in seg)
    return [k for k in b if a.get(k) != b[k]] + [k for k in a if k not in b]


def _controls(fingerprint: str) -> dict[str, str]:
    seg = next((x for x in fingerprint.split("|") if x.startswith("controls=")), "controls=")
    return dict(item.split(":", 1) for item in seg[len("controls=") :].split(",") if ":" in item)


def changed_controls(old: str, new: str) -> list[dict[str, Any]]:
    """Controls whose enabled flag, mode or shadow flag differs between two fingerprints."""
    a, b = _controls(old), _controls(new)
    out = []
    for name in b:
        if name in a and a[name] != b[name]:
            was_on, is_on = a[name][:1] == "1", b[name][:1] == "1"
            what = "switched off" if was_on and not is_on else "switched on" if is_on and not was_on else "changed"
            out.append({"id": name, "what": what})
    return out


def impact(before: Run, after: Run) -> dict[str, Any]:
    """What a configuration change did, case by case: the answer to "what did my edit just open?"."""
    old, new = before.by_id(), after.by_id()
    opened, closed, new_fp, fixed_fp = [], [], [], []
    for cid, o in new.items():
        prev = old.get(cid)
        if prev is None or prev.decision.stopped == o.decision.stopped:
            continue
        entry = {**_row(o), "was": prev.decision.action, "was_caught_by": prev.caught_by}
        if o.case.must_stop:
            (closed if o.decision.stopped else opened).append(entry)
        else:
            (new_fp if o.decision.stopped else fixed_fp).append(entry)
    cats: dict[str, int] = defaultdict(int)
    for e in opened:
        cats[e["category"]] += 1
    rb, ra = report(before), report(after)
    return {
        "ts": after.started,
        "from": before.fingerprint,
        "to": after.fingerprint,
        "cause": _cause(before.fingerprint, after.fingerprint),
        "controls": changed_controls(before.fingerprint, after.fingerprint),
        "posture_before": rb["posture"],
        "posture_after": ra["posture"],
        "opened": opened,
        "closed": closed,
        "new_false_positives": new_fp,
        "fixed_false_positives": fixed_fp,
        "opened_by_category": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
        "changed": bool(opened or closed or new_fp or fixed_fp),
    }
