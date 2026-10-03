"""Central policy: one YAML file, validated, versioned by content hash, hot-reloaded."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .types import Action, Direction

log = logging.getLogger(__name__)

Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class _Strict(BaseModel):
    # Misspelled keys in a hand-edited policy must fail loudly, not silently disable a control.
    model_config = ConfigDict(extra="forbid")


class ApiKey(_Strict):
    principal: str
    team: str
    role: str
    email: str | None = None  # joins telemetry that names a user by email (Claude Code's user.email)
    department: str | None = None  # else the department `org.departments` lists the team under


class Identity(_Strict):
    require_auth: bool = True
    api_keys: dict[str, ApiKey] = Field(default_factory=dict)
    # Guards /admin/* and /metrics (header x-admin-token or ?token=). Unset = open, for local demos only.
    admin_token: str | None = None


class ControlBase(_Strict):
    enabled: bool = True
    mode: Action = Action.BLOCK
    shadow: bool = False
    directions: list[Direction] = Field(default_factory=lambda: list(Direction))


class PatternControl(ControlBase):
    """Regex detectors; each entity maps to the action it proposes."""

    entities: dict[str, Action] = Field(default_factory=dict)


class SignatureControl(ControlBase):
    feed: str = "feeds/signatures.json"
    refresh_seconds: float = 30


class ToolAccessControl(ControlBase):
    # role -> allowed tool globs; "*" allows everything
    roles: dict[str, list[str]] = Field(default_factory=dict)
    # tools whose effect cannot be undone; always blocked pending human approval
    irreversible: list[str] = Field(default_factory=list)


class Thresholds(_Strict):
    block: Probability | None = None
    redact: Probability | None = None
    warn: Probability | None = None
    log: Probability | None = None

    def action_for(self, p: float) -> Action:
        for action in (Action.BLOCK, Action.REDACT, Action.WARN, Action.LOG):
            t = getattr(self, action.value)
            if t is not None and p >= t:
                return action
        return Action.ALLOW


class SemanticControl(ControlBase):
    """One question to the decision model. Adding a block here adds a guardrail, no code."""

    type: Literal["noul", "choice"] = "noul"
    instructions: str
    # noul: probability thresholds
    thresholds: Thresholds = Field(default_factory=Thresholds)
    # choice: option -> description, and option -> action
    criteria: dict[str, str] = Field(default_factory=dict)
    actions: dict[str, Action] = Field(default_factory=dict)
    # Regexes used only by the offline heuristic backend (noul: keywords; choice: per option).
    keywords: list[str] = Field(default_factory=list)
    option_keywords: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> SemanticControl:
        if self.type == "choice":
            if not 2 <= len(self.criteria) <= 26:
                raise ValueError("choice controls need 2-26 criteria")
            unknown = (set(self.actions) | set(self.option_keywords)) - set(self.criteria)
            if unknown:
                raise ValueError(f"actions/option_keywords reference unknown criteria: {sorted(unknown)}")
        for pattern in [*self.keywords, *(k for ks in self.option_keywords.values() for k in ks)]:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"bad keyword regex {pattern!r}: {e}") from e
        return self


class SemanticEngine(_Strict):
    backend: Literal["ollama", "heuristic", "off"] = "heuristic"
    url: str = "http://localhost:11434"
    fast_model: str = "tev1:0.8b"
    deep_model: str | None = "nimble"
    # A fast-tier probability inside this band is re-asked to the deep model.
    escalate_band: tuple[Probability, Probability] = (0.3, 0.85)
    min_confidence: Probability = 0.5
    timeout_seconds: float = Field(3.0, gt=0)
    max_chunk_chars: int = Field(6000, ge=500)
    fail_mode: Literal["open", "closed"] = "closed"
    keep_alive: str = "30m"

    @field_validator("backend", mode="before")
    @classmethod
    def _yaml_off(cls, v: Any) -> Any:
        return "off" if v is False else v  # YAML 1.1 reads a bare `off` as false


class ModelPrice(_Strict):
    usd_per_1m_input: float = 0.0
    usd_per_1m_output: float = 0.0
    # Locally hosted models: what a second of inference costs us (GPU amortisation, power).
    usd_per_compute_second: float = 0.0


class BudgetLimits(_Strict):
    requests_per_minute: int | None = None
    tokens_per_day: int | None = None
    usd_per_day: float | None = None


class LoopGuard(_Strict):
    max_identical_calls: int = 5
    window_seconds: float = 60


class Downgrade(_Strict):
    """Past `at` of a budget, route requests to a cheaper model instead of refusing them."""

    enabled: bool = False
    at: Probability = 0.8
    routes: dict[str, str] = Field(default_factory=dict)  # requested model glob -> cheaper model


class Budgets(_Strict):
    enabled: bool = True
    shadow: bool = False
    global_: BudgetLimits = Field(default_factory=BudgetLimits, alias="global")
    per_team: dict[str, BudgetLimits] = Field(default_factory=dict)
    per_principal: BudgetLimits = Field(default_factory=BudgetLimits)
    pricing: dict[str, ModelPrice] = Field(default_factory=dict, exclude=True)  # derived from the catalog
    loop_guard: LoopGuard = Field(default_factory=LoopGuard)
    downgrade: Downgrade = Field(default_factory=Downgrade)


class Models(_Strict):
    allowed: list[str] = Field(default_factory=list)  # empty = any


class TeamOverride(_Strict):
    # control name -> fields to override, e.g. {"pii": {"mode": "log"}}
    controls: dict[str, dict[str, Any]] = Field(default_factory=dict)


class Upstream(_Strict):
    backend: Literal["ollama", "openai", "mock"] = "mock"
    url: str = "http://localhost:11434"
    api_key_env: str | None = None
    mcp_servers: dict[str, str] = Field(default_factory=dict)  # name -> URL, "builtin" = demo server


class ResourceCap(_Strict):
    max_concurrent: int | None = None
    max_minutes: float | None = None  # a lease older than this is flagged (and reclaimed if the resource allows)


class RunLimits(_Strict):
    """Limits for one run of a workflow, i.e. everything sharing a task id."""

    tokens: int | None = None
    usd: float | None = None


class Workflow(_Strict):
    """One item on the menu: what a kind of work may use, and what one run of it may cost."""

    title: str = ""  # the display name ("Bug fixing"); the key is the id
    description: str = ""
    enabled: bool = True
    tier: Literal["light", "standard", "heavy"] = "standard"
    teams: list[str] = Field(default_factory=list)  # empty = every team
    roles: list[str] = Field(default_factory=list)  # empty = every role
    models: list[str] = Field(default_factory=list)  # globs, on top of models.allowed; empty = no extra limit
    tools: list[str] = Field(default_factory=list)  # globs, on top of the role's tools; empty = no extra limit
    resources: dict[str, ResourceCap] = Field(default_factory=dict)  # resources the workflow may lease
    per_run: RunLimits = Field(default_factory=RunLimits)
    approval: Literal["none", "admin"] = "none"
    keywords: list[str] = Field(default_factory=list)  # heuristic backend hints for the classifier


class Menu(_Strict):
    # Unlabeled traffic is refused. Off: it is allowed, and attributed by the classifier when possible.
    require_label: bool = False
    # Ask the fast decision model which workflow an unlabeled prompt belongs to (same call, one more question).
    classify_unlabeled: bool = True
    workflows: dict[str, Workflow] = Field(default_factory=dict)


class LeaseHandle(_Strict):
    """How to tell leases apart: the id a start tool returns, and the argument later calls pass it in."""

    pattern: str
    arg: str = "id"

    @field_validator("pattern")
    @classmethod
    def _regex(cls, v: str) -> str:
        re.compile(v)
        return v


class Resource(_Strict):
    """Something an agent holds for a while (simulator, VM, browser) or consumes in units (CI minutes)."""

    usd_per_minute: float = 0.0
    usd_per_unit: float = 0.0
    start_tools: list[str] = Field(default_factory=list)  # MCP tools that open a lease
    stop_tools: list[str] = Field(default_factory=list)  # close one lease (by handle, else the newest)
    stop_all_tools: list[str] = Field(default_factory=list)  # close every lease of this type the principal holds
    activity_tools: list[str] = Field(default_factory=list)  # keep a lease from counting as idle
    handle: LeaseHandle | None = None
    max_concurrent_per_principal: int | None = None
    idle_minutes: float | None = 15  # idle longer than this = zombie
    auto_reclaim: bool = False  # stop zombies and over-time leases by calling the first stop tool
    reclaim_after_minutes: float = Field(3, ge=0)  # grace between flagging a lease and reclaiming it


# ---- Resource catalog -----------------------------------------------------------------
# Everything an agent can touch is a catalogued resource (Kubernetes-style `kind: ResourceType`),
# named by a URN (`urn:shield:<category>:<provider>:<type>`) and decided on with Cedar's model:
# principal, action, resource, context. Three classes, each measured and limited differently.

RESOURCE_CLASSES = ("consumable", "leasable", "access_grant")
METERS = ("gateway", "mcp_tools", "telemetry", "billing_export", "report_api")


class Price(_Strict):
    usd_per_unit: float = 0.0  # per minute held, per request, per use, per GB...
    usd_per_1m_input: float = 0.0  # LLM tokens
    usd_per_1m_output: float = 0.0
    usd_per_compute_second: float = 0.0  # locally hosted models: GPU amortisation, power


class Lease(_Strict):
    """How a leasable resource is opened, kept alive and closed through MCP tools."""

    start_tools: list[str] = Field(default_factory=list)
    stop_tools: list[str] = Field(default_factory=list)  # close one lease (by handle, else the newest)
    stop_all_tools: list[str] = Field(default_factory=list)  # close every lease of this type the principal holds
    activity_tools: list[str] = Field(default_factory=list)  # keep a lease from counting as idle
    handle: LeaseHandle | None = None
    max_concurrent_per_principal: int | None = None
    idle_minutes: float | None = 15  # idle longer than this = zombie
    auto_reclaim: bool = False  # stop zombies and over-time leases by calling the first stop tool
    reclaim_after_minutes: float = Field(3, ge=0)  # grace between flagging a lease and reclaiming it


class GrantSpec(_Strict):
    """How access to a sensitive resource is obtained (OAuth RAR-style grants, time-boxed)."""

    approval: Literal["admin", "workflow"] = "admin"  # workflow: being in a workflow that includes it is enough
    max_minutes: float = Field(480, gt=0)  # longest grant an approver may give


class ResourceType(_Strict):
    """One catalog entry."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    class_: Literal["consumable", "leasable", "access_grant"] = Field("consumable", alias="class")
    urn: str
    title: str = ""
    description: str = ""
    category: str = "other"  # ai_model, compute, device, ci, data, action, saas...
    provider: str = "internal"
    unit: str = "unit"  # token, minute, request, use...
    price: Price = Field(default_factory=Price)
    meter: Literal["gateway", "mcp_tools", "telemetry", "billing_export", "report_api"] = "gateway"
    models: list[str] = Field(default_factory=list)  # ai_model consumables: the model names (globs) it covers
    tools: list[str] = Field(default_factory=list)  # access grants: the tools (globs) it protects
    actions: list[str] = Field(default_factory=list)
    lease: Lease | None = None
    grant: GrantSpec | None = None
    sensitivity: Literal["low", "medium", "high", "critical"] = "low"
    owner: str = ""

    @field_validator("urn")
    @classmethod
    def _urn(cls, v: str) -> str:
        if not re.fullmatch(r"urn:shield:[a-z0-9_.-]+(:[a-zA-Z0-9_.*-]+)+", v):
            raise ValueError(f"urn must look like urn:shield:<category>:<provider>:<type>, got {v!r}")
        return v

    @model_validator(mode="after")
    def _shape(self) -> ResourceType:
        if self.class_ == "leasable" and self.lease is None:
            raise ValueError("a leasable resource needs a `lease` section")
        if self.class_ == "access_grant":
            if not self.tools:
                raise ValueError("an access_grant resource needs the `tools` it protects")
            if self.grant is None:
                self.grant = GrantSpec()
        return self

    def legacy(self) -> Resource:
        """The older `resources:` view the lease tracker and usage reports read."""
        lease = self.lease.model_dump() if self.lease else {"idle_minutes": None}
        per = self.price.usd_per_unit
        return Resource(usd_per_minute=per if self.unit == "minute" else 0.0, usd_per_unit=per, **lease)


