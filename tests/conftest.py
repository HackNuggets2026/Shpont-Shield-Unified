from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from controllayer.decision import ScriptedBackend
from controllayer.gateway.app import create_app

ROOT = Path(__file__).resolve().parent.parent
ADMIN = "demo-admin-token"

KEYS = {
    "alice": {"Authorization": "Bearer dev-alice-key"},  # engineering / developer
    "bob": {"Authorization": "Bearer fin-bob-key"},  # finance / analyst
    "ops": {"Authorization": "Bearer ops-agent-key"},  # platform / automation
    "carol": {"Authorization": "Bearer intern-key"},  # interns / intern
}


@pytest.fixture
def policy_dir(tmp_path: Path) -> Path:
    """The shipped policy with demo mode off, so every credential is enforced (see demo_client)."""
    shutil.copy(ROOT / "policy.yaml", tmp_path / "policy.yaml")
    shutil.copytree(ROOT / "feeds", tmp_path / "feeds")
    edit_policy(tmp_path, lambda d: d["identity"].update(demo_mode=False))
    return tmp_path


def edit_policy(policy_dir: Path, mutate) -> None:
    path = policy_dir / "policy.yaml"
    data = yaml.safe_load(path.read_text())
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


@pytest.fixture
def make_client(policy_dir: Path):
    def make(backend=None, mutate=None) -> TestClient:
        if mutate:
            edit_policy(policy_dir, mutate)
        return TestClient(
            create_app(policy_dir / "policy.yaml", backend=backend, watch=False), headers={"x-admin-token": ADMIN}
        )

    return make


@pytest.fixture
def demo_client(tmp_path_factory) -> TestClient:
    """The shipped policy in demo mode (keyless callers act as alice); sends no credential of its own."""
    d = tmp_path_factory.mktemp("demo")
    shutil.copy(ROOT / "policy.yaml", d / "policy.yaml")
    shutil.copytree(ROOT / "feeds", d / "feeds")
    edit_policy(d, lambda p: p["identity"].update(demo_mode=True))
    return TestClient(create_app(d / "policy.yaml", watch=False))


@pytest.fixture
def client(make_client) -> TestClient:
    """Gateway with the shipped policy and the offline heuristic decision backend."""
    return make_client()


@pytest.fixture
def scripted() -> ScriptedBackend:
    return ScriptedBackend()


def chat(client: TestClient, text: str, who: str = "alice", model: str = "mock-model", **body):
    r = client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": text}], **body},
        headers=KEYS.get(who, {}),
    )
    return r


def reply(r) -> str:
    return r.json()["choices"][0]["message"]["content"]


def guard(client: TestClient, text: str, direction: str = "input", who: str = "alice", **extra):
    return client.post("/v1/guard", json={"text": text, "direction": direction, **extra}, headers=KEYS.get(who, {}))


def mcp(client: TestClient, method: str, params: dict | None = None, who: str = "alice", server: str = "demo"):
    return client.post(
        f"/mcp/{server}",
        json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}},
        headers=KEYS.get(who, {}),
    ).json()
