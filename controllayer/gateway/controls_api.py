"""Guardrail controls, live-editable: PATCH /admin/controls/{name} and strictness profiles.

Every edit goes through the admin overlay (validated before it is written, hot-reloaded, kept across
restarts), so a bad value is refused with the validation message and never reaches enforcement.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import ControlBase, ExfiltrationControl, PiiModelControl, Policy, PolicyStore, SemanticControl, parse_policy
from ..types import Action

PATTERN = ("secrets", "pii", "pii_model", "signatures", "exfiltration", "tool_access")
THRESHOLD_ORDER = ("block", "redact", "warn", "log")
PROFILES = ("strict", "balanced", "permissive")


def control_configs(p: Policy) -> dict[str, ControlBase]:
    return {**{n: getattr(p, n) for n in PATTERN}, **p.semantic_controls}


def main_threshold(c: ControlBase) -> tuple[str, float] | None:
    """The probability line at which a semantic control reaches its mode (else its strongest line);
    the PII model's minimum span score."""
    if isinstance(c, PiiModelControl):
        return "min_score", c.min_score
    if not isinstance(c, SemanticControl) or c.type != "noul":
        return None
    t = c.thresholds
    for a in (c.mode.value, *THRESHOLD_ORDER):
        v = getattr(t, a, None)
        if v is not None:
            return a, v
    return None


def threshold_fields(th: tuple[str, float], v: float) -> dict[str, Any]:
    return {"min_score": v} if th[0] == "min_score" else {"thresholds": {th[0]: v}}


def describe(c: ControlBase) -> str | None:
    if isinstance(c, SemanticControl):
        return " ".join(c.instructions.split())
    if isinstance(c, ExfiltrationControl):
        return "Markdown images and links to domains outside the allowlist: " + ", ".join(c.allowed_domains)
    if isinstance(c, PiiModelControl):
        return {
            "privacy_filter": "Contextual personal data from the Privacy Filter model.",
            "stub": "Contextual personal data: offline stand-in, no model loaded.",
            "off": "Contextual personal data: model backend off.",
        }[c.backend]
    return None


def control_rows(p: Policy, audit: Any) -> list[dict[str, Any]]:
    cats: dict[str, list[tuple[str, int]]] = {}
    for key, n in audit.categories.most_common():
        name, _, cat = key.partition("/")
        cats.setdefault(name, []).append((cat, n))
    rows = []
    for n, c in control_configs(p).items():
        th = main_threshold(c)
        rows.append(
            {
                "name": n,
                "kind": "semantic" if isinstance(c, SemanticControl) else "context" if n == "pii_model" else "deterministic",
                "enabled": c.enabled,
                "mode": c.mode.value,
                "shadow": c.shadow,
                "hits": audit.controls.get(n, 0),
                "threshold": {"action": th[0], "value": th[1]} if th else None,
                "description": describe(c),
                "categories": cats.get(n, []),
            }
        )
    return rows


def _section(name: str) -> list[str]:
    return [name] if name in PATTERN else ["semantic_controls", name]


def _nest(path: list[str], leaf: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = leaf
    for k in reversed(path):
        out = {k: out}
    return out


def profile_patch(base: Policy, profile: str) -> dict[str, Any]:
    """Every control's enabled/shadow/mode/main threshold, from the policy file (not the overlay), so a
    profile always lands on the same values however often it is applied. `balanced` is the file itself."""
    patch: dict[str, Any] = {}
    for n, c in control_configs(base).items():
        fields: dict[str, Any] = {"enabled": c.enabled, "shadow": c.shadow, "mode": c.mode.value}
        th = main_threshold(c)
        if profile == "strict":
            fields |= {"enabled": True, "shadow": False}
            if n == "pii":
                fields["mode"] = "block"
            if th:
                fields |= threshold_fields(th, round(max(0.05, th[1] - 0.15), 2))
        elif profile == "permissive":
            if n == "pii":
                fields["mode"] = "log"
            if th:
                fields |= threshold_fields(th, round(min(0.99, th[1] + 0.1), 2))
        elif th:
            fields |= threshold_fields(th, th[1])
        _merge_into(patch, _nest(_section(n), fields))
    return patch


def _merge_into(base: dict, patch: dict) -> None:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge_into(base[k], v)
        else:
            base[k] = v


def _leaf(node: dict[str, Any], th: tuple[str, float]) -> Any:
    return node.get("min_score") if th[0] == "min_score" else node.get("thresholds", {}).get(th[0])


def active_profile(store: PolicyStore) -> str | None:
    base = parse_policy(store.path.read_text())
    live = control_configs(store.policy)
    for prof in PROFILES:
        want = profile_patch(base, prof)
        ok = True
        for n, c in live.items():
            node = want
            for k in _section(n):
                node = node.get(k, {})
            th = main_threshold(c)
            if (
                node.get("enabled") != c.enabled
                or node.get("shadow") != c.shadow
                or node.get("mode") != c.mode.value
                or (th and _leaf(node, th) != th[1])
            ):
                ok = False
                break
        if ok:
            return prof
    return None


def register(app: FastAPI, store: PolicyStore, layer: Any, json_object: Callable) -> None:
    usage = layer.usage

    def err(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code)

    def actor(request: Request) -> str:
        return (request.headers.get("x-admin-user") or "admin")[:80]

    def write(request: Request, patch: dict, target: str, action: str) -> JSONResponse:
        try:
            p = store.write_overlay(patch)
        except ValueError as e:
            return err(f"rejected: {e}")
        usage.log_admin(actor(request), action, target, "", patch)
        return JSONResponse({"ok": True, "version": p.version})

    @app.patch("/admin/controls/{name}")
    async def edit_control(name: str, request: Request):
        """{enabled?, mode?, shadow?, threshold?}; threshold moves the control's main probability line."""
        c = control_configs(store.policy).get(name)
        if c is None:
            return err(f"unknown control {name!r}", 404)
        body = await json_object(request)
        unknown = set(body) - {"enabled", "mode", "shadow", "threshold"}
        if unknown:
            return err(f"unknown fields {sorted(unknown)}; set enabled, mode, shadow or threshold")
        if not body:
            return err("set enabled, mode, shadow or threshold")
        fields: dict[str, Any] = {}
        for k in ("enabled", "shadow"):
            if k in body:
                if not isinstance(body[k], bool):
                    return err(f"{k} must be true or false")
                fields[k] = body[k]
        if "mode" in body:
            if body["mode"] not in [a.value for a in Action]:
                return err(f"mode must be one of {[a.value for a in Action]}")
            fields["mode"] = body["mode"]
        if "threshold" in body:
            th = main_threshold(c)
            if th is None:
                return err(f"{name} has no probability threshold")
            v = body["threshold"]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return err("threshold must be a number between 0 and 1")
            fields |= threshold_fields(th, float(v))
        return write(request, _nest(_section(name), fields), name, "edit_control")

    @app.get("/admin/controls/profile")
    async def get_profile():
        return {"profiles": list(PROFILES), "active": active_profile(store)}

    @app.post("/admin/controls/profile")
    async def set_profile(request: Request):
        body = await json_object(request)
        prof = body.get("profile")
        if prof not in PROFILES:
            return err(f"profile must be one of {list(PROFILES)}")
        patch = profile_patch(parse_policy(store.path.read_text()), prof)
        return write(request, patch, prof, "controls_profile")