class Grant(_Strict):
    """One approved access, shaped like an OAuth Rich Authorization Request (RFC 9396) authorization_details entry."""

    id: str
    type: str = "shield_resource"
    resource: str  # catalog name
    locations: list[str] = Field(default_factory=list)  # URNs
    actions: list[str] = Field(default_factory=list)  # empty = every action of the resource
    workflow: str | None = None  # only inside this workflow; None = any
    expires: float | None = None
    granted_by: str = ""
    reason: str = ""
    granted_at: float | None = None

    def live(self, now: float) -> bool:
        return self.expires is None or self.expires > now


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def _shim_legacy(raw: dict) -> None:
    """Fold the older `resources:` and `budgets.pricing` keys into `catalog:`, in place."""
    catalog = raw.setdefault("catalog", {}) or {}
    raw["catalog"] = catalog
    for name, r in (raw.pop("resources", None) or {}).items():
        r = dict(r or {})
        leasing = {k: r.pop(k) for k in list(r) if k in Lease.model_fields}
        per_minute, per_unit = r.pop("usd_per_minute", 0.0), r.pop("usd_per_unit", 0.0)
        if r:
            raise ValueError(f"resources.{name}: unknown keys {sorted(r)}")
        entry: dict[str, Any] = {}
        if leasing.get("start_tools"):
            entry = {
                "class": "leasable",
                "category": "compute",
                "meter": "mcp_tools",
                "unit": "minute",
                "lease": leasing,
                "price": {"usd_per_unit": per_minute or per_unit},
            }
        elif leasing:
            entry = {"lease": leasing}  # an overlay patch to an existing leasable entry
        else:
            entry = {
                "meter": "report_api",
                "unit": "minute" if per_minute else "unit",
                "price": {"usd_per_unit": per_minute or per_unit},
            }
        if name in catalog:
            _merge(catalog[name], {k: v for k, v in entry.items() if k in ("lease", "price")})
        else:
            catalog[name] = {"urn": f"urn:shield:{entry.get('category', 'other')}:internal:{_slug(name)}", **entry}
    pricing = (raw.get("budgets") or {}).pop("pricing", None) or {}
    for glob, price in pricing.items():
        owner = next((n for n, e in catalog.items() if glob in (e.get("models") or [])), None)
        if owner:
            _merge(catalog[owner].setdefault("price", {}), dict(price or {}))
            continue
        name = _slug(glob)
        while name in catalog:
            name += "-model"
        catalog[name] = {
            "class": "consumable",
            "urn": f"urn:shield:ai_model:unknown:{_slug(glob)}",
            "category": "ai_model",
            "unit": "token",
            "models": [glob],
            "price": dict(price or {}),
        }


