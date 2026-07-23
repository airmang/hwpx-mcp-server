# SPDX-License-Identifier: Apache-2.0
"""Exact parity between the MCP authoring owner and core 4.x copy."""
from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from typing import Any

import hwpx.authoring as core_authoring
import hwpx.builder as core_builder
import hwpx.design as core_design
import hwpx.presets as core_presets
import hwpx.tools.advanced_generators as core_generators
import hwpx.tools.style_profile as core_style
import hwpx.tools.template_analyzer as core_template
from hwpx.tools.package_validator import validate_editor_open_safety

import hwpx_mcp_server.office.authoring as mcp_authoring
import hwpx_mcp_server.office.authoring.builder as mcp_builder
import hwpx_mcp_server.office.authoring.design as mcp_design
import hwpx_mcp_server.office.authoring.presets as mcp_presets
import hwpx_mcp_server.office.authoring.advanced_generators as mcp_generators
import hwpx_mcp_server.office.authoring.style_profile as mcp_style
import hwpx_mcp_server.office.authoring.template_analyzer as mcp_template


MODULE_PAIRS = (
    (core_authoring, mcp_authoring),
    (core_builder, mcp_builder),
    (core_design, mcp_design),
    (core_presets, mcp_presets),
    (core_generators, mcp_generators),
    (core_style, mcp_style),
    (core_template, mcp_template),
)


def _public_names(module: Any) -> list[str]:
    names = list(getattr(module, "__all__", ()))
    if names:
        return names
    return sorted(
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and (
            getattr(value, "__module__", None) == module.__name__
            or (
                name.isupper()
                and isinstance(
                    value,
                    (str, int, float, bool, tuple, frozenset),
                )
            )
        )
    )


def _assert_public_parity(left: Any, right: Any) -> None:
    left_names = _public_names(left)
    right_names = _public_names(right)
    assert left_names == right_names
    for name in left_names:
        left_value = getattr(left, name)
        right_value = getattr(right, name)
        assert type(left_value).__name__ == type(right_value).__name__, name
        if callable(left_value):
            try:
                left_signature = str(inspect.signature(left_value))
                right_signature = str(inspect.signature(right_value))
            except (TypeError, ValueError):
                continue
            assert left_signature == right_signature, name
        else:
            assert left_value == right_value, name


def _plan() -> dict[str, Any]:
    return {
        "schemaVersion": "hwpx.document_plan.v1",
        "title": "정본 parity",
        "blocks": [
            {"type": "heading", "level": 1, "text": "개요"},
            {"type": "paragraph", "text": "본문"},
            {
                "type": "table",
                "columns": [
                    {"key": "item", "label": "항목"},
                    {"key": "value", "label": "값"},
                ],
                "rows": [{"item": "A", "value": "1"}],
            },
        ],
    }


def test_public_exports_and_signatures_are_exact() -> None:
    for core_module, mcp_module in MODULE_PAIRS:
        _assert_public_parity(core_module, mcp_module)


def test_document_plan_schema_normalization_validation_and_errors_are_exact() -> None:
    plan = _plan()
    invalid = {
        "schemaVersion": "hwpx.document_plan.v1",
        "blocks": [{}],
    }

    assert core_authoring.get_document_plan_schema() == (
        mcp_authoring.get_document_plan_schema()
    )
    assert core_authoring.normalize_document_plan(plan).to_dict() == (
        mcp_authoring.normalize_document_plan(plan).to_dict()
    )
    assert core_authoring.validate_document_plan(plan).to_dict() == (
        mcp_authoring.validate_document_plan(plan).to_dict()
    )
    assert core_authoring.validate_document_plan(invalid).to_dict() == (
        mcp_authoring.validate_document_plan(invalid).to_dict()
    )


