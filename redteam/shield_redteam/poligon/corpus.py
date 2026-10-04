"""The self-test corpus: YAML files of attack (must be stopped) and benign (must pass) cases."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..core.types import DIRECTIONS, Case


class CorpusError(ValueError):
    pass


def load_file(path: Path) -> list[Case]:
    data = yaml.safe_load(path.read_text()) or {}
    defaults = {k: data[k] for k in ("kind", "category", "owasp", "direction", "principal", "mutable") if k in data}
    cases = []
    for raw in data.get("cases", []):
        merged = {**defaults, **raw}
        missing = [k for k in ("id", "kind", "category", "text") if not merged.get(k)]
        if missing:
            raise CorpusError(f"{path.name}: case {raw.get('id', '?')} is missing {missing}")
        if merged["kind"] not in ("attack", "benign"):
            raise CorpusError(f"{path.name}: {merged['id']} has kind {merged['kind']!r}")
        if merged.get("direction", "input") not in DIRECTIONS:
            raise CorpusError(f"{path.name}: {merged['id']} has direction {merged['direction']!r}")
        fields = {k: v if k == "mutable" else str(v) for k, v in merged.items() if k in Case.__dataclass_fields__}
        cases.append(Case(**fields))
    return cases


def load_dir(root: Path) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(root.rglob("*.yaml")):
        cases += load_file(path)
    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            raise CorpusError(f"duplicate case id {c.id}")
        seen.add(c.id)
    return cases