class PrincipalPolicy(_Strict):
    """Per-person restrictions. Usually written by admins and detections into the admin overlay."""

    status: Literal["active", "quarantined", "revoked"] = "active"
    budget_scale: float = Field(1.0, ge=0)  # multiplies the per-principal token and USD limits
    approved_workflows: list[str] = Field(default_factory=list)
    grants: list[Grant] = Field(default_factory=list)
    reason: str = ""
    by: str = ""
    since: float | None = None


class Quarantine(_Strict):
    tools: list[str] = Field(default_factory=lambda: ["read_*", "search_*"])  # on top of the role's tools
    budget_scale: float = Field(0.1, ge=0)


DETECTION_RULES = (
    "probing",
    "secret_paste",
    "exfiltration",
    "usage_spike",
    "new_client",
    "tool_drift",
    "off_hours",
    "zombie_resource",
    "unlabeled_resource",
    "permission_bypass",
    "unapproved_mcp_server",
    "rejected_edit_storm",
)


class DetectionRule(_Strict):
    enabled: bool = True
    weight: float = Field(20, ge=0)  # added to the principal's risk score, then decays
    window_minutes: float = Field(10, gt=0)
    count: int = Field(3, ge=1)  # probing, secret_paste: events within the window
    factor: float = Field(3.0, gt=0)  # usage_spike / exfiltration: window tokens vs the baseline window
    min_tokens: int = 2000  # usage_spike: never fire below this many tokens per window
    min_history: int = Field(5, ge=0)  # new_client, tool_drift: events before "new" means anything
    tools: list[str] = Field(default_factory=list)  # tool_drift: sensitive tools; unapproved_mcp_server: approved
    hours_utc: tuple[int, int] = (6, 20)  # off_hours: working hours


