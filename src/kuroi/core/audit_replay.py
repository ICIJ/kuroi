"""Replay an existing run's audit log for `kuroi undo`.

Reads the NDJSON audit log written by `AuditLog.open` and reconstructs the
set of `applied` findings. The output is consumed by the exclusion-set
builder and the regeneration step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class ReplayableFinding:
    page: int
    word_start: int
    word_end: int
    kind: str
    confidence: Confidence
    source: str
    bbox: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class ReplayableSession:
    session_id: str
    input_path: Path
    output_path: Path
    findings: tuple[ReplayableFinding, ...]


def load_session(audit_path: Path) -> ReplayableSession:
    """Parse `audit_path` (NDJSON) into a ReplayableSession.

    Only `finding` events with `decision == "applied"` are included; all other
    events are skipped. Unknown fields are tolerated for forward compat.
    Raises `ValueError` on missing `session_start` or a malformed JSON line.
    """
    session_start: dict | None = None
    findings: list[ReplayableFinding] = []

    with audit_path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{audit_path}: line {lineno}: malformed JSON: {exc}") from exc

            event = obj.get("event")
            if event == "session_start":
                session_start = obj
            elif event == "finding" and obj.get("decision") == "applied":
                bbox_raw = obj.get("bbox")
                bbox = tuple(bbox_raw) if bbox_raw is not None else None
                findings.append(
                    ReplayableFinding(
                        page=int(obj["page"]),
                        word_start=int(obj["word_start"]),
                        word_end=int(obj["word_end"]),
                        kind=str(obj["kind"]),
                        confidence=obj["confidence"],
                        source=str(obj["source"]),
                        bbox=bbox,  # type: ignore[arg-type]
                    )
                )

    if session_start is None:
        raise ValueError(f"{audit_path}: no session_start event found")

    return ReplayableSession(
        session_id=str(session_start["session_id"]),
        input_path=Path(session_start["input_path"]),
        output_path=Path(session_start["output_path"]),
        findings=tuple(findings),
    )
