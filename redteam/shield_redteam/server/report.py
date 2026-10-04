"""A posture report in Markdown: one page for management, the detail for the security team."""

from __future__ import annotations

import time
from typing import Any


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def markdown(poligon: dict[str, Any], feedcheck: dict[str, Any]) -> str:
    r = poligon.get("report")
    if not r:
        return "# Posture report\n\nNo run has completed yet.\n"
    lines = [
        "# AI control layer posture report",
        "",
        f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')} from configuration `{r['fingerprint']}`.",
        "",
        "## Summary",
        "",
        f"- Posture score: **{r['posture']} / 100**",
        f"- Known attacks stopped: **{r['attacks']['stopped']} of {r['attacks']['total']}** ({_pct(r['protection'])})",
        f"- Legitimate requests wrongly stopped: **{r['benign']['stopped']} of {r['benign']['total']}**"
        f" ({_pct(r['friction'])})",
        f"- Rewritten attacks still stopped: **{r['mutants']['stopped']} of {r['mutants']['total']}**"
        f" ({_pct(r['evasion'])})",
        f"- Signature feed `{feedcheck['feed_version']}`: {feedcheck['signatures']} signatures,"
        f" {feedcheck['broken']} broken, {feedcheck['self_tested']} carry their own tests",
        "",
        "## Coverage by OWASP LLM Top 10 category",
        "",
        "| Category | Attacks | Stopped | Protection |",
        "|---|---|---|---|",
    ]
    lines += [f"| {g['name']} | {g['attacks']} | {g['stopped']} | {_pct(g['protection'])} |" for g in r["by_owasp"]]
    lines += ["", "## Attacks that currently get through", ""]
    lines += [f"- `{o['id']}` ({o['category']}, {o['direction']}): {o['text']}" for o in r["open_attacks"]] or ["None."]
    lines += ["", "## Legitimate requests wrongly stopped", ""]
    lines += [
        f"- `{o['id']}` stopped by {', '.join(o['caught_by']) or o['action']}: {o['text']}"
        for o in r["false_positives"]
    ] or ["None."]
    lines += ["", "## Configuration changes and their impact", ""]
    for c in poligon.get("changes", []):
        stamp = time.strftime("%H:%M:%S", time.localtime(c["ts"]))
        lines.append(
            f"- {stamp}: posture {c['posture_before']} -> {c['posture_after']},"
            f" opened {len(c['opened'])}, closed {len(c['closed'])},"
            f" new false positives {len(c['new_false_positives'])}"
        )
    if not poligon.get("changes"):
        lines.append("No configuration change observed.")
    return "\n".join(lines) + "\n"
