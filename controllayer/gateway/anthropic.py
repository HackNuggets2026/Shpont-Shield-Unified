"""Anthropic Messages API (`/v1/messages`, `/v1/messages/count_tokens`) for Claude Code and the
Anthropic SDKs, under the same controls as the OpenAI-compatible chat proxy.

Every system block, message block and tool definition is inspected on every call; image and
document data the gateway cannot read, such as a PDF, is left to `opaque_documents` /
`opaque_images`. Reversible PII becomes placeholders before the model sees it and is restored in
the reply's text and tool_use input. Each reply block is checked before it is released. A streamed
reply is relayed block by block: a block's deltas are held until its `content_block_stop`, checked,
then re-emitted, with `ping`s keeping the connection alive meanwhile.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections.abc import AsyncIterator, Container, Iterator
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .. import servertiming
from ..config import PolicyStore
from ..controls.access import identify
from ..controls.patterns import remask, unmask
from ..controls.pii_model import PII_CONTROLS
from ..engine import ControlLayer, flatten
from ..types import Action, Context, Direction, Finding, Principal, Verdict
from .upstream import anthropic_mock

CHANNEL = "messages"
# Names the caller when its Authorization / x-api-key carry its own Anthropic login (ANTHROPIC_CUSTOM_HEADERS).
IDENTITY_HEADER = "x-acl-key"
# The field of a signed thinking block the upstream verifies and no one reads. Exempt from inspection
# only in a reply the gateway returned, re-sent as history: anywhere else it is ordinary content.
_OPAQUE = {"thinking": "signature", "redacted_thinking": "data"}
# Upstream response headers Claude Code acts on (retries, plan usage).
_RELAYED = ("retry-after", "x-should-retry", "request-id")
_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    429: "rate_limit_error",
}
PING_SECONDS = 10.0


class _Refused(Exception):
    def __init__(self, response: Response):
        self.response = response


def error(status: int, message: str, request_id: str | None = None, action: str | None = None) -> JSONResponse:
    headers = {"x-should-retry": "false"}
    if request_id:
        headers |= {"x-control-request-id": request_id, "x-control-action": action or "block"}
    body = {"type": "error", "error": {"type": _ERROR_TYPES.get(status, "api_error"), "message": message}}
    return JSONResponse({**body, "request_id": request_id}, status_code=status, headers=headers)


def _policy_error(v: Verdict) -> JSONResponse:
    # A content or access block is a 400: Claude Code reports any 403 as a failed login.
    status = 400 if v.status_code == 403 else v.status_code
    resp = error(status, f"blocked by policy: {v.reason}", v.request_id, v.action.value)
    if any(f.category == "rate_limit" for f in v.findings if f.action is Action.BLOCK):
        resp.headers["x-should-retry"] = "true"  # a per-minute limit clears; other budgets do not
        resp.headers["retry-after"] = "30"
    return resp


def _unsafe(v: Verdict) -> JSONResponse:
    """A redaction that could not be applied precisely: refuse rather than forward the original."""
    return error(400, f"cannot redact safely: {v.reason}", v.request_id)


def bare(block: Any) -> Any:
    """A reply block as inspected: a thinking block without its signature string, redacted thinking
    without its encrypted data."""
    if isinstance(block, dict):
        key = _OPAQUE.get(block.get("type"))  # type: ignore[arg-type]
        if key and isinstance(block.get(key), str):
            return {k: v for k, v in block.items() if k != key}
    return block


def _media(content: Any) -> Iterator[dict[str, Any]]:
    """The image and document blocks of a content list, including those in tool results and documents."""
    if not isinstance(content, list):
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_result":
            yield from _media(b.get("content"))
        elif b.get("type") in ("image", "document"):
            yield b
            src = b.get("source")
            if isinstance(src, dict) and src.get("type") == "content":
                yield from _media(src.get("content"))


def _all_media(messages: list[Any]) -> Iterator[dict[str, Any]]:
    for m in messages:
        if isinstance(m, dict):
            yield from _media(m.get("content"))


def _unreadable(block: dict[str, Any]) -> str | None:
    """What of an image or document the gateway cannot read, worded for the caller; None: nothing."""
    src = block.get("source")
    if not isinstance(src, dict) or src.get("type") in ("text", "content"):
        return None
    kind = src.get("type")
    if kind == "base64":
        media = src.get("media_type")
        what = f"{media} data" if isinstance(media, str) else "base64 data"
        return what if isinstance(src.get("data"), str) else f"non-string {what}"
    if kind == "url":
        return "content fetched from a URL"
    if kind == "file":
        return "uploaded file"
    return f"{kind!r} source"


def _image(block: dict[str, Any]) -> bool:
    """An image block whose data, if inline, is a string with an image media type; any other image is
    left to `opaque_documents`."""
    src = block.get("source")
    if block.get("type") != "image" or not isinstance(src, dict):
        return False
    media = src.get("media_type")
    return src.get("type") != "base64" or (
        isinstance(src.get("data"), str) and isinstance(media, str) and media.strip().lower().startswith("image/")
    )


def _text_media(media: Any) -> bool:
    """text/*, JSON, XML, YAML and JavaScript media types, incl. suffixes such as application/ld+json."""
    if not isinstance(media, str):
        return False
    kind = media.split(";")[0].strip().lower()
    return kind.startswith("text/") or kind.rsplit("+", 1)[-1].rsplit("/", 1)[-1] in _TEXT_SUBTYPES


_TEXT_SUBTYPES = {"json", "xml", "yaml", "x-yaml", "javascript"}


def open_documents(messages: list[Any]) -> dict[int, tuple[dict[str, Any], str]]:
    """Turns each base64 document with a text media type into a text source, in place, so every check
    reads (and can mask) its text; `seal_documents` restores the encoding."""
    opened = {}
    for k, b in enumerate(_all_media(messages)):
        src = b.get("source")
        if not (
            b.get("type") == "document"
            and isinstance(src, dict)
            and src.get("type") == "base64"
            and _text_media(src.get("media_type"))
            and isinstance(src.get("data"), str)
        ):
            continue
        # Strictly, whole: a lenient decoder stops at the first padding, hiding whatever follows it.
        compact = "".join(src["data"].split())
        try:
            raw = base64.b64decode(compact, validate=True)
            text = raw.decode()
        except ValueError:
            continue  # not UTF-8 or not base64: unreadable
        if base64.b64encode(raw).decode() != compact:
            continue  # non-canonical (stray trailing bits): decoders disagree on it
        opened[k] = (src, text)
        b["source"] = {**src, "type": "text", "data": text}
    return opened


def seal_documents(messages: list[Any], opened: dict[int, tuple[dict[str, Any], str]]) -> None:
    """Re-encodes what `open_documents` decoded: the original bytes unless a check changed the text."""
    for k, b in enumerate(_all_media(messages)):
        if k in opened:
            src, text = opened[k]
            data = b["source"]["data"]
            b["source"] = src if data == text else {**src, "data": base64.b64encode(data.encode()).decode()}


def readable(content: Any, ours: bool = False) -> Any:
    """A content list as inspected: the blocks of a reply the gateway returned (`ours`) `bare`, images
    and documents without the data the gateway cannot read (`opaque_documents` / `opaque_images`
    decide on those)."""
    if not isinstance(content, list):
        return content
    out = []
    for b in content:
        if isinstance(b, dict):
            b = bare(b) if ours else b
            t, src = b.get("type"), b.get("source")
            if t == "tool_result" and isinstance(b.get("content"), list):
                b = {**b, "content": readable(b["content"])}
            elif t in ("image", "document") and isinstance(src, dict):
                if src.get("type") == "content":
                    b = {**b, "source": {**src, "content": readable(src.get("content"))}}
                elif _unreadable(b) and "data" in src:
                    b = {**b, "source": {k: v for k, v in src.items() if k != "data"}}
        out.append(b)
    return out


def inspectable(messages: list[Any], ours: Container[int] = ()) -> list[Any]:
    """Messages as inspected: each message's content `readable`; `ours`: the indexes of replies the
    gateway returned."""
    return [
        {**m, "content": readable(m["content"], i in ours)} if isinstance(m, dict) else m
        for i, m in enumerate(messages)
    ]


def reply_signature(content: Any) -> str:
    """What identifies assistant content the gateway returned when the client re-sends it: the
    blocks without `cache_control` (added by the client) or null fields, key order ignored."""
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
    if not isinstance(blocks, list):
        return ""
    norm = [
        {k: v for k, v in b.items() if k != "cache_control" and v is not None} if isinstance(b, dict) else b
        for b in blocks
    ]
    return json.dumps(norm, sort_keys=True, default=str)


def _block_text(block: dict[str, Any]) -> str:
    """The text a reply block is judged on: a plain text block is just its text."""
    if block.get("type") == "text" and set(block) <= {"type", "text"} and isinstance(block.get("text"), str):
        return block["text"]
    return flatten({k: v for k, v in bare(block).items() if k != "type"})


# The field of a request block its own per-block check reads, and the shape that check accepts.
_OWN = {"text": ("text", str), "thinking": ("thinking", str), "tool_result": ("content", str | list)}


def _rest(block: dict[str, Any], ours: bool) -> dict[str, Any]:
    """What the per-block checks of a request do not read: all of a block but its own text."""
    key, shape = _OWN.get(block.get("type"), (None, ()))  # type: ignore[arg-type]
    if not isinstance(block.get(key), shape):
        key = None  # malformed: inspect the whole block here instead
    return {k: v for k, v in readable([block], ours)[0].items() if k != key and k != "type"}


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def block_events(index: int, block: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """A complete content block as the start / delta / stop events of a stream."""
    block = dict(block)
    unknown = block.pop("_deltas", [])  # delta types this gateway does not know, as received
    t = block.get("type")
    deltas: list[dict[str, Any]] = []
    if t == "text":
        start = {k: v for k, v in block.items() if k not in ("text", "citations")} | {"text": ""}
        if block.get("citations") is not None:
            start["citations"] = []
            deltas += [{"type": "citations_delta", "citation": c} for c in block["citations"]]
        if block.get("text"):
            deltas.append({"type": "text_delta", "text": block["text"]})
    elif t in ("tool_use", "server_tool_use"):
        start = {**block, "input": {}}
        deltas.append({"type": "input_json_delta", "partial_json": json.dumps(block.get("input", {}))})
    elif t == "thinking":
        start = {k: v for k, v in block.items() if k not in ("thinking", "signature")} | {"thinking": ""}
        if block.get("thinking"):
            deltas.append({"type": "thinking_delta", "thinking": block["thinking"]})
        if block.get("signature"):
            deltas.append({"type": "signature_delta", "signature": block["signature"]})
    else:  # redacted thinking, server tool results: complete in their start event
        start = block
    deltas += unknown
    return [
        ("content_block_start", {"type": "content_block_start", "index": index, "content_block": start}),
        *(("content_block_delta", {"type": "content_block_delta", "index": index, "delta": d}) for d in deltas),
        ("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


def message_events(msg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """A complete message as the event sequence of a stream (the mock upstream streams this way)."""
    usage = msg.get("usage") or {}
    head = {**msg, "content": [], "stop_reason": None, "stop_sequence": None, "usage": {**usage, "output_tokens": 0}}
    events = [("message_start", {"type": "message_start", "message": head})]
    for i, b in enumerate(msg.get("content") or []):
        events += block_events(i, b)
    delta = {"stop_reason": msg.get("stop_reason"), "stop_sequence": msg.get("stop_sequence")}
    events.append(("message_delta", {"type": "message_delta", "delta": delta, "usage": usage}))
    events.append(("message_stop", {"type": "message_stop"}))
    return events


def _event_data(lines: list[str]) -> dict[str, Any]:
    data = json.loads("\n".join(lines))
    if not isinstance(data, dict):
        raise ValueError("event data is not a JSON object")
    return data


async def sse_events(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Raises ValueError on data that is not a JSON object."""
    event, data = "", []
    async for line in lines:
        if line == "":
            if data:
                yield event or "message", _event_data(data)
            event, data = "", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event or "message", _event_data(data)


class _Block:
    """One streamed content block, assembled from its events; the raw events are kept for replay."""

    def __init__(self, start: dict[str, Any]):
        self.block = dict(start)
        self.raw: list[tuple[str, dict[str, Any]]] = []
        self._json: list[str] = []

    def add(self, delta: dict[str, Any]) -> None:
        t, b = delta.get("type"), self.block
        if t == "text_delta":
            b["text"] = b.get("text", "") + str(delta.get("text", ""))
        elif t == "input_json_delta":
            self._json.append(str(delta.get("partial_json", "")))
        elif t == "thinking_delta":
            b["thinking"] = b.get("thinking", "") + str(delta.get("thinking", ""))
        elif t == "signature_delta":
            b["signature"] = delta.get("signature")
        elif t == "citations_delta":
            b["citations"] = [*(b.get("citations") or []), delta.get("citation")]
        else:  # a delta type this gateway does not know: still inspected
            b.setdefault("_deltas", []).append(delta)

    def done(self) -> dict[str, Any]:
        if self._json:
            raw = "".join(self._json)
            try:
                self.block["input"] = json.loads(raw) if raw.strip() else {}
            except ValueError:
                self.block["input"] = raw  # e.g. cut off at max_tokens: inspected as the raw text
        return self.block


class _Reply:
    """Checks each reply block before release, the way the chat proxy checks a completion."""

    def __init__(
        self,
        layer: ControlLayer,
        principal: Principal,
        model: str | None,
        request_id: str,
        mask_map: dict[str, str],
        known_pii: frozenset[str],
    ):
        self.layer, self.principal, self.model = layer, principal, model
        self.request_id, self.mask_map, self.known_pii = request_id, mask_map, known_pii
        self.action = Action.ALLOW
        self.tool_uses = 0
        self.released: list[dict[str, Any]] = []

    async def check(self, block: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        t = block.get("type")
        out: dict[str, Any] | None = block
        if isinstance(t, str):
            direction = Direction.TOOL_RESULT if t.endswith("_tool_result") else Direction.OUTPUT
            ctx = Context(
                self.principal,
                direction,
                _block_text(block),
                model=self.model,
                channel=CHANNEL,
                request_id=self.request_id,
                known_pii=self.known_pii,
                fetched=True,
            )
            v = await self.layer.evaluate(ctx, extra)
            if v.action.rank > self.action.rank:
                self.action = v.action
            if v.action is Action.REDACT:
                # A thinking block is signed: it is released unchanged or not at all.
                ok = self.layer.spans_only(v) and t not in _OPAQUE and "_deltas" not in block
                out = self.layer.redact_tree(block, self.principal, direction) if ok else None
            if v.blocked or out is None:
                # The reason stays in the audit trail, not the text: clients re-send this as history.
                out = {"type": "text", "text": f"[Withheld by policy, request {self.request_id}]"}
            elif t in ("text", "tool_use"):
                out = unmask(out, self.mask_map)  # the agent acts on, and the employee sees, the real values
        elif not isinstance(t, str):
            out = {"type": "text", "text": f"[Withheld by policy, request {self.request_id}]"}
        assert out is not None
        self.tool_uses += out.get("type") == "tool_use"
        self.released.append(out)
        return out

    def remember(self) -> None:
        """Marks the released blocks as the gateway's own, in the form a client re-sends them."""
        sent = [{k: v for k, v in b.items() if k != "_deltas"} for b in self.released]
        self.layer.remember_reply(self.principal, reply_signature(sent))

    def stop_reason(self, upstream: Any) -> Any:
        # Every tool_use withheld: nothing left for the agent to run.
        return "end_turn" if upstream == "tool_use" and not self.tool_uses else upstream


def _usage_totals(usage: dict[str, Any]) -> tuple[int, int, int, int]:
    def n(k: str) -> int:
        x = usage.get(k)
        return x if isinstance(x, int) and not isinstance(x, bool) and x >= 0 else 0

    return (
        n("input_tokens"),
        n("output_tokens"),
        n("cache_creation_input_tokens"),
        n("cache_read_input_tokens"),
    )


def mount(app: FastAPI, layer: ControlLayer, store: PolicyStore, http: httpx.AsyncClient) -> None:
    def caller(request: Request) -> tuple[Principal, dict[str, str]]:
        """The principal, and the caller's own Anthropic credentials to forward (empty: use ours).

        With `x-acl-key`, that gateway key names the caller and its Authorization / x-api-key are its
        own login (a claude.ai seat), forwarded when the policy allows and never stored or logged.
        Without it, Authorization or x-api-key carries a gateway key; in demo mode any other caller is the
        demo principal."""
        policy = store.policy
        auth = request.headers.get("authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        api_key = request.headers.get("x-api-key")
        named = request.headers.get(IDENTITY_HEADER)
        if named is None:
            return identify(policy, bearer or api_key), {}
        own: dict[str, str] = {}
        if policy.upstream.anthropic.passthrough_auth:
            ours = policy.identity.api_keys
            if auth and bearer not in ours:
                own["authorization"] = auth
            if api_key and api_key not in ours:
                own["x-api-key"] = api_key
        return identify(policy, named), own

    async def inspect(
        principal: Principal, body: dict[str, Any], override: str | None, counting: bool
    ) -> tuple[dict[str, Any], Context, dict[str, str], frozenset[str]]:
        """Check every part of the request; returns the body to forward, the metered context, the
        request's masks and the PII the caller supplied. Raises _Refused."""
        model = body.get("model")
        if model is not None and not isinstance(model, str):
            raise _Refused(error(400, "model must be a string"))
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
            raise _Refused(error(400, "messages must be a non-empty list of objects"))
        mask_map: dict[str, str] = {}
        known: set[str] = set()
        # The new turn: everything after the last assistant message (Claude Code ends it with a `system`
        # reminder that is the same every turn). Older messages are history, scored once.
        new = next((i + 1 for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "assistant"), 0)
        opened = open_documents(messages)
        cfg = store.policy.upstream.anthropic
        for i, m in enumerate(messages):
            for b in _media(m.get("content")):
                what = _unreadable(b)
                kind = "image" if b.get("type") == "image" else "document"
                policy = "image" if _image(b) else "document"
                mode = cfg.opaque_images if policy == "image" else cfg.opaque_documents
                # A logged one is recorded once, in the turn that adds it.
                if what is None or mode == "allow" or (mode == "log" and (counting or i < new)):
                    continue
                ctx = Context(principal, Direction.INPUT, "", model=model, channel=CHANNEL, metered=False)
                act = Action.BLOCK if mode == "block" else Action.LOG
                setting = f"upstream.anthropic.opaque_{policy}s"
                noun = "an image" if kind == "image" else "a document"
                v = Verdict(
                    act,
                    "",
                    [Finding("opaque_content", kind, act, act, detail=what)],
                    ctx.request_id,
                    store.policy.version,
                    status_code=400 if act is Action.BLOCK else 200,
                    reason=f"{noun}'s {what} cannot be inspected by the gateway ({setting}: {mode})",
                )
                layer.audit.record(ctx, v, inspected=False)
                if v.blocked:
                    raise _Refused(_policy_error(v))

        async def check(
            text: str, direction: Direction, resent: bool, fetched: bool = False
        ) -> tuple[Context, Verdict]:
            ctx = Context(
                principal,
                direction,
                text,
                model=model,
                channel=CHANNEL,
                metered=False,
                resent=resent,
                fetched=fetched,
                pii_override=override,
                mask_map=mask_map,
            )
            v = await layer.evaluate(ctx)
            if v.blocked:
                raise _Refused(_policy_error(v))
            known.update(ctx.text[s.start : s.end] for f in v.findings if f.control in PII_CONTROLS for s in f.spans)
            return ctx, v

        def tree(obj: Any, v: Verdict, direction: Direction) -> Any:
            if v.action is not Action.REDACT:
                return obj
            cleaned = layer.redact_tree(obj, principal, direction) if layer.spans_only(v) else None
            if cleaned is None:
                raise _Refused(_unsafe(v))
            return cleaned

        async def blocks(content: list[Any], direction: Direction, resent: bool, fetched: bool) -> list[dict[str, Any]]:
            """Each text, thinking and tool_result block on its own, so masks land in the right place."""
            if not all(isinstance(b, dict) for b in content):
                raise _Refused(error(400, "content blocks must be objects"))
            out = []
            for b in content:
                t = b.get("type")
                if t == "text" and isinstance(b.get("text"), str):
                    _, v = await check(b["text"], direction, resent, fetched=fetched)
                    if v.action is Action.REDACT:
                        b = {**b, "text": v.text}
                elif t == "thinking" and isinstance(b.get("thinking"), str):
                    _, v = await check(b["thinking"], direction, resent, fetched=fetched)
                    if v.action is Action.REDACT:
                        b = {**b, "thinking": v.text}  # its signature no longer holds; the client recovers
                elif t == "tool_result" and isinstance(b.get("content"), str | list):
                    value = b["content"]
                    _, v = await check(
                        value if isinstance(value, str) else flatten(readable(value)), Direction.TOOL_RESULT, resent
                    )
                    if v.action is Action.REDACT:
                        b = {
                            **b,
                            "content": v.text if isinstance(value, str) else tree(value, v, Direction.TOOL_RESULT),
                        }
                out.append(b)
            return out

        system = body.get("system")
        if isinstance(system, str):
            _, v = await check(system, Direction.INPUT, resent=True)
            system = v.text if v.action is Action.REDACT else system
        elif isinstance(system, list):
            system = await blocks(system, Direction.INPUT, resent=True, fetched=False)
            if any(rest := [_rest(b, ours=False) for b in system]):
                system = tree(system, (await check(flatten(rest), Direction.INPUT, resent=True))[1], Direction.INPUT)
        elif system is not None:
            raise _Refused(error(400, "system must be a string or a list of blocks"))

        out_messages = []
        returned: set[int] = set()
        for i, m in enumerate(messages):
            role, content = m.get("role"), m.get("content")
            resent = counting or i < new
            # A reply this gateway returned, re-sent as history, is the model's text, not the employee's.
            ours = role == "assistant" and layer.is_our_reply(principal, reply_signature(content))
            if ours:
                returned.add(i)
            if isinstance(content, str):
                _, v = await check(content, Direction.INPUT, resent, fetched=ours)
                content = v.text if v.action is Action.REDACT else content
                parts: list[Any] = []
            elif isinstance(content, list):
                content = await blocks(content, Direction.INPUT, resent, fetched=ours)
                parts = [_rest(b, ours) for b in content]
            else:
                raise _Refused(error(400, f"messages[{i}].content must be a string or a list of blocks"))
            # Everything else: tool_use, images, documents, unknown block types and keys. Of a returned
            # reply only its own blocks are the model's; keys and `cache_control` added to it are the caller's.
            fields = {k: x for k, x in m.items() if k not in ("role", "content")}
            model_parts = [{k: x for k, x in p.items() if k != "cache_control"} for p in parts] if ours else []
            caller_parts = [fields, [p.get("cache_control") for p in parts] if ours else parts]
            for part, fetched in ((model_parts, True), (caller_parts, False)):
                if text := flatten(part):
                    _, rv = await check(text, Direction.INPUT, resent, fetched=fetched)
                    if rv.action is Action.REDACT:
                        fields, content = tree([fields, content], rv, Direction.INPUT)
            out_messages.append({"role": role, "content": content, **fields})

        out = {**body, "messages": out_messages}
        if "system" in body:
            out["system"] = system
        declared = body.get("tools")
        if declared is not None:
            if not isinstance(declared, list):
                raise _Refused(error(400, "tools must be a list"))
            kept = []
            for tool in declared:
                _, tv = await check(flatten(tool), Direction.TOOL_DESCRIPTION, resent=True)
                kept.append(tree(tool, tv, Direction.TOOL_DESCRIPTION))
            out["tools"] = kept

        # Gates and budgets once per request, over the whole new turn: the loop guard sees what changed,
        # a token count is not charged.
        metered_ctx = Context(
            principal,
            Direction.INPUT,
            flatten([m.get("content") for m in inspectable(messages[new:])]),
            model=model,
            channel=CHANNEL,
            metered=not counting,
        )
        gv = await layer.gate(metered_ctx)
        if gv.blocked:
            raise _Refused(_policy_error(gv))

        # A masked value stays masked wherever it recurs, e.g. in an earlier reply restored for the caller.
        out = remask(out, mask_map)
        # Backstop: every remaining field (metadata, tool_choice, output_config, anything a future API
        # adds) gets the deterministic detectors before it leaves.
        sweep = Context(
            principal,
            Direction.INPUT,
            flatten({**out, "messages": inspectable(out["messages"], returned)}),
            model=model,
            channel=CHANNEL,
            metered=False,
            scored=False,
        )
        sv = await layer.evaluate(sweep, semantic=False, audit_allow=False)
        if sv.blocked:
            raise _Refused(_policy_error(sv))
        if sv.action is Action.REDACT:
            cleaned = layer.redact_tree(out, principal, Direction.INPUT)
            if cleaned is None:
                raise _Refused(_unsafe(sv))
            out = cleaned
        seal_documents(out["messages"], opened)
        return out, metered_ctx, mask_map, frozenset(known)

    def upstream_request(request: Request, own: dict[str, str], path: str, body: dict[str, Any]) -> httpx.Request:
        cfg = store.policy.upstream.anthropic
        # anthropic-version, anthropic-beta (incl. the OAuth capability) and future anthropic-* verbatim.
        headers = {k: v for k, v in request.headers.items() if k.startswith("anthropic-")}
        headers.setdefault("anthropic-version", "2023-06-01")
        if own:
            headers |= own
        else:
            key = os.environ.get(cfg.api_key_env) if cfg.api_key_env else None
            if not key:
                raise _Refused(error(502, "the gateway has no Anthropic credential configured"))
            headers["x-api-key"] = key
        url = cfg.url.rstrip("/") + path + (f"?{request.url.query}" if request.url.query else "")
        return http.build_request("POST", url, json=body, headers=headers, timeout=httpx.Timeout(600, connect=10))

    def relayed(resp: httpx.Response) -> dict[str, str]:
        return {k: v for k, v in resp.headers.items() if k in _RELAYED or k.startswith("anthropic-ratelimit-")}

    def passthrough(resp: httpx.Response, content: bytes) -> Response:
        """An upstream error, unchanged: Claude Code's recovery matches on its wording."""
        media = resp.headers.get("content-type", "application/json")
        return Response(content, status_code=resp.status_code, headers=relayed(resp), media_type=media)

    def charge(metered: Context, model: str | None, usage: dict[str, Any], seconds: float) -> dict[str, Any]:
        inp, outp, write, read = _usage_totals(usage)
        cost = layer.ledger.record(
            metered, layer.policy_for(metered.principal.team), model or "unknown", inp, outp, seconds, write, read
        )
        return {"usd": round(cost, 6), "tokens": inp + outp + write + read}

    @app.api_route("/api/hello", methods=["GET", "HEAD"])
    async def hello():
        """Claude Code's connection-warming probe."""
        return Response(status_code=200)

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request):
        body = await _body(request)
        if isinstance(body, Response):
            return body
        principal, own = caller(request)
        try:
            # The whole conversation leaves the gateway here too, so it gets the same inspection.
            out, _, _, _ = await inspect(principal, body, request.headers.get("x-pii-override"), counting=True)
            if store.policy.upstream.anthropic.backend == "mock":
                return JSONResponse({"input_tokens": max(1, len(json.dumps(out)) // 4)})
            req = upstream_request(request, own, "/v1/messages/count_tokens", out)
        except _Refused as r:
            return r.response
        try:
            resp = await servertiming.timed(http.send(req))
        except httpx.HTTPError as e:
            return error(502, f"upstream unavailable: {type(e).__name__}")
        if resp.status_code != 200:
            return passthrough(resp, resp.content)
        return Response(resp.content, headers=relayed(resp), media_type="application/json")

    @app.post("/v1/messages")
    async def messages(request: Request):
        body = await _body(request)
        if isinstance(body, Response):
            return body
        principal, own = caller(request)
        model = body.get("model")
        t0 = time.perf_counter()
        try:
            out, metered, mask_map, known = await inspect(
                principal, body, request.headers.get("x-pii-override"), counting=False
            )
            req = None
            if store.policy.upstream.anthropic.backend != "mock":
                req = upstream_request(request, own, "/v1/messages", out)
        except _Refused as r:
            return r.response
        reply = _Reply(layer, principal, model, metered.request_id, mask_map, known)
        headers = {"x-control-request-id": metered.request_id}

        if not body.get("stream"):
            if req is None:
                msg, upstream_headers = anthropic_mock(out), {}
            else:
                try:
                    resp = await servertiming.timed(http.send(req))
                except httpx.HTTPError as e:
                    return error(502, f"upstream unavailable: {type(e).__name__}")
                if resp.status_code != 200:
                    return passthrough(resp, resp.content)
                try:
                    msg = resp.json()
                except ValueError:
                    return error(502, "upstream returned invalid JSON")
                upstream_headers = relayed(resp)
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list) or not all(isinstance(b, dict) for b in content):
                return error(502, "upstream returned a malformed message")
            cost = charge(metered, model, msg.get("usage") or {}, time.perf_counter() - t0)
            for k, block in enumerate(content):
                await reply.check(block, cost if k == len(content) - 1 else None)
            msg = {**msg, "content": reply.released, "stop_reason": reply.stop_reason(msg.get("stop_reason"))}
            reply.remember()
            headers |= upstream_headers | {"x-control-action": reply.action.value}
            return JSONResponse(msg, headers=headers)

        if req is None:
            source: AsyncIterator[tuple[str, dict[str, Any]]] = _replay(message_events(anthropic_mock(out)))
            closer = None
        else:
            try:
                resp = await servertiming.timed(http.send(req, stream=True))
            except httpx.HTTPError as e:
                return error(502, f"upstream unavailable: {type(e).__name__}")
            if resp.status_code != 200:
                content = await resp.aread()
                await resp.aclose()
                return passthrough(resp, content)
            headers |= relayed(resp)
            source, closer = sse_events(resp.aiter_lines()), resp.aclose
        stream = _relay(source, closer, reply, lambda usage: charge(metered, model, usage, time.perf_counter() - t0))
        return StreamingResponse(stream, media_type="text/event-stream", headers=headers)

    async def _relay(source, closer, reply: _Reply, charge_usage) -> AsyncIterator[str]:
        """Re-emit the upstream stream with every content block checked before it is released."""
        queue: asyncio.Queue = asyncio.Queue()

        async def pump() -> None:
            try:
                async for item in source:
                    await queue.put(item)
            except Exception as e:  # noqa: BLE001 - a broken upstream stream ends ours with an error event
                await queue.put(("_failed", {"error": f"{type(e).__name__}"}))
            finally:
                await queue.put(None)

        task = asyncio.create_task(pump())
        usage: dict[str, Any] = {}
        charged: dict[str, Any] | None = None
        pending: _Block | None = None
        current: _Block | None = None
        index = 0
        sent = time.monotonic()

        async def release(block: _Block, extra: dict[str, Any] | None) -> list[str]:
            nonlocal index
            assembled = dict(block.block)
            released = await reply.check(assembled, extra)
            if released == assembled:
                events = [(e, {**d, "index": index}) for e, d in block.raw]  # unchanged: byte-faithful replay
            else:
                events = block_events(index, released)
            index += 1
            return [_sse(e, d) for e, d in events]

        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), max(0.05, PING_SECONDS - (time.monotonic() - sent)))
                except TimeoutError:
                    # Held deltas send no bytes; the client gives up on a silent stream.
                    yield _sse("ping", {"type": "ping"})
                    sent = time.monotonic()
                    continue
                if item is None:
                    break
                event, data = item
                chunks: list[str] = []
                if event == "_failed":
                    yield _sse(
                        "error", {"type": "error", "error": {"type": "api_error", "message": "upstream stream failed"}}
                    )
                    return
                kind = data.get("type", event)
                if pending is not None and kind != "content_block_stop":
                    # The block after the last one carries the spend in its audit row.
                    extra = None
                    if kind == "message_delta":
                        usage |= {k: v for k, v in (data.get("usage") or {}).items() if v is not None}
                        charged = charged or charge_usage(usage)
                        extra = charged
                    chunks += await release(pending, extra)
                    pending = None
                if kind == "message_start":
                    usage |= (data.get("message") or {}).get("usage") or {}
                    chunks.append(_sse(event, data))
                elif kind == "content_block_start":
                    current = _Block(data.get("content_block") or {})
                    current.raw.append((event, data))
                elif kind == "content_block_delta" and current is not None:
                    current.add(data.get("delta") or {})
                    current.raw.append((event, data))
                elif kind == "content_block_stop" and current is not None:
                    current.raw.append((event, data))
                    current.done()
                    pending, current = current, None
                elif kind == "message_delta":
                    usage |= {k: v for k, v in (data.get("usage") or {}).items() if v is not None}
                    delta = dict(data.get("delta") or {})
                    if "stop_reason" in delta:
                        delta["stop_reason"] = reply.stop_reason(delta["stop_reason"])
                    chunks.append(_sse(event, {**data, "delta": delta}))
                elif kind == "error":
                    chunks.append(_sse(event, data))
                    for c in chunks:
                        yield c
                    return
                elif kind != "content_block_delta":  # ping, message_stop, event types added later
                    chunks.append(_sse(event, data))
                for c in chunks:
                    yield c
                if chunks:
                    sent = time.monotonic()
            if pending is not None:  # a stream that ended without message_delta
                for c in await release(pending, None):
                    yield c
        finally:
            task.cancel()
            if closer:
                await closer()
            if charged is None and usage:
                charge_usage(usage)  # the client went away or the stream ended early: still spent
            reply.remember()


async def _replay(events: list[tuple[str, dict[str, Any]]]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    for e in events:
        yield e


async def _body(request: Request) -> dict[str, Any] | Response:
    try:
        body = await request.json()
    except ValueError as e:
        return error(400, f"body is not valid JSON: {e}")
    if not isinstance(body, dict):
        return error(400, "body must be a JSON object")
    return body
