"""문단, 표, 메모 CRUD 로직."""

from __future__ import annotations

from datetime import datetime
import logging
import shutil
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree as ET

from hwpx.document import HwpxDocument

from ..compat import patch_python_hwpx

logger = logging.getLogger(__name__)

_HP_NS = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def _iter_tables(doc: HwpxDocument):
    for paragraph in doc.paragraphs:
        for table in getattr(paragraph, "tables", []):
            yield table


def _resolve_style(style: str | None) -> str | None:
    if style is None:
        return None
    value = style.strip()
    return value or None


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
    section = target.section
    style_id = _resolve_style(style)
    inserted = section.add_paragraph(text or "", style_id_ref=style_id)

    section_element = section.element
    try:
        target_position = list(section_element).index(target.element)
    except ValueError as exc:
        raise RuntimeError("대상 문단 요소를 섹션에서 찾을 수 없습니다.") from exc

    # add_paragraph는 항상 끝에 붙으므로, 생성한 요소를 제거한 뒤 목표 위치에 재삽입한다.
    section_element.remove(inserted.element)
    section_element.insert(target_position, inserted.element)
    return paragraph_index


def delete_paragraph_from_doc(doc: HwpxDocument, paragraph_index: int) -> int:
    """지정 문단을 실제 제거한다. 남은 문단 수를 반환."""
    paragraphs = doc.paragraphs
    total = len(paragraphs)
    if paragraph_index < 0 or paragraph_index >= total:
        raise ValueError(f"유효하지 않은 paragraph_index: {paragraph_index}")
    if total <= 1:
        # 최소 1개 문단은 유지해 문서 구조를 보존한다.
        target = paragraphs[paragraph_index]
        for run in target.runs:
            run.text = ""
        return total

    target = paragraphs[paragraph_index]
    section = target.section
    section_element = section.element
    try:
        section_element.remove(target.element)
    except ValueError as exc:
        raise RuntimeError("삭제할 문단 요소를 섹션에서 찾을 수 없습니다.") from exc
    section.mark_dirty()
    return total - 1


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


def merge_cells_in_table(
    doc: HwpxDocument,
    table_index: int,
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
) -> None:
    """표의 셀을 병합한다."""
    tables = list(_iter_tables(doc))
    if table_index < 0 or table_index >= len(tables):
        raise ValueError(f"유효하지 않은 table_index: {table_index}")
    if start_row > end_row or start_col > end_col:
        raise ValueError("시작 좌표는 종료 좌표보다 작거나 같아야 합니다.")

    table = tables[table_index]
    if start_row < 0 or end_row >= len(table.rows):
        raise ValueError("유효하지 않은 row 범위입니다.")
    if start_col < 0 or end_col >= len(table.rows[start_row].cells):
        raise ValueError("유효하지 않은 col 범위입니다.")

    anchor = table.rows[start_row].cells[start_col]
    anchor_span = anchor.element.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}cellSpan")
    if anchor_span is None:
        raise RuntimeError("anchor 셀에서 cellSpan 요소를 찾을 수 없습니다.")
    anchor_span.set("rowSpan", str(end_row - start_row + 1))
    anchor_span.set("colSpan", str(end_col - start_col + 1))

    for row in range(start_row, end_row + 1):
        for col in range(start_col, end_col + 1):
            if row == start_row and col == start_col:
                continue
            cell = table.rows[row].cells[col]
            span = cell.element.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}cellSpan")
            if span is not None:
                span.set("rowSpan", "0")
                span.set("colSpan", "0")
            cell.text = ""


