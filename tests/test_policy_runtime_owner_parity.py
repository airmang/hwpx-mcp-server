# SPDX-License-Identifier: Apache-2.0
"""Exact parity between the MCP owner and frozen core 4.x policy copies."""
from __future__ import annotations

import dataclasses
import importlib
import inspect
from pathlib import Path
from typing import Any, Callable

from hwpx.document import HwpxDocument

core_official_lint = importlib.import_module("hwpx.tools.official_lint")
core_pii = importlib.import_module("hwpx.tools.pii")
core_page_guard = importlib.import_module("hwpx.tools.page_guard")
core_table_compute = importlib.import_module("hwpx.tools.table_compute")
official_lint = importlib.import_module(
    "hwpx_mcp_server.office.compliance.official_lint"
)
pii = importlib.import_module("hwpx_mcp_server.office.compliance.pii")
page_guard = importlib.import_module(
    "hwpx_mcp_server.office.quality.page_guard"
)
table_compute = importlib.import_module(
    "hwpx_mcp_server.office.utilities.table_compute"
)

MODULE_PAIRS = (
    (core_official_lint, official_lint),
    (core_pii, pii),
    (core_page_guard, page_guard),
    (core_table_compute, table_compute),
)


def _signature(value: Any) -> str | None:
    if not callable(value):
        return None
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return "<uninspectable>"


def _error(call: Callable[[], Any]) -> tuple[str, str]:
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - parity projects the public error.
        return type(exc).__name__, str(exc)
    raise AssertionError("expected call to fail")


def _document(path: Path, paragraphs: list[str]) -> None:
    doc = HwpxDocument.new()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save_to_path(path)
    doc.close()


def test_public_exports_signatures_and_versions_are_exact() -> None:
    for core_module, owner_module in MODULE_PAIRS:
        assert owner_module.__all__ == core_module.__all__
        for name in core_module.__all__:
            core_value = getattr(core_module, name)
            owner_value = getattr(owner_module, name)
            assert type(owner_value).__name__ == type(core_value).__name__
            assert _signature(owner_value) == _signature(core_value), name

    assert (
        official_lint.OFFICIAL_DOCUMENT_STYLE_REPORT_VERSION
        == core_official_lint.OFFICIAL_DOCUMENT_STYLE_REPORT_VERSION
        == "official-document-style-v1"
    )
    assert (
        table_compute.TABLE_COMPUTE_REPORT_VERSION
        == core_table_compute.TABLE_COMPUTE_REPORT_VERSION
        == "table-compute-v1"
    )


def test_official_lint_and_pii_behavior_are_exact() -> None:
    paragraphs = [
        "1. 추진 목적",
        "  가. 세부 내용",
        "일시: 2026. 7. 24.",
        "금액: 금1,000원",
        "붙임  1. 자료 1부.  끝.",
    ]
    assert official_lint.inspect_official_document_style(
        paragraphs,
        document_type="gongmun",
    ) == core_official_lint.inspect_official_document_style(
        paragraphs,
        document_type="gongmun",
    )

    text = "성명: 홍길동 / 010-1234-5678 / hong@example.com"
    assert pii.detect_pii(text) == core_pii.detect_pii(text)
    assert pii.mask_pii(text) == core_pii.mask_pii(text)
    for kind, value in (
        ("phone", "010-1234-5678"),
        ("email", "hong@example.com"),
        ("name", "홍길동"),
    ):
        assert pii.mask_value(value, kind) == core_pii.mask_value(value, kind)
    assert pii.minimize_fields(
        {"name": "홍길동", "empty": "", "score": 0},
        ["score", "empty", "name"],
        drop_empty=True,
    ) == core_pii.minimize_fields(
        {"name": "홍길동", "empty": "", "score": 0},
        ["score", "empty", "name"],
        drop_empty=True,
    )
    assert pii.deidentify("홍길동", salt="s100") == core_pii.deidentify(
        "홍길동",
        salt="s100",
    )


def test_page_metrics_comparison_and_table_calculation_are_exact(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference.hwpx"
    output = tmp_path / "output.hwpx"
    _document(reference, ["기준 본문", "둘째 문단"])
    _document(output, ["기준 본문", "둘째 문단이 길어졌습니다"])

    core_reference = core_page_guard.collect_metrics(reference)
    core_output = core_page_guard.collect_metrics(output)
    owner_reference = page_guard.collect_metrics(reference)
    owner_output = page_guard.collect_metrics(output)
    assert dataclasses.asdict(owner_reference) == dataclasses.asdict(
        core_reference
    )
    assert dataclasses.asdict(owner_output) == dataclasses.asdict(core_output)
    assert page_guard.compare_metrics(
        owner_reference,
        owner_output,
    ) == core_page_guard.compare_metrics(core_reference, core_output)

    table = {
        "type": "table",
        "columns": [
            {"key": "team", "label": "팀"},
            {"key": "amount", "label": "금액"},
        ],
        "rows": [
            {"team": "A", "amount": "1,000원"},
            {"team": "A", "amount": "2,000원"},
            {"team": "B", "amount": ""},
        ],
    }
    kwargs = {
        "value_columns": ["amount"],
        "operations": ["subtotal", "sum", "average"],
        "group_by": "team",
        "label_column": "team",
    }
    assert table_compute.table_compute(table, **kwargs) == (
        core_table_compute.table_compute(table, **kwargs)
    )


def test_public_error_type_and_message_are_exact() -> None:
    assert _error(lambda: pii.PIIPolicy(mask_char="**")) == _error(
        lambda: core_pii.PIIPolicy(mask_char="**")
    )
    assert _error(
        lambda: table_compute.table_compute(
            [{"amount": 1}],
            operations=["median"],
        )
    ) == _error(
        lambda: core_table_compute.table_compute(
            [{"amount": 1}],
            operations=["median"],
        )
    )
