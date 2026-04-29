# State persistence implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve gaps 2.1, 2.2, and 2.3 from the spec — rework the audit-log schema with full provenance and SHA-by-default text, add a separate `state.toml` for "first-time" markers and run counts, and replace the implicit "backups never expire" behavior with a configurable lazy sweep plus a `kuroi backups` CLI.

Gap 2.4 (`kuroi review` session files) is deferred to the same plan that builds the review TUI; this plan does not touch it.

**Depends on the cost-and-reproducibility plan.** Task 3 reads `chunks` and `actual_cost` from `cli/run.py`'s scope, both of which are introduced by that earlier plan (tasks 8-10). Land it first; if you need to land this plan in isolation, replace `sum(c.tokens_in for c in chunks)` and `actual_cost` with literal `0` and `0.0` and add the real values when the cost work lands. The file-handling and CLI-surfaces plans are fully independent of this one.

**Architecture:** `core/audit.py` is restructured around a single `event` discriminator with four event types (`session_start`, `chunk_request`, `finding`, `session_end`). Each event gains the fields specified in section 2.1 of the spec, with text fields hashed by default and an `[audit] include_text` opt-in for plaintext. A new `core/state.py` reads/writes `$XDG_STATE_HOME/kuroi/state.toml` with the same atomic-write pattern used for config. `core/backup.py` gains a `sweep_backups()` function called eagerly from `run` and `undo`, plus a 6-char random suffix on session names. A new `cli/backups.py` Typer app provides `list` and `gc` subcommands.

**Tech Stack:** Python 3.12+, Typer, Rich, stdlib `tomllib`, stdlib `secrets` (for the random suffix), pytest, mypy, ruff.

**Spec:** `docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md`, sections 2.1, 2.2, 2.3.

---

## File structure

**Created:**
- `src/kuroi/core/state.py` — `State`, `load_state`, `write_state`, `xdg_state_home`.
- `src/kuroi/cli/backups.py` — `backups_app` Typer app with `list` and `gc`.
- `tests/core/test_state.py`
- `tests/cli/test_backups.py`

**Modified:**
- `src/kuroi/core/audit.py` — event renames, expanded schemas, hashing helpers, include_text option.
- `src/kuroi/core/backup.py` — random-suffix names, `sweep_backups()`, retention validation.
- `src/kuroi/core/config.py` — add `[audit] include_text`, `[backup] retention_hours`.
- `src/kuroi/cli/run.py` — call sweep, pass new audit fields.
- `src/kuroi/cli/undo.py` — call sweep.
- `src/kuroi/cli/__init__.py` — register `backups_app`.
- `tests/core/test_audit.py` — expanded assertions for new fields.
- `tests/core/test_backup.py` — new tests for sweep + naming.
- `tests/core/test_config.py` — new validation tests.

---

## Task 1: Rename audit events to match spec

**Why this task exists:** The spec uses `session_start` / `session_end`; the existing code uses `session_open` / `session_close`. Mechanical rename, atomic commit, no behavior change.

**Files:**
- Modify: `src/kuroi/core/audit.py`
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/core/test_audit.py`

- [ ] **Step 1: Update `tests/core/test_audit.py`**

Replace `"session_open"` with `"session_start"` and `"session_close"` with `"session_end"` in all assertions.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/core/test_audit.py -v`
Expected: FAIL — name mismatches.

- [ ] **Step 3: Rename in `audit.py`**

In `src/kuroi/core/audit.py`:

```python
# Replace "session_open" with "session_start"
self._write({
    "event": "session_start",
    ...
})

# Replace "session_close" with "session_end"
self._write({
    "event": "session_end",
    ...
})
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -q`
Expected: PASS — `cli/run.py` does not reference these names directly.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/audit.py tests/core/test_audit.py
git commit -m "refactor(audit): rename session_open/close to session_start/end"
```

---

## Task 2: Expand `session_start` schema

**Why this task exists:** The spec's session header carries full provenance: `audit_schema_version`, `session_id`, `input_sha256`, `input_pages`, `input_bytes`, `output_path`, `model_version`, `system_fingerprint`, `seed_requested`, `instructions`, `rule_set_files`, `config_resolved_from`. Capturing this is the foundation for `kuroi audit replay` (v1.1+) and for forensic review.

**Files:**
- Modify: `src/kuroi/core/audit.py`
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/core/test_audit.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_audit.py`:

