from __future__ import annotations

from hwpx_mcp_server import server


def _idea() -> dict:
    return {
        "title": "2026년 AI 중점학교 신청서 및 운영계획서",
        "organization": "매원초등학교",
        "author": "AI 교육 운영팀",
        "date": "2026",
        "idea_brief": (
            "초등학생의 AI 기초 소양과 교원의 AI 활용 수업 설계 역량을 함께 높이는 "
            "AI 중점학교 운영 계획을 작성한다."
        ),
        "sections": [
            {
                "title": "추진 배경 및 필요성",
                "paragraphs": ["AI 기초 소양 교육을 학교 교육과정 안에서 체계화해야 합니다."],
                "bullets": ["학생 맞춤형 AI 체험 기회 확대", "교원 수업 설계 역량 강화"],
            },
            {
                "title": "운영 목표",
                "paragraphs": ["교육과정, 동아리, 교원 연수를 연결한 운영 체계를 구축합니다."],
                "bullets": ["AI 융합 수업 모델 개발", "학년군별 프로젝트 운영"],
            },
            {
                "title": "세부 운영 계획",
                "paragraphs": ["준비, 실행, 성과 공유 단계로 나누어 운영합니다."],
                "bullets": ["1학기 기반 조성", "2학기 운영 및 성과 확산"],
            },
        ],
        "budget_items": [{"item": "AI 교육 운영비", "amount": "계획 수립 후 확정", "note": "수업 및 산출물 제작"}],
        "expected_outcomes": ["학생 AI 소양 강화", "학교 단위 AI 교육 운영 모델 확보"],
        "closing": "본 계획을 바탕으로 AI 중점학교 운영을 성실히 추진하겠습니다.",
    }


def test_quality_generation_tools_are_exposed() -> None:
    names = set(server.mcp._tool_manager._tools.keys())

    assert {"analyze_quality_generation", "apply_quality_generation"}.issubset(names)


def test_analyze_quality_generation_is_non_mutating_and_does_not_require_quality_sample(tmp_path) -> None:
    form = tmp_path / "form.hwpx"
    destination = tmp_path / "generated.hwpx"
    server.create_document(str(form))
    before = form.read_bytes()

    analysis = server.analyze_quality_generation(
        form_filename=str(form),
        idea_brief=_idea(),
        destination_filename=str(destination),
    )

    assert analysis["quality_sample_required"] is False
    assert analysis["inputs"]["form"]["path"] == str(form)
    assert analysis["inputs"]["destination"]["path"] == str(destination)
    assert analysis["mutated"] is False
    assert analysis["next_tool"] == "apply_quality_generation"
    assert form.read_bytes() == before
    assert not destination.exists()


def test_apply_quality_generation_creates_validated_output_with_revision_history(tmp_path) -> None:
    form = tmp_path / "form.hwpx"
    destination = tmp_path / "generated.hwpx"
    server.create_document(str(form))
    analysis = server.analyze_quality_generation(
        form_filename=str(form),
        idea_brief=_idea(),
        destination_filename=str(destination),
    )

    result = server.apply_quality_generation(analysis=analysis, confirm=True)

    assert result["quality_sample_required"] is False
    assert result["destination"]["created"] is True
    assert destination.exists()
    assert result["validation"]["reopened"] is True
    assert result["validation"]["validate_package"]["ok"] is True
    assert result["validation"]["validate_document"]["ok"] is True
    assert result["revision_history"]
    assert result["quality"]["rubric_average"] >= 4.0


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