class RiskResponse(_Strict):
    auto: bool = True  # off: only alert and recommend
    alert: float = 30
    tighten: float = 60  # budget_scale drops to tighten_budget_scale
    quarantine: float = 90
    tighten_budget_scale: float = Field(0.25, ge=0)


class Detections(_Strict):
    enabled: bool = True
    half_life_minutes: float = Field(120, gt=0)
    rules: dict[str, DetectionRule] = Field(default_factory=dict)
    response: RiskResponse = Field(default_factory=RiskResponse)

    @field_validator("rules")
    @classmethod
    def _known(cls, v: dict[str, DetectionRule]) -> dict[str, DetectionRule]:
        unknown = set(v) - set(DETECTION_RULES)
        if unknown:
            raise ValueError(f"unknown detection rules {sorted(unknown)}; known: {list(DETECTION_RULES)}")
        return v


class OrgDepartment(_Strict):
    teams: list[str] = Field(default_factory=list)
    owner: str = ""


UNASSIGNED = "Unassigned"


class Org(_Strict):
    """The company's organization units: departments, each a list of teams. A team listed nowhere belongs to
    department "Unassigned"."""

    name: str = ""
    departments: dict[str, OrgDepartment] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_department_per_team(self) -> Org:
        seen: dict[str, str] = {}
        for dept, d in self.departments.items():
            for team in d.teams:
                if team in seen and seen[team] != dept:
                    raise ValueError(f"team {team!r} is listed under both {seen[team]!r} and {dept!r}")
                seen[team] = dept
        return self

    def team_departments(self) -> dict[str, str]:
        return {team: dept for dept, d in self.departments.items() for team in d.teams}


