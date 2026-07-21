from __future__ import annotations

import pytest

import hwpx_mcp_server.quality_generation as quality_generation_module
from hwpx_mcp_server import server
from hwpx_mcp_server.fastmcp_adapter import snapshot_runtime_tools


def test_quality_generation_tools_are_removed_from_the_mcp_surface() -> None:
    # The analyze_quality_generation/apply_quality_generation MCP tools were
    # removed at the 5.0.0 major boundary (S-091). The generation engine lives on
    # in hwpx_mcp_server.quality_generation (exercised directly below); the
    # replacement product path is create_document_from_plan + inspect_document_quality.
    names = set(snapshot_runtime_tools(server.mcp))

    assert "analyze_quality_generation" not in names
    assert "apply_quality_generation" not in names


def test_quality_generation_validation_fails_closed_without_package_validator(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "generated.hwpx"
    server.create_document(str(target))
    monkeypatch.setattr(quality_generation_module, "validate_package", None, raising=False)
    monkeypatch.setattr(
        quality_generation_module,
        "_PACKAGE_VALIDATOR_IMPORT_ERROR",
        ImportError("missing package validator"),
        raising=False,
    )

    validation = quality_generation_module._runtime_validation(str(target))

    assert validation["validate_package"]["ok"] is False
    assert "python-hwpx>=2.10.3 is required" in validation["validate_package"]["errors"][0]
    assert validation["openSafety"]["ok"] is True


def test_mcp_inspect_operating_plan_quality_supports_file_only_path(tmp_path) -> None:
    destination = tmp_path / "operating-plan.hwpx"
    result = server.create_document_from_plan(
        filename=str(destination),
        document_plan={
            "schemaVersion": "hwpx.document_plan.v1",
            "title": "2026 AI 중점학교 운영계획서",
            "metadata": {
                "organization": "매원초등학교",
                "date": "2026-05-30",
                "document_type": "운영계획서",
            },
            "blocks": [
                {"type": "heading", "level": 1, "text": "Ⅰ. 신청 목적"},
                {"type": "paragraph", "text": "AI 교육 운영 목적과 필요성을 학교 교육과정 안에서 구체화한다."},
                {"type": "heading", "level": 1, "text": "Ⅱ. 운영 계획"},
                {
                    "type": "paragraph",
                    "text": (
                        "수업, 연수, 학생 프로젝트를 연결한 운영 계획을 추진하고 학년군별 "
                        "실행 담당자와 산출물 점검 절차를 정해 월별 운영 회의에서 보완한다. "
                        "학생 참여 기록, 교원 협의 결과, 예산 집행 근거를 함께 관리해 제출 전 "
                        "검토 자료로 활용한다."
                    ),
                },
                {"type": "heading", "level": 1, "text": "Ⅲ. 추진 일정 및 사업비 사용 계획"},
                {
                    "type": "table",
                    "caption": "추진 일정",
                    "columns": [
                        {"key": "phase", "label": "단계"},
                        {"key": "period", "label": "기간"},
                        {"key": "activity", "label": "세부 추진 내용"},
                    ],
                    "rows": [
                        {"phase": "준비", "period": "3월", "activity": "운영 협의체 구성"},
                        {"phase": "운영", "period": "4월~11월", "activity": "AI 활용 수업 운영"},
                        {"phase": "평가", "period": "12월", "activity": "성과 분석 및 개선안 수립"},
                    ],
                },
                {
                    "type": "table",
                    "caption": "사업비 사용 계획",
                    "columns": [
                        {"key": "item", "label": "항목"},
                        {"key": "amount", "label": "금액"},
                        {"key": "basis", "label": "산출근거"},
                    ],
                    "rows": [
                        {"item": "교육 운영비", "amount": "4,000,000원", "basis": "자료 제작"},
                        {"item": "교원 연수비", "amount": "1,000,000원", "basis": "연수 운영"},
                    ],
                },
                {"type": "heading", "level": 1, "text": "Ⅳ. 교육과정 편제표"},
                {"type": "paragraph", "text": "교육과정과 교과 운영 체계를 연계한다."},
                {"type": "heading", "level": 1, "text": "Ⅴ. 기대 효과 및 성과 관리"},
                {"type": "paragraph", "text": "기대 효과와 성과 관리를 지표와 산출물로 확인한다."},
                {"type": "heading", "level": 1, "text": "Ⅵ. 제출 및 확인"},
                {"type": "paragraph", "text": "본 계획은 검토 후 제출하며 운영 과정에서 보완한다."},
            ],
        },
        quality_profile="operating_plan",
    )

    report = server.inspect_operating_plan_quality(filename=str(destination))

    assert result["handoff_status"] == "ready"
    assert report["report_version"] == "operating-plan-quality-v1"
    assert report["status"] == "ready"
    assert report["pass"] is True
    assert report["visual_review_required"] is True
    assert report["gaps"] == []
