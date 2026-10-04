"""The sidecar's config file. `${VAR:-default}` reads the environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand(text: str) -> str:
    return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), text)


@dataclass
class Config:
    path: Path
    raw: dict[str, Any]

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    def resolve(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else self.base_dir / p

    @property
    def target(self) -> dict[str, Any]:
        defaults = {"url": "http://127.0.0.1:8787", "admin_token": "demo-admin-token"}
        return {**defaults, **self.raw.get("target", {})}

    @property
    def poligon(self) -> dict[str, Any]:
        defaults = {"corpus": "corpus", "mutate": True, "interval_seconds": 30, "workers": 8}
        return {**defaults, **self.raw.get("poligon", {})}

    @property
    def feed(self) -> str:
        """The signature feed the gateway loads (file or URL), self-tested by `feed --check`."""
        return str(self.raw.get("feed") or "")

    @property
    def server(self) -> dict[str, Any]:
        return {"host": "127.0.0.1", "port": 8799, **self.raw.get("server", {})}


def load(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists() and not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p  # run from anywhere: fall back to redteam/redteam.yaml
    p = p.resolve()
    return Config(p, yaml.safe_load(_expand(p.read_text())) or {})
