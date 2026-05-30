from __future__ import annotations

import hashlib
from pathlib import Path

import hwpx_mcp_server.server as server
from hwpx.tools.package_validator import validate_package
from hwpx.tools.validator import validate_document


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _baseline() -> dict:
    return {
        "schemaVersion": "hwpx.template-formfit.baseline.v1",
        "baselineId": "mcp-template-baseline",
        "locatorPolicy": {
            "residualMarkers": {
                "blockOutsideVisualReview": True,
                "patterns": ["작성 필요", "TODO", "□□□□", "○○"],
            }
        },
        "scalarFields": [
            {
                "id": "school.name",
                "kind": "scalar-line",
                "locator": {"kind": "scalar-line", "anchor": "학 교 명 :"},
                "sourcePath": "school.name",
            }
        ],
        "regionMappings": [
            {
                "id": "overview.background_purpose",
                "anchor": "1. 추진 배경 및 목적",
                "kind": "section-region",
                "sourcePath": "sections.background_purpose",
                "required": True,
            },
            {
                "id": "schedule.timeline",
                "anchor": "Ⅶ 추진 일정",
                "kind": "table-region",
                "sourcePath": "sections.timeline.rows[]",
                "required": True,
                "columns": ["월", "추진 내용"],
            },
        ],
        "visualReviewRegions": [{"id": "photos", "anchor": "대상 공간 사진"}],
    }


def _content() -> dict:
    return {
        "school": {"name": "광교고등학교"},
        "sections": {
            "background_purpose": "AI 융합형 교육실 구축으로 학생 맞춤형 탐구 수업을 확대한다.",
            "timeline": {
                "rows": [
                    {"월": "3월", "추진 내용": "운영 협의체 구성"},
                    {"월": "4월", "추진 내용": "공간 설계 및 기자재 선정"},
                ]
            },
        },
    }


def _build_template(path: Path) -> None:
    server.create_document(str(path))
    server.add_paragraph(str(path), "학 교 명 :")
    server.add_heading(str(path), "1. 추진 배경 및 목적", level=1)
    server.add_paragraph(str(path), "작성 필요: 추진 배경을 입력하세요.")
    server.add_heading(str(path), "Ⅶ 추진 일정", level=1)
    server.add_paragraph(str(path), "TODO")
    server.add_heading(str(path), "대상 공간 사진", level=1)


def test_template_formfit_tools_are_exposed() -> None:
    names = set(server.mcp._tool_manager._tools.keys())

    assert {"analyze_template_formfit", "apply_template_formfit"}.issubset(names)


def test_analyze_and_apply_template_formfit_are_copy_only(tmp_path: Path) -> None:
    source = tmp_path / "template.hwpx"
    destination = tmp_path / "filled.hwpx"
    _build_template(source)
    before_hash = _sha256(source)
    before_mtime = source.stat().st_mtime_ns

    analysis = server.analyze_template_formfit(
        str(source),
        baseline=_baseline(),
        content=_content(),
        destination_filename=str(destination),
    )

    assert analysis["mutated"] is False
    assert analysis["resolved_count"] == 3
    assert analysis["unresolved_count"] == 0
    assert analysis["source"]["unchanged_after_analysis"] is True
    assert not destination.exists()

    result = server.apply_template_formfit(analysis=analysis, confirm=True)

    assert result["handoff_status"] == "ready"
    assert result["source"]["preserved"] is True
    assert result["source"]["sha256_before"] == before_hash
    assert result["source"]["mtime_ns_before"] == before_mtime
    assert result["validation"]["validate_package"]["ok"] is True
    assert result["validation"]["validate_document"]["ok"] is True
    assert result["residual_markers"]["blocking"] == []
    assert validate_package(destination).ok
    assert validate_document(destination).ok
    assert _sha256(source) == before_hash
    assert source.stat().st_mtime_ns == before_mtime

    text = server.get_document_text(str(destination))["text"]
    assert "학 교 명 : 광교고등학교" in text
    assert "AI 융합형 교육실 구축으로 학생 맞춤형 탐구 수업" in text
    assert "운영 협의체 구성" in text
    assert "작성 필요" not in text
    assert "TODO" not in text


def test_template_formfit_jsonrpc_call_path(mcp_server_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "template.hwpx"
    destination = workspace / "filled.hwpx"
    _build_template(source)
    client = mcp_server_factory(cwd=workspace)

    analysis_result = client.call_tool(
        "analyze_template_formfit",
        {
            "source_filename": str(source),
            "baseline": _baseline(),
            "content": _content(),
            "destination_filename": str(destination),
        },
    )
    analysis = client.parse_tool_result_payload(analysis_result)

    assert analysis["mutated"] is False
    assert analysis["resolved_count"] == 3

    apply_result = client.call_tool(
        "apply_template_formfit",
        {"analysis": analysis, "confirm": True},
    )
    payload = client.parse_tool_result_payload(apply_result)

    assert payload["handoff_status"] == "ready"
    assert destination.exists()
