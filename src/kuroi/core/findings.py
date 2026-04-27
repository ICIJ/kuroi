"""The Finding data type — what every detector emits and the redactor consumes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Confidence = Literal["high", "medium", "low"]
Bbox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Finding:
    """A single proposed redaction.

    `page` is 1-indexed. `start` and `end` are inclusive word indices into the
    per-page word list produced by extract_word_index. `kind` is the rule
    category (e.g. "email", "person_name"). `source` records who proposed it
    ("rules:pii-en", "llm", "instruction:1").
    """

    page: int
    start: int
    end: int
    kind: str
    confidence: Confidence
    source: str

    @property
    def span_length(self) -> int:
        return self.end - self.start + 1


def bbox_union(boxes: Iterable[Bbox]) -> Bbox:
    """Return the smallest axis-aligned rectangle containing all input boxes."""
    boxes = list(boxes)
    if not boxes:
        raise ValueError("bbox_union requires at least one box")
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return (x0, y0, x1, y1)
