"""End-to-end: a run with two LLM categories on different models produces
the expected unioned findings and writes both the cache and the routing
metadata to the audit log."""

from pathlib import Path

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding


class _RoutingProvider:
    """Stub provider that returns category-specific findings based on which
    model the chunker calls it with. Caching tokens are returned non-zero
    on the second-and-later calls."""

    name = "anthropic"
    model = "claude-opus-4-7"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def detect_redactions(
        self,
        pages,
        llm_category_ids,
        *,
        instructions=(),
        seed=None,
        attempt=0,
        layout_aware=False,
        model=None,
    ):
        call_idx = len(self.calls)
        self.calls.append(
            {"model": model, "categories": llm_category_ids, "instructions": instructions}
        )
        # Findings depend on which categories were requested
        findings: list[Finding] = []
        if "person_name" in llm_category_ids:
            findings.append(
                Finding(page=1, start=0, end=1, kind="person_name", confidence="high", source="llm")
            )
        if "street_address" in llm_category_ids:
            findings.append(
                Finding(
                    page=1, start=2, end=3, kind="street_address", confidence="medium", source="llm"
                )
            )
        chunk = ChunkRecord(
            chunk_idx=0,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=None,
            seed_honored=False,
            system_fingerprint=None,
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
            tokens_in=4200,
            tokens_out=200,
            duration_ms=500,
            cache_creation_input_tokens=1180 if call_idx == 0 else 0,
            cache_read_input_tokens=0 if call_idx == 0 else 1180,
        )
        return findings, [chunk]


def test_routing_two_models_unions_findings(tmp_path: Path) -> None:
    from kuroi.core.chunking import detect_redactions_chunked
    from kuroi.core.config import DEFAULT_RETRY_POLICY
    from kuroi.core.pdf import Page, Word
    from kuroi.core.rules import Category

    pages = (
        Page(
            number=1,
            words=(
                Word(idx=0, text="John", bbox=(0, 0, 1, 1)),
                Word(idx=1, text="Smith", bbox=(0, 0, 1, 1)),
                Word(idx=2, text="123", bbox=(0, 0, 1, 1)),
                Word(idx=3, text="Main", bbox=(0, 0, 1, 1)),
            ),
        ),
    )
    cats = (
        Category("person_name", "person", "llm", "high", None, model="claude-haiku-4-5"),
        Category("street_address", "addr", "llm", "medium", None, model=None),  # default
    )
    provider = _RoutingProvider()

    findings, chunks = detect_redactions_chunked(
        provider,
        pages,
        ("person_name", "street_address"),
        pages_per_batch=1,
        retry_policy=DEFAULT_RETRY_POLICY,
        categories=cats,
    )

    # Two calls, one per model group.
    assert len(provider.calls) == 2
    by_model = {c["model"]: c["categories"] for c in provider.calls}
    assert by_model["claude-haiku-4-5"] == ("person_name",)
    assert by_model["claude-opus-4-7"] == ("street_address",)

    # Both findings preserved; deduplicated correctly (none collide).
    kinds = {f.kind for f in findings}
    assert kinds == {"person_name", "street_address"}

    # Cache tokens: one write (call 0), one read (call 1).
    total_writes = sum(c.cache_creation_input_tokens for c in chunks)
    total_reads = sum(c.cache_read_input_tokens for c in chunks)
    assert total_writes == 1180
    assert total_reads == 1180