```python
import json
import uuid


def test_audit_session_start_includes_full_provenance(tmp_path):
    log_path = tmp_path / "session.jsonl"
    sid = str(uuid.uuid4())
    log = AuditLog.open(
        log_path,
        original=tmp_path / "input.pdf",
        output=tmp_path / "output.pdf",
        provider="anthropic",
        model="claude-opus-4-7",
        rules=("pii-en",),
        session_id=sid,
        input_sha256="3a7f" + "0" * 60,
        input_pages=12,
        input_bytes=1234567,
        model_version="claude-opus-4-7@2026-04-15",
        instructions=(),
        config_resolved_from=("flag", "user_config"),
    )
    log.close(verification_passed=True, redaction_count=0,
              tokens_in=0, tokens_out=0, cost_usd=0.0, output_sha256="0" * 64)

    header = json.loads(log_path.read_text().splitlines()[0])
    assert header["event"] == "session_start"
    assert header["audit_schema_version"] == 1
    assert header["session_id"] == sid
    assert header["input_sha256"] == "3a7f" + "0" * 60
    assert header["input_pages"] == 12
    assert header["input_bytes"] == 1234567
    assert header["model_version"] == "claude-opus-4-7@2026-04-15"
    assert header["instructions"] == []
    assert header["rules"] == ["pii-en"]
    assert header["config_resolved_from"] == ["flag", "user_config"]
```

- [ ] **Step 2: Run the test to verify failure**

Run: `uv run pytest tests/core/test_audit.py::test_audit_session_start_includes_full_provenance -v`
Expected: FAIL — `AuditLog.open` does not accept these kwargs.

- [ ] **Step 3: Update `AuditLog.open`**

In `src/kuroi/core/audit.py`, replace the `open` classmethod:

```python
@classmethod
def open(
    cls,
    path: Path,
    *,
    original: Path,
    output: Path,
    provider: str,
    model: str,
    rules: tuple[str, ...],
    session_id: str,
    input_sha256: str,
    input_pages: int,
    input_bytes: int,
    model_version: str,
    instructions: tuple[dict, ...] = (),
    config_resolved_from: tuple[str, ...] = (),
) -> AuditLog:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(str(path.parent), 0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    fh = os.fdopen(fd, "w", encoding="utf-8")
    log = cls(fh)
    log._write(
        {
            "event": "session_start",
            "audit_schema_version": 1,
            "session_id": session_id,
            "ts_start": _now_iso(),
            "kuroi_version": _kuroi_version(),
            "input_path": str(original),
            "input_sha256": input_sha256,
            "input_pages": input_pages,
            "input_bytes": input_bytes,
            "output_path": str(output),
            "provider": provider,
            "model": model,
            "model_version": model_version,
            "rules": list(rules),
            "instructions": list(instructions),
            "config_resolved_from": list(config_resolved_from),
        }
    )
    return log


def _kuroi_version() -> str:
    from kuroi import __version__
    return __version__
```

- [ ] **Step 4: Update `cli/run.py` to compute and pass these**

In `src/kuroi/cli/run.py`, before opening the audit log, compute:

```python
import hashlib
import uuid

with pdf.open("rb") as fh:
    input_bytes = fh.read()
input_sha256 = hashlib.sha256(input_bytes).hexdigest()
session_id = str(uuid.uuid4())
```

Pass to `AuditLog.open`:

```python
audit = AuditLog.open(
    audit_path,
    original=pdf,
    output=output,
    provider=provider.name,
    model=provider.model,
    rules=tuple(rs.name for rs in rule_sets),
    session_id=session_id,
    input_sha256=input_sha256,
    input_pages=len(pages),
    input_bytes=len(input_bytes),
    model_version=getattr(provider, "model_version", provider.model),
    instructions=(),
    config_resolved_from=(),  # populated by the CLI-surfaces plan with -v
)
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: failures in any test that calls `AuditLog.open` directly without the new fields. Update each test to pass the kwargs.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/audit.py src/kuroi/cli/run.py tests/core/test_audit.py
git commit -m "feat(audit): expand session_start with full provenance"
```

---

## Task 3: Expand `session_end` schema

**Why this task exists:** Spec section 2.1 requires the footer to carry `tokens_in`, `tokens_out`, `cost_usd`, `verify_result`, `verify_leak_count`, `output_sha256`, `redactions_applied`, `redactions_rejected`, `redactions_excluded`, `duration_ms`, plus `status` (`ok`/`failed`/`verify_failed`).

