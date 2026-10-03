#!/usr/bin/env python3
"""Wazuh active response: raise a person's AI Control Layer risk level when Wazuh flags them.

Wazuh (4.2+) writes one JSON message to stdin. For an `add`, this script names the user it acts on
(`check_keys`), and on `continue` posts a signal to the gateway:

    POST <url>/admin/risk/<user>/signal  {"source": "rule-<id>", "level", "ttl_seconds", "reason"}

extra_args (from the <command> block): <gateway url> [level=watch] [ttl_seconds=86400]
Token: the file named by $ACL_SIGNAL_TOKEN_FILE (default /var/ossec/etc/controllayer.token).
User map (optional): /var/ossec/etc/controllayer-users.json, {"os-or-ad-user": "gateway principal"}.
Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TOKEN_FILE = os.environ.get("ACL_SIGNAL_TOKEN_FILE", "/var/ossec/etc/controllayer.token")
USER_MAP = os.environ.get("ACL_SIGNAL_USER_MAP", "/var/ossec/etc/controllayer-users.json")
LOG = os.environ.get("ACL_SIGNAL_LOG", "/var/ossec/logs/active-responses.log")
# Where common Wazuh decoders put the user an alert is about, most specific first.
USER_FIELDS = (
    "dstuser",
    "srcuser",
    "user",
    "win.eventdata.targetUserName",
    "win.eventdata.subjectUserName",
    "audit.auid",
)


def log(msg: str) -> None:
    try:
        with open(LOG, "a") as fh:
            fh.write(f"controllayer-signal: {msg}\n")
    except OSError:
        print(f"controllayer-signal: {msg}", file=sys.stderr)


def field(data: dict, dotted: str):
    for part in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def user_of(alert: dict) -> str | None:
    data = alert.get("data") or {}
    for name in USER_FIELDS:
        value = field(data, name)
        if isinstance(value, str) and value.strip():
            user = value.strip()
            try:
                with open(USER_MAP) as fh:
                    return json.load(fh).get(user, user)
            except FileNotFoundError:
                return user
    return None


def main() -> int:
    msg = json.loads(sys.stdin.readline())
    if msg.get("command") != "add":
        return 0  # stateless: nothing to undo on delete
    params = msg.get("parameters") or {}
    alert = params.get("alert") or {}
    args = params.get("extra_args") or []
    if not args:
        log("missing extra_args: <gateway url> [level] [ttl_seconds]")
        return 1
    url, level = args[0].rstrip("/"), args[1] if len(args) > 1 else "watch"
    ttl = float(args[2]) if len(args) > 2 else 86400
    rule = alert.get("rule") or {}
    if "controllayer" in (rule.get("groups") or []):
        return 0  # never feed the gateway's own alerts back to it
    user = user_of(alert)
    if not user:
        log(f"rule {rule.get('id')}: no user field in alert")
        return 0

    # Ask execd whether this user is already being handled (Wazuh active-response protocol).
    keys = {"version": 1, "origin": {"name": "controllayer-signal", "module": "active-response"}}
    print(json.dumps(keys | {"command": "check_keys", "parameters": {"keys": [user]}}), flush=True)
    reply = json.loads(sys.stdin.readline() or "{}")
    if reply.get("command") != "continue":
        return 0

    with open(TOKEN_FILE) as fh:
        token = fh.read().strip()
    agent = (alert.get("agent") or {}).get("name", "?")
    body = {
        "source": f"rule-{rule.get('id', 'unknown')}",
        "level": level,
        "ttl_seconds": ttl,
        "reason": f"Wazuh rule {rule.get('id')} on {agent}: {rule.get('description', '')}"[:500],
    }
    req = urllib.request.Request(
        f"{url}/admin/risk/{urllib.parse.quote(user, safe='')}/signal",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            log(f"{user}: {r.status} {r.read().decode()[:300]}")
    except urllib.error.HTTPError as e:
        log(f"{user}: HTTP {e.code} {e.read().decode()[:300]}")
        return 1
    except urllib.error.URLError as e:
        log(f"{user}: {e.reason}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
