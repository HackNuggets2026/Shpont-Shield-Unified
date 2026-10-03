"""Claude Code as a data source: its OpenTelemetry export (observe) and its hooks (enforce).

Telemetry arrives as OTLP/HTTP JSON at /v1/logs and /v1/metrics. Money and tokens come only from
`api_request` log events, never from the `cost.usage` / `token.usage` metrics, which report the same spend
a second time. Value metrics (lines of code, commits, pull requests, active time, edit decisions) become
`metric.*` events. Prompt text is never stored, even when the user enabled OTEL_LOG_USER_PROMPTS.
"""

from __future__ import annotations

from typing import Any

PREFIX = "claude_code."
# Metrics we keep as value signals, by short name. Cost and token metrics duplicate api_request events.
VALUE_METRICS = {
    "session.count",
    "lines_of_code.count",
    "pull_request.count",
    "commit.count",
    "code_edit_tool.decision",
    "active_time.total",
}
DROP_ATTRS = {"prompt", "user.account_uuid", "organization.id", "tool_parameters", "terminal.type", "event.timestamp",
              "event.sequence", "workflow", "task", "principal", "team", "email", "prompt.id"}  # fmt: skip
BYPASS_MODES = {"bypasspermissions", "bypass_permissions", "dangerously-skip-permissions", "yolo"}


def _value(v: dict[str, Any]) -> Any:
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return attrs(v["kvlistValue"].get("values", []))
    return None


def attrs(kvs: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {kv.get("key"): _value(kv.get("value", {})) for kv in kvs or [] if kv.get("key")}


def _ns(t: Any) -> float | None:
    try:
        return int(t) / 1e9 if t not in (None, "", "0", 0) else None
    except (TypeError, ValueError):
        return None


def _short(name: str) -> str:
    return name[len(PREFIX) :] if name.startswith(PREFIX) else name


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def identity(res: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    """Who and what for, from resource attributes (OTEL_RESOURCE_ATTRIBUTES) and the record's own."""
    a = {**res, **rec}

    def pick(*keys: str) -> Any:
        return next((a[k] for k in keys if a.get(k) not in (None, "")), None)

    version = pick("app.version", "service.version")
    term = pick("terminal.type")
    return {
        "principal": pick("principal", "shield.principal", "enduser.id"),
        "email": pick("user.email", "email"),
        "team": pick("team", "shield.team"),
        "workflow": pick("workflow", "shield.workflow"),
        "task": pick("task", "shield.task"),
        "session": pick("session.id", "session_id"),
        "prompt_id": pick("prompt.id", "prompt_id"),
        "client": "claude-code" + (f"/{version}" if version else "") + (f" ({term})" if term else ""),
    }


def log_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """OTLP logs from Claude Code to flat events (see events.Ingestor)."""
    out = []
    for rl in payload.get("resourceLogs") or []:
        res = attrs((rl.get("resource") or {}).get("attributes"))
        for sl in rl.get("scopeLogs") or []:
            for lr in sl.get("logRecords") or []:
                a = attrs(lr.get("attributes"))
                body = _value(lr.get("body") or {})
                name = _short(str(a.get("event.name") or (body if isinstance(body, str) else "") or "event"))
                if name == "hook_execution_start" or (
                    name == "hook_execution_complete" and str(a.get("num_blocking", "0")) == "0"
                ):
                    continue  # Claude Code reporting on our own hooks: only a blocking run is news
                ts = _ns(lr.get("timeUnixNano")) or _ns(lr.get("observedTimeUnixNano"))
                if a.get("event.timestamp"):
                    ts = ts or _iso(a["event.timestamp"])
                out.append(_event(name, a, identity(res, a), ts))
    return out


def _iso(v: str) -> float | None:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _event(name: str, a: dict[str, Any], who: dict[str, Any], ts: float | None) -> dict[str, Any]:
    e: dict[str, Any] = {**who, "ts": ts, "source": "claude_code", "kind": f"cc.{name}", "severity": "info"}
    detail = {k: v for k, v in a.items() if k not in DROP_ATTRS and not k.startswith(("user.", "session.", "app."))}
    detail.pop("event.name", None)
    if name == "api_request":
        e.update(
            model=a.get("model"),
            usd=_num(a.get("cost_usd")),
            input_tokens=int(_num(a.get("input_tokens"))),
            output_tokens=int(_num(a.get("output_tokens"))),
            meter=True,
            decision="ok",
        )
    elif name == "api_error":
        e.update(model=a.get("model"), decision="error", severity="low")
    elif name in ("tool_decision", "tool_result"):
        e["tool"] = a.get("tool_name") or a.get("name")
        if name == "tool_decision":
            e["decision"] = str(a.get("decision") or "")
            e["severity"] = "low" if e["decision"] == "reject" else "info"
        else:
            ok = str(a.get("success")).lower() in ("true", "1")
            e["decision"] = "success" if ok else "failure"
    elif name == "user_prompt":
        e["decision"] = "submitted"
        detail = {"prompt_length": a.get("prompt_length")}
    elif "permission" in name and "mode" in name:
        mode = str(a.get("to_mode") or a.get("new_mode") or a.get("permission_mode") or a.get("mode") or "")
        e["decision"] = mode
        e["severity"] = "medium" if mode.lower() in BYPASS_MODES else "info"
    elif "mcp" in name:
        e["tool"] = a.get("server_name") or a.get("mcp_server_name") or a.get("name")
        e["decision"] = str(a.get("status") or a.get("connection_status") or "")
    e["detail"] = detail or None
    return e


def metric_events(payload: dict[str, Any], last: Any) -> list[dict[str, Any]]:
    """OTLP metrics to `metric.*` events with the increment as `value`.

    Delta temporality (OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta) is used as sent; cumulative
    series are differenced against `last` (a mapping with get and item assignment), which the caller
    keeps between exports."""
    out = []
    for rm in payload.get("resourceMetrics") or []:
        res = attrs((rm.get("resource") or {}).get("attributes"))
        for sm in rm.get("scopeMetrics") or []:
            for m in sm.get("metrics") or []:
                name = _short(str(m.get("name") or ""))
                if name not in VALUE_METRICS:
                    continue
                data = m.get("sum") or m.get("gauge") or {}
                cumulative = data.get("aggregationTemporality") in (2, "AGGREGATION_TEMPORALITY_CUMULATIVE")
                for dp in data.get("dataPoints") or []:
                    a = attrs(dp.get("attributes"))
                    v = _num(dp.get("asDouble", dp.get("asInt", 0)))
                    who = identity(res, a)
                    if cumulative:
                        key = (
                            name,
                            who.get("session"),
                            who.get("email"),
                            tuple(sorted((k, str(x)) for k, x in a.items())),
                        )
                        prev, last[key] = last.get(key), v
                        v = v - prev if prev is not None and v >= prev else v  # first point, or a restart
                    if not v:
                        continue
                    sub = a.get("type") or a.get("decision") or ""
                    out.append(
                        {
                            **who,
                            "ts": _ns(dp.get("timeUnixNano")),
                            "source": "claude_code",
                            "kind": f"metric.{name.rsplit('.', 1)[0]}",
                            "decision": str(sub) or None,
                            "tool": a.get("tool") or a.get("tool_name"),
                            "severity": "info",
                            "detail": {"value": v, **{k: x for k, x in a.items() if k not in DROP_ATTRS
                                                      and not k.startswith(("user.", "session.", "app."))}},
                        }
                    )  # fmt: skip
    return out
