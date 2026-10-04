"""Contextual PII (Privacy Filter sidecar): detection, reversible masking, overrides, failure modes."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from controllayer.decision import ScriptedBackend
from controllayer.gateway.app import create_app
from services.privacy_filter.server import decode

from .conftest import ADMIN, KEYS, edit_policy, guard

NAME = "my name is Jan Kowalski"


def recording(policy_dir, backend=None, pf=None):
    """App whose upstream LLM (and optionally Privacy Filter sidecar) are recorded mocks."""
    sent = []

    def handler(req):
        if req.url.path == "/detect":
            return httpx.Response(200, json=pf(json.loads(req.content)["text"]))
        body = json.loads(req.content)
        sent.append(body)
        last = body["messages"][-1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": f"Hello {last}"}}], "usage": {}})

    edit_policy(policy_dir, lambda p: p["upstream"].update(backend="ollama", url="http://llm.example"))
    if pf:
        edit_policy(policy_dir, lambda p: p["pii_model"].update(backend="privacy_filter", url="http://pf.example"))
    app = create_app(
        policy_dir / "policy.yaml",
        backend=backend,
        watch=False,
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return TestClient(app, headers={"x-admin-token": ADMIN}), sent


def chat(c, messages, who="alice", headers=None):
    return c.post(
        "/v1/chat/completions",
        headers={**KEYS[who], **(headers or {})},
        json={"model": "mock-model", "messages": messages},
    )


# --- BIOES decoding in the sidecar ------------------------------------------------------


def tok(tag, start, end, score=0.9):
    return {"entity": tag, "start": start, "end": end, "score": score}


def test_decode_merges_bioes_spans():
    toks = [
        tok("O", 0, 2),
        tok("B-private_person", 3, 6),
        tok("I-private_person", 7, 9),
        tok("E-private_person", 10, 14),
        tok("O", 15, 16),
        tok("S-private_email", 17, 30),
    ]
    assert [(s["label"], s["start"], s["end"]) for s in decode(toks)] == [
        ("private_person", 3, 14),
        ("private_email", 17, 30),
    ]


def test_decode_splits_on_new_begin_and_type_change():
    toks = [tok("B-private_person", 0, 3), tok("B-private_person", 4, 7), tok("I-private_address", 8, 12)]
    assert [(s["label"], s["start"]) for s in decode(toks)] == [
        ("private_person", 0),
        ("private_person", 4),
        ("private_address", 8),
    ]


def test_decode_keeps_stray_inside_tag():
    assert decode([tok("I-secret", 5, 9)])[0]["label"] == "secret"


# --- reversible masking ------------------------------------------------------------------


def test_model_never_sees_name_but_employee_does(policy_dir):
    c, sent = recording(policy_dir)
    r = chat(c, [{"role": "user", "content": NAME}])
    assert "Jan Kowalski" not in json.dumps(sent[0])
    assert sent[0]["messages"][0]["content"] == "my name is <PRIVATE_PERSON_1>"
    assert r.json()["choices"][0]["message"]["content"] == "Hello my name is Jan Kowalski"


def test_placeholders_are_consistent_across_messages(policy_dir):
    c, sent = recording(policy_dir)
    chat(
        c,
        [
            {"role": "user", "content": "customer Anna Nowak called"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "customer Anna Nowak and customer Jan Kowalski"},
        ],
    )
    msgs = [m["content"] for m in sent[0]["messages"]]
    assert msgs[0] == "customer <PRIVATE_PERSON_1> called"
    assert msgs[2] == "customer <PRIVATE_PERSON_1> and customer <PRIVATE_PERSON_2>"


def test_audit_never_holds_the_masked_value(policy_dir):
    c, _ = recording(policy_dir)
    chat(c, [{"role": "user", "content": NAME}])
    assert "Kowalski" not in (policy_dir / "data/audit.jsonl").read_text()


def test_non_chat_directions_are_redacted_not_masked(client):
    body = guard(client, "customer Jan Kowalski paid", direction="tool_result").json()
    assert body["text"] == "customer [REDACTED:private_person] paid"


# --- overrides ---------------------------------------------------------------------------


def test_permitted_role_can_override_with_reason(policy_dir):
    c, sent = recording(policy_dir)
    r = chat(
        c,
        [{"role": "user", "content": "reply to customer Jan Kowalski"}],
        who="bob",
        headers={"x-pii-override": "ticket 991 needs the name"},
    )
    assert r.status_code == 200 and "Jan Kowalski" in sent[0]["messages"][0]["content"]
    log = (policy_dir / "data/audit.jsonl").read_text()
    assert "overridden by user: ticket 991" in log


def test_other_roles_cannot_override_and_attempt_is_logged(policy_dir):
    c, sent = recording(policy_dir)
    chat(c, [{"role": "user", "content": NAME}], headers={"x-pii-override": "trust me"})
    assert "Jan Kowalski" not in json.dumps(sent[0])
    assert "pii_override" in (policy_dir / "data/audit.jsonl").read_text()


def test_override_never_lifts_a_block(policy_dir):
    c, _ = recording(policy_dir)  # finance blocks card numbers
    r = chat(c, [{"role": "user", "content": "card 4111 1111 1111 1111"}], who="bob", headers={"x-pii-override": "x"})
    assert r.status_code == 403


def test_model_override_when_pii_is_needed(policy_dir):
    sb = ScriptedBackend({"pii_necessary": [("reply to", 0.95)]})
    c, sent = recording(policy_dir, backend=sb)
    chat(c, [{"role": "user", "content": "reply to customer Jan Kowalski about the refund"}])
    assert "Jan Kowalski" in sent[0]["messages"][0]["content"]
    chat(c, [{"role": "user", "content": "summarise what customer Jan Kowalski said"}])
    assert "Jan Kowalski" not in sent[1]["messages"][0]["content"]


# --- real sidecar contract and failure modes ----------------------------------------------


def test_privacy_filter_spans_are_used(policy_dir):
    def pf(text):
        i = text.find("Kraków")
        return {"spans": [{"label": "private_address", "start": i, "end": i + 6, "score": 0.97}] if i >= 0 else []}

    c, sent = recording(policy_dir, pf=pf)
    chat(c, [{"role": "user", "content": "I live in Kraków"}])
    assert sent[0]["messages"][0]["content"] == "I live in <PRIVATE_ADDRESS_1>"


def test_low_score_spans_are_ignored(policy_dir):
    c, sent = recording(
        policy_dir, pf=lambda t: {"spans": [{"label": "private_person", "start": 0, "end": 3, "score": 0.2}]}
    )
    chat(c, [{"role": "user", "content": "Bob is here"}])
    assert sent[0]["messages"][0]["content"] == "Bob is here"


@pytest.mark.parametrize("fail_mode,status", [("open", 200), ("closed", 403)])
@pytest.mark.parametrize(
    "bad", [{"spans": "nope"}, {"spans": [{"label": "x", "start": 5, "end": 999, "score": 1}]}, {}]
)
def test_sidecar_failure_follows_fail_mode(policy_dir, fail_mode, status, bad):
    edit_policy(policy_dir, lambda p: p["pii_model"].update(fail_mode=fail_mode))
    c, _ = recording(policy_dir, pf=lambda t: bad)
    assert chat(c, [{"role": "user", "content": "hello there"}]).status_code == status


def test_reply_echoing_overridden_pii_is_not_re_redacted(policy_dir):
    sb = ScriptedBackend({"pii_necessary": [("reply to", 0.95)]})
    c, _ = recording(policy_dir, backend=sb)
    r = chat(c, [{"role": "user", "content": "reply to customer Jan Kowalski"}])
    assert r.json()["choices"][0]["message"]["content"] == "Hello reply to customer Jan Kowalski"


def test_reply_still_redacts_pii_the_caller_did_not_supply(policy_dir):
    c, _ = recording(policy_dir)
    r = chat(
        c,
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "x"},
            {"role": "user", "content": "customer Anna Nowak"},
        ],
    )
    assert "Anna Nowak" in r.json()["choices"][0]["message"]["content"]  # masked in, restored out
    body = guard(c, "customer Anna Nowak", direction="output").json()
    assert body["action"] == "redact"


def test_one_audit_row_per_clean_chat_turn(client):
    before = client.get("/admin/summary").json()["totals"]["events"]
    client.post(
        "/v1/chat/completions",
        headers=KEYS["alice"],
        json={"model": "mock-model", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert client.get("/admin/summary").json()["totals"]["events"] - before == 2  # input + output


def test_cached_history_never_mixes_up_masked_people(policy_dir):
    c, sent = recording(policy_dir)
    chat(
        c,
        [
            {"role": "user", "content": "customer Jan Kowalski"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "customer Jan Kowalski"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "x"},
        ],
    )
    chat(
        c,
        [
            {"role": "user", "content": "customer Anna Nowak"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "customer Jan Kowalski"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "y"},
        ],
    )
    msgs = [m["content"] for m in sent[1]["messages"]]
    assert msgs[0] == "customer <PRIVATE_PERSON_1>" and msgs[2] == "customer <PRIVATE_PERSON_2>"


def test_model_override_never_releases_regex_pii(policy_dir):
    sb = ScriptedBackend({"pii_necessary": [("reply to", 0.95)]})
    c, sent = recording(policy_dir, backend=sb)
    chat(c, [{"role": "user", "content": "reply to customer Jan Kowalski, card 4111 1111 1111 1111, ssn 123-45-6789"}])
    out = sent[0]["messages"][0]["content"]
    assert "Jan Kowalski" in out and "4111" not in out and "123-45-6789" not in out
