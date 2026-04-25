from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_patch_state(monkeypatch: pytest.MonkeyPatch):
    """Reset compat patches so each test exercises the install-time path."""

    from hwpx_mcp_server import compat

    monkeypatch.setattr(compat, "_ID_GENERATORS_PATCHED", False)
    yield


def test_patch_clamps_paragraph_id_to_signed_int32() -> None:
    from hwpx_mcp_server.compat import _patch_upstream_id_generators_to_signed_int32

    _patch_upstream_id_generators_to_signed_int32()

    from hwpx.oxml import document as _hwpx_document

    samples = [int(_hwpx_document._paragraph_id()) for _ in range(500)]
    assert max(samples) < 2**31, (
        f"_paragraph_id produced {max(samples)} (>= 2^31) after compat patch"
    )
    assert min(samples) >= 0


def test_patch_covers_object_and_memo_ids() -> None:
    from hwpx_mcp_server.compat import _patch_upstream_id_generators_to_signed_int32

    _patch_upstream_id_generators_to_signed_int32()

    from hwpx.oxml import document as _hwpx_document

    for fn in (_hwpx_document._object_id, _hwpx_document._memo_id):
        for _ in range(200):
            value = int(fn())
            assert 0 <= value < 2**31, (
                f"{fn.__name__} produced {value} (0x{value:x}); "
                "must be in [0, 2^31)"
            )


def test_patch_is_idempotent_when_called_twice() -> None:
    from hwpx_mcp_server.compat import _patch_upstream_id_generators_to_signed_int32

    _patch_upstream_id_generators_to_signed_int32()
    _patch_upstream_id_generators_to_signed_int32()  # second call is a no-op

    from hwpx.oxml import document as _hwpx_document
    samples = [int(_hwpx_document._paragraph_id()) for _ in range(200)]
    assert max(samples) < 2**31


def test_paragraphs_added_after_patch_have_in_range_id() -> None:
    """End-to-end: ``add_paragraph`` issues in-range ids once the compat
    patch has run.

    The seed paragraph carried over from the upstream ``Skeleton.hwpx``
    template can still be out of range (filed and patched separately
    upstream); this test checks only paragraphs that were added by us
    after the patch is in effect.
    """

    from hwpx_mcp_server.compat import _patch_upstream_id_generators_to_signed_int32

    _patch_upstream_id_generators_to_signed_int32()

    from hwpx import HwpxDocument

    doc = HwpxDocument.new()
    seed_paragraph_count = sum(len(s.paragraphs) for s in doc.sections)

    for _ in range(100):
        doc.add_paragraph("x")

    import io
    import re
    import zipfile

    buf = io.BytesIO()
    doc.save_to_stream(buf)
    buf.seek(0)
    section_xml = zipfile.ZipFile(buf).read("Contents/section0.xml").decode()

    paragraph_ids = [
        int(m.group(1))
        for m in re.finditer(r'<hp:p[^>]*\sid="(\d+)"', section_xml)
    ]
    # Skip the inherited skeleton paragraph(s). We only care about the
    # ones add_paragraph emitted after the compat patch was active.
    new_ids = paragraph_ids[seed_paragraph_count:]

    bad = [v for v in new_ids if v >= 2**31]
    assert not bad, (
        f"add_paragraph emitted {len(bad)} ids >= 2^31 after compat patch was "
        f"installed; first offender: {bad[0]:#x}"
    )
    assert len(new_ids) >= 100, (
        f"expected at least 100 new paragraphs, got {len(new_ids)}"
    )
