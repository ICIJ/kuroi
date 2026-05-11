"""kuroi undo — restore from backup, optionally scoped to file/pages/elements."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console

from kuroi.cli.undo_picker import pick_findings
from kuroi.core.audit_replay import (
    ReplayableFinding,
    ReplayableSession,
    build_exclusion_set,
    load_session,
)
from kuroi.core.backup import (
    find_session_by_path,
    find_session_by_timestamp,
    latest_backup,
    sweep_backups,
)
from kuroi.core.config import (
    ConfigError,
    ConfigOverrides,
    resolve_config,
    xdg_config_home,
    xdg_data_home,
)
from kuroi.core.findings import Finding
from kuroi.core.locks import LockHeldError, output_lock
from kuroi.core.page_selection import PageSelectionError
from kuroi.core.page_selection import parse as parse_pages
from kuroi.core.pdf import extract_word_index
from kuroi.core.redaction import apply_redactions
from kuroi.core.undo_audit import UndoAuditLog
from kuroi.core.verification import verify_pdf

console = Console()


def _stdin_isatty() -> bool:
    """Return True if stdin is an interactive TTY.

    Isolated as a module-level callable so tests can monkeypatch
    ``kuroi.cli.undo._stdin_isatty`` independently of Click/CliRunner's
    stdin replacement.
    """
    return sys.stdin.isatty()


def _parse_words(spec: str | None) -> tuple[int, int] | None:
    if spec is None:
        return None
    try:
        left, _, right = spec.partition("-")
        return (int(left), int(right))
    except (ValueError, AttributeError):
        console.print(f"  [red]--words must be START-END (got {spec!r})[/]")
        raise typer.Exit(code=2)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_session_start(audit_path: Path) -> dict:
    """Read just the first NDJSON line, parsed."""
    with audit_path.open("r", encoding="utf-8") as fh:
        line = fh.readline().strip()
    return json.loads(line)


def _replayable_to_finding(rf: ReplayableFinding) -> Finding:
    return Finding(
        page=rf.page,
        start=rf.word_start,
        end=rf.word_end,
        kind=rf.kind,
        confidence=rf.confidence,
        source=rf.source,
    )


def _reconstruct_text(
    backup_pdf: Path,
    findings: tuple[ReplayableFinding, ...],
) -> dict[int, str]:
    """Re-extract words from the backup so the picker can show real text."""
    pages_by_number = {p.number: p for p in extract_word_index(backup_pdf).pages}
    text_by_index: dict[int, str] = {}
    for idx, f in enumerate(findings):
        page = pages_by_number.get(f.page)
        if page is None:
            text_by_index[idx] = ""
            continue
        words = page.words[f.word_start : f.word_end + 1]
        text_by_index[idx] = " ".join(w.text for w in words)
    return text_by_index


def _legacy_full_restore(
    bak,  # noqa: ANN001 — kuroi.core.backup.Backup, avoid extra import
    *,
    yes: bool,
) -> None:
    console.print(f"  Last backup: {bak.timestamp}")
    console.print(f"  Will restore: {bak.original_path}")
    if not yes:
        if not typer.confirm("Restore now?", default=True):
            raise typer.Exit(code=0)
    bak.original_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bak.copy_path, bak.original_path)
    console.print("  Restored.")


def undo(
    input: Path | None = typer.Argument(
        None,
        help="PDF whose backup should be restored. Default: most recent backup overall.",
    ),
    yes: bool = typer.Option(False, "-y", help="Skip the restore confirmation."),
    backup_dir: Path | None = typer.Option(
        None,
        "--backup-dir",
        help="Backup directory [default: $XDG_DATA_HOME/kuroi/backups].",
    ),
    audit_dir: Path | None = typer.Option(
        None,
        "--audit-dir",
        help="Audit directory [default: $XDG_DATA_HOME/kuroi/audit].",
    ),
    session: str | None = typer.Option(
        None, "--session", help="Exact backup-session timestamp to undo."
    ),
    pages: str | None = typer.Option(
        None,
        "--pages",
        help="Restrict undo to a page range (same syntax as `kuroi run --pages`).",
    ),
    page: int | None = typer.Option(None, "--page", help="Single page filter."),
    kind: str | None = typer.Option(None, "--kind", help="Finding kind filter."),
    words: str | None = typer.Option(
        None, "--words", help="Word range filter START-END (requires --page)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan, write nothing."),
) -> None:
    """Restore an original PDF from a backup; supports file/page/element scoping."""

    # ---- Validation
    if words is not None and page is None:
        console.print("  [red]--words requires --page[/]")
        raise typer.Exit(code=2)
    words_tuple = _parse_words(words)

    pages_tuple: tuple[int, ...] | None = None
    if pages is not None:
        try:
            pages_tuple = parse_pages(pages).pages
        except PageSelectionError as exc:
            console.print(f"  [red]{exc}[/]")
            raise typer.Exit(code=2)

    has_element_selector = page is not None or kind is not None or words is not None

    # ---- Config + dirs
    if backup_dir is None:
        backup_dir = xdg_data_home() / "kuroi" / "backups"
    if audit_dir is None:
        audit_dir = xdg_data_home() / "kuroi" / "audit"

    try:
        config = resolve_config(
            ConfigOverrides(),
            env=os.environ,
            file_path=xdg_config_home() / "kuroi" / "config.toml",
        )
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        raise typer.Exit(code=2) from exc

    sweep_backups(backup_dir, retention_hours=config.backup_retention_hours)

    # ---- Locate the session
    if session is not None:
        bak = find_session_by_timestamp(backup_dir, session)
        if bak is None:
            console.print(f"  No backup at {backup_dir}/{session}.")
            console.print("  Run 'kuroi backups list' to see what exists.")
            raise typer.Exit(code=1)
    elif input is not None:
        bak = find_session_by_path(backup_dir, input)
        if bak is None:
            console.print(
                f"  No backup found for {input}. "
                "Was this run made with --no-backup?"
            )
            raise typer.Exit(code=1)
    else:
        bak = latest_backup(backup_dir)
        if bak is None:
            console.print(f"  No backup found in {backup_dir}.")
            raise typer.Exit(code=1)

    audit_path = audit_dir / f"{bak.timestamp}.jsonl"
    audit_present = audit_path.is_file()

    # ---- Path A: no audit log, no selectors → legacy full restore
    if not audit_present and not has_element_selector and pages_tuple is None:
        _legacy_full_restore(bak, yes=yes)
        return

    # ---- Path B: missing audit log but selectors requested → exit 3
    if not audit_present and (has_element_selector or pages_tuple is not None):
        console.print(
            f"  Audit log not found for session {bak.timestamp}; "
            "element-level undo requires the audit log."
        )
        raise typer.Exit(code=3)

    # ---- Load the audit log
    try:
        session_obj = load_session(audit_path)
    except ValueError as exc:
        console.print(f"  Failed to parse audit log: {exc}")
        raise typer.Exit(code=3) from exc

    # ---- Backup integrity check
    session_start = _read_session_start(audit_path)
    expected_sha = session_start.get("input_sha256")
    actual_sha = _hash_file(bak.copy_path)
    if expected_sha and expected_sha != actual_sha:
        console.print(
            "  backup file modified since run — refusing to regenerate."
        )
        raise typer.Exit(code=1)

    # ---- Decide whether to open the picker
    open_picker = (
        _stdin_isatty()
        and not has_element_selector
    )
    picker_indices: tuple[int, ...] = ()
    selector_interactive = False
    if open_picker:
        text_by_index = _reconstruct_text(bak.copy_path, session_obj.findings)
        try:
            picker_indices = pick_findings(
                session_obj,
                text_by_index=text_by_index,
                pages_filter=pages_tuple,
            )
        except KeyboardInterrupt:
            raise typer.Exit(code=130) from None
        selector_interactive = True

    # No selectors AND picker was skipped (non-TTY or no findings) → treat as "exclude every finding".
    if not has_element_selector and pages_tuple is None and not picker_indices and not selector_interactive:
        excluded = frozenset(range(len(session_obj.findings)))
    else:
        excluded = build_exclusion_set(
            session_obj.findings,
            pages=pages_tuple,
            page=page,
            kind=kind,
            words=words_tuple,
            picker_indices=picker_indices,
        )

    if not excluded:
        console.print("  Nothing to undo (filters matched no findings).")
        raise typer.Exit(code=6)

    # ---- Confirmation summary
    console.print(
        f"  Will un-redact {len(excluded)} finding(s) "
        f"(regenerate {session_obj.output_path} from backup):"
    )
    for idx in sorted(excluded):
        f = session_obj.findings[idx]
        console.print(f"    p.{f.page}  {f.kind:<10}  (words {f.word_start}-{f.word_end})")
    if not yes:
        if not typer.confirm("Proceed?", default=True):
            raise typer.Exit(code=0)
    if dry_run:
        console.print("  Dry run: not writing.")
        return

    # ---- Regenerate
    output_path = session_obj.output_path
    kept = [
        _replayable_to_finding(rf)
        for i, rf in enumerate(session_obj.findings)
        if i not in excluded
    ]

    selector_payload = {
        "interactive": selector_interactive,
        "pages": pages,
        "page": page,
        "kind": kind,
        "words": words,
    }
    undo_ts_path = audit_dir / f"{_undo_timestamp()}.undo.jsonl"
    undo_log = UndoAuditLog.open(
        undo_ts_path,
        source_session_id=session_obj.session_id,
        source_session_ts=bak.timestamp,
        source_audit_path=audit_path,
        input_path=session_obj.input_path,
        output_path=output_path,
        backup_path=bak.copy_path,
        selector=selector_payload,
        findings_total=len(session_obj.findings),
        findings_excluded=len(excluded),
    )

    # collect text_sha256 / context_sha256 for the undo_finding rows
    sha_by_index: dict[int, tuple[str, str]] = {}
    with audit_path.open("r", encoding="utf-8") as fh:
        idx_counter = 0
        for raw in fh:
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "finding" and obj.get("decision") == "applied":
                if idx_counter in excluded:
                    sha_by_index[idx_counter] = (
                        obj.get("text_sha256", ""),
                        obj.get("context_sha256", ""),
                    )
                idx_counter += 1

    for idx in sorted(excluded):
        rf = session_obj.findings[idx]
        text_sha, context_sha = sha_by_index.get(idx, ("", ""))
        undo_log.write_undo_finding(
            rf, text_sha256=text_sha, context_sha256=context_sha
        )

    try:
        with output_lock(output_path):
            temp_out = output_path.with_suffix(
                output_path.suffix + ".kuroi-undo-tmp"
            )

            if not kept:
                # Excluding every finding → backup IS the answer.
                shutil.copy2(bak.copy_path, temp_out)
            else:
                extraction = extract_word_index(bak.copy_path)
                apply_redactions(bak.copy_path, kept, extraction.pages, temp_out)

            report = verify_pdf(temp_out)
            if not report.passed:
                temp_out.unlink(missing_ok=True)
                undo_log.close(
                    status="verify_failed",
                    redactions_kept=len(kept),
                    redactions_un_redacted=len(excluded),
                    verify_result="fail",
                    verify_leak_count=len(report.leaks),
                    output_sha256="",
                )
                console.print(
                    f"  [red]Verification FAILED.[/] "
                    f"{len(report.leaks)} leaks; output not written."
                )
                raise typer.Exit(code=4)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp_out), str(output_path))
            new_sha = _hash_file(output_path)

        undo_log.close(
            status="ok",
            redactions_kept=len(kept),
            redactions_un_redacted=len(excluded),
            verify_result="pass",
            verify_leak_count=0,
            output_sha256=new_sha,
        )
        console.print(f"  Regenerated {output_path}")
        console.print(f"  Undo audit: {undo_ts_path}")
    except LockHeldError as exc:
        undo_log.close(
            status="failed",
            redactions_kept=0,
            redactions_un_redacted=0,
            verify_result="skipped",
            verify_leak_count=0,
            output_sha256="",
        )
        console.print(f"  [red]Lock held:[/] {exc}")
        raise typer.Exit(code=5) from exc
    except typer.Exit:
        raise
    except BaseException:
        undo_log.close(
            status="failed",
            redactions_kept=0,
            redactions_un_redacted=0,
            verify_result="skipped",
            verify_leak_count=0,
            output_sha256="",
        )
        raise


def _undo_timestamp() -> str:
    from kuroi.core.backup import session_timestamp

    return session_timestamp()
