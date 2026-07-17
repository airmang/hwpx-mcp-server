# SPDX-License-Identifier: Apache-2.0
"""Stateless HWPX MCP 서버."""

from __future__ import annotations

from typing import Any

from hwpx import (
    table_compute as build_hwpx_table_compute,
)

from ..core.content import (
    add_heading_to_doc,
    add_page_break_to_doc,
    add_paragraph_to_doc,
    add_table_to_doc,
    delete_paragraph_from_doc,
    fill_by_path_in_doc,
    format_table_in_doc,
    insert_paragraph_to_doc,
    merge_cells_in_table,
    set_cell_text,
    split_cell_in_table,
)
from ..core.document import open_doc
from ..core.locations import location_from_anchor, resolve_paragraph_reference
from ..core.search import _replace_in_runs, batch_replace_in_doc, replace_in_doc
from .. import quality as quality_contract
from ..mutation_models import (
    EditOperation,
    operation_payloads,
)
from ..upstream import (
    create_text_extractor,
    repair_pathological_text_spacing,
)
from ..utils.helpers import resolve_path
from ..runtime_services import RUNTIME_SERVICES
from ._shared import (
    _decode_image_base64,
    _id_integrity_payload,
    _idempotency_fingerprint,
    _idempotency_replay,
    _idempotency_scope,
    _idempotency_store,
    _normalize_fill_mappings,
    _revision_guard,
    _save_doc_verification,
    _with_dry_run_verification,
    _with_save_verification,
)


def _build_verification_plan_operation(path: str, instruction: str) -> dict[str, Any]:
    needle = (instruction or "").strip()
    if not needle:
        raise ValueError("instruction cannot be empty")

    with create_text_extractor(path) as extractor:
        for paragraph in extractor.iter_document_paragraphs():
            text = paragraph.text(preserve_breaks=True)
            if needle not in text:
                continue
            # FastMCP currently exposes only a single instruction string here.
            # Anchor the hardened pipeline on the first matching paragraph and
            # keep the replacement as a no-op so preview/apply remain truthful.
            return {
                "target": {"sectionIndex": 0, "paraIndex": paragraph.index},
                "match": text,
                "replacement": text,
                "limit": 1,
                "dryRun": True,
                "atomic": True,
            }

    raise ValueError("instruction text was not found in the document")


def table_compute(
    table: dict | list,
    value_columns: list = None,
    operations: list = None,
    append: str = "rows",
    group_by: str | int = None,  # type: ignore[assignment]  # Frozen ToolSpec default.
    label_column: str | int = None,  # type: ignore[assignment]  # Frozen ToolSpec default.
    labels: dict = None,
) -> dict:
    """일반 표에 합계·평균·소계 행/열을 추가하고 계산 근거를 반환합니다."""
    if build_hwpx_table_compute is None:
        raise RuntimeError("installed python-hwpx does not provide table compute tools")
    return build_hwpx_table_compute(
        table,
        value_columns=value_columns,
        operations=operations,
        append=append,
        group_by=group_by,
        label_column=label_column,
        labels=labels,
    )


def _operation_value(
    operation: dict[str, Any], *names: str, default: Any = None
) -> Any:
    for name in names:
        if name in operation:
            return operation[name]
    return default