def split_cell_in_table(doc: HwpxDocument, table_index: int, row: int, col: int) -> dict:
    """병합된 셀을 분할한다. 원래 span 정보를 반환한다."""
    tables = list(_iter_tables(doc))
    if table_index < 0 or table_index >= len(tables):
        raise ValueError(f"유효하지 않은 table_index: {table_index}")
    table = tables[table_index]
    if row < 0 or row >= len(table.rows):
        raise ValueError(f"유효하지 않은 row: {row}")
    if col < 0 or col >= len(table.rows[row].cells):
        raise ValueError(f"유효하지 않은 col: {col}")

    cell = table.rows[row].cells[col]
    span = cell.element.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}cellSpan")
    if span is None:
        return {"rowSpan": 1, "colSpan": 1}

    original = {
        "rowSpan": int(span.get("rowSpan", "1")),
        "colSpan": int(span.get("colSpan", "1")),
    }
    span.set("rowSpan", "1")
    span.set("colSpan", "1")

    for r in range(row, row + max(1, original["rowSpan"])):
        if r >= len(table.rows):
            break
        for c in range(col, col + max(1, original["colSpan"])):
            if c >= len(table.rows[r].cells) or (r == row and c == col):
                continue
            child = table.rows[r].cells[c]
            child_span = child.element.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}cellSpan")
            if child_span is not None:
                child_span.set("rowSpan", "1")
                child_span.set("colSpan", "1")
    return original


def format_table_in_doc(doc: HwpxDocument, table_index: int, has_header_row: bool = None) -> None:
    """표 서식을 변경한다. 헤더 행 강조 등."""
    if has_header_row is None:
        return
    tables = list(_iter_tables(doc))
    if table_index < 0 or table_index >= len(tables):
        raise ValueError(f"유효하지 않은 table_index: {table_index}")
    table = tables[table_index]
    if not table.rows:
        return
    for cell in table.rows[0].cells:
        for paragraph in getattr(cell, "paragraphs", []):
            for run in paragraph.runs:
                run.bold = bool(has_header_row)


def copy_document_file(source: str, destination: str = None) -> str:
    """문서를 복사한다. destination이 None이면 자동 이름 생성."""
    if destination is None:
        stem, ext = source.rsplit(".", 1) if "." in source else (source, "hwpx")
        destination = f"{stem}_copy.{ext}"
    shutil.copy2(source, destination)
    return destination


# ── 메모 ──────────────────────────────────────────────

def _looks_like_mixed_xml_type_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        message = str(current)
        if "lxml.etree._Element" in message and "ElementTree.Element" in message:
            return True
        if "SubElement() argument 1 must be" in message and "xml.etree.ElementTree.Element" in message:
            return True
        current = current.__cause__
    return False


def _append_child_element(parent: Any, tag: str, attrs: dict[str, str] | None = None) -> Any:
    payload = dict(attrs or {})
    try:
        return ET.SubElement(parent, tag, payload)
    except TypeError:
        maker = getattr(parent, "makeelement", None)
        if not callable(maker):
            raise
        child = maker(tag, payload)
        parent.append(child)
        return child


def _make_element_like(parent: Any, tag: str, attrs: dict[str, str] | None = None) -> Any:
    payload = dict(attrs or {})
    maker = getattr(parent, "makeelement", None)
    if callable(maker):
        return maker(tag, payload)
    return ET.Element(tag, payload)


