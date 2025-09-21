from pathlib import Path

import pytest

from hwpx.document import HwpxDocument
from hwpx_mcp_server.hwpx_ops import HwpxOps
import hwpx_mcp_server.hwpx_ops as ops_module
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


def test_read_text_uses_default_limit(monkeypatch, tmp_path):
    class FakeParagraph:
        def __init__(self, index: int, content: str) -> None:
            self.index = index
            self._content = content

        def text(
            self,
            *,
            annotations=None,
            preserve_breaks: bool = False,
        ) -> str:
            return self._content

    paragraphs = [FakeParagraph(index, f"Paragraph {index}") for index in range(500)]

    class FakeExtractor:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_document_paragraphs(self):
            yield from paragraphs

    monkeypatch.setattr(ops_module, "TextExtractor", FakeExtractor)

    ops = HwpxOps(base_directory=tmp_path)
    dummy = tmp_path / "dummy.hwpx"
    dummy.write_bytes(b"")

    result = ops.read_text(dummy.name)
    lines = result["textChunk"].splitlines()
    assert len(lines) <= ops_module.DEFAULT_PAGING_PARAGRAPH_LIMIT
    assert result["nextOffset"] == ops_module.DEFAULT_PAGING_PARAGRAPH_LIMIT

    expanded_limit = ops_module.DEFAULT_PAGING_PARAGRAPH_LIMIT + 50
    expanded = ops.read_text(dummy.name, limit=expanded_limit)
    expanded_lines = expanded["textChunk"].splitlines()
    assert len(expanded_lines) == expanded_limit
    assert expanded["nextOffset"] == expanded_limit


def test_find_returns_matches(ops_with_sample):
    ops, path = ops_with_sample
    matches = ops.find(str(path), "HWPX")
    assert matches["matches"]
    assert any("HWPX" in match["context"] for match in matches["matches"])


def test_find_truncates_context_and_respects_radius(monkeypatch, tmp_path):
    text = "A" * 200 + "needle" + "B" * 200

    class FakeParagraph:
        def __init__(self, index: int, content: str) -> None:
            self.index = index
            self._content = content

        def text(self) -> str:
            return self._content

    class FakeExtractor:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_document_paragraphs(self):
            yield FakeParagraph(0, text)

    monkeypatch.setattr(ops_module, "TextExtractor", FakeExtractor)

    ops = HwpxOps(base_directory=tmp_path)
    dummy = tmp_path / "dummy.hwpx"
    dummy.write_bytes(b"")

    default = ops.find(dummy.name, "needle")
    context = default["matches"][0]["context"]
    assert context.startswith("...")
    assert context.endswith("...")
    assert len(context) < len(text)
    assert "needle" in context

    expanded = ops.find(dummy.name, "needle", context_radius=500)
    expanded_context = expanded["matches"][0]["context"]
    assert expanded_context == text


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
    )

    assert update == {"ok": True}

    refreshed = HwpxDocument.open(path)
    refreshed_tables: list = []
    for paragraph in refreshed.paragraphs:
        refreshed_tables.extend(paragraph.tables)
    assert refreshed_tables[index].cell(0, 0).text == "이름"


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


def test_get_table_cell_map_serializes_grid_with_merges(ops_with_sample):
    ops, path = ops_with_sample
    table_info = ops.add_table(str(path), rows=3, cols=3)
    index = table_info["tableIndex"]

    document = HwpxDocument.open(path)
    tables: list = []
    for paragraph in document.paragraphs:
        tables.extend(paragraph.tables)
    target_table = tables[index]
    for row in range(3):
        for col in range(3):
            target_table.cell(row, col).text = f"R{row}C{col}"
    target_table.merge_cells(0, 0, 1, 1)
    target_table.merge_cells(0, 2, 2, 2)
    document.save(path)

    grid_info = ops.get_table_cell_map(str(path), table_index=index)
    grid = grid_info["grid"]

    assert grid_info["rowCount"] == 3
    assert grid_info["columnCount"] == 3
    assert all(len(row) == 3 for row in grid)

    coords = {(cell["row"], cell["column"]) for row in grid for cell in row}
    assert coords == {(r, c) for r in range(3) for c in range(3)}

    top_left = grid[0][0]
    assert top_left == {
        "row": 0,
        "column": 0,
        "anchor": {"row": 0, "column": 0},
        "rowSpan": 2,
        "colSpan": 2,
        "text": "R0C0",
    }

    overlapped = grid[1][1]
    assert overlapped["anchor"] == {"row": 0, "column": 0}
    assert overlapped["rowSpan"] == 2
    assert overlapped["colSpan"] == 2
    assert overlapped["text"] == "R0C0"

    vertical_anchor = grid[0][2]
    assert vertical_anchor["anchor"] == {"row": 0, "column": 2}
    assert vertical_anchor["rowSpan"] == 3
    assert vertical_anchor["colSpan"] == 1
    assert vertical_anchor["text"] == "R0C2"

    bottom_left = grid[2][0]
    assert bottom_left["anchor"] == {"row": 2, "column": 0}
    assert bottom_left["rowSpan"] == 1
    assert bottom_left["colSpan"] == 1
    assert bottom_left["text"] == "R2C0"