**Files:**
- Modify: `src/kuroi/core/audit.py`
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/core/test_audit.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_audit.py`:

```python
def test_audit_session_end_carries_full_metrics(tmp_path):
    log_path = tmp_path / "session.jsonl"
    log = AuditLog.open(
        log_path,
        original=tmp_path / "in.pdf",
        output=tmp_path / "out.pdf",
        provider="anthropic",
        model="claude-opus-4-7",
        rules=(),
        session_id="x",
        input_sha256="0" * 64,
        input_pages=1,
        input_bytes=100,
        model_version="claude-opus-4-7",
    )
    log.close(
        verification_passed=True,
        redaction_count=5,
        tokens_in=1000,
        tokens_out=200,
        cost_usd=0.0285,
        output_sha256="e" * 64,
        verify_leak_count=0,
        redactions_rejected=2,
        redactions_excluded=1,
    )

    footer = json.loads(log_path.read_text().splitlines()[-1])
    assert footer["event"] == "session_end"
    assert footer["status"] == "ok"
    assert footer["tokens_in"] == 1000
    assert footer["tokens_out"] == 200
    assert footer["cost_usd"] == 0.0285
    assert footer["output_sha256"] == "e" * 64
    assert footer["verify_result"] == "pass"
    assert footer["verify_leak_count"] == 0
    assert footer["redactions_applied"] == 5
    assert footer["redactions_rejected"] == 2
    assert footer["redactions_excluded"] == 1
    assert "duration_ms" in footer
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_audit.py::test_audit_session_end_carries_full_metrics -v`
Expected: FAIL.

- [ ] **Step 3: Update `AuditLog.close` and the constructor**

In `src/kuroi/core/audit.py`:

In `__init__`, capture a start timestamp:

```python
def __init__(self, fh: IO[str]) -> None:
    self._fh = fh
    self._started = time.monotonic()
```

Add `import time` at the top.

Replace `close`:

```python
def close(
    self,
    *,
    verification_passed: bool,
    redaction_count: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    output_sha256: str = "",
    verify_leak_count: int = 0,
    redactions_rejected: int = 0,
    redactions_excluded: int = 0,
) -> None:
    duration_ms = int((time.monotonic() - self._started) * 1000)
    if verification_passed:
        status, verify_result = "ok", "pass"
    elif verify_leak_count > 0:
        status, verify_result = "verify_failed", "fail"
    else:
        status, verify_result = "failed", "skipped"
    self._write(
        {
            "event": "session_end",
            "ts_end": _now_iso(),
            "status": status,
            "redactions_applied": redaction_count,
            "redactions_rejected": redactions_rejected,
            "redactions_excluded": redactions_excluded,
            "duration_ms": duration_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "verify_result": verify_result,
            "verify_leak_count": verify_leak_count,
            "output_sha256": output_sha256,
        }
    )
    self._fh.close()
```

- [ ] **Step 4: Update `cli/run.py` to pass these on close**

After successful redaction:

```python
import hashlib

with output.open("rb") as fh:
    output_sha256 = hashlib.sha256(fh.read()).hexdigest()

audit.close(
    verification_passed=True,
    redaction_count=len(findings),
    tokens_in=sum(c.tokens_in for c in chunks),
    tokens_out=sum(c.tokens_out for c in chunks),
    cost_usd=actual_cost,
    output_sha256=output_sha256,
)
```

For the verification-failed branch:

```python
audit.close(
    verification_passed=False,
    redaction_count=len(findings),
    tokens_in=sum(c.tokens_in for c in chunks),
    tokens_out=sum(c.tokens_out for c in chunks),
    cost_usd=0.0,
    verify_leak_count=len(report.leaks),
)
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -q`
Expected: PASS. Update any callers of `audit.close()` that pass only the original two args.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/audit.py src/kuroi/cli/run.py tests/core/test_audit.py
git commit -m "feat(audit): expand session_end with metrics and verify status"
```

---

## Task 4: Expand `finding` event schema

**Why this task exists:** Per section 2.1, every finding event must carry `bbox`, `text_sha256`, `text_length`, `context_sha256`, `decision`, `reviewer`. The hashing default is the privacy boundary — no plaintext on disk by default.