class UsageStoreCfg(_Strict):
    path: str = "data/usage.sqlite"  # ":memory:" keeps nothing across restarts


class Privacy(_Strict):
    show_risk_to_employee: bool = True  # employees see the incidents and score that concern them
    require_reason_for_content: bool = True  # admins state a reason to read one person's events; logged


class Audit(_Strict):
    path: str = "data/audit.jsonl"
    store_raw_text: bool = False  # off = only redacted text + sha256 of the original
    ring_size: int = 2000


class Policy(_Strict):
    name: str = "default"
    identity: Identity = Field(default_factory=Identity)
    models: Models = Field(default_factory=Models)
    upstream: Upstream = Field(default_factory=Upstream)
    semantic: SemanticEngine = Field(default_factory=SemanticEngine)
    budgets: Budgets = Field(default_factory=Budgets)
    pii: PatternControl = Field(default_factory=PatternControl)
    secrets: PatternControl = Field(default_factory=PatternControl)
    signatures: SignatureControl = Field(default_factory=SignatureControl)
    tool_access: ToolAccessControl = Field(default_factory=ToolAccessControl)
    semantic_controls: dict[str, SemanticControl] = Field(default_factory=dict)
    teams: dict[str, TeamOverride] = Field(default_factory=dict)
    audit: Audit = Field(default_factory=Audit)
    menu: Menu = Field(default_factory=Menu)
    catalog: dict[str, ResourceType] = Field(default_factory=dict)
    # Derived from `catalog` (older policies set these directly; the loader folds them into the catalog).
    resources: dict[str, Resource] = Field(default_factory=dict, exclude=True)
    principals: dict[str, PrincipalPolicy] = Field(default_factory=dict)
    quarantine: Quarantine = Field(default_factory=Quarantine)
    detections: Detections = Field(default_factory=Detections)
    usage: UsageStoreCfg = Field(default_factory=UsageStoreCfg)
    privacy: Privacy = Field(default_factory=Privacy)
    org: Org = Field(default_factory=Org)
    # Restrictions written from the admin dashboard and by automatic responses, merged over this file.
    admin_overlay: str | None = "data/admin-overlay.yaml"

    version: str = ""  # content hash, filled by the loader

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        if isinstance(data, dict) and (data.get("resources") or (data.get("budgets") or {}).get("pricing")):
            data = {**data, "budgets": dict(data.get("budgets") or {})}
            _shim_legacy(data)
        return data

    @model_validator(mode="after")
    def _known_entities(self) -> Policy:
        from .controls.patterns import PII, SECRETS  # patterns imports this module

        for name, known, cfg in (("pii", PII, self.pii), ("secrets", SECRETS, self.secrets)):
            unknown = set(cfg.entities) - set(known)
            if unknown:
                raise ValueError(f"{name}.entities: unknown {sorted(unknown)}; known: {sorted(known)}")
        self.resources = {n: r.legacy() for n, r in self.catalog.items() if r.class_ != "access_grant" and not r.models}
        self.budgets.pricing = {
            glob: ModelPrice(**r.price.model_dump(exclude={"usd_per_unit"}))
            for r in self.catalog.values()
            for glob in r.models
        }
        for wf_name, wf in self.menu.workflows.items():
            unknown = set(wf.resources) - set(self.catalog)
            if unknown:
                raise ValueError(f"menu.workflows.{wf_name}: unknown resources {sorted(unknown)}")
        for who, pp in self.principals.items():
            unknown = set(pp.approved_workflows) - set(self.menu.workflows)
            if unknown:
                raise ValueError(f"principals.{who}: approved_workflows not on the menu: {sorted(unknown)}")
            unknown = {g.resource for g in pp.grants} - set(self.catalog)
            if unknown:
                raise ValueError(f"principals.{who}: grants for unknown resources {sorted(unknown)}")
        return self

    # ---- catalog lookups -----------------------------------------------------------

    def model_resource(self, model: str | None) -> str | None:
        """The catalog entry metering an LLM model: exact name first, then globs."""
        if not model:
            return None
        models = [(n, g) for n, r in self.catalog.items() for g in r.models]
        return next((n for n, g in models if g == model), None) or next(
            (n for n, g in models if fnmatch(model, g)), None
        )

    def grant_resources(self, tool: str) -> list[str]:
        """Access-grant resources protecting this tool."""
        return [
            n for n, r in self.catalog.items() if r.class_ == "access_grant" and any(fnmatch(tool, g) for g in r.tools)
        ]

    def live_grants(self, pid: str, now: float | None = None) -> list[Grant]:
        now = now or time.time()
        return [g for g in self.principal(pid).grants if g.live(now)]

    def grant_for(self, pid: str, resource: str, action: str | None, workflow: str | None) -> Grant | None:
        return next(
            (
                g
                for g in self.live_grants(pid)
                if g.resource == resource
                and (not g.actions or action is None or action in g.actions)
                and (g.workflow is None or g.workflow == workflow)
            ),
            None,
        )

    def covering_grant(self, pid: str, tool: str, workflow: str | None) -> Grant | None:
        """A live grant that lets this principal call this tool (RBAC and the irreversible list included)."""
        for name in self.grant_resources(tool):
            g = self.grant_for(pid, name, None, workflow)
            if g:
                return g
        return None

    def identity_of(self, pid: str | None = None, email: str | None = None) -> ApiKey | None:
        """The directory entry for a principal id or an email address."""
        for k in self.identity.api_keys.values():
            if (pid and k.principal == pid) or (email and k.email and k.email.lower() == email.lower()):
                return k
        return None

    def principal(self, pid: str) -> PrincipalPolicy:
        return self.principals.get(pid) or _ACTIVE

    def department_of(self, team: str | None) -> str:
        """The department a team belongs to (`org.departments`), else "Unassigned"."""
        for dept, d in self.org.departments.items():
            if team in d.teams:
                return dept
        return UNASSIGNED

    def budget_scale(self, pid: str) -> float:
        pp = self.principal(pid)
        scale = pp.budget_scale
        if pp.status == "quarantined":
            scale = min(scale, self.quarantine.budget_scale)
        return scale

    def for_team(self, team: str) -> Policy:
        """Policy with the team's control overrides applied."""
        override = self.teams.get(team)
        if not override or not override.controls:
            return self
        data = self.model_dump(by_alias=True)
        for name, fields in override.controls.items():
            if name in data and isinstance(data[name], dict):
                _merge(data[name], fields)
            elif name in data["semantic_controls"]:
                _merge(data["semantic_controls"][name], fields)
            else:
                raise ValueError(f"team {team!r} overrides unknown control {name!r}")
        return Policy.model_validate(data)


