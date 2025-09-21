from pathlib import Path

import pytest

from hwpx.document import HwpxDocument
from hwpx_mcp_server.hwpx_ops import HwpxOps
from hwpx_mcp_server.tools import build_tool_definitions


@pytest.fixture()
def ops_with_sample(tmp_path) -> tuple[HwpxOps, Path]:
    sample = Path(__file__).with_name("sample.hwpx")
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    target = workdir / "sample.hwpx"
    target.write_bytes(sample.read_bytes())
    ops = HwpxOps(base_directory=workdir, auto_backup=True)
    return ops, target


def test_open_info_counts(ops_with_sample):
    ops, path = ops_with_sample
    info = ops.open_info(str(path))
    assert info["sectionCount"] >= 1
    assert info["paragraphCount"] >= 1
    assert info["meta"]["absolutePath"].endswith("sample.hwpx")


def test_read_text_pagination(ops_with_sample):
    ops, path = ops_with_sample
    result = ops.read_text(str(path), limit=2)
    assert "HWPX" in result["textChunk"]
    assert result["nextOffset"] >= 1


def test_find_returns_matches(ops_with_sample):
    ops, path = ops_with_sample
    matches = ops.find(str(path), "HWPX")
    assert matches["matches"]
    assert any("HWPX" in match["context"] for match in matches["matches"])


def test_replace_text_in_runs_dry_run_does_not_modify(ops_with_sample):
    ops, path = ops_with_sample
    ops.replace_text_in_runs(str(path), "HWPX", "DOCX", dry_run=True)
    text = ops.read_text(str(path), limit=5)["textChunk"]
    assert "DOCX" not in text


def test_replace_text_in_runs_updates_file_and_backup(ops_with_sample):
    ops, path = ops_with_sample
    ops.replace_text_in_runs(str(path), "HWPX", "DOCX", dry_run=False)
    text = ops.read_text(str(path), limit=5)["textChunk"]
    assert "DOCX" in text
    backup = path.with_suffix(path.suffix + ".bak")
    assert backup.exists()


def test_save_as_creates_new_file(ops_with_sample, tmp_path):
    ops, path = ops_with_sample
    out = path.with_name("copy.hwpx")
    result = ops.save_as(str(path), str(out))
    assert Path(result["outPath"]).exists()


def test_package_set_xml_tool_accepts_alias_arguments(ops_with_sample):
    ops, path = ops_with_sample
    ops.enable_opc_write = True
    tools = {tool.name: tool for tool in build_tool_definitions()}
    package_set_tool = tools["package_set_xml"]
    schema = package_set_tool.input_model.model_json_schema(by_alias=True)
    assert "xmlString" in schema["properties"]

    part_name = "Contents/section0.xml"
    package_get_tool = tools["package_get_xml"]
    xml_payload = package_get_tool.call(
        ops,
        {
            "path": str(path),
            "partName": part_name,
        },
    )

    result = package_set_tool.call(
        ops,
        {
            "path": str(path),
            "partName": part_name,
            "xmlString": xml_payload["xmlString"],
            "dryRun": True,
        },
    )

    assert result == {"updated": False}


def test_add_table_returns_valid_index(ops_with_sample):
    ops, path = ops_with_sample
    result = ops.add_table(str(path), rows=2, cols=2)

    assert result["cellCount"] == 4
    index = result["tableIndex"]

    # ensure the table can be edited using the reported index
    update = ops.set_table_cell_text(
        str(path),
        table_index=index,
        row=0,
        col=0,
        text="이름",
        dry_run=False,
    )

    assert update == {"ok": True}


def test_set_table_cell_supports_logical_and_split_flags(ops_with_sample):
    ops, path = ops_with_sample
    table_info = ops.add_table(str(path), rows=3, cols=3)
    index = table_info["tableIndex"]

    document = HwpxDocument.open(path)
    tables = []
    for paragraph in document.paragraphs:
        tables.extend(paragraph.tables)
    tables[index].merge_cells(0, 0, 1, 1)
    document.save(path)

    logical_result = ops.set_table_cell_text(
        str(path),
        table_index=index,
        row=1,
        col=1,
        text="Merged anchor",
        logical=True,
        dry_run=False,
    )

    assert logical_result == {"ok": True}

    merged_state = HwpxDocument.open(path)
    merged_tables: list = []
    for paragraph in merged_state.paragraphs:
        merged_tables.extend(paragraph.tables)
    merged_cell = merged_tables[index].cell(0, 0)
    assert merged_cell.span == (2, 2)
    assert merged_cell.text == "Merged anchor"

    split_result = ops.set_table_cell_text(
        str(path),
        table_index=index,
        row=1,
        col=1,
        text="Bottom-right",
        logical=True,
        split_merged=True,
        dry_run=False,
    )

    assert split_result == {"ok": True}

    split_state = HwpxDocument.open(path)
    split_tables: list = []
    for paragraph in split_state.paragraphs:
        split_tables.extend(paragraph.tables)
    top_left = split_tables[index].cell(0, 0)
    bottom_right = split_tables[index].cell(1, 1)
    assert top_left.span == (1, 1)
    assert top_left.text == "Merged anchor"
    assert bottom_right.span == (1, 1)
    assert bottom_right.text == "Bottom-right"


def test_replace_region_and_split_tool_handle_merged_cells(ops_with_sample):
    ops, path = ops_with_sample
    table_info = ops.add_table(str(path), rows=3, cols=3)
    index = table_info["tableIndex"]

    document = HwpxDocument.open(path)
    tables = []
    for paragraph in document.paragraphs:
        tables.extend(paragraph.tables)
    target_table = tables[index]
    target_table.merge_cells(0, 0, 1, 1)
    document.save(path)

    region_result = ops.replace_table_region(
        str(path),
        table_index=index,
        start_row=0,
        start_col=0,
        values=[["A", "B"], ["C", "D"]],
        logical=True,
        split_merged=True,
        dry_run=False,
    )

    assert region_result["updatedCells"] == 4

    updated_state = HwpxDocument.open(path)
    updated_tables: list = []
    for paragraph in updated_state.paragraphs:
        updated_tables.extend(paragraph.tables)
    updated_table = updated_tables[index]
    assert updated_table.cell(0, 0).span == (1, 1)
    assert updated_table.cell(0, 0).text == "A"
    assert updated_table.cell(0, 1).text == "B"
    assert updated_table.cell(1, 0).text == "C"
    assert updated_table.cell(1, 1).text == "D"

    # Merge a new column region and split it using the dedicated tool
    updated_table.merge_cells(0, 2, 1, 2)
    updated_state.save(path)

    split_meta = ops.split_table_cell(str(path), table_index=index, row=0, col=2)

    assert split_meta == {"startRow": 0, "startCol": 2, "rowSpan": 2, "colSpan": 1}

    split_state = HwpxDocument.open(path)
    split_tables: list = []
    for paragraph in split_state.paragraphs:
        split_tables.extend(paragraph.tables)
    column_cell = split_tables[index].cell(0, 2)
    assert column_cell.span == (1, 1)
