"""Deterministic page-height estimate for console HTML, standing in for a browser (none is available).

It walks the rendered markup and adds up the heights the console's own components take with Primer
21.5.1 at a 1280 x 900 viewport (container 1248 px wide: 1440 max, 16 px gutters). Calibrated by hand
against Primer's box model:

| Piece | px |
|---|---|
| Header (py-2, f4 link) | 56 |
| UnderlineNav | 48 |
| <main> padding (py-3) | 32 |
| flash note | 54 (incl. 16 margin) |
| stat tile | 74, +10 with a meter; tiles wrap at 150 px min + 12 px gap; grid margin 12 |
| card | 2 border + 37 header + 32 body padding + content |
| grid row gap (acl-cols) | 12 |
| table header / row | 29 / 33 (a detail row counts as a row) |
| ranked bar row | 24 |
| 100% split bar | 14 |
| legend | 24 per line, ~6 entries per line at card width |
| svg chart | viewBox height x (box width / viewBox width) |
| line of 12 px text | 20, wrapping at ~7 px per character |
| list item (li) | 20 per line + 9 padding |
| pager / toolbar row (d-flex) | 36 |
| form control (input, select, textarea rows=n) | 32, textarea 20 x rows + 12 |

Containers without a rule stack their children. Margins from Primer utilities (mt-1..mt-4, mb-*) add
4/8/16/24 px.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

VIEWPORT = (1280, 900)
CONTENT = 1248
BUDGET = 2 * VIEWPORT[1]  # a page other than a database inspection page fits in two screens
VOID = {"br", "img", "input", "hr", "meta", "link", "path", "rect", "line", "circle", "polyline"}
SPACE = {"1": 4, "2": 8, "3": 16, "4": 24}
SPAN = re.compile(r"\bacl-c(\d+)\b")


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list[Node | str] = field(default_factory=list)

    @property
    def cls(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].children.append(data)


def parse(html: str) -> Node:
    t = _Tree()
    t.feed(html)
    return t.root


def _lines(text: str, width: float, px: float = 7.0) -> int:
    text = " ".join(text.split())
    return max(1, math.ceil(len(text) * px / max(width, 40))) if text else 0


def _margins(n: Node) -> int:
    out = 0
    for c in n.cls:
        m = re.fullmatch(r"(?:m[tby]|p[tby]|my|py)-(\d)", c)
        if m:
            out += SPACE.get(m.group(1), 0) * (2 if c[1] in "y" else 1)
    return out


def height(n: Node | str, width: float) -> float:
    if isinstance(n, str):
        return 20 * _lines(n, width)
    cls, tag = n.cls, n.tag
    if tag in ("script", "style", "option"):
        return 0
    if tag == "svg":
        vb = n.attrs.get("viewbox", "0 0 640 180").split()
        return float(vb[3]) * width / float(vb[2])
    if "Header" in cls:
        return 56
    if "UnderlineNav" in cls:
        return 48
    if "flash" in cls:
        return 54
    if "acl-tiles" in cls:
        tiles = [c for c in n.children if isinstance(c, Node)]
        per_row = max(1, int((width + 12) // (150 + 12)))
        rows = [tiles[i : i + per_row] for i in range(0, len(tiles), per_row)]
        tall = [max(74 + (10 if "acl-meter" in _classes(t) else 0) for t in r) for r in rows]
        return sum(tall) + 12 * (len(rows) - 1) + 12
    if "acl-cols" in cls:
        return _grid(n, width)
    if "acl-card" in cls:
        header = next((c for c in n.children if isinstance(c, Node) and "Box-header" in c.cls), None)
        body = [c for c in n.children if isinstance(c, Node) and c is not header]
        return 2 + (37 if header else 0) + sum(height(c, width - 2) for c in body) + _margins(n)
    if "Box-body" in cls:
        return 32 + _stack(n, width - 32)
    if tag == "table":
        rows = sum(1 for _ in _find(n, "tr"))
        has_head = any(True for _ in _find(n, "thead"))
        return 29 * has_head + 33 * (rows - has_head) + _margins(n)
    if "acl-hbars" in cls:
        return 24 * len([c for c in n.children if isinstance(c, Node)])
    if "acl-split" in cls:
        return 14
    if "acl-legend" in cls:
        items = len([c for c in n.children if isinstance(c, Node)])
        return 6 + 24 * max(1, math.ceil(items / max(1, int(width // 180)))) + _margins(n)
    if tag == "li":
        return 9 + _stack(n, width) + _margins(n)
    if tag == "textarea":
        return 20 * int(n.attrs.get("rows", "2")) + 12 + _margins(n)
    if tag in ("input", "select"):
        return 32
    if tag == "details" and "open" not in n.attrs:
        summary = next((c for c in n.children if isinstance(c, Node) and c.tag == "summary"), None)
        return (height(summary, width) if summary else 20) + _margins(n)
    if "d-flex" in cls or "acl-tools" in cls or "BtnGroup" in cls:
        return max([36] + [height(c, width) for c in n.children if isinstance(c, Node) and _is_block(c)]) + _margins(n)
    if not any(isinstance(c, Node) and _is_block(c) for c in n.children):
        return 20 * _lines(n.text(), width) + _margins(n)  # a line of inline content
    return _stack(n, width) + _margins(n)


BLOCK = {
    "div",
    "section",
    "table",
    "ul",
    "ol",
    "li",
    "p",
    "svg",
    "header",
    "nav",
    "main",
    "details",
    "form",
    "pre",
    "textarea",
    "h1",
    "h2",
    "h3",
    "h4",
    "figure",
}


def _is_block(n: Node) -> bool:
    return n.tag in BLOCK or bool(n.cls & {"acl-hbar-row", "acl-tile", "d-block"})


def _stack(n: Node, width: float) -> float:
    total, inline = 0.0, []
    for c in n.children:
        if isinstance(c, Node) and _is_block(c):
            if inline:
                total += 20 * _lines(" ".join(x if isinstance(x, str) else x.text() for x in inline), width)
                inline = []
            total += height(c, width)
        else:
            inline.append(c)
    if inline:
        total += 20 * _lines(" ".join(x if isinstance(x, str) else x.text() for x in inline), width)
    return total


def _grid(n: Node, width: float) -> float:
    col = (width - 11 * 12) / 12
    rows, row, used = [], [], 0
    for c in n.children:
        if not isinstance(c, Node):
            continue
        m = SPAN.search(c.attrs.get("class", ""))
        span = int(m.group(1)) if m else 12
        if used + span > 12:
            rows.append(row)
            row, used = [], 0
        row.append(height(c, col * span + 12 * (span - 1)))
        used += span
    if row:
        rows.append(row)
    return sum(max(r) for r in rows) + 12 * max(0, len(rows) - 1)


def _find(n: Node, tag: str):
    for c in n.children:
        if isinstance(c, Node):
            if c.tag == tag:
                yield c
            yield from _find(c, tag)


def _classes(n: Node) -> str:
    return " ".join([n.attrs.get("class", "")] + [_classes(c) for c in n.children if isinstance(c, Node)])


def estimate(html: str, width: float = CONTENT) -> int:
    """Estimated rendered height in px of a console page (its #acl-root HTML)."""
    return round(height(parse(html), width))
