"""Central policy: one YAML file, validated, versioned by content hash, hot-reloaded."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .types import Action, Direction

log = logging.getLogger(__name__)


class _Strict(BaseModel):
    # Misspelled keys in a hand-edited policy must fail loudly, not silently disable a control.
    model_config = ConfigDict(extra="forbid")


class ApiKey(_Strict):
    principal: str
    team: str
    role: str


class Identity(_Strict):
    require_auth: bool = True
    api_keys: dict[str, ApiKey] = Field(default_factory=dict)


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
    # tools that need an explicit approval flag because their effect cannot be undone
    irreversible: list[str] = Field(default_factory=list)


class Thresholds(_Strict):
    block: float | None = None
    redact: float | None = None
    warn: float | None = None
    log: float | None = None

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
            unknown = set(self.actions) - set(self.criteria)
            if unknown:
                raise ValueError(f"actions reference unknown criteria: {sorted(unknown)}")
        return self


class SemanticEngine(_Strict):
    backend: Literal["ollama", "heuristic", "off"] = "heuristic"
    url: str = "http://localhost:11434"
    fast_model: str = "tev1:0.8b"
    deep_model: str | None = "nimble"
    # A fast-tier probability inside this band is re-asked to the deep model.
    escalate_band: tuple[float, float] = (0.3, 0.85)
    min_confidence: float = 0.5
    timeout_seconds: float = 3.0
    max_chunk_chars: int = 6000
    fail_mode: Literal["open", "closed"] = "closed"
    keep_alive: str = "30m"


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

    version: str = ""  # content hash, filled by the loader

    def for_team(self, team: str) -> Policy:
        """Policy with the team's control overrides applied."""
        override = self.teams.get(team)
        if not override or not override.controls:
            return self
        data = self.model_dump(by_alias=True)
        for name, fields in override.controls.items():
            if name in data and isinstance(data[name], dict):
                data[name].update(fields)
            elif name in data["semantic_controls"]:
                data["semantic_controls"][name].update(fields)
            else:
                raise ValueError(f"team {team!r} overrides unknown control {name!r}")
        return Policy.model_validate(data)


def parse_policy(text: str) -> Policy:
    raw = yaml.safe_load(text) or {}
    raw.pop("version", None)
    policy = Policy.model_validate(raw)
    policy.version = hashlib.sha256(text.encode()).hexdigest()[:12]
    for team in policy.teams:  # surface bad overrides at load time, not on first request
        policy.for_team(team)
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
                cb(old, new)
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
