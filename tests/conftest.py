from __future__ import annotations

import json
import pickle
import shutil
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from controllayer import config, seed
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


# libyaml reads and writes the same documents as safe_load/safe_dump, 20x faster.
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


class _ParseOnce:
    """Stands in for `yaml` inside controllayer.config: each distinct policy text goes through the real
    safe_load once, and every later parse gets a fresh copy of that result. Hundreds of apps parse the
    same few texts, and pure-Python YAML was otherwise most of the suite's time."""

    def __init__(self):
        self.parsed: dict[str, bytes] = {}

    def safe_load(self, text: str):
        if text not in self.parsed:
            self.parsed[text] = pickle.dumps(yaml.safe_load(text))
        return pickle.loads(self.parsed[text])

    def __getattr__(self, name: str):
        return getattr(yaml, name)


@pytest.fixture(scope="session", autouse=True)
def _parse_each_policy_text_once():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "yaml", _ParseOnce())
        yield


def _shipped_policy(demo_mode: bool) -> str:
    data = yaml.load((ROOT / "policy.yaml").read_text(), Loader=_LOADER)
    data["identity"]["demo_mode"] = demo_mode
    return yaml.dump(data, Dumper=_DUMPER, sort_keys=False)


@pytest.fixture(scope="session")
def shipped_policy() -> dict[bool, str]:
    """The shipped policy's text with demo mode off and on."""
    return {mode: _shipped_policy(mode) for mode in (False, True)}


@pytest.fixture
def policy_dir(tmp_path: Path, shipped_policy) -> Path:
    """The shipped policy with demo mode off, so every credential is enforced (see demo_client)."""
    (tmp_path / "policy.yaml").write_text(shipped_policy[False])
    shutil.copytree(ROOT / "feeds", tmp_path / "feeds")
    return tmp_path


def edit_policy(policy_dir: Path, mutate) -> None:
    path = policy_dir / "policy.yaml"
    data = yaml.load(path.read_text(), Loader=_LOADER)
    mutate(data)
    path.write_text(yaml.dump(data, Dumper=_DUMPER, sort_keys=False))


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
def demo_client(tmp_path_factory, shipped_policy) -> TestClient:
    """The shipped policy in demo mode (keyless callers act as alice); sends no credential of its own."""
    d = tmp_path_factory.mktemp("demo")
    (d / "policy.yaml").write_text(shipped_policy[True])
    shutil.copytree(ROOT / "feeds", d / "feeds")
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


@pytest.fixture(scope="session")
def seeded_org(tmp_path_factory):
    """`seeded_org(people, rng=1)` -> (dir, seed.build output): an org under the shipped policy, built
    once per session per size and seed (2,000 people take seconds). Both are shared: read only, and
    give each app its own directory with org_copy."""
    built = {}

    def get(people: int, rng: int = 1) -> tuple[Path, dict]:
        if (people, rng) not in built:
            d = tmp_path_factory.mktemp(f"seed{people}x{rng}")
            text = (ROOT / "policy.yaml").read_text()
            (d / "policy.yaml").write_text(text)
            out = seed.build(config.parse_policy(text), people, 30, rng, time.time())
            (d / "data").mkdir()
            (d / "data" / "org.json").write_text(json.dumps(out["directory"]))
            (d / "data" / "history.json").write_text(json.dumps(out["history"]))
            built[people, rng] = d, out
        return built[people, rng]

    return get


def org_copy(seeded: Path, dest: Path, state: dict | None = None) -> Path:
    """`dest` holding the seeded org's policy and feeds, its large read-only seed files linked, and
    data/state.json written from `state` when given; returns the policy path."""
    (dest / "data").mkdir(parents=True)
    shutil.copy(seeded / "policy.yaml", dest / "policy.yaml")
    shutil.copytree(ROOT / "feeds", dest / "feeds")
    for name in ("org.json", "history.json"):
        (dest / "data" / name).symlink_to(seeded / "data" / name)
    if state is not None:
        (dest / "data" / "state.json").write_text(json.dumps(state))
    return dest / "policy.yaml"
