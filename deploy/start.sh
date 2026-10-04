#!/bin/sh
# Hosted demo entry point (render.yaml). Run from the image's /app.
# The host's filesystem is ephemeral, so every boot seeds a fresh synthetic organisation and,
# once the gateway answers, replays the scripted agent so the console shows live decisions.
set -e
PORT="${PORT:-10000}"
python -m controllayer.seed --policy policy.yaml --people "${ACL_SEED_PEOPLE:-2000}"
(
  for _ in $(seq 60); do
    if python -c "import httpx; httpx.get('http://127.0.0.1:$PORT/admin/summary').raise_for_status()" 2>/dev/null; then
      python demo/agent.py --gateway "http://127.0.0.1:$PORT" >/dev/null || echo "demo agent failed" >&2
      break
    fi
    sleep 1
  done
) &
exec python -m controllayer --host 0.0.0.0 --port "$PORT" --policy policy.yaml