def _apply_edit_operation(
    doc: Any, operation: dict[str, Any], index: int
) -> dict[str, Any]:
    if not isinstance(operation, dict):
        raise TypeError(f"operation {index} must be an object")
    raw_type = _operation_value(operation, "type", "op", "operation")
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise ValueError(f"operation {index} must include a type")
    op_type = raw_type.strip().replace("-", "_")

    if op_type == "replace_text":
        find = _operation_value(operation, "findText", "find_text", "find")
        replace = _operation_value(
            operation, "replaceText", "replace_text", "replace", default=""
        )
        if find is None:
            raise ValueError("replace_text requires findText")
        count = replace_in_doc(doc, find_text=str(find), replace_text=str(replace))
        return {"type": op_type, "replaced_count": count}

    if op_type == "batch_replace":
        replacements = _operation_value(operation, "replacements")
        if not isinstance(replacements, list):
            raise ValueError("batch_replace requires a replacements list")
        result = batch_replace_in_doc(doc, replacements)
        return {"type": op_type, **result}

    if op_type == "add_heading":
        text = _operation_value(operation, "text", default="")
        level = int(_operation_value(operation, "level", default=1))
        paragraph_index = add_heading_to_doc(doc, str(text), level)
        return {"type": op_type, "paragraph_index": paragraph_index}

    if op_type == "add_paragraph":
        text = _operation_value(operation, "text", default="")
        style = _operation_value(operation, "style")
        paragraph_index = add_paragraph_to_doc(doc, str(text), style)
        return {"type": op_type, "paragraph_index": paragraph_index}

    if op_type == "insert_paragraph":
        paragraph_index = _operation_value(
            operation, "paragraphIndex", "paragraph_index"
        )
        if paragraph_index is None:
            raise ValueError("insert_paragraph requires paragraphIndex")
        text = _operation_value(operation, "text", default="")
        style = _operation_value(operation, "style")
        inserted = insert_paragraph_to_doc(doc, int(paragraph_index), str(text), style)
        return {"type": op_type, "inserted_index": inserted}

    if op_type == "delete_paragraph":
        paragraph_index = _operation_value(
            operation, "paragraphIndex", "paragraph_index"
        )
        if paragraph_index is None:
            raise ValueError("delete_paragraph requires paragraphIndex")
        remaining = delete_paragraph_from_doc(doc, int(paragraph_index))
        return {
            "type": op_type,
            "deleted_index": int(paragraph_index),
            "remaining_paragraphs": remaining,
        }

    if op_type == "add_table":
        rows = _operation_value(operation, "rows")
        cols = _operation_value(operation, "cols", "columns")
        if rows is None or cols is None:
            raise ValueError("add_table requires rows and cols")
        data = _operation_value(operation, "data")
        table_index = add_table_to_doc(doc, int(rows), int(cols), data)
        return {"type": op_type, "table_index": table_index}

    if op_type == "set_table_cell_text":
        table_index = _operation_value(
            operation, "tableIndex", "table_index", default=0
        )
        row = _operation_value(operation, "row")
        col = _operation_value(operation, "col", "column")
        text = _operation_value(operation, "text", default="")
        if row is None or col is None:
            raise ValueError("set_table_cell_text requires row and col")
        preserve_format = bool(
            _operation_value(
                operation, "preserveFormat", "preserve_format", default=True
            )
        )
        split_paragraphs = bool(
            _operation_value(
                operation, "splitParagraphs", "split_paragraphs", default=False
            )
        )
        set_cell_text(
            doc,
            int(table_index),
            int(row),
            int(col),
            str(text),
            preserve_format=preserve_format,
            split_paragraphs=split_paragraphs,
        )
        return {
            "type": op_type,
            "table_index": int(table_index),
            "row": int(row),
            "col": int(col),
        }

    if op_type == "fill_by_path":
        mappings = _operation_value(operation, "mappings")
        if not isinstance(mappings, dict):
            raise ValueError("fill_by_path requires mappings")
        result = fill_by_path_in_doc(doc, _normalize_fill_mappings(mappings))
        return {"type": op_type, **result}

    if op_type == "add_page_break":
        add_page_break_to_doc(doc)
        return {"type": op_type, "success": True}

    raise ValueError(f"unsupported operation type: {raw_type}")


