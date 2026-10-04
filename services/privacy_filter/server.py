"""OpenAI Privacy Filter as an HTTP sidecar for the control layer.

    pip install -r services/privacy_filter/requirements.txt
    uvicorn services.privacy_filter.server:app --port 8790

POST /detect {"text": "..."} -> {"spans": [{"label", "start", "end", "score", "text"}]}
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

MODEL = os.environ.get("PRIVACY_FILTER_MODEL", "openai/privacy-filter")


def decode(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """BIOES token tags (with character offsets) -> merged spans.

    A span starts at B-/S- or at a change of entity type, continues through I-/E- of the same
    type, and ends after E-/S-. Stray I-/E- tokens open a span rather than being dropped, so a
    mis-tagged boundary never leaks the rest of an entity.
    """
    spans: list[dict[str, Any]] = []
    open_span: dict[str, Any] | None = None
    for t in tokens:
        tag = str(t.get("entity", "O"))
        if tag == "O" or "-" not in tag:
            open_span = None
            continue
        prefix, label = tag.split("-", 1)
        if open_span is None or prefix in ("B", "S") or open_span["label"] != label:
            open_span = {"label": label, "start": t["start"], "end": t["end"], "scores": [t["score"]]}
            spans.append(open_span)
        else:
            open_span["end"] = t["end"]
            open_span["scores"].append(t["score"])
        if prefix in ("E", "S"):
            open_span = None
    return [
        {"label": s["label"], "start": s["start"], "end": s["end"], "score": sum(s["scores"]) / len(s["scores"])}
        for s in spans
    ]


class DetectIn(BaseModel):
    text: str


app = FastAPI(title="privacy-filter sidecar")
_pipe = None


def pipe():
    global _pipe
    if _pipe is None:
        from transformers import pipeline  # heavy import, deferred so tests can import decode()

        _pipe = pipeline("token-classification", model=MODEL, aggregation_strategy="none")
    return _pipe


@app.post("/detect")
def detect(body: DetectIn) -> dict[str, Any]:
    spans = decode(pipe()(body.text))
    for s in spans:
        s["text"] = body.text[s["start"] : s["end"]]
    return {"model": MODEL, "spans": spans}


@app.get("/health")
def health() -> dict[str, str]:
    return {"model": MODEL, "loaded": str(_pipe is not None)}
