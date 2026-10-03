"""Synthetic organisation for demos: `python -m controllayer.seed --people 2000 --days 30`.

Writes, next to the policy:
- the people directory the policy names in `identity.directory`: employees and their agents in
  teams, each with a daily budget (`budgets.per_person`) and a random API key;
- data/history.json: usage per person, model and company service over the last `--days` days,
  decision counts, risk scores and alerts, and recent audit events, read by the gateway at start;
- data/state.json: agent grants, a few security overrides, external risk signals and a suspended
  resource, added to whatever the file already holds.

The same `--seed` gives the same people, budgets and usage (API keys and timestamps differ).
"""

from __future__ import annotations

import argparse
import json
import random
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from .analytics import DAY, HOUR, UsageHistory
from .config import Policy, parse_policy
from .controls.budget import BudgetLedger
from .state import StateStore
from .types import Context, Direction, Principal

# team: share of headcount, role, base daily budget (USD), model mix, services used on top of entitlement
TEAMS: dict[str, tuple[float, str, float, dict[str, float]]] = {
    "engineering": (
        0.30,
        "developer",
        12,
        {"claude-sonnet-5": 5, "claude-opus-5-5": 2, "gpt-4o-mini": 1, "qwen3:8b": 1},
    ),
    "platform": (0.07, "developer", 15, {"claude-sonnet-5": 4, "claude-opus-5-5": 2, "llama3.2:3b": 1}),
    "data": (0.06, "developer", 12, {"claude-sonnet-5": 3, "gpt-4o-mini": 2, "qwen3:8b": 1}),
    "security": (0.03, "developer", 10, {"claude-sonnet-5": 3, "claude-opus-5-5": 1}),
    "product": (0.06, "analyst", 6, {"claude-sonnet-5": 3, "gpt-4o-mini": 2}),
    "design": (0.04, "analyst", 4, {"claude-sonnet-5": 2, "gpt-4o-mini": 2}),
    "sales": (0.14, "analyst", 4, {"gpt-4o-mini": 4, "claude-haiku-4-5": 3, "claude-sonnet-5": 1}),
    "support": (0.12, "analyst", 3, {"claude-haiku-4-5": 4, "gpt-4o-mini": 3}),
    "marketing": (0.06, "analyst", 4, {"gpt-4o-mini": 3, "claude-sonnet-5": 2}),
    "finance": (0.06, "analyst", 6, {"claude-sonnet-5": 2, "gpt-4o-mini": 2, "claude-haiku-4-5": 1}),
    "legal": (0.03, "analyst", 5, {"claude-opus-5-5": 1, "claude-sonnet-5": 2}),
    "people-ops": (0.03, "analyst", 3, {"gpt-4o-mini": 3, "claude-haiku-4-5": 1}),
}
FIRST = (
    "Ada Adam Agata Aisha Alan Alba Aleksander Alice Amir Ana Anna Anton Aria Arjun Ava Bartek Beatriz Ben Bianca "
    "Boris Carla Carlos Chen Chloe Chris Clara Daniel Dara David Diego Dmitri Elena Eli Emil Emma Eva Felix Fatima "
    "Filip Gabriel Grace Hana Hannah Hugo Ian Igor Ines Iris Isaac Ivan Jakub Jan Jana Javier Jin Joanna John Jonas "
    "Julia Kai Kamil Karin Kasia Kenji Laila Lars Laura Lea Leo Liam Lina Lucas Lucia Maja Marco Maria Marta Mateo "
    "Maya Mei Mia Michal Mila Nadia Nina Noah Nora Olga Oliver Omar Oskar Paula Pawel Piotr Priya Rafael Rosa Ruth "
    "Sam Sara Sofia Sven Tara Theo Tomas Uma Victor Wiktor Yara Yusuf Zofia Zoe"
).split()
LAST = (
    "Abe Ahmed Alvarez Andersen Bauer Becker Berg Brown Castro Chen Costa Cruz Dahl Diaz Dubois Edwards Eriksson "
    "Fischer Fontaine Garcia Gomez Gruber Hansen Hoffmann Huang Ito Jansen Jensen Johnson Kaminski Kim Klein Kowalski "
    "Kozlov Lambert Larsen Lee Lewandowski Lindqvist Lopez Martin Meyer Moreau Muller Nagy Nakamura Nielsen Novak "
    "Nowak Oliveira Patel Perez Petrov Rossi Russo Santos Sato Schmidt Schulz Silva Singh Smith Sousa Suzuki Tanaka "
    "Taylor Thomas Torres Varga Wagner Walker Wang Weber Wilson Wojcik Wright Yamamoto Young Zielinski"
).split()
AGENT_KINDS = ("coder", "assistant", "analyst", "ops")
FINDINGS = (
    ("pii", "credit_card", "redact"),
    ("pii", "iban", "redact"),
    ("secrets", "jwt", "redact"),
    ("pii", "email", "log"),
    ("prompt_injection", "prompt_injection", "block"),
    ("data_exfiltration", "data_exfiltration", "block"),
    ("secrets", "aws_access_key", "block"),
    ("signatures", "reverse_shell", "block"),
    ("harmful_request", "malware", "block"),
)


