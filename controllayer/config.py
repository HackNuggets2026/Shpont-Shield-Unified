"""Central policy: one YAML file, validated, versioned by content hash, hot-reloaded."""

from __future__ import annotations

import hashlib
import logging
import os
import re
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
    kind: Literal["human", "agent"] = "human"
    owner: str | None = None  # agents: the employee who decides which resources the agent may use

    @model_validator(mode="after")
    def _owner(self) -> ApiKey:
        if (self.kind == "agent") != (self.owner is not None):
            raise ValueError("agents need an owner; humans must not have one")
        return self


class Integration(_Strict):
    """An external tool (SIEM, EDR) that may raise a person's insider-risk level, never lower it."""

    token_env: str  # environment variable holding this integration's own bearer token
    max_level: Literal["watch", "restricted"] = "watch"  # stronger signals are capped to this
    max_ttl_hours: float = Field(168, gt=0)


class Identity(_Strict):
    require_auth: bool = True
    api_keys: dict[str, ApiKey] = Field(default_factory=dict)
    # Guards /admin/* and /metrics (header x-admin-token or ?token=). Unset = open, for local demos only.
    admin_token: str | None = None
    # Demo only: the employee panel picks whom to show (x-acl-as) instead of asking for a key.
    panel_demo: bool = False
    integrations: dict[Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+$")], Integration] = Field(default_factory=dict)


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


class Entitlement(_Strict):
    roles: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    principals: list[str] = Field(default_factory=list)

    def allows(self, principal: str, team: str, role: str) -> bool:
        return principal in self.principals or team in self.teams or role in self.roles


class Resource(_Strict):
    """A company resource employees can delegate to their agents. Agents never see its secret:
    the gateway brokers every use and injects credentials itself."""

    type: Literal["server", "credential", "saas", "mcp_server"]
    name: str
    description: str = ""
    sensitivity: Literal["low", "medium", "high"] = "medium"
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    entitled: Entitlement = Field(default_factory=Entitlement)
    max_grant_hours: float | None = Field(None, gt=0)
    suspended: bool = False
    # saas/credential: base_url, auth_header, secret_env; server: host; mcp_server: server (upstream.mcp_servers key)
    connection: dict[str, Any] = Field(default_factory=dict)


class RiskLevels(_Strict):
    watch: float = 20
    restricted: float = 60


class AlertSink(_Strict):
    type: Literal["webhook", "file"]
    url: str | None = None
    path: str | None = None
    format: Literal["native", "ocsf", "ecs"] = "native"


class InsiderRisk(_Strict):
    enabled: bool = True
    # Points per finding by the action it proposed; categories can add more.
    weights: dict[Action, float] = Field(
        default_factory=lambda: {Action.LOG: 0.5, Action.WARN: 2, Action.REDACT: 3, Action.BLOCK: 10}
    )
    category_weights: dict[str, float] = Field(default_factory=dict)
    half_life_hours: float = Field(24, gt=0)
    levels: RiskLevels = Field(default_factory=RiskLevels)
    owner_share: Probability = 0.5  # fraction of an agent's points also charged to its owner
    # Under watch: control overrides (merged like a team override) and full-text capture.
    watch_controls: dict[str, dict[str, Any]] = Field(default_factory=dict)
    watch_capture_raw: bool = True
    alert_on_block_while_watched: bool = True
    alert_categories: list[str] = Field(default_factory=list)
    sinks: list[AlertSink] = Field(default_factory=list)


class PiiOverride(_Strict):
    enabled: bool = True
    instructions: str = (
        "Is the personal data in this content necessary to complete the requested task "
        "(for example replying to that customer or filling in their own form)?"
    )
    threshold: Probability = 0.85
    keywords: list[str] = Field(default_factory=list)
    # Only these Privacy Filter labels may be released by the model; regex PII (cards, IBANs) never is.
    labels: list[str] = Field(
        default_factory=lambda: ["private_person", "private_email", "private_phone", "private_address"]
    )


class PiiModelControl(ControlBase):
    """Contextual PII spans from a token classifier (OpenAI Privacy Filter sidecar)."""

    backend: Literal["privacy_filter", "stub", "off"] = "off"
    url: str = "http://localhost:8790"
    timeout_seconds: float = Field(3.0, gt=0)
    min_score: Probability = 0.5
    fail_mode: Literal["open", "closed"] = "open"
    entities: dict[str, Action] = Field(default_factory=dict)
    # Labels replaced by numbered placeholders that are put back into the model's reply.
    reversible: list[str] = Field(default_factory=list)
    # Who may send `x-pii-override: <reason>`; overrides never lift more than `override_max`.
    override_roles: list[str] = Field(default_factory=list)
    override_max: Action = Action.REDACT
    model_override: PiiOverride = Field(default_factory=PiiOverride)

    @field_validator("backend", mode="before")
    @classmethod
    def _yaml_off(cls, v: Any) -> Any:
        return "off" if v is False else v


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


class Budgets(_Strict):
    enabled: bool = True
    shadow: bool = False
    global_: BudgetLimits = Field(default_factory=BudgetLimits, alias="global")
    per_team: dict[str, BudgetLimits] = Field(default_factory=dict)
    per_principal: BudgetLimits = Field(default_factory=BudgetLimits)
    pricing: dict[str, ModelPrice] = Field(default_factory=dict)
    loop_guard: LoopGuard = Field(default_factory=LoopGuard)


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
    resources: dict[str, Resource] = Field(default_factory=dict)
    insider_risk: InsiderRisk = Field(default_factory=InsiderRisk)
    pii_model: PiiModelControl = Field(default_factory=PiiModelControl)

    version: str = ""  # content hash, filled by the loader

    @model_validator(mode="after")
    def _known_entities(self) -> Policy:
        from .controls.patterns import PII, SECRETS  # patterns imports this module

        owners = {k.principal: k for k in self.identity.api_keys.values()}
        for k in self.identity.api_keys.values():
            if k.owner and (k.owner not in owners or owners[k.owner].kind != "human"):
                raise ValueError(f"agent {k.principal!r}: owner {k.owner!r} is not a known human principal")
        for rid, r in self.resources.items():
            if r.type == "mcp_server" and r.connection.get("server") not in self.upstream.mcp_servers:
                raise ValueError(f"resource {rid!r}: connection.server must name an upstream.mcp_servers entry")

        for name, known, cfg in (("pii", PII, self.pii), ("secrets", SECRETS, self.secrets)):
            unknown = set(cfg.entities) - set(known)
            if unknown:
                raise ValueError(f"{name}.entities: unknown {sorted(unknown)}; known: {sorted(known)}")
        return self

    def for_team(self, team: str) -> Policy:
        """Policy with the team's control overrides applied."""
        override = self.teams.get(team)
        if not override or not override.controls:
            return self
        try:
            return self.with_overrides(override.controls)
        except ValueError as e:
            raise ValueError(f"team {team!r}: {e}") from e

    def with_overrides(self, controls: dict[str, dict[str, Any]]) -> Policy:
        """Policy with per-control field overrides deep-merged in."""
        if not controls:
            return self
        data = self.model_dump(by_alias=True)
        for name, fields in controls.items():
            if name in data and isinstance(data[name], dict):
                _merge(data[name], fields)
            elif name in data["semantic_controls"]:
                _merge(data["semantic_controls"][name], fields)
            else:
                raise ValueError(f"override of unknown control {name!r}")
        return Policy.model_validate(data)


def _merge(base: dict, patch: dict) -> None:
    """Deep merge, so overriding one entity or threshold keeps its siblings."""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


_ENV = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _expand_env(text: str) -> str:
    """${VAR} or ${VAR:-default} (default also when VAR is empty, as in the shell)."""
    return _ENV.sub(lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), text)


def parse_policy(text: str) -> Policy:
    expanded = _expand_env(text)
    raw = yaml.safe_load(expanded) or {}
    raw.pop("version", None)
    policy = Policy.model_validate(raw)
    policy.version = hashlib.sha256(expanded.encode()).hexdigest()[:12]
    for team in policy.teams:  # surface bad overrides at load time, not on first request
        policy.for_team(team)
    try:
        policy.with_overrides(policy.insider_risk.watch_controls)
    except ValueError as e:
        raise ValueError(f"insider_risk.watch_controls: {e}") from e
    return policy


class PolicyStore:
    """Holds the live policy; `poll()` swaps it when the file changes.

    An invalid edit keeps the previous policy and records the error, so a typo
    never drops enforcement.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.policy = parse_policy(self.path.read_text())
        self.base_dir = self.path.parent
        self.last_error: str | None = None
        self.reloads = 0
        self._mtime = self.path.stat().st_mtime_ns
        self.listeners: list[Any] = []

    def reload(self) -> bool:
        text = self.path.read_text()
        try:
            new = parse_policy(text)
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
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return
        if mtime != self._mtime:
            self._mtime = mtime
            self.reload()
