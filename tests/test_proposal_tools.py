from hwpx_mcp_server import server


def _spec() -> dict:
    return {
        "title": "AI 융합형 교육실 구축 제안서",
        "organization": "샘플 고등학교",
        "author": "교육혁신팀",
        "date": "2026-05-06",
        "executive_summary": "AI 융합형 교육실 구축을 통해 맞춤형 수업 환경을 조성합니다.",
        "sections": [
            {"title": "추진 배경 및 문제 정의", "paragraphs": ["기존 실습 환경의 한계를 개선합니다."]},
            {"title": "제안 내용", "paragraphs": ["AI 실습 공간과 운영 프로그램을 함께 제안합니다."]},
            {"title": "구축 및 운영 계획", "paragraphs": ["준비, 구축, 운영, 평가 단계로 추진합니다."]},
        ],
        "budget_items": [{"item": "기자재", "amount": "5,000,000원", "note": "노트북"}],
        "expected_outcomes": ["참여형 수업 확대"],
        "closing": "검토 후 승인 요청드립니다.",
    }


def test_create_and_inspect_proposal_document(tmp_path, monkeypatch):
    monkeypatch.setenv("HWPX_MCP_SANDBOX_ROOT", str(tmp_path))
    filename = str(tmp_path / "proposal.hwpx")
    result = server.create_proposal_document(filename, _spec())

    assert result["created"] is True
    assert result["quality"]["rubric_average"] >= 4.0
    assert result["quality"]["report_version"] == "proposal-quality-v2"
    assert result["quality"]["sample_match"]["pass"] is True

    inspection = server.inspect_document_quality(filename)
    assert inspection["outline"]["required_sections_present"] is True
    assert inspection["table_checks"]["has_budget_table"] is True
    assert inspection["sample_match"]["visual_review_required"] is True
