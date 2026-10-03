"""FinOps FOCUS cost records: export AI spend in the format finance tools read, import cloud billing exports.

Columns follow FOCUS 1.1 (BilledCost, ChargePeriodStart, ConsumedQuantity, ResourceId, ServiceCategory, Tags...);
attribution the spec has no column for goes in `x_` columns and in Tags.
"""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import Policy

COLUMNS = [
    "BillingAccountId",
    "BillingCurrency",
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "ChargePeriodStart",
    "ChargePeriodEnd",
    "ChargeCategory",
    "ChargeDescription",
    "BilledCost",
    "EffectiveCost",
    "ListCost",
    "ConsumedQuantity",
    "ConsumedUnit",
    "ProviderName",
    "PublisherName",
    "ResourceId",
    "ResourceName",
    "ResourceType",
    "ServiceCategory",
    "ServiceName",
    "SubAccountId",
    "SubAccountName",
    "Tags",
    "x_Principal",
    "x_Workflow",
    "x_Task",
    "x_Source",
    "x_ResourceClass",
]

SERVICE_CATEGORY = {
    "ai_model": "AI and Machine Learning",
    "compute": "Compute",
    "device": "Compute",
    "ci": "Developer Tools",
    "data": "Databases",
    "saas": "Business Applications",
}


def _qty(x: Any) -> str:
    """A quantity in full: `:g` would round 1234567 tokens to 1.23457e+06."""
    x = float(x or 0)
    return str(int(x)) if x.is_integer() else repr(x)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def export_csv(policy: Policy, rows: list[dict[str, Any]], account: str = "shpont-shield") -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=COLUMNS)
    w.writeheader()
    for r in rows:
        start = datetime.strptime(r["day"], "%Y-%m-%d").replace(tzinfo=UTC)
        month = start.replace(day=1)
        next_month = (month + timedelta(days=32)).replace(day=1)
        entry = policy.catalog.get(r["resource"] or "")
        tokens = entry is not None and entry.unit == "token"
        qty = r["tokens"] if tokens or (not r["quantity"] and r["tokens"]) else r["quantity"]
        unit = (
            "token" if tokens or (not r["quantity"] and r["tokens"]) else (r["unit"] or (entry.unit if entry else ""))
        )
        tags = {k: r[k] for k in ("principal", "team", "workflow", "task") if r.get(k)}
        if r.get("model"):
            tags["model"] = r["model"]
        w.writerow(
            {
                "BillingAccountId": account,
                "BillingCurrency": "USD",
                "BillingPeriodStart": _iso(month),
                "BillingPeriodEnd": _iso(next_month),
                "ChargePeriodStart": _iso(start),
                "ChargePeriodEnd": _iso(start + timedelta(days=1)),
                "ChargeCategory": "Usage",
                "ChargeDescription": f"{r['resource']} for {r['principal']}"
                + (f" ({r['workflow']})" if r.get("workflow") else ""),
                "BilledCost": f"{r['usd'] or 0:.8f}",
                "EffectiveCost": f"{r['usd'] or 0:.8f}",
                "ListCost": f"{r['usd'] or 0:.8f}",
                "ConsumedQuantity": _qty(qty),
                "ConsumedUnit": unit,
                "ProviderName": entry.provider if entry else "",
                "PublisherName": entry.provider if entry else "",
                "ResourceId": entry.urn if entry else f"urn:shield:other:unknown:{r['resource']}",
                "ResourceName": r["resource"],
                "ResourceType": entry.title or r["resource"] if entry else r["resource"],
                "ServiceCategory": SERVICE_CATEGORY.get(entry.category if entry else "", "Other"),
                "ServiceName": entry.title if entry and entry.title else r["resource"],
                "SubAccountId": r["team"] or "",
                "SubAccountName": r["team"] or "",
                "Tags": json.dumps(tags, sort_keys=True),
                "x_Principal": r["principal"],
                "x_Workflow": r.get("workflow") or "",
                "x_Task": r.get("task") or "",
                "x_Source": r.get("source") or "",
                "x_ResourceClass": entry.class_ if entry else "",
            }
        )
    return out.getvalue()


def _ts(value: str) -> float:
    v = value.strip().replace("Z", "+00:00")
    d = datetime.fromisoformat(v)
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).timestamp()


def parse_csv(policy: Policy, text: str, default_resource: str = "cloud") -> list[dict[str, Any]]:
    """FOCUS rows as usage records. Rows that cannot be read raise ValueError naming the line."""
    from .controls.resources import resolve

    out = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = {"BilledCost", "ChargePeriodStart"} - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"not a FOCUS file: missing columns {sorted(missing)}")
    for n, row in enumerate(reader, start=2):
        try:
            tags = json.loads(row.get("Tags") or "{}") or {}
            if not isinstance(tags, dict):
                tags = {}
            resource = (
                resolve(policy, row.get("ResourceName") or "")
                or resolve(policy, row.get("ResourceId") or "")
                or default_resource
            )
            unit = row.get("ConsumedUnit") or ""
            qty = float(row.get("ConsumedQuantity") or 0)
            usd = float(row.get("BilledCost") or 0)  # negative is a credit or refund
            if not (math.isfinite(qty) and math.isfinite(usd)):
                raise ValueError("BilledCost and ConsumedQuantity must be finite numbers")
            out.append(
                {
                    "ts": _ts(row["ChargePeriodStart"]) + 1,  # inside the charge period's day
                    "principal": row.get("x_Principal") or tags.get("principal") or "unattributed",
                    "team": row.get("SubAccountName") or tags.get("team") or "unattributed",
                    "workflow": row.get("x_Workflow") or tags.get("workflow") or None,
                    "task": row.get("x_Task") or tags.get("task") or None,
                    "resource": resource,
                    "model": tags.get("model"),
                    "input_tokens": int(qty) if unit == "token" else 0,
                    "quantity": 0.0 if unit == "token" else qty,
                    "unit": unit,
                    "usd": usd,
                }
            )
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            raise ValueError(f"line {n}: {e}") from e
    return out
