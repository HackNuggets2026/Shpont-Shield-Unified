from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Action(StrEnum):
    """Enforcement ladder, weakest first. A control's `mode` caps how far up it may go."""

    ALLOW = "allow"
    LOG = "log"
    WARN = "warn"
    REDACT = "redact"
    BLOCK = "block"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def cap(self, ceiling: Action) -> Action:
        return self if self.rank <= ceiling.rank else ceiling


_RANK = {a: i for i, a in enumerate(Action)}


class Direction(StrEnum):
    INPUT = "input"  # prompt going to a model
    OUTPUT = "output"  # model completion coming back
    TOOL_CALL = "tool_call"  # agent invoking a tool
    TOOL_RESULT = "tool_result"  # tool output flowing back into the agent
    TOOL_DESCRIPTION = "tool_description"  # MCP tools/list metadata


@dataclass
class Principal:
    id: str
    team: str
    role: str
    authenticated: bool = True


@dataclass
class Context:
    principal: Principal
    direction: Direction
    text: str
    model: str | None = None
    tool: str | None = None
    tool_args: dict[str, Any] | None = None
    channel: str = "api"  # chat | mcp | sdk | dashboard
    # False for re-checks of conversation history: content is inspected, but not charged to budgets.
    metered: bool = True
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    # Attribution: which menu workflow and which task (ticket, branch, run) this traffic belongs to.
    workflow: str | None = None
    # declared (client header, or inherited from its session) | classified (decision model guess) | None.
    # Only declared workflows are gated; a guess is used for reporting, never to refuse.
    workflow_source: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    client: str | None = None  # "ip|user-agent", for stolen-key detection


@dataclass
class Span:
    start: int
    end: int
    label: str


@dataclass
class Finding:
    control: str
    category: str
    action: Action  # after the control's mode cap
    proposed: Action  # before the cap; differs in shadow/log modes
    score: float = 1.0
    detail: str = ""
    spans: list[Span] = field(default_factory=list)
    tier: str = "deterministic"  # deterministic | fast | deep
    shadow: bool = False


@dataclass
class Verdict:
    action: Action
    text: str  # possibly redacted
    findings: list[Finding]
    request_id: str
    policy_version: str
    latency_ms: dict[str, float] = field(default_factory=dict)
    status_code: int = 200
    reason: str = ""
    ts: float = field(default_factory=time.time)

    @property
    def blocked(self) -> bool:
        return self.action is Action.BLOCK