**Files:**
- Modify: `src/kuroi/core/audit.py`
- Modify: `src/kuroi/core/findings.py`
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/core/test_audit.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_audit.py`:

```python
def test_audit_finding_carries_bbox_and_hashes(tmp_path):
    log_path = tmp_path / "f.jsonl"
    log = AuditLog.open(
        log_path,
        original=tmp_path / "i.pdf",
        output=tmp_path / "o.pdf",
        provider="anthropic", model="m", rules=(),
        session_id="x", input_sha256="0" * 64,
        input_pages=1, input_bytes=1, model_version="m",
    )
    log.write_finding(
        Finding(page=1, start=5, end=6, kind="email", confidence="high",
                source="rules:pii-en"),
        bbox=(10.0, 20.0, 30.0, 40.0),
        redacted_text="x@y.z",
        context_text="see x@y.z for contact",
    )
    log.close(verification_passed=True, redaction_count=1)

    finding = json.loads(log_path.read_text().splitlines()[1])
    assert finding["event"] == "finding"
    assert finding["bbox"] == [10.0, 20.0, 30.0, 40.0]
    assert finding["text_length"] == 5
    assert len(finding["text_sha256"]) == 64
    assert len(finding["context_sha256"]) == 64
    assert finding["decision"] == "applied"
    assert finding["reviewer"] == "auto"
    # Plaintext fields default off:
    assert "text" not in finding
    assert "context" not in finding
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_audit.py::test_audit_finding_carries_bbox_and_hashes -v`
Expected: FAIL.

- [ ] **Step 3: Update `write_finding`**

In `src/kuroi/core/audit.py`:

```python
def write_finding(
    self,
    finding: Finding,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    redacted_text: str = "",
    context_text: str = "",
    decision: str = "applied",
    reviewer: str = "auto",
    include_text: bool = False,
) -> None:
    payload: dict[str, Any] = {
        "event": "finding",
        "ts": _now_iso(),
        "page": finding.page,
        "word_start": finding.start,
        "word_end": finding.end,
        "kind": finding.kind,
        "confidence": finding.confidence,
        "source": finding.source,
        "bbox": list(bbox) if bbox is not None else None,
        "text_length": len(redacted_text),
        "text_sha256": hashlib.sha256(redacted_text.encode("utf-8")).hexdigest(),
        "context_sha256": hashlib.sha256(context_text.encode("utf-8")).hexdigest(),
        "decision": decision,
        "reviewer": reviewer,
    }
    if include_text:
        payload["text"] = redacted_text
        payload["context"] = context_text
    self._write(payload)
```

Add `import hashlib` at the top.

- [ ] **Step 4: Update `cli/run.py` to compute bbox and surrounding text**

The bbox of a multi-word finding is the union of its word bboxes, computable from the page's `Word` list:

```python
from kuroi.core.findings import bbox_union

