"""MCP 도구 정의를 통한 종단 간 self-test 스위트."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Dict
import warnings

import pytest

from hwpx.document import HwpxDocument

from hwpx_mcp_server.hwpx_ops import HwpxOps, HwpxOperationError
from hwpx_mcp_server.tools import ToolDefinition, build_tool_definitions


HP_NS = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HM_NS = HP_NS


def _paragraph_texts(document: HwpxDocument) -> list[str]:
    texts: list[str] = []
    for paragraph in document.paragraphs:
        parts = [run.text for run in paragraph.runs]
        texts.append("".join(parts))
    return texts


def _field_ids(paragraph) -> list[str]:
    ids: list[str] = []
    for run in paragraph.element.findall(f"{HP_NS}run"):
        ctrl = run.find(f"{HP_NS}ctrl")
        if ctrl is None:
            continue
        field_begin = ctrl.find(f"{HP_NS}fieldBegin")
        if field_begin is not None and field_begin.get("id"):
            ids.append(field_begin.get("id"))
    return ids


def _memo_ids(document: HwpxDocument) -> set[str]:
    identifiers: set[str] = set()
    for section in document.sections:
        for memo in section.memos:
            if memo.id:
                identifiers.add(memo.id)
    return identifiers


def _count_tag(path: Path, tag_name: str) -> int:
    document = HwpxDocument.open(path)
    count = 0
    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            for child in run.element:
                if child.tag == tag_name:
                    count += 1
    return count


def _count_controls(path: Path, ctrl_type: str) -> int:
    document = HwpxDocument.open(path)
    count = 0
    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            for child in run.element.findall(f"{HP_NS}ctrl"):
                if child.get("type") == ctrl_type:
                    count += 1
    return count


@pytest.fixture()
def sample_workspace(tmp_path: Path) -> tuple[Path, Path]:
    source = Path(__file__).with_name("sample.hwpx")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "sample.hwpx"
    target.write_bytes(source.read_bytes())
    return workspace, target


@pytest.fixture()
def ops(sample_workspace: tuple[Path, Path]) -> HwpxOps:
    workspace, _ = sample_workspace
    return HwpxOps(base_directory=workspace, auto_backup=True, enable_opc_write=True)


@pytest.fixture()
def tool_map() -> Dict[str, ToolDefinition]:
    return {tool.name: tool for tool in build_tool_definitions()}


def _call(tool_map: Dict[str, ToolDefinition], name: str, ops: HwpxOps, **arguments):
    return tool_map[name].call(ops, arguments)


def test_metadata_navigation_tools(
    ops: HwpxOps,
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
) -> None:
    _, doc_path = sample_workspace
    rel_path = doc_path.name

    document = HwpxDocument.open(doc_path)
    info = _call(tool_map, "open_info", ops, path=rel_path)
    assert info["sectionCount"] == len(document.sections)
    assert info["paragraphCount"] == sum(len(section.paragraphs) for section in document.sections)

    sections = _call(tool_map, "list_sections", ops, path=rel_path)["sections"]
    assert len(sections) == len(document.sections)
    assert sections[0]["partName"] == getattr(document.sections[0], "part_name", None)

    headers = _call(tool_map, "list_headers", ops, path=rel_path)["headers"]
    assert len(headers) == len(document.headers)

    master_info = _call(
        tool_map,
        "list_master_pages_histories_versions",
        ops,
        path=rel_path,
    )
    assert master_info["masterPages"] == [getattr(page, "part_name", None) for page in document.master_pages]
    assert master_info["histories"] == [getattr(history, "part_name", None) for history in document.histories]


def test_text_reading_and_search_tools(
    ops: HwpxOps,
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
) -> None:
    _, doc_path = sample_workspace
    rel_path = doc_path.name

    page = _call(tool_map, "read_text", ops, path=rel_path, limit=4)
    assert "Hello HWPX!" in page["textChunk"]

    report = _call(tool_map, "text_extract_report", ops, path=rel_path)
    assert "Remember to replace HWPX references." in report["content"]

    matches = _call(tool_map, "find", ops, path=rel_path, query="HWPX")["matches"]
    assert matches and matches[0]["context"].startswith("Hello")

    first_run_char = HwpxDocument.open(doc_path).paragraphs[1].runs[0].char_pr_id_ref
    runs = _call(
        tool_map,
        "find_runs_by_style",
        ops,
        path=rel_path,
        filters={"charPrIDRef": first_run_char},
    )["runs"]
    assert any(run["paragraphIndex"] == 1 for run in runs)


def test_replace_text_in_runs_respects_dry_run(
    ops: HwpxOps,
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
) -> None:
    _, doc_path = sample_workspace
    rel_path = doc_path.name

    result = _call(
        tool_map,
        "replace_text_in_runs",
        ops,
        path=rel_path,
        search="HWPX",
        replacement="DOCX",
        dryRun=True,
    )
    assert result["replacedCount"] >= 1
    after_dry = _call(tool_map, "read_text", ops, path=rel_path, limit=6)["textChunk"]
    assert "DOCX" not in after_dry

    result_live = _call(
        tool_map,
        "replace_text_in_runs",
        ops,
        path=rel_path,
        search="HWPX",
        replacement="DOCX",
        dryRun=False,
    )
    assert result_live["replacedCount"] >= 1
    updated = _call(tool_map, "read_text", ops, path=rel_path, limit=6)["textChunk"]
    assert "DOCX" in updated

    backup = doc_path.with_suffix(doc_path.suffix + ".bak")
    assert backup.exists()
    backup_text = "\n".join(_paragraph_texts(HwpxDocument.open(backup)))
    assert "HWPX" in backup_text


def test_paragraph_insertion_and_style_application(
    ops: HwpxOps,
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
) -> None:
    _, doc_path = sample_workspace
    rel_path = doc_path.name

    before_count = len(HwpxDocument.open(doc_path).paragraphs)

    add_result = _call(
        tool_map,
        "add_paragraph",
        ops,
        path=rel_path,
        text="새 문단",
        runStyle={"bold": True},
    )
    assert add_result["paragraphIndex"] == before_count

    bulk_result = _call(
        tool_map,
        "insert_paragraphs_bulk",
        ops,
        path=rel_path,
        paragraphs=["대량 추가 1", "대량 추가 2"],
        runStyle={"colorHex": "#3366FF"},
        dryRun=False,
    )
    assert bulk_result["added"] == 2

    ensure_result = _call(
        tool_map,
        "ensure_run_style",
        ops,
        path=rel_path,
        colorHex="#FF0000",
        underline=True,
    )
    style_id = ensure_result["charPrIDRef"]
    assert style_id is not None

    apply_result = _call(
        tool_map,
        "apply_style_to_paragraphs",
        ops,
        path=rel_path,
        paragraphIndexes=[before_count, before_count + 1, before_count + 2],
        charPrIDRef=style_id,
        dryRun=False,
    )
    assert apply_result["updated"] == 3

    document = HwpxDocument.open(doc_path)
    texts = _paragraph_texts(document)
    assert "새 문단" in texts[-3]
    assert texts[-2:] == ["대량 추가 1", "대량 추가 2"]
    for paragraph in document.paragraphs[-3:]:
        for run in paragraph.runs:
            assert run.char_pr_id_ref == style_id


def test_apply_style_to_text_ranges(
    ops: HwpxOps,
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
) -> None:
    _, doc_path = sample_workspace
    rel_path = doc_path.name

    add_result = _call(
        tool_map,
        "add_paragraph",
        ops,
        path=rel_path,
        text="alpha beta gamma",
    )
    paragraph_index = add_result["paragraphIndex"]

    ensure_result = _call(
        tool_map,
        "ensure_run_style",
        ops,
        path=rel_path,
        bold=True,
    )
    style_id = ensure_result["charPrIDRef"]
    assert style_id is not None

    apply_result = _call(
        tool_map,
        "apply_style_to_text_ranges",
        ops,
        path=rel_path,
        spans=[
            {"paragraphIndex": paragraph_index, "start": 6, "end": 10},
            {"paragraphIndex": paragraph_index, "start": 11, "end": 16},
        ],
        charPrIDRef=style_id,
        dryRun=False,
    )
    assert apply_result["styledSpans"] == 2

    document = HwpxDocument.open(doc_path)
    paragraph = document.paragraphs[paragraph_index]
    texts = [run.text for run in paragraph.runs if run.text]
    assert texts == ["alpha ", "beta", " ", "gamma"]
    styled_segments = [run.text for run in paragraph.runs if run.char_pr_id_ref == style_id]
    assert styled_segments == ["beta", "gamma"]


def test_table_workflow(
    ops: HwpxOps,
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
) -> None:
    _, doc_path = sample_workspace
    rel_path = doc_path.name

    table_result = _call(
        tool_map,
        "add_table",
        ops,
        path=rel_path,
        rows=2,
        cols=2,
    )
    table_index = table_result["tableIndex"]
    assert table_result["cellCount"] == 4

    set_result = _call(
        tool_map,
        "set_table_cell_text",
        ops,
        path=rel_path,
        tableIndex=table_index,
        row=0,
        col=0,
        text="헤더",
        dryRun=False,
    )
    assert set_result == {"ok": True}

    replace_result = _call(
        tool_map,
        "replace_table_region",
        ops,
        path=rel_path,
        tableIndex=table_index,
        startRow=0,
        startCol=0,
        values=[["A", "B"], ["C", "D"]],
        dryRun=False,
    )
    assert replace_result["updatedCells"] == 4

    document = HwpxDocument.open(doc_path)
    tables = []
    for paragraph in document.paragraphs:
        tables.extend(paragraph.tables)
    target_table = tables[table_index]
    assert [[target_table.cell(r, c).text for c in range(target_table.column_count)] for r in range(target_table.row_count)] == [
        ["A", "B"],
        ["C", "D"],
    ]


def test_shape_control_and_memo_tools(
    ops: HwpxOps,
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
) -> None:
    _, doc_path = sample_workspace
    rel_path = doc_path.name

    original_shape_count = _count_tag(doc_path, f"{HP_NS}RECTANGLE")
    shape_result = _call(
        tool_map,
        "add_shape",
        ops,
        path=rel_path,
        dryRun=False,
    )
    assert "objectId" in shape_result
    assert _count_tag(doc_path, f"{HP_NS}RECTANGLE") == original_shape_count + 1

    original_ctrl = _count_controls(doc_path, "TEXTBOX")
    control_result = _call(
        tool_map,
        "add_control",
        ops,
        path=rel_path,
        controlType="TEXTBOX",
        dryRun=False,
    )
    assert "objectId" in control_result
    assert _count_controls(doc_path, "TEXTBOX") == original_ctrl + 1

    memo_result = _call(
        tool_map,
        "add_memo",
        ops,
        path=rel_path,
        text="테스트 메모",
    )
    memo_id = memo_result["memoId"]
    assert memo_id in _memo_ids(HwpxDocument.open(doc_path))

    field_result = _call(
        tool_map,
        "attach_memo_field",
        ops,
        path=rel_path,
        paragraphIndex=1,
        memoId=memo_id,
    )
    field_id = field_result["fieldId"]
    paragraph = HwpxDocument.open(doc_path).paragraphs[1]
    assert field_id in _field_ids(paragraph)

    anchor_result = _call(
        tool_map,
        "add_memo_with_anchor",
        ops,
        path=rel_path,
        text="앵커 메모",
    )
    anchor_id = anchor_result["memoId"]
    anchored_doc = HwpxDocument.open(doc_path)
    assert anchor_result["paragraphIndex"] == len(anchored_doc.paragraphs) - 1
    assert anchor_result["fieldId"] in _field_ids(anchored_doc.paragraphs[anchor_result["paragraphIndex"]])
    assert anchor_id in _memo_ids(anchored_doc)

    remove_result = _call(
        tool_map,
        "remove_memo",
        ops,
        path=rel_path,
        memoId=memo_id,
        dryRun=False,
    )
    assert remove_result == {"removed": True}
    remaining_ids = _memo_ids(HwpxDocument.open(doc_path))
    assert memo_id not in remaining_ids
    assert anchor_id in remaining_ids


def test_package_tools_with_and_without_writes(
    ops: HwpxOps,
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
) -> None:
    _, doc_path = sample_workspace
    rel_path = doc_path.name

    parts = _call(tool_map, "package_parts", ops, path=rel_path)["parts"]
    assert "Contents/section0.xml" in parts
    target_part = "Contents/section0.xml"

    text_payload = _call(
        tool_map,
        "package_get_text",
        ops,
        path=rel_path,
        partName=target_part,
    )["text"]

    dry_text = _call(
        tool_map,
        "package_set_text",
        ops,
        path=rel_path,
        partName=target_part,
        text=text_payload,
        dryRun=True,
    )
    assert dry_text == {"updated": False}

    xml_payload = _call(
        tool_map,
        "package_get_xml",
        ops,
        path=rel_path,
        partName=target_part,
    )["xmlString"]

    dry_xml = _call(
        tool_map,
        "package_set_xml",
        ops,
        path=rel_path,
        partName=target_part,
        xmlString=xml_payload,
        dryRun=True,
    )
    assert dry_xml == {"updated": False}

    modified_text = text_payload.replace("Hello HWPX!", "Hello HWPX! (patched)")
    live_text = _call(
        tool_map,
        "package_set_text",
        ops,
        path=rel_path,
        partName=target_part,
        text=modified_text,
        dryRun=False,
    )
    assert live_text == {"updated": True}

    after_text = _call(
        tool_map,
        "package_get_text",
        ops,
        path=rel_path,
        partName=target_part,
    )["text"]
    assert "Hello HWPX! (patched)" in after_text

    xml_after_text = _call(
        tool_map,
        "package_get_xml",
        ops,
        path=rel_path,
        partName=target_part,
    )["xmlString"]

    modified_xml = xml_after_text.replace(
        "Table below demonstrates cell editing.",
        "Table below demonstrates cell editing (XML).",
    )
    live_xml = _call(
        tool_map,
        "package_set_xml",
        ops,
        path=rel_path,
        partName=target_part,
        xmlString=modified_xml,
        dryRun=False,
    )
    assert live_xml == {"updated": True}

    final_text = _call(
        tool_map,
        "package_get_text",
        ops,
        path=rel_path,
        partName=target_part,
    )["text"]
    assert "Table below demonstrates cell editing (XML)." in final_text

    backup = doc_path.with_suffix(doc_path.suffix + ".bak")
    assert backup.exists()
    backup_text = _call(
        tool_map,
        "package_get_text",
        ops,
        path=backup.name,
        partName=target_part,
    )["text"]
    assert "Hello HWPX! (patched)" in backup_text
    assert "Table below demonstrates cell editing (XML)." not in backup_text


def test_save_operations_and_blank_document(
    ops: HwpxOps,
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    _, doc_path = sample_workspace
    rel_path = doc_path.name

    save_result = _call(tool_map, "save", ops, path=rel_path)
    assert save_result == {"ok": True}
    backup = doc_path.with_suffix(doc_path.suffix + ".bak")
    assert backup.exists()

    out_path = doc_path.with_name("copy.hwpx")
    save_as = _call(
        tool_map,
        "save_as",
        ops,
        path=rel_path,
        out=str(out_path),
    )
    assert Path(save_as["outPath"]).exists()
    assert len(_paragraph_texts(HwpxDocument.open(out_path))) >= 1

    blank_path = tmp_path / "blank.hwpx"
    make_blank = _call(
        tool_map,
        "make_blank",
        ops,
        out=str(blank_path),
    )
    assert Path(make_blank["outPath"]).exists()
    blank_doc = HwpxDocument.open(blank_path)
    assert len(blank_doc.sections) == 1
    assert len(list(blank_doc.paragraphs)) == 1


def test_diagnostic_tools_and_validation(
    ops: HwpxOps,
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
) -> None:
    _, doc_path = sample_workspace
    rel_path = doc_path.name

    tables = _call(
        tool_map,
        "object_find_by_tag",
        ops,
        path=rel_path,
        tagName=f"{HP_NS}tbl",
    )["objects"]
    assert any(obj["type"].endswith("tbl") for obj in tables)

    document = HwpxDocument.open(doc_path)
    existing_memo = next(iter(_memo_ids(document)))
    memo_objects = _call(
        tool_map,
        "object_find_by_attr",
        ops,
        path=rel_path,
        elementType=f"{HM_NS}memo",
        attr="id",
        value=existing_memo,
    )["objects"]
    assert memo_objects

    clean_validation = _call(tool_map, "validate_structure", ops, path=rel_path)
    assert clean_validation["ok"] is True
    assert clean_validation["issues"] == []

    broken_path = doc_path.with_name("broken.hwpx")
    broken_path.write_bytes(doc_path.read_bytes())
    minimal_section = "<section xmlns=\"http://www.hancom.co.kr/hwpml/2011/section\" />"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(broken_path, "a") as archive:
            archive.writestr("Contents/section0.xml", minimal_section)

    broken_report = _call(tool_map, "validate_structure", ops, path=broken_path.name)
    assert broken_report["ok"] is False
    assert broken_report["issues"]

    lint_clean = _call(tool_map, "lint_text_conventions", ops, path=rel_path)
    assert lint_clean["warnings"] == []

    lint_violations = _call(
        tool_map,
        "lint_text_conventions",
        ops,
        path=rel_path,
        rules={"forbidPatterns": ["HWPX"]},
    )
    assert lint_violations["warnings"]


def test_failure_paths_raise_runtime_errors(
    tool_map: Dict[str, ToolDefinition],
    sample_workspace: tuple[Path, Path],
) -> None:
    workspace, doc_path = sample_workspace
    ops = HwpxOps(base_directory=workspace, auto_backup=True, enable_opc_write=True)
    rel_path = doc_path.name

    with pytest.raises(FileNotFoundError):
        ops._resolve_path("missing.hwpx", must_exist=True)

    with pytest.raises(RuntimeError):
        _call(
            tool_map,
            "replace_table_region",
            ops,
            path=rel_path,
            tableIndex=99,
            startRow=0,
            startCol=0,
            values=[["X"]],
            dryRun=False,
        )


def test_hwpx_operation_error_inheritance() -> None:
    assert issubclass(HwpxOperationError, RuntimeError)