_ACTIVE = PrincipalPolicy()


def _merge(base: dict, patch: dict, delete_none: bool = False) -> None:
    """Deep merge, so overriding one entity or threshold keeps its siblings."""
    for k, v in patch.items():
        if v is None and delete_none:
            base.pop(k, None)
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v, delete_none)
        else:
            base[k] = v


# What the admin overlay may touch: restrictions and limits, never identity, upstreams or detectors.
OVERLAY_KEYS = {"principals", "menu", "budgets", "catalog", "resources", "detections", "quarantine"}


_ENV = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _expand_env(text: str) -> str:
    """${VAR} or ${VAR:-default} (default also when VAR is empty, as in the shell)."""
    return _ENV.sub(lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), text)


def parse_policy(text: str, overlay: str | None = None) -> Policy:
    expanded = _expand_env(text)
    raw = yaml.safe_load(expanded) or {}
    raw.pop("version", None)
    if overlay:
        patch = yaml.safe_load(overlay) or {}
        if not isinstance(patch, dict):
            raise ValueError("admin overlay must be a mapping")
        bad = set(patch) - OVERLAY_KEYS
        if bad:
            raise ValueError(f"admin overlay may only set {sorted(OVERLAY_KEYS)}, not {sorted(bad)}")
        _merge(raw, patch)
    policy = Policy.model_validate(raw)
    policy.version = hashlib.sha256((expanded + "\0" + (overlay or "")).encode()).hexdigest()[:12]
    for team in policy.teams:  # surface bad overrides at load time, not on first request
        policy.for_team(team)
    return policy


