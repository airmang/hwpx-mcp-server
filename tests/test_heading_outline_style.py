# SPDX-License-Identifier: Apache-2.0
"""add_heading 마크다운 '#' 누출 수정 회귀 테스트.

구버전 add_heading은 본문에 "# 제목" 리터럴을 저장했다 (한컴 화면에 # 노출).
수정 후: 텍스트는 깨끗하게, 개요 수준은 "개요 N" 문단 스타일로 표현하며,
outline 읽기 경로는 스타일 기반 + 구버전 '#' 텍스트 기반을 모두 인식한다.
"""

import hwpx_mcp_server.server as server
from hwpx_mcp_server.core.document import open_doc


def test_add_heading_stores_clean_text(tmp_path):
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    server.add_heading(target, "추진 배경", level=1)

    text = server.get_document_text(target)["text"]
    assert "#" not in text
    assert "추진 배경" in text


def test_add_heading_applies_outline_style(tmp_path):
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    server.add_heading(target, "제목 하나", level=1)
    server.add_heading(target, "제목 둘", level=2)

    doc = open_doc(target)
    from hwpx_mcp_server.core.formatting import outline_style_levels

    levels = outline_style_levels(doc)
    styled = [
        levels.get(str(para.style_id_ref))
        for para in doc.paragraphs
        if (para.text or "").strip() in {"제목 하나", "제목 둘"}
    ]
    assert styled == [1, 2]


def test_add_heading_strips_markdown_prefix_input(tmp_path):
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    server.add_heading(target, "## 이미 프리픽스 있음", level=1)

    text = server.get_document_text(target)["text"]
    assert "#" not in text
    assert "이미 프리픽스 있음" in text


def test_outline_detects_style_based_heading(tmp_path):
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    server.add_heading(target, "스타일 기반 제목", level=2)
    server.add_paragraph(target, "본문 문단은 개요로 잡히면 안 되는 충분히 긴 텍스트로 채운다. " * 3)

    outline = server.get_document_outline(target)["outline"]
    matches = [o for o in outline if o["text"] == "스타일 기반 제목"]
    assert matches and matches[0]["level"] == 2
    assert all(o["text"] != "본문 문단은 개요로 잡히면 안 되는 충분히 긴 텍스트로 채운다. " * 3 for o in outline)


def test_outline_does_not_promote_plain_short_paragraphs(tmp_path):
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    server.add_paragraph(target, "짧은 문단")
    server.add_paragraph(target, "1. 번호처럼 보이지만 스타일 없는 문단")

    assert server.get_document_outline(target)["outline"] == []


def test_outline_still_detects_legacy_hash_heading(tmp_path):
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    # 구버전 산출물 모사: '#' 리터럴 헤딩
    server.add_paragraph(target, "## 구버전 헤딩")

    outline = server.get_document_outline(target)["outline"]
    matches = [o for o in outline if "구버전 헤딩" in o["text"]]
    assert matches and matches[0]["level"] == 2


def test_paragraph_after_heading_does_not_inherit_outline(tmp_path):
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    server.add_heading(target, "제목", level=1)
    server.add_paragraph(target, "헤딩 뒤 일반 문단")

    doc = open_doc(target)
    from hwpx_mcp_server.core.formatting import outline_style_levels

    levels = outline_style_levels(doc)
    body = [p for p in doc.paragraphs if (p.text or "").strip() == "헤딩 뒤 일반 문단"]
    assert body
    assert str(body[0].style_id_ref) not in levels


def test_heading_document_passes_open_safety(tmp_path):
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    server.add_heading(target, "안전성 확인", level=1)
    server.add_paragraph(target, "본문")

    from hwpx.tools.package_validator import validate_editor_open_safety

    report = validate_editor_open_safety(target)
    payload = report.as_dict() if hasattr(report, "as_dict") else {}
    assert payload.get("ok", getattr(report, "ok", False))
