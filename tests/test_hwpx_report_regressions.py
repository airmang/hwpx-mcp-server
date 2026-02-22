from __future__ import annotations

import importlib
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

import anyio
import pytest

from hwpx_mcp_server.compat import patch_python_hwpx


def _reload_server():
    import hwpx_mcp_server.server as server_module

    return importlib.reload(server_module)


def _report_sample_path() -> Path:
    return Path(__file__).with_name("hwpx_mcp_test.hwpx")


def _copy_report_sample(tmp_path: Path, name: str) -> Path:
    source = _report_sample_path()
    target = tmp_path / name
    shutil.copy2(source, target)
    return target


def test_add_and_insert_paragraph_accept_null_style_in_tool_validation(tmp_path: Path):
    server = _reload_server()
    target = _copy_report_sample(tmp_path, "style_nullable.hwpx")

    async def _run_calls() -> None:
        await server.mcp.call_tool(
            "add_paragraph",
            {"filename": str(target), "text": "style 생략"},
        )
        await server.mcp.call_tool(
            "add_paragraph",
            {"filename": str(target), "text": "style null", "style": None},
        )
        await server.mcp.call_tool(
            "insert_paragraph",
            {
                "filename": str(target),
                "paragraph_index": 1,
                "text": "insert null style",
                "style": None,
            },
        )

    anyio.run(_run_calls)

    text = server.get_document_text(str(target), max_chars=12000)["text"]
    assert "style null" in text
    assert "insert null style" in text


def test_search_and_replace_replaces_visible_paragraph_text(tmp_path: Path):
    server = _reload_server()
    target = _copy_report_sample(tmp_path, "replace_visible.hwpx")

    result = server.search_and_replace(
        str(target),
        "2026학년도 교육정보",
        "[치환완료]",
    )
    text = server.get_document_text(str(target), max_chars=8000)["text"]

    assert result["replaced_count"] >= 1
    assert "[치환완료]" in text


def test_add_memo_lxml_etree_compatibility_patch_handles_mixed_parent():
    pytest.importorskip("lxml.etree")
    from lxml import etree as LET

    patch_python_hwpx()
    parent = LET.Element("root")
    child = ET.SubElement(parent, "child", {"kind": "memo"})

    assert child.tag == "child"
    assert parent[0].get("kind") == "memo"


def test_batch_replace_schema_and_runtime_align(tmp_path: Path):
    server = _reload_server()
    target = tmp_path / "batch_replace.hwpx"
    server.create_document(str(target))
    server.add_paragraph(str(target), "2026년 계획, 2025년 실적")

    schema = server.mcp._tool_manager._tools["batch_replace"].parameters
    assert "replacements" in schema.get("properties", {})
    assert "replacements" in schema.get("required", [])

    result = server.batch_replace(
        str(target),
        [
            {"find": "2026", "replace": "2027"},
            {"find": "2025", "replace": "2026"},
        ],
    )
    text = server.get_document_text(str(target), max_chars=1000)["text"]

    assert result["total_replaced"] == 2
    assert "2027년 계획, 2026년 실적" in text


def test_delete_paragraph_removes_node_without_empty_placeholder(tmp_path: Path):
    server = _reload_server()
    target = tmp_path / "delete_placeholder.hwpx"
    server.create_document(str(target))
    server.add_paragraph(str(target), "첫 문단")
    server.add_paragraph(str(target), "둘 문단")
    server.add_paragraph(str(target), "셋 문단")

    before = len(server.get_paragraphs_text(str(target))["paragraphs"])
    server.insert_paragraph(str(target), 1, "임시 문단")
    after_insert = len(server.get_paragraphs_text(str(target))["paragraphs"])
    server.delete_paragraph(str(target), 1)
    after_delete = len(server.get_paragraphs_text(str(target))["paragraphs"])

    rows = server.get_paragraphs_text(str(target), 0, 6)["paragraphs"]
    texts = [row["text"] for row in rows]

    assert after_insert == before + 1
    assert after_delete == before
    assert "임시 문단" not in texts
    assert texts[1:4] == ["첫 문단", "둘 문단", "셋 문단"]


def test_add_table_and_document_info_table_count_stay_consistent(tmp_path: Path):
    server = _reload_server()
    target = _copy_report_sample(tmp_path, "table_count_consistency.hwpx")

    before = server.get_document_info(str(target))
    added = server.add_table(str(target), 2, 2, [["A", "B"], ["C", "D"]])
    after = server.get_document_info(str(target))
    table = server.get_table_text(str(target), added["table_index"])

    assert after["tables"] == before["tables"] + 1
    assert added["table_index"] == after["tables"] - 1
    assert table["data"][0][0] == "A"


def test_table_count_uses_section_xml_tree_not_paragraph_tables():
    server = _reload_server()
    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

    section_element = ET.Element(f"{hp}sec")
    para = ET.SubElement(section_element, f"{hp}p")
    ET.SubElement(para, f"{hp}tbl")
    outer = ET.SubElement(section_element, f"{hp}tbl")
    row = ET.SubElement(outer, f"{hp}tr")
    ET.SubElement(row, f"{hp}tbl")

    class _Section:
        def __init__(self, element):
            self.element = element

    class _Paragraph:
        tables = []

    class _Doc:
        sections = [_Section(section_element)]
        paragraphs = [_Paragraph()]

    assert server._table_count(_Doc()) == 3
