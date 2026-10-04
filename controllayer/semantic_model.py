"""Open CPU prompt-injection classifier for the semantic tier (no Ollama, no GPU).

protectai/deberta-v3-base-prompt-injection-v2 (Apache-2.0), ONNX, run with onnxruntime on CPU.
Optional: `pip install -e '.[classifier]'`, then pre-fetch the ~739 MB model with
`python -m controllayer.semantic_model download`. Without the extra or the files the gateway
keeps working on the keyword fallback, and /admin/summary says so.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

log = logging.getLogger("controllayer.semantic_model")

DEFAULT_MODEL = "protectai/deberta-v3-base-prompt-injection-v2"
FILES = ("onnx/model.onnx", "onnx/tokenizer.json", "onnx/config.json")
HF_URL = "https://huggingface.co/{repo}/resolve/main/{path}"


def cache_root() -> Path:
    if os.environ.get("ACL_MODEL_DIR"):
        return Path(os.environ["ACL_MODEL_DIR"]).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return Path(base).expanduser() / "controllayer" / "models"


def model_dir(repo: str = DEFAULT_MODEL, root: str | Path | None = None) -> Path:
    return Path(root).expanduser() if root else cache_root() / repo.replace("/", "--")


def deps_installed() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return False
    return True


def files_present(repo: str = DEFAULT_MODEL, root: str | Path | None = None) -> bool:
    d = model_dir(repo, root)
    return all((d / f).is_file() for f in FILES)


def download(repo: str = DEFAULT_MODEL, root: str | Path | None = None, progress: bool = False) -> Path:
    """Fetch the ONNX files into the cache (atomic per file: a partial download never looks complete)."""
    import httpx

    d = model_dir(repo, root)
    for f in FILES:
        dest = d / f
        if dest.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with httpx.stream("GET", HF_URL.format(repo=repo, path=f), follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            total, done, last = int(r.headers.get("content-length") or 0), 0, 0.0
            with open(tmp, "wb") as out:
                for block in r.iter_bytes(1 << 20):
                    out.write(block)
                    done += len(block)
                    if progress and total and time.monotonic() - last > 1:
                        last = time.monotonic()
                        print(f"\r{f}: {done / 1e6:.0f}/{total / 1e6:.0f} MB", end="", file=sys.stderr, flush=True)
        tmp.replace(dest)
        if progress:
            print(f"\r{f}: done{' ' * 20}", file=sys.stderr)
    return d


# --- Pre-normalisation --------------------------------------------------------------------------
# Both the classifier and the keyword fallback score the original text plus whatever is hidden in it.

_INVISIBLE = re.compile("[­​-‏⁠-⁤﻿]")
_B64 = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{16,}={0,2}(?![A-Za-z0-9+/=_-])")
# Four or more single characters each followed by 1-3 spaces: "I g n o r e  a l l".
_SPACED = re.compile(r"(?<!\S)(?:\S {1,3}){3,}\S(?!\S)")


def _decode_b64(token: str) -> str | None:
    raw = token.rstrip("=")
    if len(raw) < 16 or raw.isalpha() or raw.isdigit():  # plain words and numbers are not base64
        return None
    try:
        data = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_" if "-" in raw or "_" in raw else None)
        text = data.decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    printable = sum(ch.isprintable() or ch.isspace() for ch in text)
    if not text.strip() or printable < len(text) * 0.95 or not re.search(r"[A-Za-z]{3}", text):
        return None
    return text


def _collapse(run: str) -> str:
    words = re.split(r" {2,}", run) if re.search(r" {2,}", run) else [run]
    return " ".join(w.replace(" ", "") for w in words)


def normalize(text: str) -> str:
    """The text with zero-width characters removed and NFKC applied, plus decoded base64 blobs and
    collapsed letter-spaced runs appended on their own lines. Unchanged when nothing is hidden."""
    clean = unicodedata.normalize("NFKC", _INVISIBLE.sub("", text))
    extra = [d for m in _B64.finditer(clean) if (d := _decode_b64(m.group()))]
    extra += [_collapse(m.group()) for m in _SPACED.finditer(clean)]
    return "\n".join([clean, *extra]) if extra else clean


def is_structured(text: str) -> bool:
    s = text.strip()
    if not s or s[0] not in "[{":
        return False
    try:
        json.loads(s)
    except ValueError:
        return False
    return True


def json_strings(text: str, min_chars: int) -> list[str]:
    """String leaves of a JSON document that are long enough to carry an instruction."""
    out: list[str] = []

    def walk(v):
        if isinstance(v, str):
            if len(v.strip()) >= min_chars:
                out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(json.loads(text))
    return out


# --- Classifier -----------------------------------------------------------------------------------


class InjectionClassifier:
    """P(injection) for a text. Long texts are scored in overlapping 512-token windows (max wins)."""

    WINDOW = 510  # 512 minus [CLS] and [SEP]
    STRIDE = 384
    MAX_WINDOWS = 16

    def __init__(self, repo: str = DEFAULT_MODEL, root: str | Path | None = None, threads: int = 2):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = model_dir(repo, root)
        self.np = np
        self.repo = repo
        self.tok = Tokenizer.from_file(str(d / "onnx/tokenizer.json"))
        self.tok.no_truncation()
        self.tok.no_padding()
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(str(d / "onnx/model.onnx"), so, providers=["CPUExecutionProvider"])
        self.device = "cpu"
        cfg = json.loads((d / "onnx/config.json").read_text())
        self.inj = next(int(k) for k, v in cfg["id2label"].items() if str(v).upper().startswith("INJ"))
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self.cls = self.tok.token_to_id("[CLS]")
        self.sep = self.tok.token_to_id("[SEP]")

    def score(self, text: str) -> float:
        np = self.np
        ids = self.tok.encode(text, add_special_tokens=False).ids
        starts = list(range(0, max(len(ids) - self.WINDOW, 0) + 1, self.STRIDE))[: self.MAX_WINDOWS]
        if starts[-1] + self.WINDOW < len(ids) and len(starts) < self.MAX_WINDOWS:
            starts.append(len(ids) - self.WINDOW)
        best = 0.0
        for s in starts:
            window = np.array([[self.cls, *ids[s : s + self.WINDOW], self.sep]], dtype=np.int64)
            feed = {"input_ids": window, "attention_mask": np.ones_like(window)}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(window)
            logits = self.sess.run(None, feed)[0][0]
            p = np.exp(logits - logits.max())
            best = max(best, float(p[self.inj] / p.sum()))
        return best


_loaded: dict[tuple[str, str, int], InjectionClassifier] = {}
_lock = threading.Lock()


def load(repo: str = DEFAULT_MODEL, root: str | Path | None = None, threads: int = 2) -> InjectionClassifier:
    """One shared session per model per process (several gateways in one test run reuse it)."""
    key = (repo, str(model_dir(repo, root)), threads)
    with _lock:
        if key not in _loaded:
            t = time.perf_counter()
            _loaded[key] = InjectionClassifier(repo, root, threads)
            log.info("loaded %s in %.1f s", repo, time.perf_counter() - t)
        return _loaded[key]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m controllayer.semantic_model")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("download", "status", "score"):
        p = sub.add_parser(name)
        p.add_argument("--model", default=DEFAULT_MODEL)
        p.add_argument("--dir", default=None, help="model folder (default: ~/.cache/controllayer/models/...)")
        if name == "score":
            p.add_argument("text")
    a = ap.parse_args(argv)
    if a.cmd == "download":
        print(download(a.model, a.dir, progress=True))
        return 0
    if a.cmd == "status":
        print(
            json.dumps(
                {
                    "model": a.model,
                    "dir": str(model_dir(a.model, a.dir)),
                    "deps": deps_installed(),
                    "files": files_present(a.model, a.dir),
                }
            )
        )
        return 0
    clf = load(a.model, a.dir)
    t = time.perf_counter()
    p = clf.score(normalize(a.text))
    print(f"P(injection)={p:.3f} in {(time.perf_counter() - t) * 1000:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
