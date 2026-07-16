from pathlib import Path

from hwpx_mcp_server.server import (
    add_paragraph,
    apply_edits,
    create_document,
    get_document_text,
    get_table_text,
    undo_last_edit,
)


def test_dry_run_does_not_modify_document_or_create_backup(tmp_path: Path) -> None:
    target = tmp_path / "dry-run.hwpx"
    create_document(str(target))
    before = target.read_bytes()

    result = add_paragraph(str(target), "dry-run paragraph", dry_run=True)

    assert target.read_bytes() == before
    assert not target.with_suffix(target.suffix + ".bak").exists()
    assert result["dryRun"] is True
    assert result["wouldSave"] is True
    assert result["openSafety"]["ok"] is True
    assert result["semanticDiff"]["changed"] is True
    assert "dry-run paragraph" not in get_document_text(str(target))["text"]


def test_apply_edits_saves_once_with_backup_and_semantic_diff(tmp_path: Path) -> None:
    target = tmp_path / "apply.hwpx"
    create_document(str(target))

    result = apply_edits(
        str(target),
        [
            {"type": "add_paragraph", "text": "2025 plan"},
            {"type": "replace_text", "findText": "2025", "replaceText": "2026"},
            {"type": "add_table", "rows": 1, "cols": 2, "data": [["label", "old"]]},
            {"type": "set_table_cell_text", "tableIndex": 0, "row": 0, "col": 1, "text": "new"},
        ],
    )

    assert result["ok"] is True
    assert result["operationsApplied"] == 4
    assert result["openSafety"]["ok"] is True
    assert result["backup"]["created"] is True
    assert target.with_suffix(target.suffix + ".bak").exists()
    assert result["semanticDiff"]["changed"] is True
    assert "2026 plan" in get_document_text(str(target))["text"]
    assert get_table_text(str(target), 0)["data"] == [["label", "new"]]


def test_apply_edits_failure_rolls_back_without_backup(tmp_path: Path) -> None:
    target = tmp_path / "rollback.hwpx"
    create_document(str(target))
    before = target.read_bytes()

    result = apply_edits(
        str(target),
        [
            {"type": "add_paragraph", "text": "not saved"},
            {"type": "set_table_cell_text", "tableIndex": 99, "row": 0, "col": 0, "text": "boom"},
        ],
    )

    assert result["ok"] is False
    assert result["rolledBack"] is True
    assert result["failedOperationIndex"] == 1
    assert target.read_bytes() == before
    assert not target.with_suffix(target.suffix + ".bak").exists()
    assert "not saved" not in get_document_text(str(target))["text"]


def test_undo_last_edit_restores_previous_backup(tmp_path: Path) -> None:
    target = tmp_path / "undo.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")

    undo = undo_last_edit(str(target))
    text = get_document_text(str(target))["text"]

    assert undo["restored"] is True
    assert undo["openSafety"]["ok"] is True
    assert undo["semanticDiff"]["changed"] is True
    assert "first saved paragraph" in text
    assert "second saved paragraph" not in text
