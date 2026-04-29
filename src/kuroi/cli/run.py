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
from kuroi.core.backup import create_backup
from kuroi.core.config import (
    ConfigError,
    ConfigOverrides,
    resolve_config,
    xdg_config_home,
)
from kuroi.core.findings import Finding, bbox_union
from kuroi.core.pdf import extract_word_index, serialize_for_llm
from kuroi.core.pricing import count_tokens, estimate_cost, load_pricing
from kuroi.core.redaction import apply_redactions
from kuroi.core.rules import apply_regex_rules, llm_categories, load_rule_set
from kuroi.core.verification import verify_pdf
from kuroi.providers.factory import make_provider

console = Console()


def run(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    output: Path = typer.Option(..., "-o", "--output"),
    rules: str = typer.Option("pii", "--rules", help="Comma-separated rule set names."),
    yes: bool = typer.Option(False, "-y", help="Skip the apply confirmation."),
    backup_dir: Path = typer.Option(
        Path.home() / "Documents" / "kuroi-backups",
        "--backup-dir",
    ),
    audit_dir: Path = typer.Option(
        Path.home() / ".local" / "share" / "kuroi" / "audit",
        "--audit-dir",
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
    rule_set_names = tuple(name.strip() for name in rules.split(",") if name.strip())
    if not rule_set_names:
        console.print("[red]No rule sets specified.[/]")
        raise typer.Exit(code=2)

    rule_sets = [load_rule_set(name) for name in rule_set_names]

    if output.resolve() == pdf.resolve():
        console.print("  [red]Refusing to overwrite the input file.[/] Use a different `-o` path.")
        raise typer.Exit(code=2)

    try:
        config = resolve_config(
            ConfigOverrides(provider=provider_name, model=model, ollama_url=ollama_url),
            env=os.environ,
            file_path=xdg_config_home() / "kuroi" / "config.toml",
        )
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        raise typer.Exit(code=2) from exc

    pages = extract_word_index(pdf)

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
    provider_findings, chunks = provider.detect_redactions(
        pages, tuple(llm_cat_ids), seed=seed
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

    backup = create_backup(pdf, backup_root=backup_dir)
    audit_path = audit_dir / f"{backup.timestamp}.jsonl"
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
        config_resolved_from=(),  # populated in cluster 4 with -v
    )

    for chunk in chunks:
        audit.write_event("chunk_request", **asdict(chunk))

    temp_out = output.parent / (output.stem + ".kuroi-tmp" + output.suffix)
    moved = False
    try:
        for f in findings:
            page = pages[f.page - 1]
            words = page.words[f.start : f.end + 1]
            bbox = bbox_union(w.bbox for w in words) if words else None
            redacted_text = " ".join(w.text for w in words)
            context_words = page.words[max(0, f.start - 16) : min(len(page.words), f.end + 17)]
            context_text = " ".join(w.text for w in context_words)
            audit.write_finding(
                f,
                bbox=bbox,
                redacted_text=redacted_text,
                context_text=context_text,
            )

        # Apply to a temp file. Only promote to output if verification passes.
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
                f"  [red]Verification FAILED.[/] {len(report.leaks)} leaks; output not written."
            )
            raise typer.Exit(code=4)

        shutil.move(str(temp_out), str(output))
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

        with output.open("rb") as fh:
            output_sha256 = hashlib.sha256(fh.read()).hexdigest()

        audit.close(
            verification_passed=True,
            redaction_count=len(findings),
            tokens_in=actual_in,
            tokens_out=actual_out,
            cost_usd=actual_cost,
            output_sha256=output_sha256,
        )
        console.print(f"  Wrote {output}")
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