def _add_memo_with_anchor_fallback(paragraph: Any, text: str) -> None:
    section = paragraph.section
    section_element = section.element
    memo_group = section_element.find(f"{_HP_NS}memogroup")
    if memo_group is None:
        memo_group = _append_child_element(section_element, f"{_HP_NS}memogroup")

    memo_id = uuid4().hex[:10]
    memo_element = _append_child_element(memo_group, f"{_HP_NS}memo", {"id": memo_id})
    para_list = _append_child_element(memo_element, f"{_HP_NS}paraList")
    memo_para = _append_child_element(
        para_list,
        f"{_HP_NS}p",
        {
            "id": f"memo-{memo_id}-p",
            "paraPrIDRef": "0",
            "styleIDRef": "0",
            "pageBreak": "0",
            "columnBreak": "0",
            "merged": "0",
        },
    )

    char_ref = str(paragraph.char_pr_id_ref or "0")
    memo_run = _append_child_element(memo_para, f"{_HP_NS}run", {"charPrIDRef": char_ref})
    _append_child_element(memo_run, f"{_HP_NS}t").text = text

    field_id = uuid4().hex
    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_begin = _make_element_like(paragraph.element, f"{_HP_NS}run", {"charPrIDRef": char_ref})
    ctrl_begin = _append_child_element(run_begin, f"{_HP_NS}ctrl")
    field_begin = _append_child_element(
        ctrl_begin,
        f"{_HP_NS}fieldBegin",
        {
            "id": field_id,
            "type": "MEMO",
            "editable": "true",
            "dirty": "false",
            "fieldid": field_id,
            "command": f"memoId={memo_id};",
        },
    )

    parameters = _append_child_element(field_begin, f"{_HP_NS}parameters", {"count": "5", "name": ""})
    _append_child_element(parameters, f"{_HP_NS}stringParam", {"name": "ID"}).text = memo_id
    _append_child_element(parameters, f"{_HP_NS}integerParam", {"name": "Number"}).text = "1"
    _append_child_element(parameters, f"{_HP_NS}stringParam", {"name": "CreateDateTime"}).text = created
    _append_child_element(parameters, f"{_HP_NS}stringParam", {"name": "Author"}).text = ""
    _append_child_element(parameters, f"{_HP_NS}stringParam", {"name": "MemoShapeID"}).text = ""

    sub_list = _append_child_element(
        field_begin,
        f"{_HP_NS}subList",
        {
            "id": f"memo-field-{memo_id}",
            "textDirection": "HORIZONTAL",
            "lineWrap": "BREAK",
            "vertAlign": "TOP",
        },
    )
    sub_para = _append_child_element(
        sub_list,
        f"{_HP_NS}p",
        {
            "id": f"memo-field-{memo_id}-p",
            "paraPrIDRef": "0",
            "styleIDRef": "0",
            "pageBreak": "0",
            "columnBreak": "0",
            "merged": "0",
        },
    )
    sub_run = _append_child_element(sub_para, f"{_HP_NS}run", {"charPrIDRef": char_ref})
    _append_child_element(sub_run, f"{_HP_NS}t").text = memo_id

    run_end = _make_element_like(paragraph.element, f"{_HP_NS}run", {"charPrIDRef": char_ref})
    ctrl_end = _append_child_element(run_end, f"{_HP_NS}ctrl")
    _append_child_element(ctrl_end, f"{_HP_NS}fieldEnd", {"beginIDRef": field_id, "fieldid": field_id})

    paragraph.element.insert(0, run_begin)
    paragraph.element.append(run_end)
    section.mark_dirty()


def _extract_memo_id_from_field_begin(field_begin: Any) -> str | None:
    command = (field_begin.get("command") or "")
    if "memoId=" in command:
        memo_id = command.split("memoId=", 1)[1].split(";", 1)[0].strip()
        if memo_id:
            return memo_id

    parameters = field_begin.find(f"{_HP_NS}parameters")
    if parameters is None:
        return None
    for item in parameters.findall(f"{_HP_NS}stringParam"):
        if (item.get("name") or "").strip().lower() != "id":
            continue
        memo_id = (item.text or "").strip()
        if memo_id:
            return memo_id
    return None


def add_memo_to_doc(doc: HwpxDocument, paragraph_index: int, text: str) -> None:
    """문단에 메모를 추가한다."""
    patch_python_hwpx()
    paragraphs = doc.paragraphs
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        raise ValueError(f"유효하지 않은 paragraph_index: {paragraph_index}")

    paragraph = paragraphs[paragraph_index]
    try:
        doc.add_memo_with_anchor(text or "", paragraph=paragraph)
    except Exception as exc:  # noqa: BLE001
        if not _looks_like_mixed_xml_type_error(exc):
            raise
        logger.warning("메모 추가 중 혼합 XML 타입 충돌 감지, fallback 경로 사용: %s", exc)
        _add_memo_with_anchor_fallback(paragraph, text or "")


def remove_memo_from_doc(doc: HwpxDocument, paragraph_index: int) -> None:
    """문단의 메모를 제거한다."""
    patch_python_hwpx()
    paragraphs = doc.paragraphs
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        raise ValueError(f"유효하지 않은 paragraph_index: {paragraph_index}")
    paragraph = paragraphs[paragraph_index]

    memo_ids: set[str] = set()
    for run in paragraph.runs:
        for ctrl in run.element.findall(f"{_HP_NS}ctrl"):
            field_begin = ctrl.find(f"{_HP_NS}fieldBegin")
            if field_begin is None:
                continue
            memo_id = _extract_memo_id_from_field_begin(field_begin)
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
