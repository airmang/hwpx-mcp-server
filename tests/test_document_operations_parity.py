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
    def available(self) -> bool:
        return False

    def render_many(self, pairs: Any) -> Any:  # pragma: no cover
        raise AssertionError("render_many must not run when unavailable")


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