def build(policy: Policy, people: int, days: int, seed: int, now: float) -> dict[str, Any]:
    rng = random.Random(seed)
    taken = {k.principal for k in policy.identity.api_keys.values()}
    humans: list[dict[str, Any]] = []
    for team, (share, role, base, _) in TEAMS.items():
        for _ in range(round(people * share)):
            first, last = rng.choice(FIRST), rng.choice(LAST)
            pid, n = f"{first}.{last}".lower(), 2
            while pid in taken:
                pid, n = f"{first}.{last}{n}".lower(), n + 1
            taken.add(pid)
            seniority = rng.choices((1, 1.5, 2.5), (0.7, 0.22, 0.08))[0]
            humans.append(
                {
                    "principal": pid,
                    "name": f"{first} {last}",
                    "team": team,
                    "role": role,
                    "cap": round(base * seniority * 2) / 2,
                    "drive": rng.lognormvariate(0, 0.8),  # how heavily this person uses AI
                }
            )
    humans = humans[:people]
    agents = []
    for h in humans:
        for i in range(rng.choices((0, 1, 2), (0.83, 0.14, 0.03))[0]):
            agents.append(
                {
                    "principal": f"{h['principal']}-{AGENT_KINDS[(i + len(agents)) % 4]}",
                    "name": f"{h['name']}'s {AGENT_KINDS[(i + len(agents)) % 4]}",
                    "team": h["team"],
                    "role": h["role"],
                    "owner": h["principal"],
                    "cap": rng.choice((5, 10, 15, 25)),
                    "drive": rng.lognormvariate(-0.3, 0.7),
                }
            )
    everyone = humans + agents
    directory = {
        "api_keys": {
            f"org-{secrets.token_hex(12)}": {
                k: p[k] for k in ("principal", "name", "team", "role", "owner") if p.get(k) is not None
            }
            | ({"kind": "agent"} if p.get("owner") else {})
            for p in everyone
        },
        "budgets": {p["principal"]: {"usd_per_day": p["cap"]} for p in everyone},
    }

    services = {
        rid: r
        for rid, r in policy.resources.items()
        if r.type == "service" and r.connection["service"] in policy.budgets.services
    }
    ledger = BudgetLedger()  # prices every synthetic call exactly as the gateway would
    history = UsageHistory()
    today = int(now // DAY)
    risky = set(rng.sample([h["principal"] for h in humans], k=max(3, len(humans) // 25)))
    for p in everyone:
        team = TEAMS[p["team"]]
        mine = [
            rid
            for rid, r in services.items()
            if r.entitled.allows(p.get("owner") or p["principal"], p["team"], p["role"])
        ]
        used = rng.sample(mine, k=min(len(mine), rng.randint(1, 4))) if mine else []
        ctx = Context(Principal(p["principal"], p["team"], p["role"]), Direction.INPUT, "")
        pmult = 2.5 if p["principal"] in risky else 1.0
        for d in range(today - days + 1, today + 1):
            weekend = time.gmtime(d * DAY).tm_wday >= 5
            if rng.random() > (0.12 if weekend else min(0.95, 0.35 + p["drive"] / 2)):
                continue
            # Share of the daily cap spent: mostly well under, a heavy tail over it.
            target = p["cap"] * 0.3 * p["drive"] * rng.lognormvariate(0, 0.45)
            hours = sorted(rng.sample(range(7, 19), k=rng.randint(2, 6)))
            if d == today:
                hours = [h for h in hours if h <= time.gmtime(now).tm_hour] or [time.gmtime(now).tm_hour]
            for model, weight in team[3].items():
                if rng.random() > 0.35 + weight / 8:
                    continue
                share = target * weight / sum(team[3].values())
                inp, out = rng.randint(1200, 6000), rng.randint(200, 1200)
                unit = ledger.record(ctx, policy, model, inp, out, rng.uniform(0.8, 3.5))  # prices are linear
                n = max(1, round(share / unit)) if unit else rng.randint(3, 40)
                emit(history, p["principal"], f"model:{model}", d, hours, n, (inp + out) * n, unit * n, rng, now)
                decisions(history, p["principal"], d * DAY + hours[0] * HOUR, n, rng, pmult)
            for rid in used:
                if rng.random() > 0.55:
                    continue
                svc = services[rid].connection["service"]
                calls = max(1, int(rng.lognormvariate(1.6, 0.9) * (2 if p.get("owner") else 1)))
                usd = calls * policy.budgets.call_price(svc, None)
                emit(history, p["principal"], f"service:{svc}", d, hours, calls, 0, usd, rng, now)
                decisions(history, p["principal"], d * DAY + hours[0] * HOUR, calls * 2, rng, pmult)

    state = {"grants": {}, "watch": {}, "signals": {}, "suspended": {}}
    for a in agents:
        owner = next(h for h in humans if h["principal"] == a["owner"])
        mine = [
            rid
            for rid, r in policy.resources.items()
            if r.entitled.allows(owner["principal"], owner["team"], owner["role"])
        ]
        for rid in rng.sample(mine, k=min(len(mine), rng.randint(1, 4))):
            r = policy.resources[rid]
            allowed = r.scopes_for(owner["principal"], owner["team"], owner["role"])
            hours = r.max_grant_hours or 168
            at = now - rng.uniform(0.1, 1.2) * hours * 3600
            state["grants"].setdefault(a["principal"], {})[rid] = {
                "scopes": sorted(rng.sample(allowed, k=rng.randint(1, len(allowed)))),
                "granted_by": owner["principal"],
                "granted_at": at,
                "expires_at": at + hours * 3600,
            }
    if "vercel" in policy.resources:
        state["suspended"]["vercel"] = {"reason": "credential rotation", "at": now - 3 * HOUR}

    scores, alerts = {}, []
    lv = policy.insider_risk.levels
    ordered = sorted(risky)
    for i, pid in enumerate(ordered):
        h = next(x for x in humans if x["principal"] == pid)
        # Current score: a few restricted, a quarter on watch, the rest below the watch line.
        current = (
            rng.uniform(lv.restricted, lv.restricted * 1.6)
            if i < max(1, len(ordered) // 12)
            else rng.uniform(lv.watch, lv.restricted * 0.9)
            if i < len(ordered) // 3
            else rng.uniform(2, lv.watch * 0.9)
        )
        ago = rng.uniform(0.5, 20)
        scores[pid] = [
            round(current * 2 ** (ago / 24 / (policy.insider_risk.half_life_hours / 24)), 2),
            now - ago * HOUR,
        ]
        for back in range(min(days, 10), -1, -1):
            history.score(pid, now - back * DAY, current * max(0.05, 1 - back / 10) * rng.uniform(0.7, 1.1))
        level = "restricted" if current >= lv.restricted else "watch" if current >= lv.watch else "normal"
        if level != "normal":
            alerts.append(alert(h, level, round(current, 2), f"level normal -> {level}", now - ago * HOUR))
        if rng.random() < 0.5:
            control, category, _ = rng.choice(FINDINGS[4:])
            alerts.append(
                alert(
                    h, level, round(current, 2), f"alert category: {category}", now - rng.uniform(1, days * 24) * HOUR
                )
                | {"findings": [f"{control}/{category}"], "action": "block", "channel": "chat", "direction": "input"}
            )
    for pid, level, reason in (
        (ordered[-1], "watch", "HR case 2291: review AI use"),
        (ordered[-2], "restricted", "credential leak under investigation"),
        (ordered[0], "normal", "reviewed: test data, not customer data"),
    ):
        state["watch"][pid] = {"level": level, "reason": reason, "at": now - rng.uniform(2, 72) * HOUR}
    for pid in rng.sample(ordered[3:-2], k=min(4, max(0, len(ordered) - 5))):
        state["signals"][pid] = {
            "wazuh": {
                "level": "watch",
                "requested": "watch",
                "score": None,
                "reason": rng.choice(
                    ("USB mass storage on laptop", "impossible travel login", "mass download from Drive")
                ),
                "at": now - rng.uniform(1, 6) * HOUR,
                "expires_at": now + rng.uniform(4, 18) * HOUR,
            }
        }

    events = []
    active = [p for p in everyone if history.last_seen.get(p["principal"], 0) > now - DAY]
    for _ in range(1600):
        p = rng.choice(active) if active else rng.choice(everyone)
        events.append(event(p, now - rng.uniform(0, 4) * HOUR, rng, p["principal"] in risky, policy))
    events.sort(key=lambda e: e["ts"])
    alerts.sort(key=lambda a: a["ts"])
    return {
        "directory": directory,
        "history": {
            "history": history.dump(),
            "risk_scores": scores,
            "alerts": alerts[-500:],
            "events": events,
        },
        "state": state,
        "counts": {"people": len(humans), "agents": len(agents), "risky": len(risky)},
    }


def emit(
    h: UsageHistory,
    pid: str,
    item: str,
    d: int,
    hours: list[int],
    n: int,
    tokens: int,
    usd: float,
    rng: random.Random,
    now: float,
) -> None:
    """One day of one item, spread over the hours worked, none of it later than `now`."""
    weights = [rng.random() + 0.2 for _ in hours]
    total = sum(weights)
    for hour, w in zip(hours, weights, strict=True):
        f = w / total
        h.charge(pid, item, min(now, d * DAY + hour * HOUR + rng.uniform(0, HOUR - 1)), n * f, tokens * f, usd * f)


def decisions(h: UsageHistory, pid: str, ts: float, n: int, rng: random.Random, mult: float) -> None:
    """`n` decisions: mostly allowed, some blocked, redacted or warned (`mult` times more for risky people)."""
    flagged = {
        a: int(n * rate * mult + rng.random()) for a, rate in (("block", 0.002), ("redact", 0.012), ("warn", 0.006))
    }
    for action, k in flagged.items():
        if k:
            h.decision(pid, ts, action, k)
    h.decision(pid, ts, "allow", max(0, n - sum(flagged.values())))


def alert(h: dict[str, Any], level: str, score: float, reason: str, ts: float) -> dict[str, Any]:
    return {
        "ts": ts,
        "principal": h["principal"],
        "owner": None,
        "team": h["team"],
        "level": level,
        "score": score,
        "reason": reason,
        "request_id": None,
        "channel": None,
        "direction": None,
        "tool": None,
        "action": None,
        "findings": [],
    }


def event(p: dict[str, Any], ts: float, rng: random.Random, risky: bool, policy: Policy) -> dict[str, Any]:
    x = rng.random() / (4 if risky else 1)
    finding = None
    if x < 0.03:
        finding = rng.choice(FINDINGS[4:])
    elif x < 0.12:
        finding = rng.choice(FINDINGS[:4])
    action = finding[2] if finding else "allow"
    mcp = rng.random() < 0.25
    tool = rng.choice(("snowflake_query", "postgres_query", "github_get_file", "zendesk_get_ticket")) if mcp else None
    return {
        "ts": ts,
        "request_id": uuid.UUID(int=rng.getrandbits(128)).hex[:16],
        "channel": "mcp" if mcp else "chat",
        "direction": "tool_call" if mcp else "input",
        "principal": p["principal"],
        "owner": p.get("owner"),
        "team": p["team"],
        "role": p["role"],
        "model": None if mcp else rng.choice(list(TEAMS[p["team"]][3])),
        "tool": tool,
        "src_ip": f"10.{rng.randint(0, 40)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
        "action": action,
        "status_code": 403 if action == "block" else 200,
        "reason": f"{finding[0]}/{finding[1]}" if finding else "",
        "policy_version": policy.version,
        "latency_ms": {"total": round(rng.uniform(0.6, 9), 2)},
        "text_sha256": None,
        "text": None,
        "findings": [
            {
                "control": finding[0],
                "category": finding[1],
                "action": action,
                "proposed": action,
                "score": 1.0,
                "tier": "deterministic",
                "shadow": False,
                "detail": "synthetic",
            }
        ]
        if finding
        else [],
        "synthetic": True,
    }


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m controllayer.seed", description=__doc__.splitlines()[0])
    ap.add_argument("--policy", default="policy.yaml")
    ap.add_argument("--people", type=int, default=2000)
    ap.add_argument("--days", type=int, default=30, choices=range(1, 32), metavar="1-31")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    path = Path(args.policy)
    policy = parse_policy(path.read_text())  # without the directory: it is about to be replaced
    if not policy.identity.directory:
        raise SystemExit(f"{path}: set identity.directory to say where the people file goes")
    out = build(policy, args.people, args.days, args.seed, time.time())
    base = path.parent
    target = base / policy.identity.directory
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out["directory"]))
    (base / "data").mkdir(exist_ok=True)
    (base / "data" / "history.json").write_text(json.dumps(out["history"]))
    store = StateStore(base / "data" / "state.json")
    for section, values in out["state"].items():
        store.data[section].update(values)
    store.save()
    c = out["counts"]
    total = sum(r[-1] for r in out["history"]["history"]["daily"] if r[0] == "*")
    print(
        f"{c['people']} people, {c['agents']} agents in {len(TEAMS)} teams -> {target}; "
        f"{args.days} days, ${total:,.0f} spent; {c['risky']} with risk scores -> {base / 'data'}"
    )


if __name__ == "__main__":
    main()
