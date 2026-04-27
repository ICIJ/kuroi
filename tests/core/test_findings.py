from kuroi.core.findings import Finding, bbox_union


def test_finding_basic_construction() -> None:
    f = Finding(
        page=4,
        start=5,
        end=6,
        kind="person_name",
        confidence="high",
        source="rules:pii-en",
    )
    assert f.page == 4
    assert f.span_length == 2  # inclusive on both ends


def test_bbox_union_combines_words() -> None:
    boxes = [
        (10.0, 20.0, 30.0, 40.0),
        (32.0, 21.0, 50.0, 39.0),
    ]
    x0, y0, x1, y1 = bbox_union(boxes)
    assert x0 == 10.0
    assert y0 == 20.0
    assert x1 == 50.0
    assert y1 == 40.0


def test_bbox_union_empty_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        bbox_union([])