for f in findings:
    page = pages[f.page - 1]
    words = page.words[f.start : f.end + 1]
    bbox = bbox_union(w.bbox for w in words)
    redacted_text = " ".join(w.text for w in words)
    context_words = page.words[max(0, f.start - 16) : min(len(page.words), f.end + 17)]
    context_text = " ".join(w.text for w in context_words)
    audit.write_finding(
        f,
        bbox=bbox,
        redacted_text=redacted_text,
        context_text=context_text,
    )
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/audit.py src/kuroi/cli/run.py tests/core/test_audit.py
git commit -m "feat(audit): expand finding events with bbox + hashes"
```

---

## Task 5: Optional plaintext (`[audit] include_text`)

**Why this task exists:** Legal teams need a self-contained log; spec section 2.1 commits to opt-in plaintext via config.

**Files:**
- Modify: `src/kuroi/core/config.py`
- Modify: `src/kuroi/cli/run.py`
- Create: a focused test in `tests/core/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_config.py`:

```python
def test_config_audit_include_text_default_false(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('provider = "anthropic"\nmodel = "m"\n')
    config = resolve_config(
        ConfigOverrides(),
        env={},
        file_path=cfg_file,
    )
    assert config.audit_include_text is False


def test_config_audit_include_text_from_file(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        'provider = "anthropic"\nmodel = "m"\n\n[audit]\ninclude_text = true\n'
    )
    config = resolve_config(
        ConfigOverrides(),
        env={},
        file_path=cfg_file,
    )
    assert config.audit_include_text is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_config.py::test_config_audit_include_text_default_false -v`
Expected: FAIL — `Config` has no `audit_include_text` field.

- [ ] **Step 3: Add `audit_include_text` to `Config`**

In `src/kuroi/core/config.py`:

```python
@dataclass(frozen=True)
class Config:
    provider: ProviderName
    model: str
    ollama_url: str
    audit_include_text: bool = False
```

In `resolve_config`, after the existing fields:

```python
audit_include_text_raw = file_data.get("audit", {}).get("include_text", False)
if not isinstance(audit_include_text_raw, bool):
    raise ConfigError(
        f"Expected boolean for `audit.include_text`, got {type(audit_include_text_raw).__name__}"
    )
```

Pass to `Config(...)`.

- [ ] **Step 4: Wire `cli/run.py` to pass the flag**

In `cli/run.py`, change the `audit.write_finding` call to:

```python
audit.write_finding(
    f,
    bbox=bbox,
    redacted_text=redacted_text,
    context_text=context_text,
    include_text=config.audit_include_text,
)
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/config.py src/kuroi/cli/run.py tests/core/test_config.py
git commit -m "feat(audit): add [audit] include_text opt-in for plaintext"
```

---

## Task 6: `core/state.py` module

**Why this task exists:** Spec section 2.2 specifies a separate `state.toml` file for first-cloud-use marker, training-wheels counter, total runs. The module exists in this plan but has no caller yet — first-cloud-use prompt and training-wheels are part of separate features. Landing the foundation here means those features are unblocked when their plans run.

**Files:**
- Create: `src/kuroi/core/state.py`
- Create: `tests/core/test_state.py`
- Modify: `tests/conftest.py` (add `XDG_STATE_HOME` to the isolation fixture)

- [ ] **Step 1: Update conftest fixture**

In `tests/conftest.py`, find the `_isolate_kuroi_config` fixture. After the existing `monkeypatch.setenv("XDG_CONFIG_HOME", ...)` line, add:

```python
monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
```

- [ ] **Step 2: Write the failing tests**

Create `tests/core/test_state.py`:

```python
import os
import stat
from pathlib import Path

from kuroi.core.state import State, load_state, write_state, xdg_state_home


def test_xdg_state_home_uses_env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "x"))
    assert xdg_state_home() == tmp_path / "x"


def test_load_state_returns_defaults_when_missing(tmp_path):
    state = load_state(tmp_path / "state.toml")
    assert state.schema_version == 1
    assert state.cloud_acknowledged is False
    assert state.training_wheels_remaining == 3
    assert state.total_runs == 0


def test_write_then_load_roundtrip(tmp_path):
    p = tmp_path / "state.toml"
    s = State(
        schema_version=1,
        cloud_acknowledged=True,
        cloud_acknowledged_at="2026-04-29T12:00:00Z",
        training_wheels_remaining=2,
        total_runs=7,
    )
    write_state(p, s)
    loaded = load_state(p)
    assert loaded == s


def test_state_file_is_mode_0600(tmp_path):
    p = tmp_path / "state.toml"
    write_state(p, State(schema_version=1))
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/core/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `core/state.py`**

Create `src/kuroi/core/state.py`:

```python
"""kuroi user-state file at $XDG_STATE_HOME/kuroi/state.toml.

State is "what kuroi knows about the user" — the cloud-use acknowledgement,
the training-wheels run counter, and the total run count. Distinct from
config (which is "what the user has set"). Atomic writes; mode 0600.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class State:
    schema_version: int = 1
    cloud_acknowledged: bool = False
    cloud_acknowledged_at: str = ""
    training_wheels_remaining: int = 3
    total_runs: int = 0


def xdg_state_home() -> Path:
    """`$XDG_STATE_HOME` or `~/.local/state`."""
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "state"


def state_path() -> Path:
    return xdg_state_home() / "kuroi" / "state.toml"


def load_state(path: Path) -> State:
    """Load state. Returns defaults when the file is missing."""
    if not path.exists():
        return State()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return State(
        schema_version=int(raw.get("schema_version", 1)),
        cloud_acknowledged=bool(raw.get("cloud_acknowledged", False)),
        cloud_acknowledged_at=str(raw.get("cloud_acknowledged_at", "")),
        training_wheels_remaining=int(raw.get("training_wheels_remaining", 3)),
        total_runs=int(raw.get("total_runs", 0)),
    )


def write_state(path: Path, state: State) -> None:
    """Atomic write to `path` with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"schema_version = {state.schema_version}\n"
        f"cloud_acknowledged = {str(state.cloud_acknowledged).lower()}\n"
        f'cloud_acknowledged_at = "{state.cloud_acknowledged_at}"\n'
        f"training_wheels_remaining = {state.training_wheels_remaining}\n"
        f"total_runs = {state.total_runs}\n"
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(str(path), 0o600)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/core/test_state.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/state.py tests/core/test_state.py tests/conftest.py
git commit -m "feat(state): add state.toml at XDG_STATE_HOME with atomic write"
```

