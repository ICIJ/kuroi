"""kuroi run — the canonical redaction command."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.audit import AuditLog
from kuroi.core.backup import create_backup, session_timestamp, sweep_backups
from kuroi.core.config import (
    ConfigError,
    ConfigOverrides,
    resolve_config,
    xdg_config_home,
    xdg_data_home,
)
from kuroi.core.findings import Finding, bbox_union
from kuroi.core.locks import LockHeldError, output_lock
from kuroi.core.output_resolution import (
    OutputCollisionError,
    OutputResolutionError,
    resolve_output_path,
)
from kuroi.core.pdf import OcrRequiredError, extract_word_index, serialize_for_llm
from kuroi.core.pricing import count_tokens, estimate_cost, load_pricing
from kuroi.core.redaction import apply_redactions
from kuroi.core.rules import apply_regex_rules, llm_categories, load_rule_set
from kuroi.core.verification import verify_pdf
from kuroi.providers.factory import make_provider

console = Console()


def run(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    output: Path | None = typer.Option(None, "-o", "--output"),
    in_place: bool = typer.Option(
        False, "--in-place", help="Write to the input path; backup is taken."
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing output file."),
    rules: str = typer.Option("", "--rules", help="Comma-separated rule set names."),
    instruct: str | None = typer.Option(
        None, "--instruct", "-i", help="Natural-language redaction instruction."
    ),
    yes: bool = typer.Option(False, "-y", help="Skip the apply confirmation."),
    backup_dir: Path | None = typer.Option(
        None,
        "--backup-dir",
        help="Backup directory [default: $XDG_DATA_HOME/kuroi/backups].",
    ),
    no_backup: bool = typer.Option(
        False,
        "--no-backup",
        help="Skip backup creation. With --in-place the original is unrecoverable.",
    ),
    audit_dir: Path | None = typer.Option(
        None,
        "--audit-dir",
        help="Audit log directory [default: $XDG_DATA_HOME/kuroi/audit].",
    ),
    provider_name: str | None = typer.Option(
        None,
        "--provider",
        help="LLM provider: 'anthropic' or 'ollama'. Overrides env and config.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Model ID. Overrides env and config.",
    ),
    ollama_url: str | None = typer.Option(
        None,
        "--ollama-url",
        help="Base URL of the Ollama daemon. Overrides env and config.",
    ),
    seed: int | None = typer.Option(
        None,
        "--seed",
        help="Reproducibility seed. Best-effort per provider; recorded in audit.",
    ),
) -> None:
    """Redact a PDF using rules and/or instructions, with verification gating."""
    if backup_dir is None:
        backup_dir = xdg_data_home() / "kuroi" / "backups"
    if audit_dir is None:
        audit_dir = xdg_data_home() / "kuroi" / "audit"
    if no_backup and in_place:
        console.print(
            "  [yellow]warning:[/] --no-backup with --in-place: "
            "the original file will be unrecoverable."
        )

    rule_set_names = tuple(name.strip() for name in rules.split(",") if name.strip())
    has_rules = bool(rule_set_names)
    has_instruct = bool(instruct)

    if not has_rules and not has_instruct:
        if yes:
            console.print("[red]Pass --rules, --instruct, or both; -y cannot prompt.[/]")
            raise typer.Exit(code=2)
        instruct = typer.prompt("Redaction instructions")
        has_instruct = True

    rule_sets = [load_rule_set(name) for name in rule_set_names]

    try:
        final_output = resolve_output_path(
            pdf, output=output, in_place=in_place, overwrite=overwrite
        )
    except OutputResolutionError as exc:
        console.print(f"  [red]{exc}[/]")
        raise typer.Exit(code=2) from exc
    except OutputCollisionError as exc:
        console.print(
            f"  [red]I won't overwrite {exc.target}.[/]\n\n"
            f"    Try one of:\n"
            f"      kuroi run {pdf} -o {exc.suggestion}\n"
            f"      kuroi run {pdf} -o {exc.target} --overwrite\n"
        )
        raise typer.Exit(code=2) from exc

    try:
        with output_lock(final_output):
            try:
                config = resolve_config(
                    ConfigOverrides(provider=provider_name, model=model, ollama_url=ollama_url),
                    env=os.environ,
                    file_path=xdg_config_home() / "kuroi" / "config.toml",
                )
            except ConfigError as exc:
                console.print(f"[red]Config error:[/] {exc}")
                raise typer.Exit(code=2) from exc

            if not no_backup:
                try:
                    backup_dir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    console.print(
                        f"  [red]Cannot create backup directory {backup_dir}:[/] {exc}\n"
                        f"  Pass --backup-dir to specify a writable location, "
                        f"or --no-backup to skip the backup."
                    )
                    raise typer.Exit(code=2) from exc

                sweep_backups(backup_dir, retention_hours=config.backup_retention_hours)

            try:
                result = extract_word_index(pdf)
            except OcrRequiredError as exc:
                pages_str = ", ".join(str(p) for p in exc.page_numbers)
                console.print(
                    f"  [red]Scanned pages detected (pages {pages_str}) but tesseract is not installed.[/]\n"
                    f"  Install tesseract and re-run, or run `kuroi doctor` for details."
                )
                raise typer.Exit(code=2) from exc

            pages = result.pages

            if result.ocr_page_count > 0:
                console.print(f"  OCR applied to {result.ocr_page_count} scanned page(s).")

            pricing = load_pricing()
            input_tokens = count_tokens(serialize_for_llm(pages))
            estimated_cost = estimate_cost(
                pricing, config.provider, config.model, input_tokens=input_tokens
            )
            console.print(
                f"  Estimated cost: ${estimated_cost:.4f}  "
                f"({input_tokens} input tokens, {config.provider}/{config.model})"
            )

            findings: list[Finding] = []
            llm_cat_ids: list[str] = []
            for rs in rule_sets:
                findings.extend(apply_regex_rules(pages, rs))
                llm_cat_ids.extend(c.id for c in llm_categories(rs))

            provider = make_provider(config)
            if seed is not None and provider.name == "anthropic":
                console.print(
                    "  [yellow]note:[/] --seed recorded but only temperature=0 "
                    "is enforced for this provider"
                )
            instruction_tuple: tuple[str, ...] = (instruct,) if instruct else ()
            provider_findings, chunks = provider.detect_redactions(
                pages, tuple(llm_cat_ids), instructions=instruction_tuple, seed=seed
            )
            findings.extend(provider_findings)
            actual_cost = 0.0

            if not findings:
                console.print(f"  No redactions proposed for {pdf}. Exiting.")
                raise typer.Exit(code=0)

            console.print(f"  Found {len(findings)} candidate redactions.")

            if not yes:
                confirm = typer.confirm("Apply redactions?", default=True)
                if not confirm:
                    raise typer.Exit(code=0)

            with pdf.open("rb") as fh:
                input_bytes = fh.read()
            input_sha256 = hashlib.sha256(input_bytes).hexdigest()
            session_id = str(uuid.uuid4())

            if no_backup:
                backup = None
                timestamp = session_timestamp()
            else:
                backup = create_backup(pdf, backup_root=backup_dir)
                timestamp = backup.timestamp
            audit_path = audit_dir / f"{timestamp}.jsonl"
            audit = AuditLog.open(
                audit_path,
                original=pdf,
                output=final_output,
                provider=provider.name,
                model=provider.model,
                rules=tuple(rs.name for rs in rule_sets),
                session_id=session_id,
                input_sha256=input_sha256,
                input_pages=len(pages),
                input_bytes=len(input_bytes),
                model_version=getattr(provider, "model_version", provider.model),
                instructions=({"text": instruct},) if instruct else (),
                config_resolved_from=(),
            )

            for chunk in chunks:
                audit.write_event("chunk_request", **asdict(chunk))

            temp_out = final_output.parent / (
                final_output.stem + ".kuroi-tmp" + final_output.suffix
            )
            moved = False
            try:
                for f in findings:
                    page = pages[f.page - 1]
                    words = page.words[f.start : f.end + 1]
                    bbox = bbox_union(w.bbox for w in words) if words else None
                    redacted_text = " ".join(w.text for w in words)
                    context_words = page.words[
                        max(0, f.start - 16) : min(len(page.words), f.end + 17)
                    ]
                    context_text = " ".join(w.text for w in context_words)
                    audit.write_finding(
                        f,
                        bbox=bbox,
                        redacted_text=redacted_text,
                        context_text=context_text,
                        include_text=config.audit_include_text,
                    )

                temp_out.parent.mkdir(parents=True, exist_ok=True)
                apply_redactions(pdf, findings, pages, temp_out)

                report = verify_pdf(temp_out)
                if not report.passed:
                    audit.write_event(
                        "verification_failed",
                        leaks=[
                            {"page": leak.page, "kind": leak.kind, "detail": leak.detail}
                            for leak in report.leaks
                        ],
                    )
                    audit.close(
                        verification_passed=False,
                        redaction_count=len(findings),
                        tokens_in=sum(c.tokens_in for c in chunks),
                        tokens_out=sum(c.tokens_out for c in chunks),
                        cost_usd=0.0,
                        verify_leak_count=len(report.leaks),
                    )
                    console.print(
                        f"  [red]Verification FAILED.[/] "
                        f"{len(report.leaks)} leaks; output not written."
                    )
                    raise typer.Exit(code=4)

                shutil.move(str(temp_out), str(final_output))
                moved = True

                actual_in = sum(c.tokens_in for c in chunks)
                actual_out = sum(c.tokens_out for c in chunks)
                if actual_in > 0:
                    rates = pricing.rates(config.provider, config.model)
                    actual_cost = (
                        actual_in / 1_000_000 * rates.input_per_million
                        + actual_out / 1_000_000 * rates.output_per_million
                    )
                    if estimated_cost > 0 and actual_cost / estimated_cost > 2.0:
                        console.print(
                            "  [yellow]note:[/] cost estimate diverged from actual "
                            f"(${estimated_cost:.4f} → ${actual_cost:.4f})"
                        )

                with final_output.open("rb") as fh:
                    output_sha256 = hashlib.sha256(fh.read()).hexdigest()

                audit.close(
                    verification_passed=True,
                    redaction_count=len(findings),
                    tokens_in=actual_in,
                    tokens_out=actual_out,
                    cost_usd=actual_cost,
                    output_sha256=output_sha256,
                )
                console.print(f"  Wrote {final_output}")
                if backup is not None:
                    console.print(f"  Backup: {backup.copy_path}")
                console.print(f"  Audit: {audit_path}")
            except typer.Exit:
                raise
            except BaseException:
                audit.write_event("error")
                audit.close(verification_passed=False, redaction_count=0)
                raise
            finally:
                if not moved:
                    temp_out.unlink(missing_ok=True)
    except LockHeldError as exc:
        console.print(f"  [red]{exc}[/]")
        raise typer.Exit(code=2) from exc
