"""integrations/wazuh: the rules read fields our alert JSON has; the active response reaches a live gateway."""

import json
import re
import socket
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import uvicorn

from controllayer.gateway.app import create_app

from .conftest import guard

KIT = Path(__file__).resolve().parent.parent / "integrations" / "wazuh"
SCRIPT = KIT / "controllayer-signal.py"


def flat(doc: dict, prefix: str = "") -> dict[str, object]:
    """Keys as Wazuh's JSON decoder names them (nested objects joined with dots)."""
    out = {}
    for k, v in doc.items():
        if isinstance(v, dict):
            out |= flat(v, f"{prefix}{k}.")
        else:
            out[f"{prefix}{k}"] = v
    return out


def os_regex(pattern: str, value: object) -> bool:
    """The subset of Wazuh's OS_Regex the rules use: ^, $, \\.+ and | between alternatives."""
    return any(re.search(alt.replace("\\.+", ".+"), str(value)) for alt in pattern.split("|"))


@pytest.mark.parametrize("fmt", ["native", "ocsf"])
def test_rules_match_the_file_sink_output(make_client, policy_dir, fmt):
    c = make_client(mutate=lambda p: p["insider_risk"]["sinks"][0].update(format=fmt))
    guard(c, "-----BEGIN RSA PRIVATE KEY-----\nMIIE")  # alert category at level normal
    c.post("/admin/risk/alice", json={"level": "restricted"})
    guard(c, "key AKIAIOSFODNN7EXAMPLE")  # blocked while restricted
    alerts = [flat(json.loads(line)) for line in (policy_dir / "data/security-alerts.jsonl").read_text().splitlines()]
    assert len(alerts) == 2

    rules = ET.parse(KIT / "controllayer_rules.xml").getroot().findall("rule")
    base = {"native": "100700", "ocsf": "100710"}[fmt]

    def fired(alert):
        def matches(rule):
            return all(f.get("name") in alert and os_regex(f.text, alert[f.get("name")]) for f in rule.findall("field"))

        top = next(r for r in rules if r.get("id") == base)
        if not matches(top):
            return None
        children = [r for r in rules if r.findtext("if_sid") == base and matches(r)]
        return {r.get("id") for r in children}

    assert fired(alerts[0]) == {str(int(base) + 1)}  # alert category
    assert fired(alerts[1]) == {str(int(base) + 3)}  # restricted
    for rule in rules:  # every $(field) in a description exists in that format's alerts
        if rule.get("id").startswith(base[:5]):
            for name in re.findall(r"\$\(([^)]+)\)", rule.findtext("description")):
                assert name in alerts[0], (rule.get("id"), name)


@pytest.fixture
def gateway(policy_dir, monkeypatch):
    monkeypatch.setenv("ACL_WAZUH_TOKEN", "wz-secret")
    app = create_app(policy_dir / "policy.yaml", watch=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}", app
    server.should_exit = True
    thread.join(5)


def run_ar(tmp_path: Path, url: str, alert: dict, reply: str = "continue") -> tuple[int, list[dict], str]:
    """Drive the script the way wazuh-execd does: one message in, check_keys out, continue/abort in."""
    (tmp_path / "token").write_text("wz-secret\n")
    (tmp_path / "users.json").write_text(json.dumps({"alice.k": "alice"}))
    env = {
        "ACL_SIGNAL_TOKEN_FILE": str(tmp_path / "token"),
        "ACL_SIGNAL_USER_MAP": str(tmp_path / "users.json"),
        "ACL_SIGNAL_LOG": str(tmp_path / "ar.log"),
    }
    msg = {
        "version": 1,
        "origin": {"name": "node01", "module": "wazuh-execd"},
        "command": "add",
        "parameters": {"extra_args": [url, "restricted", "3600"], "alert": alert, "program": "controllayer-signal"},
    }
    p = subprocess.Popen(
        [sys.executable, str(SCRIPT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env
    )
    p.stdin.write(json.dumps(msg) + "\n")
    p.stdin.flush()
    out = []
    line = p.stdout.readline()
    if line:
        out.append(json.loads(line))
        p.stdin.write(json.dumps({"version": 1, "origin": {}, "command": reply, "parameters": {}}) + "\n")
        p.stdin.flush()
    code = p.wait(15)
    log = (tmp_path / "ar.log").read_text() if (tmp_path / "ar.log").exists() else ""
    return code, out, log


SSH_ALERT = {
    "rule": {
        "id": "5712",
        "level": 10,
        "description": "sshd: brute force",
        "groups": ["sshd", "authentication_failures"],
    },
    "agent": {"id": "001", "name": "laptop-alice"},
    "data": {"srcip": "203.0.113.9", "dstuser": "alice.k"},
}


def test_active_response_posts_a_capped_signal(gateway, tmp_path):
    url, app = gateway
    code, out, log = run_ar(tmp_path, url, SSH_ALERT)
    assert code == 0, log
    assert out == [
        {
            "version": 1,
            "origin": {"name": "controllayer-signal", "module": "active-response"},
            "command": "check_keys",
            "parameters": {"keys": ["alice"]},
        }
    ]
    assert "alice: 200" in log
    sig = app.state.layer.state.signals["alice"]["wazuh/rule-5712"]
    assert (sig["requested"], sig["level"]) == ("restricted", "watch")  # wazuh max_level in policy.yaml
    assert sig["reason"] == "Wazuh rule 5712 on laptop-alice: sshd: brute force"
    assert 3590 < sig["expires_at"] - sig["at"] <= 3600


def test_active_response_honours_abort_and_skips_our_own_alerts(gateway, tmp_path):
    url, app = gateway
    assert run_ar(tmp_path, url, SSH_ALERT, reply="abort")[0] == 0
    own = SSH_ALERT | {"rule": SSH_ALERT["rule"] | {"groups": ["controllayer", "insider_risk"]}}
    code, out, _ = run_ar(tmp_path, url, own)
    assert (code, out) == (0, [])
    no_user = SSH_ALERT | {"data": {"srcip": "203.0.113.9"}}
    code, out, log = run_ar(tmp_path, url, no_user)
    assert (code, out) == (0, []) and "no user field" in log
    assert app.state.layer.state.signals == {}


def test_active_response_reports_a_rejected_token(gateway, tmp_path, monkeypatch):
    url, app = gateway
    monkeypatch.setenv("ACL_WAZUH_TOKEN", "rotated")
    code, _, log = run_ar(tmp_path, url, SSH_ALERT)
    assert code == 1 and "HTTP 401" in log
    assert app.state.layer.state.signals == {}
