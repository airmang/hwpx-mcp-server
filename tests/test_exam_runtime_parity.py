# SPDX-License-Identifier: Apache-2.0
"""Exact core-4.x compatibility parity for the canonical MCP exam runtime."""

from __future__ import annotations

import dataclasses
import inspect
import zipfile
from pathlib import Path
from random import Random
from types import ModuleType
from typing import Any
from uuid import UUID

import hwpx.oxml.document as oxml_document
import pytest
from hwpx import exam as core_exam
from hwpx.exam import compose as core_compose
from hwpx.exam import ir as core_ir
from hwpx.exam import measure as core_measure
from hwpx.exam import parser as core_parser
from hwpx.exam import profile as core_profile
from hwpx.tools.package_validator import validate_editor_open_safety
from hwpx.visual.oracle import NullOracle, WordBox

from hwpx_mcp_server.office import exam as mcp_exam
from hwpx_mcp_server.office.exam import compose as mcp_compose
from hwpx_mcp_server.office.exam import ir as mcp_ir
from hwpx_mcp_server.office.exam import measure as mcp_measure
from hwpx_mcp_server.office.exam import parser as mcp_parser
from hwpx_mcp_server.office.exam import profile as mcp_profile

CORE_FIXTURES = (
    Path(core_ir.__file__).resolve().parents[3] / "tests" / "fixtures" / "exam"
)
MODULE_PAIRS = (
    (core_ir, mcp_ir),
    (core_parser, mcp_parser),
    (core_profile, mcp_profile),
    (core_measure, mcp_measure),
    (core_compose, mcp_compose),
)
DATACLASS_PAIRS = (
    (core_ir.Placeholder, mcp_ir.Placeholder),
    (core_ir.Question, mcp_ir.Question),
    (core_ir.QuestionSet, mcp_ir.QuestionSet),
    (core_ir.ExamDoc, mcp_ir.ExamDoc),
    (core_profile.ResolvedStyle, mcp_profile.ResolvedStyle),
    (core_profile.FormProfile, mcp_profile.FormProfile),
    (core_measure.SplitReport, mcp_measure.SplitReport),
    (core_compose.ParaSpec, mcp_compose.ParaSpec),
    (core_compose.ComposePlan, mcp_compose.ComposePlan),
    (core_compose.ComposeResult, mcp_compose.ComposeResult),
)


def _owned_public(module: ModuleType) -> list[str]:
    return sorted(
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and getattr(value, "__module__", None) == module.__name__
    )


def _field_shape(cls: type[Any]) -> list[tuple[str, str, str, str]]:
    return [
        (
            field.name,
            str(field.type),
            repr(field.default),
            repr(field.default_factory),
        )
        for field in dataclasses.fields(cls)
    ]


def _glyph(text: str, x: float, y: float, *, line: int) -> WordBox:
    return WordBox(
        x0=x,
        y0=y,
        x1=x + 8,
        y1=y + 12,
        text=text,
        page=0,
        block=0,
        line=line,
        word_no=0,
    )


def _block_projection(blocks: list[Any]) -> list[tuple[str, tuple[str, ...]]]:
    return [(block.id, tuple(glyph.text for glyph in block.glyphs)) for block in blocks]


def test_owned_exports_signatures_and_ten_dataclasses_are_exact() -> None:
    assert core_exam.__all__ == mcp_exam.__all__
    for core_module, mcp_module in MODULE_PAIRS:
        core_names = _owned_public(core_module)
        mcp_names = _owned_public(mcp_module)
        assert core_names == mcp_names
        for name in core_names:
            core_value = getattr(core_module, name)
            mcp_value = getattr(mcp_module, name)
            assert type(core_value).__name__ == type(mcp_value).__name__, name
            if callable(core_value):
                try:
                    core_signature = str(inspect.signature(core_value))
                    mcp_signature = str(inspect.signature(mcp_value))
                except (TypeError, ValueError):
                    continue
                assert core_signature == mcp_signature, name

    assert len(DATACLASS_PAIRS) == 10
    for core_cls, mcp_cls in DATACLASS_PAIRS:
        assert _field_shape(core_cls) == _field_shape(mcp_cls)
        assert repr(core_cls.__dataclass_params__) == repr(mcp_cls.__dataclass_params__)