class PolicyStore:
    """Holds the live policy; `poll()` swaps it when the file changes.

    An invalid edit keeps the previous policy and records the error, so a typo
    never drops enforcement.
    """

    def __init__(self, path: str | Path, data_dir: str | Path | None = None):
        self.path = Path(path)
        self.base_dir = self.path.parent
        # Where state lives (usage db, audit log, admin overlay). Unset: paths resolve next to the policy file.
        data_dir = data_dir or os.environ.get("ACL_DATA_DIR") or None
        self.data_dir = Path(data_dir) if data_dir else None
        self.last_error: str | None = None
        # The base file names the overlay (which cannot rename itself), so it is read first.
        self.policy = parse_policy(self.path.read_text())
        try:
            self.policy = parse_policy(self.path.read_text(), self._overlay_text())
        except Exception as e:  # noqa: BLE001 - a broken overlay must not stop the gateway: base policy applies
            self.last_error = f"admin overlay ignored: {e}"
            log.error(self.last_error)
        self.reloads = 0
        self._mtime = self._mtimes()
        self.listeners: list[Any] = []

    @property
    def overlay_path(self) -> Path | None:
        rel = self.policy.admin_overlay
        return self.data_path(rel) if rel else None

    def data_path(self, rel: str) -> Path:
        """A state file named in the policy (`data/usage.sqlite`), moved under `data_dir` when one is set."""
        p = Path(rel)
        if p.is_absolute():
            return p
        if self.data_dir is None:
            return self.base_dir / p
        parts = p.parts[1:] if p.parts and p.parts[0] == "data" else p.parts
        return self.data_dir.joinpath(*parts)

    def _overlay_text(self) -> str | None:
        p = self.overlay_path
        return p.read_text() if p and p.exists() else None

    def _mtimes(self) -> tuple[int, int]:
        p = self.overlay_path
        o = p.stat().st_mtime_ns if p and p.exists() else 0
        return (self.path.stat().st_mtime_ns, o)

    def overlay(self) -> dict:
        text = self._overlay_text()
        return (yaml.safe_load(text) or {}) if text else {}

    def write_overlay(self, patch: dict) -> Policy:
        """Merge `patch` into the admin overlay (None deletes a key). Validated before it is written,
        so an admin action can never leave the gateway on a policy that does not load."""
        path = self.overlay_path
        if path is None:
            raise ValueError("admin_overlay is disabled in the policy")
        data = self.overlay()
        _merge(data, patch, delete_none=True)
        text = yaml.safe_dump(data, sort_keys=False)
        parse_policy(self.path.read_text(), text)  # raises on anything invalid
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(path)
        self._mtime = self._mtimes()
        self.reload()
        if self.last_error:
            raise ValueError(self.last_error)
        return self.policy

    def reload(self) -> bool:
        text = self.path.read_text()
        try:
            new = parse_policy(text, self._overlay_text())
        except Exception as e:  # noqa: BLE001 - any parse/validation error keeps the old policy
            self.last_error = str(e)
            log.error("policy reload rejected: %s", e)
            return False
        self.last_error = None
        if new.version != self.policy.version:
            old = self.policy.version
            self.policy = new
            self.reloads += 1
            log.info("policy reloaded %s -> %s", old, new.version)
            for cb in self.listeners:
                try:
                    cb(old, new)
                except Exception as e:  # noqa: BLE001 - a listener failure must not abort the reload
                    log.error("policy listener failed: %s", e)
        return True

    def poll(self) -> None:
        """Reload if the file changed since the last look."""
        try:
            mtime = self._mtimes()
        except FileNotFoundError:
            return
        if mtime != self._mtime:
            self._mtime = mtime
            self.reload()