---

## Task 7: Add 6-char random suffix to backup directory names

**Why this task exists:** Concurrent runs (the file-handling plan adds output locks) require non-colliding backup-dir names even within a second. The lazy sweep parses the timestamp prefix and ignores the suffix.

**Files:**
- Modify: `src/kuroi/core/backup.py`
- Modify: `tests/core/test_backup.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_backup.py`:

```python
import re


def test_backup_timestamp_has_random_suffix(tmp_path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    backup = create_backup(src, backup_root=tmp_path / "backups")
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z-[0-9a-f]{6}$",
        backup.timestamp,
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_backup.py::test_backup_timestamp_has_random_suffix -v`
Expected: FAIL — current names lack the suffix.

- [ ] **Step 3: Update `_next_timestamp`**

In `src/kuroi/core/backup.py`:

```python
import secrets


def _next_timestamp(backup_root: Path) -> str:
    suffix = secrets.token_hex(3)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{ts}-{suffix}"
```

The collision-retry loop is no longer needed; remove it.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/core/test_backup.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/backup.py tests/core/test_backup.py
git commit -m "feat(backup): add 6-char random suffix to session names"
```

---

## Task 8: Backup retention validation in config

**Why this task exists:** Spec section 2.3 says negative `retention_hours` is rejected at config load. The validator gates the lazy sweep behavior.

**Files:**
- Modify: `src/kuroi/core/config.py`
- Modify: `tests/core/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_config.py`:

```python
def test_config_backup_retention_default_24(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('provider = "anthropic"\nmodel = "m"\n')
    config = resolve_config(ConfigOverrides(), env={}, file_path=cfg)
    assert config.backup_retention_hours == 24


def test_config_backup_retention_zero_means_keep_forever(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        'provider = "anthropic"\nmodel = "m"\n[backup]\nretention_hours = 0\n'
    )
    config = resolve_config(ConfigOverrides(), env={}, file_path=cfg)
    assert config.backup_retention_hours == 0


def test_config_backup_retention_negative_rejected(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        'provider = "anthropic"\nmodel = "m"\n[backup]\nretention_hours = -1\n'
    )
    with pytest.raises(ConfigError, match="positive integer or 0"):
        resolve_config(ConfigOverrides(), env={}, file_path=cfg)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_config.py::test_config_backup_retention_default_24 -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/kuroi/core/config.py`, add `backup_retention_hours: int = 24` to `Config`. In `resolve_config`:

```python
backup_retention_raw = file_data.get("backup", {}).get("retention_hours", 24)
if not isinstance(backup_retention_raw, int) or backup_retention_raw < 0:
    raise ConfigError(
        "kuroi requires backups for in-place edits; set retention_hours "
        "to a positive integer or 0"
    )
```

Pass to `Config(...)`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/core/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/config.py tests/core/test_config.py
git commit -m "feat(config): validate [backup] retention_hours"
```

---

## Task 9: `sweep_backups()` function

**Why this task exists:** Section 2.3 commits to lazy cleanup at every `kuroi run` and `kuroi undo`. The function is pure — it takes a root directory and a retention threshold, parses entry timestamps, removes expired ones.

**Files:**
- Modify: `src/kuroi/core/backup.py`
- Modify: `tests/core/test_backup.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_backup.py`:

```python
import shutil
from datetime import datetime, timedelta, UTC

from kuroi.core.backup import sweep_backups


def test_sweep_backups_removes_expired(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    # Old: 30h ago.
    old_ts = (datetime.now(UTC) - timedelta(hours=30)).strftime("%Y-%m-%dT%H-%M-%SZ-aaaaaa")
    (root / old_ts).mkdir()
    (root / old_ts / "manifest.json").write_text('{}')
    # New: just now.
    new_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ-bbbbbb")
    (root / new_ts).mkdir()
    (root / new_ts / "manifest.json").write_text('{}')

    pruned = sweep_backups(root, retention_hours=24)

    assert pruned == 1
    assert not (root / old_ts).exists()
    assert (root / new_ts).exists()


def test_sweep_backups_zero_means_keep_all(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    very_old = (datetime.now(UTC) - timedelta(days=400)).strftime("%Y-%m-%dT%H-%M-%SZ-cccccc")
    (root / very_old).mkdir()

    pruned = sweep_backups(root, retention_hours=0)

    assert pruned == 0
    assert (root / very_old).exists()


def test_sweep_backups_ignores_non_kuroi_dirs(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    (root / "user-dropped-this").mkdir()
    (root / "random-file.txt").write_text("hi")

    pruned = sweep_backups(root, retention_hours=24)

    assert pruned == 0
    assert (root / "user-dropped-this").exists()
    assert (root / "random-file.txt").exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_backup.py::test_sweep_backups_removes_expired -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `sweep_backups`**

In `src/kuroi/core/backup.py`:

```python
import re
import shutil

_BACKUP_DIR_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)(-[0-9a-f]{6})?$"
)