def test_parser_ir_lowering_and_errors_are_exact() -> None:
    markdown = """# 중간고사

## 1. (3점)
다음 중 옳은 것은? [그림1]
① 가
② 나

## 2∼3. 세트
공통 지문
### 2.
둘째 발문
① 다
### 3. (2점)
셋째 발문
① 라
"""
    core_doc = core_parser.parse_exam_markdown(markdown)
    mcp_doc = mcp_parser.parse_exam_markdown(markdown)
    assert dataclasses.asdict(core_doc) == dataclasses.asdict(mcp_doc)
    assert [question.number for question in core_doc.iter_questions()] == [
        question.number for question in mcp_doc.iter_questions()
    ]

    bad = "본문이 문항 헤더 없이 먼저 나온다.\n## 1.\n발문\n"
    with pytest.raises(core_parser.ExamParseError) as core_error:
        core_parser.parse_exam_markdown(bad)
    with pytest.raises(mcp_parser.ExamParseError) as mcp_error:
        mcp_parser.parse_exam_markdown(bad)
    assert (
        str(core_error.value),
        core_error.value.line_no,
        core_error.value.text,
        core_error.value.reason,
    ) == (
        str(mcp_error.value),
        mcp_error.value.line_no,
        mcp_error.value.text,
        mcp_error.value.reason,
    )


def test_measurement_and_profile_projections_are_exact() -> None:
    glyphs = [
        _glyph("1", 10, 10, line=0),
        _glyph(".", 18, 10, line=0),
        _glyph("①", 10, 30, line=1),
        _glyph("가", 18, 30, line=1),
        _glyph("2", 330, 10, line=2),
        _glyph(".", 338, 10, line=2),
    ]
    assert core_measure.column_x_bounds(glyphs) == mcp_measure.column_x_bounds(glyphs)
    assert _block_projection(core_measure.group_question_blocks(glyphs)) == (
        _block_projection(mcp_measure.group_question_blocks(glyphs))
    )

    from hwpx.document import HwpxDocument

    core_profile_value = core_profile.profile_form(
        HwpxDocument.open(CORE_FIXTURES / "A_form.hwpx")
    )
    mcp_profile_value = mcp_profile.profile_form(
        HwpxDocument.open(CORE_FIXTURES / "A_form.hwpx")
    )
    assert dataclasses.asdict(core_profile_value) == dataclasses.asdict(
        mcp_profile_value
    )


def test_fixture_composition_bytes_reopen_and_open_safety_are_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown = (CORE_FIXTURES / "sample_exam.md").read_text(encoding="utf-8")
    source = CORE_FIXTURES / "A_form.hwpx"
    core_out = tmp_path / "core.hwpx"
    mcp_out = tmp_path / "mcp.hwpx"

    def deterministic_uuid4(seed: int):
        rng = Random(seed)
        return lambda: UUID(int=rng.getrandbits(128))

    monkeypatch.setattr(oxml_document, "uuid4", deterministic_uuid4(103))
    core_result = core_compose.compose_exam_into_form(
        str(source),
        markdown,
        str(core_out),
        oracle=NullOracle(),
    )
    monkeypatch.setattr(oxml_document, "uuid4", deterministic_uuid4(103))
    mcp_result = mcp_compose.compose_exam_into_form(
        str(source),
        markdown,
        str(mcp_out),
        oracle=NullOracle(),
    )
    core_projection = dataclasses.asdict(core_result)
    mcp_projection = dataclasses.asdict(mcp_result)
    core_projection["out_path"] = "<output>"
    mcp_projection["out_path"] = "<output>"
    assert core_projection == mcp_projection
    with zipfile.ZipFile(core_out) as core_zip, zipfile.ZipFile(mcp_out) as mcp_zip:
        assert core_zip.namelist() == mcp_zip.namelist()
        assert {name: core_zip.read(name) for name in core_zip.namelist()} == {
            name: mcp_zip.read(name) for name in mcp_zip.namelist()
        }
    assert validate_editor_open_safety(core_out).ok is True
    assert validate_editor_open_safety(mcp_out).ok is True