def test_generators_proposal_and_style_profile_payloads_are_exact() -> None:
    hierarchy = {"name": "대표", "children": [{"name": "팀"}]}
    profile = {
        "schemaVersion": "hwpx.style-profile.v1",
        "page": {
            "orientation": "LANDSCAPE",
            "widthMm": 297,
            "heightMm": 210,
            "marginsMm": {
                "left": 20,
                "right": 20,
                "top": 15,
                "bottom": 15,
            },
        },
        "body": {"font": "함초롬바탕", "sizePt": 11},
    }

    assert core_generators.build_image_grid(
        ["a.png", "b.png"],
        columns=2,
        image_width_mm=50,
    ) == mcp_generators.build_image_grid(
        ["a.png", "b.png"],
        columns=2,
        image_width_mm=50,
    )
    assert core_generators.build_meeting_nameplates(
        ["가", "나"],
        columns=2,
    ) == mcp_generators.build_meeting_nameplates(
        ["가", "나"],
        columns=2,
    )
    assert core_generators.build_organization_chart(
        hierarchy
    ) == mcp_generators.build_organization_chart(hierarchy)
    assert dataclasses.asdict(
        core_presets.normalize_proposal_spec(
            {"title": "제안", "sections": [{"title": "배경"}]}
        )
    ) == dataclasses.asdict(
        mcp_presets.normalize_proposal_spec(
            {"title": "제안", "sections": [{"title": "배경"}]}
        )
    )
    assert core_style.apply_style_profile_to_plan(
        _plan(),
        profile,
    ) == mcp_style.apply_style_profile_to_plan(_plan(), profile)


def test_document_plan_creation_save_reopen_and_open_safety_are_exact(
    tmp_path: Path,
) -> None:
    core_path = tmp_path / "core-plan.hwpx"
    mcp_path = tmp_path / "mcp-plan.hwpx"
    core_document = core_authoring.create_document_from_plan(_plan())
    mcp_document = mcp_authoring.create_document_from_plan(_plan())
    try:
        assert core_document.export_text() == mcp_document.export_text()
        core_document.save_to_path(core_path)
        mcp_document.save_to_path(mcp_path)
    finally:
        core_document.close()
        mcp_document.close()

    core_safety = validate_editor_open_safety(core_path)
    mcp_safety = validate_editor_open_safety(mcp_path)
    assert core_safety.ok is True
    assert mcp_safety.ok is True
    assert core_safety.summary == mcp_safety.summary


def test_builder_lowering_and_computed_fields_are_exact(tmp_path: Path) -> None:
    core_path = tmp_path / "core-builder.hwpx"
    mcp_path = tmp_path / "mcp-builder.hwpx"
    core_plan = core_builder.Document(
        sections=[
            core_builder.Section(
                children=[
                    core_builder.Paragraph(text="{{ commas(1234567) }}"),
                    core_builder.Table(
                        header=["항목", "값"],
                        rows=[["A", "1"]],
                    ),
                ]
            )
        ]
    )
    mcp_plan = mcp_builder.Document(
        sections=[
            mcp_builder.Section(
                children=[
                    mcp_builder.Paragraph(text="{{ commas(1234567) }}"),
                    mcp_builder.Table(
                        header=["항목", "값"],
                        rows=[["A", "1"]],
                    ),
                ]
            )
        ]
    )
    core_document = core_plan.lower()
    mcp_document = mcp_plan.lower()
    try:
        assert core_document.export_text() == mcp_document.export_text()
        core_document.save_to_path(core_path)
        mcp_document.save_to_path(mcp_path)
    finally:
        core_document.close()
        mcp_document.close()

    assert validate_editor_open_safety(core_path).ok is True
    assert validate_editor_open_safety(mcp_path).ok is True


def test_design_profile_registry_and_template_analysis_are_exact(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.hwpx"
    document = core_authoring.create_document_from_plan(_plan())
    try:
        document.save_to_path(source)
    finally:
        document.close()

    assert core_design.available_profiles() == mcp_design.available_profiles()
    assert core_template.template_analysis_agent_schema() == (
        mcp_template.template_analysis_agent_schema()
    )
    assert dataclasses.asdict(core_template.analyze_template(source)) == (
        dataclasses.asdict(mcp_template.analyze_template(source))
    )