def sweep_backups(root: Path, *, retention_hours: int) -> int:
    """Remove backup subdirectories older than `retention_hours`.

    `retention_hours = 0` disables pruning (legal-hold mode).
    Entries whose names don't match the kuroi timestamp pattern are left alone.
    Returns the number of pruned subdirectories.
    """
    if retention_hours == 0 or not root.is_dir():
        return 0
    cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
    pruned = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        m = _BACKUP_DIR_RE.match(child.name)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        if ts < cutoff:
            shutil.rmtree(child)
            pruned += 1
    return pruned
```

Add `from datetime import timedelta` to the existing datetime import.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/core/test_backup.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/backup.py tests/core/test_backup.py
git commit -m "feat(backup): add sweep_backups for lazy expiration"
```

---

## Task 10: Call `sweep_backups` from `run` and `undo`

**Why this task exists:** Plumbs the lazy sweep into the actual entry points so backups are pruned automatically.

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Modify: `src/kuroi/cli/undo.py`
- Modify: `tests/cli/test_run.py`

- [ ] **Step 1: Add a sweep call at the start of `run.py`**

In `src/kuroi/cli/run.py`, after `config = resolve_config(...)`:

```python
from kuroi.core.backup import sweep_backups

sweep_backups(backup_dir, retention_hours=config.backup_retention_hours)
```

- [ ] **Step 2: Same in `undo.py`**

In `src/kuroi/cli/undo.py`, before `latest_backup`:

```python
from kuroi.core.backup import sweep_backups
from kuroi.core.config import resolve_config, ConfigOverrides, xdg_config_home

config = resolve_config(
    ConfigOverrides(), env=os.environ,
    file_path=xdg_config_home() / "kuroi" / "config.toml",
)
sweep_backups(backup_dir, retention_hours=config.backup_retention_hours)
```

(Adjust to whatever the existing `undo.py` shape is — `os.environ` should already be available; if not, `import os`.)

- [ ] **Step 3: Verify with an integration test**

Add to `tests/cli/test_run.py`:

```python
from datetime import datetime, timedelta, UTC


def test_run_sweeps_expired_backups(tmp_path, monkeypatch, make_pdf):
    monkeypatch.setenv("KUROI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old_name = (datetime.now(UTC) - timedelta(hours=48)).strftime(
        "%Y-%m-%dT%H-%M-%SZ-deadbe"
    )
    (backup_dir / old_name).mkdir()
    (backup_dir / old_name / "manifest.json").write_text("{}")

    pdf = make_pdf(["one"])
    out = tmp_path / "out.pdf"

    # Stub the SDK so the run completes.
    from kuroi.providers import anthropic as ap

    class _Stub:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                r = type("R", (), {})()
                r.content = [type("B", (), {"text": '{"findings": []}'})()]
                r.usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()
                r.system_fingerprint = None
                return r

    real = ap.AnthropicProvider
    monkeypatch.setattr(
        ap, "AnthropicProvider",
        lambda **kw: real(client=_Stub(), model=kw.get("model", "claude-opus-4-7")),
    )

    result = runner.invoke(app, [
        "run", str(pdf), "-o", str(out), "-y", "--backup-dir", str(backup_dir),
    ])
    assert result.exit_code == 0
    assert not (backup_dir / old_name).exists()
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/run.py src/kuroi/cli/undo.py tests/cli/test_run.py
git commit -m "feat(backup): sweep expired backups at start of run and undo"
```

---

## Task 11: `kuroi backups list` and `kuroi backups gc`

**Why this task exists:** Spec section 2.3 commits to a `kuroi backups` CLI for visibility and explicit control.

**Files:**
- Create: `src/kuroi/cli/backups.py`
- Create: `tests/cli/test_backups.py`
- Modify: `src/kuroi/cli/__init__.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_backups.py`:

