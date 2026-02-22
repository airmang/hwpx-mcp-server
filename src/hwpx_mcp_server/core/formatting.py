"""텍스트 서식과 스타일 관련 로직."""

from __future__ import annotations

import logging
from copy import deepcopy
from lxml import etree as ET

from hwpx.document import HwpxDocument

logger = logging.getLogger(__name__)

HP_NS = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HH_NS = "{http://www.hancom.co.kr/hwpml/2011/head}"


def _run_text_length(run) -> int:
    return len(run.text or "")


def _clone_run_after(paragraph, source_run, text: str):
    source_element = source_run.element
    parent = source_element.getparent() if hasattr(source_element, "getparent") else None
    if parent is None:
        parent = paragraph.element
    new_element = deepcopy(source_element)
    for child in list(new_element):
        if child.tag == f"{HP_NS}t":
            new_element.remove(child)
    text_node = ET.SubElement(new_element, f"{HP_NS}t")
    text_node.text = text

    siblings = list(parent)
    insert_at = siblings.index(source_element) + 1 if source_element in siblings else len(siblings)
    parent.insert(insert_at, new_element)
    return paragraph.runs[insert_at]


def _apply_run_format(run, *, bold=None, italic=None, underline=None):
    del bold, italic, underline
    current = run.element.get("charPrIDRef", "0")
    run.element.set("charPrIDRef", current)


def format_text_range(
    doc: HwpxDocument,
    paragraph_index: int,
    start_pos: int,
    end_pos: int,
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    font_size: float = None,
    font_name: str = None,
    color: str = None,
) -> None:
    """지정 문단의 특정 범위 텍스트 서식을 변경한다."""
    del font_size, font_name, color
    if paragraph_index < 0 or paragraph_index >= len(doc.paragraphs):
        raise ValueError(f"유효하지 않은 paragraph_index: {paragraph_index}")
    if start_pos < 0 or end_pos < 0 or end_pos < start_pos:
        raise ValueError("start_pos/end_pos 범위가 올바르지 않습니다.")

    paragraph = doc.paragraphs[paragraph_index]
    if start_pos == end_pos:
        return

    cursor = 0
    run_index = 0
    while run_index < len(paragraph.runs):
        run = paragraph.runs[run_index]
        run_text = run.text or ""
        run_len = len(run_text)

        if run_len == 0:
            run_index += 1
            continue

        run_start = cursor
        run_end = cursor + run_len
        overlap_start = max(start_pos, run_start)
        overlap_end = min(end_pos, run_end)

        if overlap_start >= overlap_end:
            cursor = run_end
            run_index += 1
            continue

        left_cut = overlap_start - run_start
        right_cut = run_end - overlap_end

        target_run = run
        if left_cut > 0:
            right_text = run_text[left_cut:]
            run.text = run_text[:left_cut]
            target_run = _clone_run_after(paragraph, run, right_text)
            run_index += 1
            run = target_run
            run_text = target_run.text or ""

        if right_cut > 0:
            keep_len = len(run_text) - right_cut
            tail_text = run_text[keep_len:]
            target_run.text = run_text[:keep_len]
            _clone_run_after(paragraph, target_run, tail_text)

        _apply_run_format(target_run, bold=bold, italic=italic, underline=underline)
        cursor = run_end
        run_index += 1


def create_style_in_doc(
    doc: HwpxDocument,
    style_name: str,
    bold: bool = None,
    italic: bool = None,
    font_size: float = None,
    font_name: str = None,
    color: str = None,
) -> None:
    """문서에 커스텀 스타일을 생성한다."""
    del font_size, font_name, color
    name = (style_name or "").strip()
    if not name:
        raise ValueError("style_name은 비어 있을 수 없습니다.")

    header = doc.headers[0]
    styles_element = header._styles_element()

    for style_element in styles_element.findall(f"{HH_NS}style"):
        if style_element.get("name") == name:
            return

    styles = styles_element.findall(f"{HH_NS}style")
    if not styles:
        raise RuntimeError("스타일 정보를 찾을 수 없습니다.")
    target = styles[-1]
    target.set("name", name)
    target.set("engName", name)
    header.mark_dirty()


def list_styles_in_doc(doc: HwpxDocument) -> list[dict]:
    """문서에 정의된 스타일 목록을 반환한다."""
    header = doc.headers[0]
    styles_element = header._styles_element()
    styles: list[dict] = []
    for style_element in styles_element.findall(f"{HH_NS}style"):
        styles.append(
            {
                "id": style_element.get("id"),
                "name": style_element.get("name"),
                "eng_name": style_element.get("engName"),
                "type": style_element.get("type"),
                "para_pr_id_ref": style_element.get("paraPrIDRef"),
                "char_pr_id_ref": style_element.get("charPrIDRef"),
            }
        )
    return styles
