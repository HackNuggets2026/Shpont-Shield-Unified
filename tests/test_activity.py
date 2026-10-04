"""The Activity page's backend: /admin/audit/stats, filtered and full-file exports, the live cursor."""

import csv
import io
import json
import time

from controllayer.audit import AuditLog, stats

from .conftest import chat

AWS = "key AKIAIOSFODNN7EXAMPLE"


def traffic(client):
    chat(client, "hello there")
    chat(client, "what is the weather")
    chat(client, AWS)
    chat(client, "hello from bob", who="bob")


def test_stats_shape_and_counts(client):
    traffic(client)
    s = client.get("/admin/audit/stats", params={"window": 600, "bucket": 60}).json()
    assert s["window_s"] == 600 and s["bucket_s"] == 60 and len(s["timeline"]) == 10
    n = len(client.get("/admin/events", params={"limit": 1000}).json())  # a chat is an input and an output decision
    assert n >= 4 and s["total"] == n == sum(s["by_action"].values())
    assert s["by_action"]["block"] >= 1 and s["by_action"]["allow"] >= 1
    assert s["blocked_rate"] == round(s["by_action"]["block"] / n, 4)
    assert s["redacted_rate"] == round(s["by_action"]["redact"] / n, 4)
    assert sum(sum(b[a] for a in s["by_action"]) for b in s["timeline"]) == n
    assert any(r["count"] >= 1 for r in s["top_reasons"])
    assert "secrets" in s["controls"] and s["controls"]["secrets"].get("block", 0) >= 1
    assert any(c["category"].startswith("secrets/") for c in s["top_categories"])
    lat = s["latency_ms"]["total"]
    assert lat["count"] == n and lat["p50"] <= lat["p95"] <= lat["max"]


def test_stats_honour_filters_and_validate(client):
    traffic(client)
    s = client.get("/admin/audit/stats", params={"action": "block"}).json()
    assert s["total"] == s["by_action"]["block"] >= 1
    assert client.get("/admin/audit/stats", params={"control": "nope"}).json()["total"] == 0
    assert client.get("/admin/audit/stats", params={"window": 0}).status_code == 400


def test_stats_buckets_by_time():
    now = 10_000.0
    events = [
        {"ts": now - 5, "action": "allow", "findings": [], "latency_ms": {"total": 1.0}},
        {"ts": now - 65, "action": "block", "reason": "r", "findings": [{"control": "c", "category": "k", "action": "block"}]},
        {"ts": now - 9999, "action": "allow", "findings": []},  # outside the window
    ]
    s = stats(events, 120, 60, now)
    assert s["total"] == 2 and [b["allow"] + b["block"] for b in s["timeline"]] == [1, 1]
    assert s["timeline"][0]["block"] == 1 and s["controls"] == {"c": {"block": 1}}
    assert s["top_reasons"] == [{"reason": "r", "count": 1}]


def test_export_honours_filters(client):
    traffic(client)
    blocked = client.get("/admin/audit/export", params={"format": "jsonl", "action": "block"}).text.splitlines()
    assert blocked and all(json.loads(line)["action"] == "block" for line in blocked)
    rows = list(csv.DictReader(io.StringIO(client.get("/admin/audit/export", params={"format": "csv", "q": "weather"}).text)))
    assert rows == [] or all(r["action"] for r in rows)
    allowed = client.get("/admin/audit/export", params={"format": "csv", "action": "allow"}).text
    assert "block" not in {r["action"] for r in csv.DictReader(io.StringIO(allowed))}
    ocsf = [json.loads(x) for x in client.get("/admin/audit/export", params={"format": "ocsf", "action": "block"}).text.splitlines()]
    assert len(ocsf) == len(blocked)
    assert client.get("/admin/audit/export", params={"format": "jsonl", "control": "nope"}).text == ""
    future = client.get("/admin/audit/export", params={"since": time.time() + 60}).text
    assert future == ""


def test_export_reads_the_whole_file_not_just_the_ring(policy_dir, tmp_path):
    path = tmp_path / "audit.jsonl"
    with path.open("w") as fh:
        for i in range(30):
            fh.write(json.dumps({"ts": 1000 + i, "request_id": f"r{i}", "action": "allow", "findings": []}) + "\n")
        fh.write(json.dumps({"ts": 2000, "kind": "grant", "actor": "x"}) + "\n")
    log = AuditLog(path, ring_size=5)
    assert len(log.events) <= 5
    events, notes = log.history()
    assert len(events) == 30 and len(notes) == 1
    with path.open("a") as fh:  # appended later, plus a half-written line
        fh.write(json.dumps({"ts": 3000, "request_id": "late", "action": "block", "findings": []}) + "\n" + '{"ts": 4')
    events, _ = log.history()
    assert len(events) == 31 and events[-1]["request_id"] == "late"


def test_events_live_cursor_and_window(client):
    chat(client, "first")
    first = client.get("/admin/events", params={"limit": 1}).json()[0]
    chat(client, "second")
    newer = client.get("/admin/events", params={"since": first["ts"]}).json()
    assert [e["request_id"] for e in newer] != [] and first["request_id"] not in {e["request_id"] for e in newer}
    assert client.get("/admin/events", params={"since": time.time() + 60}).json() == []
    assert len(client.get("/admin/events", params={"window": 600}).json()) == len(client.get("/admin/events").json())
