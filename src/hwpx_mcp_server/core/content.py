"""문단, 표, 메모 CRUD 로직."""

from __future__ import annotations

import logging
from typing import Any

from hwpx.document import HwpxDocument

logger = logging.getLogger(__name__)


def _iter_tables(doc: HwpxDocument):
    seen: set[int] = set()
    for paragraph in doc.paragraphs:
        for table in getattr(paragraph, "tables", []):
            key = id(table)
            if key not in seen:
                seen.add(key)
                yield table


def _resolve_style(style: str | None) -> str | None:
    if style is None:
        return None
    value = style.strip()
    return value or None


def _ensure_lxml_et_for_memo() -> None:
    """python-hwpx memo API의 ET 타입 불일치를 보정한다."""
    import hwpx.oxml.document as oxml_document
    from lxml import etree

    oxml_document.ET = etree


# ── 문단 ──────────────────────────────────────────────

def add_heading_to_doc(doc: HwpxDocument, text: str, level: int = 1) -> int:
    """문서 끝에 제목(헤딩) 문단을 추가한다. 추가된 paragraph_index를 반환."""
    safe_level = min(6, max(1, int(level)))
    stripped = (text or "").strip()
    heading_text = stripped if stripped.startswith("#") else f"{'#' * safe_level} {stripped}"
    doc.add_paragraph(heading_text)
    return len(doc.paragraphs) - 1


def add_paragraph_to_doc(doc: HwpxDocument, text: str, style: str = None) -> int:
    """문서 끝에 일반 문단을 추가한다. 추가된 paragraph_index를 반환."""
    style_id = _resolve_style(style)
    doc.add_paragraph(text or "", style_id_ref=style_id)
    return len(doc.paragraphs) - 1


def insert_paragraph_to_doc(doc: HwpxDocument, paragraph_index: int, text: str, style: str = None) -> int:
    """지정 위치 앞에 문단을 삽입한다. 삽입된 paragraph_index를 반환."""
    total = len(doc.paragraphs)
    if paragraph_index < 0 or paragraph_index > total:
        raise ValueError(f"유효하지 않은 paragraph_index: {paragraph_index}")

    if paragraph_index == total:
        return add_paragraph_to_doc(doc, text, style)

    target = doc.paragraphs[paragraph_index]
    style_id = _resolve_style(style)
    inserted = target.section.add_paragraph(text or "", style_id_ref=style_id)

    target_parent = target.element.getparent()
    if target_parent is None:
        raise RuntimeError("대상 문단의 부모 요소를 찾을 수 없습니다.")

    inserted_parent = inserted.element.getparent()
    if inserted_parent is None:
        raise RuntimeError("삽입 문단의 부모 요소를 찾을 수 없습니다.")

    inserted_parent.remove(inserted.element)
    target_parent.insert(target_parent.index(target.element), inserted.element)
    return paragraph_index


def delete_paragraph_from_doc(doc: HwpxDocument, paragraph_index: int) -> int:
    """지정 문단 내용을 비워 삭제 효과를 낸다. 남은 문단 수를 반환."""
    paragraphs = doc.paragraphs
    total = len(paragraphs)
    if paragraph_index < 0 or paragraph_index >= total:
        raise ValueError(f"유효하지 않은 paragraph_index: {paragraph_index}")
    target = paragraphs[paragraph_index]
    for run in target.runs:
        run.text = ""
    for table in getattr(target, "tables", []):
        for row in table.rows:
            for cell in row.cells:
                cell.text = ""
    return total


# ── 표 ────────────────────────────────────────────────

def add_table_to_doc(doc: HwpxDocument, rows: int, cols: int, data: list[list[str]] = None) -> int:
    """문서 끝에 표를 추가한다. 추가된 table_index를 반환."""
    if rows <= 0 or cols <= 0:
        raise ValueError("rows와 cols는 1 이상이어야 합니다.")
    table = doc.add_table(rows=rows, cols=cols)
    payload = data or []
    for r in range(min(rows, len(payload))):
        row_data = payload[r] or []
        for c in range(min(cols, len(row_data))):
            table.rows[r].cells[c].text = str(row_data[c])
    return len(list(_iter_tables(doc))) - 1


def get_table_data(doc: HwpxDocument, table_index: int) -> dict:
    """표의 모든 셀 텍스트를 2D 배열로 반환한다."""
    tables = list(_iter_tables(doc))
    if table_index < 0 or table_index >= len(tables):
        raise ValueError(f"유효하지 않은 table_index: {table_index}")
    table = tables[table_index]
    data = [[cell.text or "" for cell in row.cells] for row in table.rows]
    rows = len(data)
    cols = len(data[0]) if data else 0
    return {"rows": rows, "cols": cols, "data": data}


def set_cell_text(doc: HwpxDocument, table_index: int, row: int, col: int, text: str) -> None:
    """표의 특정 셀 텍스트를 변경한다."""
    tables = list(_iter_tables(doc))
    if table_index < 0 or table_index >= len(tables):
        raise ValueError(f"유효하지 않은 table_index: {table_index}")
    table = tables[table_index]
    if row < 0 or row >= len(table.rows):
        raise ValueError(f"유효하지 않은 row: {row}")
    if col < 0 or col >= len(table.rows[row].cells):
        raise ValueError(f"유효하지 않은 col: {col}")
    table.rows[row].cells[col].text = text or ""


# ── 메모 ──────────────────────────────────────────────

def add_memo_to_doc(doc: HwpxDocument, paragraph_index: int, text: str) -> None:
    """문단에 메모를 추가한다."""
    paragraphs = doc.paragraphs
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        raise ValueError(f"유효하지 않은 paragraph_index: {paragraph_index}")

    _ensure_lxml_et_for_memo()
    paragraph = paragraphs[paragraph_index]
    doc.add_memo_with_anchor(text or "", paragraph=paragraph)


def remove_memo_from_doc(doc: HwpxDocument, paragraph_index: int) -> None:
    """문단의 메모를 제거한다."""
    paragraphs = doc.paragraphs
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        raise ValueError(f"유효하지 않은 paragraph_index: {paragraph_index}")
    paragraph = paragraphs[paragraph_index]

    memo_ids: set[str] = set()
    for run in paragraph.runs:
        for ctrl in run.element.findall("{http://www.hancom.co.kr/hwpml/2011/paragraph}ctrl"):
            field_begin = ctrl.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}fieldBegin")
            if field_begin is None:
                continue
            command = (field_begin.get("command") or "")
            if "memoId=" in command:
                memo_id = command.split("memoId=", 1)[1].split(";", 1)[0].strip()
                if memo_id:
                    memo_ids.add(memo_id)

    for memo in list(doc.memos):
        if memo.id in memo_ids:
            doc.remove_memo(memo)


# ── 페이지 ────────────────────────────────────────────

def add_page_break_to_doc(doc: HwpxDocument) -> None:
    """문서 끝에 페이지 나누기를 추가한다."""
    doc.add_paragraph("", pageBreak="1")


# ── 텍스트 수집 ───────────────────────────────────────

def iter_all_paragraphs(doc: Any):
    for paragraph in doc.paragraphs:
        yield paragraph


def iter_table_texts(doc: Any):
    for table in _iter_tables(doc):
        for row in table.rows:
            for cell in row.cells:
                yield cell.text or ""


def collect_full_text(doc: Any) -> str:
    chunks: list[str] = []
    for paragraph in iter_all_paragraphs(doc):
        chunks.append(paragraph.text or "")
    for text in iter_table_texts(doc):
        if text:
            chunks.append(text)
    return "\n".join(chunks)
