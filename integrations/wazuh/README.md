# Wazuh integration

Two directions:

- **Gateway alerts into Wazuh.** The gateway's alert file sink is read by a Wazuh agent, and `controllayer_rules.xml` turns each line into a Wazuh alert.
- **Wazuh detections back into the gateway.** `controllayer-signal.py` is an active response. When a chosen Wazuh rule fires for a user, it posts an external risk signal (`POST /admin/risk/{pid}/signal`). The signal can only raise that person's level, is capped at the integration's `max_level`, and expires.

## Not run here

Wazuh itself was not available on the build machine, so none of this has run inside Wazuh. These checks were run (`tests/test_wazuh_kit.py`):

- The rules file parses as XML.
- Every `<field>` and `$(field)` in the rules exists in the gateway's real native and OCSF alert lines, with nested keys joined by dots the way Wazuh's JSON decoder names them.
- A small emulation of OS_Regex, covering only what the rules use, picks the expected child rule.
- The script runs as a subprocess against a live gateway on a local port, using the execd stdin protocol (`add`, `check_keys`, `continue`/`abort`). The signal lands with the cap and TTL applied, and a rejected token is reported.

Not verified: Wazuh's own rule loading and matching, the order in which execd runs the script, and which `data.*` user fields your decoders produce.

## Setup

Paths assume a default install (`/var/ossec`).

### 1. Gateway: sink and integration token

In `policy.yaml`:

```yaml
identity:
  integrations:
    wazuh: { token_env: ACL_WAZUH_TOKEN, max_level: watch, max_ttl_hours: 24 }
insider_risk:
  sinks:
    - { type: file, path: data/security-alerts.jsonl }        # or format: ocsf; the rules handle both
```

Start the gateway with `ACL_WAZUH_TOKEN` set to a long random value, for example `openssl rand -hex 32`. Do not reuse the admin token: the gateway rejects an integration token that equals it.

### 2. Gateway alerts into Wazuh

On the host that runs the gateway (with a Wazuh agent), add this to `ossec.conf`:

```xml
<localfile>
  <log_format>json</log_format>
  <location>/opt/controllayer/data/security-alerts.jsonl</location>
</localfile>
```

On the manager:

```sh
cp controllayer_rules.xml /var/ossec/etc/rules/
chown wazuh:wazuh /var/ossec/etc/rules/controllayer_rules.xml
/var/ossec/bin/wazuh-analysisd -t        # config test
systemctl restart wazuh-manager
```

No custom decoder is needed. With `log_format json`, Wazuh's built-in JSON decoder decodes every line. A custom decoder for the same lines would never be reached, because the built-in `json` decoder matches them first.

| Rule | Level | When |
|---|---|---|
| 100700 / 100710 | 3 | any gateway alert (native / OCSF) |
| 100701 / 100711 | 10 | sensitive category (`alert category: ...`): leaked keys, exfiltration, malware |
| 100702 / 100712 | 8 | person at `watch` |
| 100703 / 100713 | 12 | person at `restricted` |

To try the rules on one line, run `/var/ossec/bin/wazuh-logtest` and paste a line from `data/security-alerts.jsonl`.

### 3. Wazuh detections into the gateway

On the manager:

```sh
cp controllayer-signal.py /var/ossec/active-response/bin/
chmod 750 /var/ossec/active-response/bin/controllayer-signal.py
chown root:wazuh /var/ossec/active-response/bin/controllayer-signal.py
printf '%s' "$ACL_WAZUH_TOKEN" > /var/ossec/etc/controllayer.token
chmod 640 /var/ossec/etc/controllayer.token && chown root:wazuh /var/ossec/etc/controllayer.token
# optional: OS/AD user -> gateway principal
echo '{"alice.k": "alice"}' > /var/ossec/etc/controllayer-users.json
```

Add this to the manager's `ossec.conf`, then restart it. `<extra_args>` is the gateway URL, the requested level, and the TTL in seconds:

```xml
<command>
  <name>controllayer-signal</name>
  <executable>controllayer-signal.py</executable>
  <extra_args>https://gateway.internal:8787 watch 86400</extra_args>
  <timeout_allowed>no</timeout_allowed>
</command>

<active-response>
  <command>controllayer-signal</command>
  <location>server</location>
  <rules_id>5712,40111</rules_id>   <!-- the endpoint rules that should raise a person's AI risk level -->
</active-response>
```

How the script behaves:

- It takes the user from the first of `data.dstuser`, `data.srcuser`, `data.user`, `data.win.eventdata.targetUserName`, `data.win.eventdata.subjectUserName` and `data.audit.auid` that is set. It maps that user through `controllayer-users.json` if the file exists.
- It sends `source: rule-<id>`, so each Wazuh rule holds its own signal (`wazuh/rule-5712` in `/admin/risk`). The next firing of that rule replaces its signal.
- It skips alerts in the `controllayer` group, so the gateway's own alerts never loop back.
- It logs to `/var/ossec/logs/active-responses.log`. It needs `python3` on the manager's PATH.

Security sees active signals in the security console, next to each person's risk level, and can dismiss one with a click. A level that security set by hand always wins over signals.
