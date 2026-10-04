"""Traps (policy.yaml `decoys`): listing one is harmless, opening one restricts, and the person never learns it
was a trap."""

from __future__ import annotations

import json

import pytest

from controllayer.config import parse_policy

from .conftest import KEYS, ROOT, chat, guard
from .test_claude_code import hook
from .test_governance import me, tool

BOARD_PACK = "finance/board-pack-Q3-2026-UNPUBLISHED.pdf"
MARKER = "ACME-BP-Q3-7F2K"


def trap_incidents(c, who=None):
    rows = c.get("/admin/incidents").json()["incidents"]
    return [i for i in rows if i["rule"].startswith("decoy_") and (who is None or i["principal"] == who)]


def status(c, who):
    """The person's insider-risk level (a trap restricts at once)."""
    return next(r for r in c.get("/admin/principals").json() if r["principal"] == who)["level"]


def test_searching_lists_a_trap_without_tripping_it(client):
    out = tool(client, "search_docs", {"query": "q3 results"})
    assert BOARD_PACK in out["result"]["content"][0]["text"]
    assert trap_incidents(client) == []


def test_opening_a_trap_serves_its_decoy_content_and_restricts(client):
    out = tool(client, "read_file", {"path": BOARD_PACK})
    text = out["result"]["content"][0]["text"]
    assert MARKER in text and "Board pack Q3 2026" in text
    assert set(out["result"]) == {"content", "isError"} and out["result"]["isError"] is False
    assert "decoy" not in json.dumps(out).lower()  # nothing in the reply gives the trap away
    [inc] = trap_incidents(client, "alice")
    assert (inc["rule"], inc["severity"]) == ("decoy_touch", "high")
    assert "read_file" in inc["detail"] and inc["evidence"]
    assert status(client, "alice") == "restricted"


def test_naming_a_trap_in_a_prompt_is_only_an_alert(client):
    assert chat(client, "can you pull up the unpublished board pack for me?", who="bob").status_code == 200
    [inc] = trap_incidents(client, "bob")
    assert (inc["rule"], inc["severity"]) == ("decoy_mention", "medium")
    assert status(client, "bob") == "normal"


def test_moving_trap_content_on_trips_it(client):
    chat(client, f"summarise this for the press: ref {MARKER.lower().replace('-', ' ')}", who="bob")
    assert [i["rule"] for i in trap_incidents(client, "bob")] == ["decoy_touch"]


def test_a_trap_in_conversation_history_does_not_count(client):
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-model",
            "messages": [
                {"role": "user", "content": "what is in the finance folder?"},
                {"role": "assistant", "content": f"I found q2 results and {BOARD_PACK}."},
                {"role": "user", "content": "thanks, now summarise the handbook"},
            ],
        },
        headers=KEYS["alice"],
    )
    assert r.status_code == 200
    assert trap_incidents(client) == []


def test_claude_code_reading_a_planted_file_trips_it_and_is_allowed(client):
    out = hook(client, "PreToolUse", tool_name="Read", tool_input={"file_path": f"/repo/{BOARD_PACK}"})
    assert out.get("decision") != "block"
    assert (out.get("hookSpecificOutput") or {}).get("permissionDecision") != "deny"
    assert [i["rule"] for i in trap_incidents(client, "alice")] == ["decoy_touch"]


def test_the_person_never_sees_that_it_was_a_trap(client):
    tool(client, "read_file", {"path": BOARD_PACK})
    mine = me(client)
    assert [i["rule"] for i in mine["risk"]["incidents"]] == ["restricted_material"]
    for path in ("/me/summary", "/me/events", "/me/activity"):
        body = json.dumps(client.get(path, headers=KEYS["alice"]).json()).lower()
        assert "decoy" not in body and "board pack" not in body, path
    assert client.get("/me/activity", params={"decision": "decoy_touch"}, headers=KEYS["alice"]).json() == []
    g = guard(client, "please fetch the unpublished board pack", who="bob").json()
    assert all(f["control"] != "decoys" for f in g["findings"])


def test_admins_see_each_trap_and_who_touched_it(client):
    tool(client, "read_file", {"path": BOARD_PACK})
    d = {x["name"]: x for x in client.get("/admin/decoys").json()["decoys"]}
    assert d["board_pack"]["touches"] == 1 and d["board_pack"]["recent"][0]["principal"] == "alice"
    assert d["customer_export"]["touches"] == 0
    assert client.get("/admin/decoys", headers={"x-admin-token": "wrong"}).status_code == 401


def test_a_trap_must_carry_its_marker_and_never_look_like_a_resource():
    text = (ROOT / "policy.yaml").read_text()
    with pytest.raises(ValueError, match="marker"):
        parse_policy(text.replace("ref ACME-BP-Q3-7F2K", "ref none", 1))
    with pytest.raises(ValueError, match="share names"):
        parse_policy(text.replace("  board_pack:\n", "  prod_db:\n", 1))
