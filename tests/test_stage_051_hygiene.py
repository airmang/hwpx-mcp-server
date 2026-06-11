from __future__ import annotations

import json
from pathlib import Path

import pytest

import hwpx_mcp_server.server as server


def _plan(title: str = "Stage 051 Compact Response") -> dict:
    return {
        "schemaVersion": "hwpx.document_plan.v1",
        "title": title,
        "blocks": [
            {"type": "heading", "level": 1, "text": "Overview"},
            {"type": "paragraph", "text": "Compact write responses keep success evidence short."},
            {"type": "heading", "level": 2, "text": "Metrics"},
            {
                "type": "table",
                "columns": [
                    {"key": "name", "label": "Name"},
                    {"key": "value", "label": "Value"},
                ],
                "rows": [
                    {"name": "tool count", "value": "84"},
                    {"name": "default verbosity", "value": "compact"},
                ],
            },
        ],
        "qualityGates": {
            "validatePackage": True,
            "validateDocument": True,
            "reopen": True,
            "minNonEmptyParagraphs": 3,
        },
    }


def _json_size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def test_create_document_from_plan_defaults_to_compact_verification(tmp_path: Path) -> None:
    compact_path = tmp_path / "compact.hwpx"
    full_path = tmp_path / "full.hwpx"

    compact = server.create_document_from_plan(str(compact_path), _plan("Compact"))
    full = server.create_document_from_plan(str(full_path), _plan("Full"), verbosity="full")

    assert compact["created"] is True
    assert compact["verification"]["openSafety"]["ok"] is True
    assert "sectionReports" not in compact["verification"]
    assert "sectionReports" in full["verification"]
    assert _json_size(compact["verification"]) < _json_size(full["verification"]) * 0.5
    assert _json_size(compact) < _json_size(full)


def test_idempotency_key_replay_prevents_double_add_paragraph(tmp_path: Path) -> None:
    target = tmp_path / "idempotent.hwpx"
    server.create_document(str(target))

    first = server.add_paragraph(str(target), "중복되면 안 되는 문단", idempotency_key="retry-1")
    second = server.add_paragraph(str(target), "중복되면 안 되는 문단", idempotency_key="retry-1")

    text = server.get_document_text(str(target))["text"]
    assert text.count("중복되면 안 되는 문단") == 1
    assert first["idempotentReplay"] is False
    assert second["idempotentReplay"] is True
    assert second["paragraph_index"] == first["paragraph_index"]


def test_idempotency_key_reuse_with_different_arguments_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "idempotent-mismatch.hwpx"
    server.create_document(str(target))
    server.add_paragraph(str(target), "원문", idempotency_key="retry-2")

    with pytest.raises(ValueError, match="different arguments"):
        server.add_paragraph(str(target), "다른 본문", idempotency_key="retry-2")


def test_get_document_map_combines_outline_tables_fields_and_anchors(tmp_path: Path) -> None:
    target = tmp_path / "map.hwpx"
    server.create_document(str(target))
    server.add_heading(str(target), "문서 지도", level=1)
    server.add_paragraph(str(target), "문서 지도 본문")
    server.add_table(str(target), 2, 2, [["항목", "값"], ["표", "있음"]])

    result = server.get_document_map(str(target))

    assert result["document_revision"].startswith("sha256:")
    assert result["info"]["paragraphs"] >= 2
    assert any(item["text"] == "문서 지도" for item in result["outline"])
    assert result["tables"]["tables"]
    assert "formFields" in result
    assert result["anchors"]["paragraphs"]
    assert result["anchors"]["tables"]
    assert len(result["sourceTools"]) >= 3