```python
from datetime import datetime, timedelta, UTC
from pathlib import Path

from typer.testing import CliRunner

from kuroi.cli import app

runner = CliRunner()


def test_backups_list_shows_each_session(tmp_path: Path):
    bdir = tmp_path / "backups"
    bdir.mkdir()
    name = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ-abcdef")
    (bdir / name).mkdir()
    (bdir / name / "manifest.json").write_text(
        '{"original_path": "/x.pdf", "copy_path": "y", "timestamp": "' + name + '"}'
    )

    result = runner.invoke(app, ["backups", "list", "--root", str(bdir)])

    assert result.exit_code == 0
    assert name in result.stdout


def test_backups_gc_prunes_old(tmp_path: Path):
    bdir = tmp_path / "backups"
    bdir.mkdir()
    old = (datetime.now(UTC) - timedelta(hours=48)).strftime(
        "%Y-%m-%dT%H-%M-%SZ-aaaaaa"
    )
    (bdir / old).mkdir()
    (bdir / old / "manifest.json").write_text("{}")

    result = runner.invoke(app, ["backups", "gc", "--root", str(bdir), "--max-age", "24"])

    assert result.exit_code == 0
    assert "Pruned 1" in result.stdout
    assert not (bdir / old).exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_backups.py -v`
Expected: FAIL — no `backups` subcommand.

- [ ] **Step 3: Implement `cli/backups.py`**

Create `src/kuroi/cli/backups.py`:

```python
"""kuroi backups — list and garbage-collect backup sessions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.backup import sweep_backups

backups_app = typer.Typer(help="Manage the kuroi backup directory.")
console = Console()


DEFAULT_ROOT = Path.home() / "Documents" / "kuroi-backups"


@backups_app.command("list")
def list_(
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Backup directory."),
) -> None:
    """List backup sessions with their ages."""
    if not root.is_dir():
        console.print(f"  No backups directory at {root}.")
        return
    entries = sorted(p for p in root.iterdir() if p.is_dir())
    if not entries:
        console.print("  (no backups)")
        return
    now = datetime.now(UTC)
    for entry in entries:
        manifest = entry / "manifest.json"
        if not manifest.exists():
            continue
        data = json.loads(manifest.read_text())
        ts_str = data.get("timestamp", entry.name)
        try:
            # The timestamp prefix is the first 20 chars of the dir name.
            ts = datetime.strptime(ts_str[:20], "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
            age = now - ts
            age_label = f"{int(age.total_seconds() / 3600)}h ago"
        except ValueError:
            age_label = "(unknown age)"
        console.print(f"  {entry.name}  {age_label}  {data.get('original_path', '?')}")


@backups_app.command("gc")
def gc(
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Backup directory."),
    max_age: int = typer.Option(24, "--max-age", help="Hours; 0 = keep all."),
) -> None:
    """Prune backups older than `--max-age` hours."""
    pruned = sweep_backups(root, retention_hours=max_age)
    console.print(f"  Pruned {pruned} backup{'s' if pruned != 1 else ''}.")
```

- [ ] **Step 4: Register in `cli/__init__.py`**

Add the import:

```python
from kuroi.cli.backups import backups_app
```

And the registration:

```python
app.add_typer(backups_app, name="backups", help="Manage backups.")
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/cli/backups.py src/kuroi/cli/__init__.py tests/cli/test_backups.py
git commit -m "feat(cli): add kuroi backups list and gc subcommands"
```

---

## Self-review checklist

- [ ] `uv run pytest tests/ -q` — all green.
- [ ] `uv run mypy src/` — no errors.
- [ ] `uv run ruff check src/ tests/` — no warnings.
- [ ] An audit log written by `kuroi run` contains `event: session_start`, `event: chunk_request` (from the cost-and-reproducibility plan), `event: finding`, `event: session_end` lines, all with the spec's expanded fields.
- [ ] A finding event has `text_sha256` and no `text` field by default; setting `[audit] include_text = true` in config flips this.
- [ ] `kuroi backups list` shows session ages.
- [ ] `kuroi backups gc --max-age 24` prunes expired backups.
- [ ] Backup directory names match `^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z-[0-9a-f]{6}$`.
- [ ] `~/.local/state/kuroi/state.toml` is writable, mode 0600, default contents on first read.
- [ ] Setting `[backup] retention_hours = -1` in config raises `ConfigError` at startup.
