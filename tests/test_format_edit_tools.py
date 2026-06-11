from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from hwpx.document import HwpxDocument
from hwpx.tools.package_validator import validate_editor_open_safety
from hwpx_mcp_server import server
from hwpx_mcp_server.hwpx_ops import HwpxOps
from hwpx_mcp_server.tools import build_tool_definitions


HH_NS = "http://www.hancom.co.kr/hwpml/2011/head"
HP_NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = f"{{{HH_NS}}}"
HP = f"{{{HP_NS}}}"


def _mm(value: float) -> int:
    return round(value * 7200 / 25.4)


def _local_name(element: ET.Element) -> str:
    if "}" in element.tag:
        return element.tag.split("}", 1)[1]
    return element.tag


def _descendants(element: ET.Element, local_name: str) -> list[ET.Element]:
    return [
        child
        for child in element.iter()
        if child is not element and _local_name(child) == local_name
    ]


def _para_pr_for_paragraph(document: HwpxDocument, paragraph_index: int) -> ET.Element:
    para_pr_id = document.paragraphs[paragraph_index].para_pr_id_ref
    assert para_pr_id is not None
    para_pr = document.headers[0].element.find(f".//{HH}paraPr[@id='{para_pr_id}']")
    assert para_pr is not None
    return para_pr


def test_fastmcp_format_edit_tools_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "format-tools.hwpx"
    server.create_document(str(target))
    paragraph_index = server.add_paragraph(str(target), "줄간격 160%")["paragraph_index"]

    paragraph_result = server.set_paragraph_format(
        str(target),
        paragraph_index=paragraph_index,
        alignment="center",
        line_spacing_percent=160,
        indent_left_mm=10,
        spacing_before_pt=6,
        spacing_after_pt=3,
    )
    assert paragraph_result["openSafety"]["ok"] is True

    page_result = server.set_page_setup(
        str(target),
        paper_size="A4",
        orientation="landscape",
        margin_left_mm=20,
        margin_right_mm=15,
        margin_top_mm=12,
        margin_bottom_mm=12,
    )
    assert page_result["openSafety"]["ok"] is True
    assert page_result["pageSize"]["width"] == _mm(297)

    header_result = server.set_header_footer(str(target), kind="header", text="Confidential")
    page_number_result = server.set_page_number(
        str(target),
        target="footer",
        format="page/total",
        prefix="Page ",
    )
    list_result = server.set_list_format(
        str(target),
        paragraph_index=paragraph_index,
        kind="bullet",
        bullet_char="※",
    )
    assert header_result["openSafety"]["ok"] is True
    assert page_number_result["headerFooter"]["pageNumberCount"] == 2
    assert list_result["openSafety"]["ok"] is True

    assert validate_editor_open_safety(target).ok
    reopened = HwpxDocument.open(target)
    para_pr = _para_pr_for_paragraph(reopened, paragraph_index)
    heading = para_pr.find(f"{HH}heading")
    assert heading is not None
    assert heading.get("type") == "BULLET"
    assert reopened.headers[0].element.find(f".//{HH}bullet[@char='※']") is not None

    line_spacing_values = {
        node.get("value")
        for node in _descendants(reopened.headers[0].element, "lineSpacing")
    }
    assert "160" in line_spacing_values
    assert reopened.sections[0].properties.page_size.width == _mm(297)
    assert reopened.sections[0].properties.page_margins.left == _mm(20)
    assert reopened.sections[0].properties.get_header().text == "Confidential"
    assert reopened.sections[0].properties.get_footer().element.find(f".//{HP}pageNum") is not None


def test_legacy_format_tools_schema_and_call(tmp_path: Path) -> None:
    target = tmp_path / "legacy-format.hwpx"
    server.create_document(str(target))
    paragraph_index = server.add_paragraph(str(target), "legacy")["paragraph_index"]

    definitions = {definition.name: definition for definition in build_tool_definitions()}
    for name in {
        "set_paragraph_format",
        "set_page_setup",
        "set_header_footer",
        "set_page_number",
        "set_list_format",
    }:
        assert name in definitions

    ops = HwpxOps(base_directory=tmp_path, auto_backup=False)
    result = definitions["set_paragraph_format"].call(
        ops,
        {
            "document": {"type": "path", "path": str(target)},
            "paragraphIndex": paragraph_index,
            "lineSpacingPercent": 160,
            "dryRun": False,
        },
    )
    assert result["openSafety"]["ok"] is True
    assert result["verificationReport"]["openSafety"]["ok"] is True
