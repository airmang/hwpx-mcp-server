# SPDX-License-Identifier: Apache-2.0
"""Exact parity between core 4.x compatibility and MCP canonical owners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hwpx import HwpxDocument
from hwpx.tools.doc_diff import (
    build_comparison_table_plan as compatibility_comparison_plan,
)
from hwpx.tools.mail_merge import mail_merge as compatibility_mail_merge
from hwpx.tools.redline import author_demo_redline
from hwpx.tools.redline import verify_redline as compatibility_verify_redline

from hwpx_mcp_server.office.document_ops import (
    build_comparison_table_plan,
    build_mail_merge,
    verify_redline,
)


class _UnavailableOracle:
    """A core RenderBackend that reports itself unusable."""

    def available(self) -> bool:
        return False

    def check(self, before_hwpx: Any, after_hwpx: Any, **_kwargs: Any) -> Any:
        from hwpx.quality.rendering import VisualReport

        return VisualReport(
            ok=True,
            render_checked=False,
            warnings=["test backend is unavailable; nothing was rendered"],
        )


def _template(path: Path) -> None:
    document = HwpxDocument.new()
    try:
        document.add_paragraph("name={{name}} / phone={{phone}}")
        document.save_to_path(path)
    finally:
        document.close()


def _redline_pair(tmp_path: Path) -> tuple[Path, Path]:
    document = HwpxDocument.new()
    before = tmp_path / "before.hwpx"
    after = tmp_path / "after.hwpx"
    try:
        document.add_paragraph("baseline")
        document.save_to_path(before)
        author_demo_redline(document)
        document.save_to_path(after)
    finally:
        document.close()
    return before, after


def _mail_merge_projection(report: dict[str, Any]) -> dict[str, Any]:
    row = report["rows"][0]
    return {
        "report_version": report["report_version"],
        "placeholderKeys": report["placeholderKeys"],
        "fitAware": report["fitAware"],
        "rowCount": report["rowCount"],
        "createdCount": report["createdCount"],
        "rowsWithIssues": report["rowsWithIssues"],
        "ok": report["ok"],
        "openSafety": report["openSafety"],
        "row": {
            key: row[key]
            for key in (
                "rowIndex",
                "created",
                "replacedCount",
                "missingKeys",
                "unresolvedPlaceholders",
                "openSafety",
                "fitFields",
                "maskedFields",
                "reasons",
                "ok",
            )
        },
    }


def test_comparison_plan_is_exact() -> None:
    old = ["제1조 목적", "제2조 예산"]
    new = ["제1조 목적", "제2조 예산 변경", "제3조 시행"]

    assert build_comparison_table_plan(
        old,
        new,
        title="신구대조표",
        include_equal=False,
    ) == compatibility_comparison_plan(
        old,
        new,
        title="신구대조표",
        include_equal=False,
    )


def test_mail_merge_policy_and_receipts_are_exact(tmp_path: Path) -> None:
    template = tmp_path / "template.hwpx"
    _template(template)
    rows = [{"name": "홍길동", "phone": "010-1234-5678"}]

    compatibility = compatibility_mail_merge(
        template,
        rows,
        output_dir=tmp_path / "compatibility",
    )
    canonical = build_mail_merge(
        template,
        rows,
        output_dir=tmp_path / "canonical",
    )

    assert _mail_merge_projection(canonical) == _mail_merge_projection(
        compatibility
    )
    compatibility_doc = HwpxDocument.open(
        compatibility["rows"][0]["filename"]
    )
    canonical_doc = HwpxDocument.open(canonical["rows"][0]["filename"])
    try:
        assert canonical_doc.export_text() == compatibility_doc.export_text()
        assert "010-****-****" in canonical_doc.export_text()
    finally:
        canonical_doc.close()
        compatibility_doc.close()


def test_unavailable_oracle_redline_receipt_is_exact(tmp_path: Path) -> None:
    before, after = _redline_pair(tmp_path)
    oracle = _UnavailableOracle()

    assert verify_redline(
        before,
        after,
        oracle=oracle,
    ) == compatibility_verify_redline(
        before,
        after,
        oracle=oracle,
    )


def test_canonical_owner_injects_a_backend_instead_of_degrading(monkeypatch) -> None:
    """The owner's job is to supply a renderer, and it must actually do it.

    Core's verify_redline degrades honestly when nothing is injected. That is
    right for a library and wrong for the canonical owner: a caller reaching the
    MCP surface asked for a Hancom-backed verdict. Before this feature an import
    ban enforced the split; now that the owner delegates to core, the guard has
    to be behavioural instead.
    """

    from hwpx_mcp_server.office.document_ops import redline as owner

    supplied: list[object] = []

    def _fake_core_verify(before, after, *, oracle=None):
        supplied.append(oracle)
        return {"report_version": "redline-verify-v1"}

    monkeypatch.setattr(owner, "_core_verify_redline", _fake_core_verify)
    owner.verify_redline("before.hwpx", "after.hwpx")

    assert len(supplied) == 1
    backend = supplied[0]
    assert backend is not None, "the owner degraded instead of supplying a backend"
    assert hasattr(backend, "available") and hasattr(backend, "check")