def search_and_replace(
    filename: str,
    find_text: str,
    replace_text: str,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """문서에서 텍스트를 치환합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("search_and_replace", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "find_text": find_text,
            "replace_text": replace_text,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    replaced_count = replace_in_doc(doc, find_text=find_text, replace_text=replace_text)
    result = {
        "replaced_count": replaced_count,
        "find_text": find_text,
        "replace_text": replace_text,
    }
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification(result, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification(result, verification),
    )


def batch_replace(
    filename: str,
    replacements: list[dict[str, str]],
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """여러 치환 규칙을 순서대로 적용합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("batch_replace", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "replacements": replacements,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    result = batch_replace_in_doc(doc, replacements)
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification(result, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification(result, verification),
    )


def apply_edits(
    filename: str,
    operations: list[EditOperation],
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
    quality: dict[str, Any] | str | None = None,
) -> dict:
    """여러 편집 operation을 원자적으로 적용합니다. 실패 시 원본 파일은 변경하지 않습니다.

    ``quality``는 저장 게이트 정책입니다(생략 시 transparent = 열림안전만). ``"strict"``
    또는 ``{"mode":"strict","overflowPolicy":"fail","layoutLint":"strict"}`` 처럼 올리면
    SavePipeline이 FormFit/레이아웃/시각 게이트를 적용하고, 실패 시 저장을 보류하며
    ``visualComplete`` 블록과 구조화된 오류 코드를 반환합니다.
    """
    operations_payload = operation_payloads(operations)
    path = resolve_path(filename)
    scope = _idempotency_scope("apply_edits", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "operations": operations_payload,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    if not isinstance(operations, list):
        raise TypeError("operations must be a list")

    doc = open_doc(path)
    operation_results: list[dict[str, Any]] = []
    try:
        for index, operation in enumerate(operations_payload):
            result = _apply_edit_operation(doc, operation, index)
            result["operationIndex"] = index
            operation_results.append(result)
    except Exception as exc:
        return {
            "ok": False,
            "rolledBack": True,
            "dryRun": dry_run,
            "filename": filename,
            "failedOperationIndex": len(operation_results),
            "error": str(exc),
            "operationsApplied": 0,
        }

    result = {
        "ok": True,
        "rolledBack": False,
        "filename": filename,
        "operationsApplied": len(operation_results),
        "operationResults": operation_results,
    }
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification(result, doc, path, quality=quality),
        )
    verification = _save_doc_verification(doc, path, quality=quality)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification(result, verification),
    )


def undo_last_edit(filename: str) -> dict:
    """마지막 저장 전 .bak 백업과 현재 문서를 교체해 직전 편집을 되돌립니다."""
    path = resolve_path(filename)
    return RUNTIME_SERVICES.ops.undo_last_edit(path)


def byte_preserving_patch(
    filename: str,
    patches: list[dict[str, Any]],
    output: str | None = None,
) -> dict:
    """section XML 바이트 splice 기반 문단 텍스트 패치를 적용합니다.

    바이트 보존 fast path: python-hwpx의 ``patch`` → SavePipeline(open-safety)로 게이트되고
    capability handshake로 fail-closed 됩니다. 단, 바이트를 보존하므로 전체 재렌더(VisualComplete
    render) 게이트는 적용되지 않습니다(설계상 카브아웃).
    """
    quality_contract.assert_write_capability()  # fail-closed on capability skew
    return RUNTIME_SERVICES.ops.byte_preserving_patch(filename, patches, output=output)


def add_heading(
    filename: str,
    text: str,
    level: int = 1,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """문서 끝에 제목 문단을 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("add_heading", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "text": text,
            "level": level,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    idx = add_heading_to_doc(doc, text, level)
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification({"paragraph_index": idx}, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification({"paragraph_index": idx}, verification),
    )


def add_paragraph(
    filename: str,
    text: str,
    style: str | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """문서 끝에 문단을 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("add_paragraph", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "text": text,
            "style": style,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    idx = add_paragraph_to_doc(doc, text, style)
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification({"paragraph_index": idx}, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification({"paragraph_index": idx}, verification),
    )


def insert_paragraph(
    filename: str,
    paragraph_index: int,
    text: str,
    style: str | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """지정 위치 앞에 문단을 삽입합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("insert_paragraph", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "paragraph_index": paragraph_index,
            "text": text,
            "style": style,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    idx = insert_paragraph_to_doc(doc, paragraph_index, text, style)
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification({"inserted_index": idx}, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification({"inserted_index": idx}, verification),
    )


def delete_paragraph(
    filename: str,
    paragraph_index: int,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """지정 문단을 삭제합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("delete_paragraph", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "paragraph_index": paragraph_index,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    remaining = delete_paragraph_from_doc(doc, paragraph_index)
    result = {"deleted_index": paragraph_index, "remaining_paragraphs": remaining}
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification(result, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification(result, verification),
    )


def add_table(
    filename: str,
    rows: int,
    cols: int,
    data: list[list[str]] = None,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """문서 끝에 표를 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("add_table", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "rows": rows,
            "cols": cols,
            "data": data,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    idx = add_table_to_doc(doc, rows, cols, data)
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification({"table_index": idx}, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification({"table_index": idx}, verification),
    )


def set_table_cell_text(
    filename: str,
    table_index: int,
    row: int,
    col: int,
    text: str,
    preserve_format: bool = True,
    split_paragraphs: bool = False,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """표 셀 텍스트를 변경합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("set_table_cell_text", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "table_index": table_index,
            "row": row,
            "col": col,
            "text": text,
            "preserve_format": preserve_format,
            "split_paragraphs": split_paragraphs,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    set_cell_text(
        doc,
        table_index,
        row,
        col,
        text,
        preserve_format=preserve_format,
        split_paragraphs=split_paragraphs,
    )
    result = {
        "table_index": table_index,
        "row": row,
        "col": col,
        "text": text,
        "preserve_format": preserve_format,
        "split_paragraphs": split_paragraphs,
    }
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification(result, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification(result, verification),
    )


def add_page_break(
    filename: str,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """문서 끝에 페이지 나누기를 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    add_page_break_to_doc(doc)
    if dry_run:
        return _with_dry_run_verification({"success": True}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"success": True}, verification)


def insert_picture(
    filename: str,
    image_base64: str,
    image_format: str = "png",
    width: int | None = None,
    height: int | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    section_index: int | None = None,
    align: str | None = None,
    output: str | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """본문에 그림 객체를 삽입하고 BinData/manifest 참조를 함께 저장합니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    target_path = resolve_path(output) if output else path
    doc = open_doc(path)
    image_data = _decode_image_base64(image_base64)
    doc.add_picture(
        image_data,
        image_format,
        width=width,
        height=height,
        width_mm=width_mm,
        height_mm=height_mm,
        section_index=section_index,
        align=align,
    )
    picture_refs = doc.picture_references()
    result = {
        "ok": True,
        "filename": filename,
        "outputPath": target_path,
        "picture": picture_refs[-1] if picture_refs else None,
        "pictureReferences": picture_refs,
        "idIntegrity": _id_integrity_payload(doc),
    }
    if dry_run:
        return _with_dry_run_verification(result, doc, target_path)
    verification = _save_doc_verification(doc, target_path)
    return _with_save_verification(result, verification)


def replace_picture(
    filename: str,
    image_base64: str,
    image_format: str = "png",
    picture_index: int = 0,
    binary_item_id_ref: str | None = None,
    remove_orphaned: bool = True,
    output: str | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """그림 객체의 geometry를 유지하고 연결된 이미지 asset만 교체합니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    target_path = resolve_path(output) if output else path
    doc = open_doc(path)
    image_data = _decode_image_base64(image_base64)
    replacement = doc.replace_picture(
        image_data,
        image_format,
        picture_index=picture_index,
        binary_item_id_ref=binary_item_id_ref,
        remove_orphaned=remove_orphaned,
    )
    result = {
        "ok": True,
        "filename": filename,
        "outputPath": target_path,
        "replacement": replacement,
        "pictureReferences": doc.picture_references(),
        "idIntegrity": _id_integrity_payload(doc),
    }
    if dry_run:
        return _with_dry_run_verification(result, doc, target_path)
    verification = _save_doc_verification(doc, target_path)
    return _with_save_verification(result, verification)


def _anchor_position(anchor: dict[str, Any] | str) -> int | None:
    if isinstance(anchor, dict):
        value = anchor.get("position")
        if value is None:
            return None
        return int(value)
    if isinstance(anchor, str) and "@" in anchor:
        return int(anchor.rsplit("@", 1)[1])
    return None


def _replace_visible_span_in_runs(
    runs: list[Any],
    start: int,
    end: int,
    replacement: str,
) -> int:
    if start < 0 or end < start:
        raise ValueError("invalid replacement span")

    boundaries: list[tuple[int, int, Any]] = []
    cursor = 0
    for run in runs:
        text = run.text or ""
        next_cursor = cursor + len(text)
        boundaries.append((cursor, next_cursor, run))
        cursor = next_cursor

    affected = [
        (run_start, run_end, run)
        for run_start, run_end, run in boundaries
        if start < run_end and end > run_start
    ]
    if not affected:
        return 0

    first_start, first_end, first_run = affected[0]
    last_start, last_end, last_run = affected[-1]
    first_text = first_run.text or ""
    last_text = last_run.text or ""
    prefix = first_text[: max(0, start - first_start)]
    suffix = last_text[max(0, end - last_start) :]

    first_run.text = prefix + replacement + (suffix if first_run is last_run else "")
    for _, _, run in affected[1:-1]:
        run.text = ""
    if last_run is not first_run:
        last_run.text = suffix
    return 1


def replace_in_paragraph(
    filename: str,
    old_text: str,
    new_text: str,
    paragraph_index: int | None = None,
    location: dict[str, Any] | None = None,
    count: int | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """본문/표 셀 문단 하나에서 run 서식을 유지하며 부분 텍스트를 치환합니다."""
    if old_text == "":
        raise ValueError("old_text는 빈 문자열일 수 없습니다.")
    if count is not None and count <= 0:
        return {
            "replaced_count": 0,
            "location": location or {"paragraph_index": paragraph_index},
            "dryRun": dry_run,
        }

    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    resolved = resolve_paragraph_reference(
        doc, paragraph_index=paragraph_index, location=location
    )
    paragraph = resolved.paragraph
    runs = list(getattr(paragraph, "runs", []))
    before_run_texts = [run.text or "" for run in runs]

    if count is None:
        replaced = _replace_in_runs(runs, old_text, new_text) if runs else 0
    else:
        replaced = 0
        for run in runs:
            remaining = count - replaced
            if remaining <= 0:
                break
            if not (run.text or ""):
                continue
            if hasattr(run, "replace_text"):
                replaced += int(run.replace_text(old_text, new_text, count=remaining))
            else:
                before = run.text or ""
                after = before.replace(old_text, new_text, remaining)
                if after != before:
                    run.text = after
                    replaced += before.count(old_text) - after.count(old_text)

    if replaced == 0 and not runs:
        before = paragraph.text or ""
        limit = -1 if count is None else count
        after = before.replace(old_text, new_text, limit)
        if after != before:
            paragraph.text = after
            replaced = (
                before.count(old_text)
                if count is None
                else min(before.count(old_text), count)
            )

    if replaced:
        changed_runs = [
            run
            for index, run in enumerate(getattr(paragraph, "runs", []) or [])
            if (run.text or "")
            and (
                index >= len(before_run_texts)
                or (run.text or "") != before_run_texts[index]
            )
        ]
        repair_pathological_text_spacing(
            doc,
            paragraph=paragraph,
            runs=changed_runs,
        )
        result = {"replaced_count": replaced, "location": resolved.location}
        if dry_run:
            return _with_dry_run_verification(result, doc, path)
        verification = _save_doc_verification(doc, path)
        return _with_save_verification(result, verification)
    return {
        "replaced_count": replaced,
        "location": resolved.location,
        "dryRun": dry_run,
    }


def replace_by_anchor(
    filename: str,
    anchor: dict[str, Any] | str,
    old_text: str,
    new_text: str,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """find_text가 반환한 anchor 위치에서 run 서식을 유지하며 텍스트를 치환합니다."""
    if old_text == "":
        raise ValueError("old_text는 빈 문자열일 수 없습니다.")

    location = location_from_anchor(anchor)
    position = _anchor_position(anchor)
    if position is None:
        return replace_in_paragraph(
            filename,
            old_text,
            new_text,
            location=location,
            count=1,
            dry_run=dry_run,
            expected_revision=expected_revision,
        )

    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    resolved = resolve_paragraph_reference(doc, location=location)
    paragraph = resolved.paragraph
    before = paragraph.text or ""
    end = position + len(old_text)
    if before[position:end] != old_text:
        raise ValueError("anchor position does not match old_text")

    runs = list(getattr(paragraph, "runs", []))
    before_run_texts = [run.text or "" for run in runs]
    if runs:
        replaced = _replace_visible_span_in_runs(runs, position, end, new_text)
    else:
        paragraph.text = before[:position] + new_text + before[end:]
        replaced = 1

    changed_runs = [
        run
        for index, run in enumerate(getattr(paragraph, "runs", []) or [])
        if (run.text or "")
        and (
            index >= len(before_run_texts)
            or (run.text or "") != before_run_texts[index]
        )
    ]
    repair_pathological_text_spacing(
        doc,
        paragraph=paragraph,
        runs=changed_runs,
    )

    result = {
        "replaced_count": replaced,
        "location": resolved.location,
        "position": position,
    }
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


def merge_table_cells(
    filename: str,
    table_index: int,
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """표 셀 범위를 병합합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    merge_cells_in_table(doc, table_index, start_row, start_col, end_row, end_col)
    result = {
        "merged": True,
        "range": f"({start_row},{start_col})~({end_row},{end_col})",
    }
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


def split_table_cell(
    filename: str,
    table_index: int,
    row: int,
    col: int,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """병합된 셀을 분할합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    span_info = split_cell_in_table(doc, table_index, row, col)
    if dry_run:
        return _with_dry_run_verification(
            {"split": True, "original_span": span_info}, doc, path
        )
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(
        {"split": True, "original_span": span_info}, verification
    )


def format_table(
    filename: str,
    table_index: int,
    has_header_row: bool = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """표 서식을 적용합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    format_table_in_doc(doc, table_index, has_header_row=has_header_row)
    if dry_run:
        return _with_dry_run_verification(
            {"formatted": True, "table_index": table_index}, doc, path
        )
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(
        {"formatted": True, "table_index": table_index}, verification
    )


def plan_edit(filename: str, instruction: str) -> dict:
    """[고급] instruction 기준 검증용 편집 계획을 생성합니다."""
    path = resolve_path(filename)
    operation = _build_verification_plan_operation(path, instruction)
    return RUNTIME_SERVICES.ops.plan_edit(path=path, operations=[operation])


def preview_edit(filename: str, plan_id: str) -> dict:
    """[고급] plan_edit 결과 미리보기를 조회합니다."""
    del filename
    return RUNTIME_SERVICES.ops.preview_edit(plan_id=plan_id)


def apply_edit(filename: str, plan_id: str) -> dict:
    """[고급] 검증 계획을 적용합니다. 원본 HWPX는 직접 수정하지 않습니다."""
    del filename
    return RUNTIME_SERVICES.ops.apply_edit(plan_id=plan_id, confirm=True)


__all__ = [
    "add_heading",
    "add_paragraph",
    "insert_paragraph",
    "delete_paragraph",
    "add_page_break",
    "apply_edits",
    "plan_edit",
    "preview_edit",
    "apply_edit",
    "undo_last_edit",
    "replace_by_anchor",
    "replace_in_paragraph",
    "search_and_replace",
    "batch_replace",
    "byte_preserving_patch",
    "insert_picture",
    "replace_picture",
    "add_table",
    "set_table_cell_text",
    "merge_table_cells",
    "split_table_cell",
    "format_table",
    "table_compute",
]
