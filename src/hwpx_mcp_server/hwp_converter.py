"""HWP(.hwp) -> HWPX(.hwpx) 변환 유틸리티."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from hwpx.document import HwpxDocument

from .hwp_support import HwpBinaryError, extract_hwp_text


class HwpConversionError(RuntimeError):
    """HWP 변환 과정에서 발생하는 오류."""


@dataclass(slots=True)
class TablePayload:
    """변환 대상 표 데이터."""

    rows: List[List[str]]


@dataclass(slots=True)
class ConversionResult:
    """HWP -> HWPX 변환 결과 요약."""

    success: bool
    output_path: str
    paragraphs_converted: int
    tables_converted: int
    skipped_elements: List[str]
    warnings: List[str]


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _iter_by_names(root: ET.Element, names: Sequence[str]) -> Iterable[ET.Element]:
    wanted = {name.lower() for name in names}
    for element in root.iter():
        if _local_name(element.tag).lower() in wanted:
            yield element


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _collect_paragraph_text(paragraph_element: ET.Element) -> str:
    text_chunks: List[str] = []
    for element in paragraph_element.iter():
        local = _local_name(element.tag).lower()
        if local in {"text", "t", "char"} and element.text:
            normalized = _normalize_text(element.text)
            if normalized:
                text_chunks.append(normalized)
    if not text_chunks:
        for part in paragraph_element.itertext():
            normalized = _normalize_text(part)
            if normalized:
                text_chunks.append(normalized)
    return " ".join(text_chunks).strip()


def _parse_table_rows(table_element: ET.Element) -> TablePayload | None:
    row_elements = list(_iter_by_names(table_element, ["TableRow", "tr", "row"]))
    rows: List[List[str]] = []

    if row_elements:
        for row_element in row_elements:
            row_cells: List[str] = []
            cell_elements = list(_iter_by_names(row_element, ["TableCell", "tc", "cell", "td"]))
            for cell_element in cell_elements:
                text = _collect_paragraph_text(cell_element)
                row_cells.append(text)
            if row_cells:
                rows.append(row_cells)
    else:
        cells = list(_iter_by_names(table_element, ["TableCell", "tc", "cell", "td"]))
        if cells:
            rows.append([_collect_paragraph_text(cell) for cell in cells])

    if not rows:
        return None

    max_cols = max(len(row) for row in rows)
    for row in rows:
        if len(row) < max_cols:
            row.extend([""] * (max_cols - len(row)))
    return TablePayload(rows=rows)


def _parse_hwp5proc_xml(xml_text: str) -> tuple[List[str], List[TablePayload], List[str]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise HwpConversionError(f"hwp5proc XML 파싱 실패: {exc}") from exc

    paragraphs: List[str] = []
    tables: List[TablePayload] = []
    warnings: List[str] = []
    parent_map = {child: parent for parent in root.iter() for child in parent}

    def _inside_table(node: ET.Element) -> bool:
        current = parent_map.get(node)
        while current is not None:
            if _local_name(current.tag).lower() in {"tablecontrol", "tbl", "table"}:
                return True
            current = parent_map.get(current)
        return False

    for paragraph_element in _iter_by_names(root, ["Paragraph", "p", "para"]):
        if _inside_table(paragraph_element):
            continue
        text = _collect_paragraph_text(paragraph_element)
        if text:
            paragraphs.append(text)

    for table_element in _iter_by_names(root, ["TableControl", "tbl", "table"]):
        parsed = _parse_table_rows(table_element)
        if parsed is not None:
            tables.append(parsed)

    if not paragraphs:
        warnings.append("문단 노드를 직접 매핑하지 못해 보조 텍스트 추출로 대체될 수 있습니다.")

    return paragraphs, tables, warnings


def _run_hwp5proc_xml(hwp_path: Path) -> str:
    try:
        completed = subprocess.run(
            ["hwp5proc", "xml", "--format", "nested", str(hwp_path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError as exc:
        raise HwpConversionError("hwp5proc 명령을 찾을 수 없습니다. pyhwp 설치 여부를 확인하세요.") from exc

    if completed.returncode != 0:
        stderr = completed.stderr.strip() or "unknown error"
        raise HwpConversionError(f"hwp5proc 실행 실패: {stderr}")

    xml_text = completed.stdout.strip()
    if not xml_text:
        raise HwpConversionError("hwp5proc XML 출력이 비어 있습니다")
    return xml_text


def convert_hwp_to_hwpx(hwp_path: str, output_path: str) -> ConversionResult:
    source = Path(hwp_path).expanduser().resolve()
    target = Path(output_path).expanduser().resolve()

    if source.suffix.lower() != ".hwp":
        raise HwpConversionError("source는 .hwp 파일이어야 합니다")
    if not source.exists():
        raise HwpConversionError(f"입력 파일이 존재하지 않습니다: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)

    xml_text = _run_hwp5proc_xml(source)
    paragraphs, tables, warnings = _parse_hwp5proc_xml(xml_text)

    skipped_elements: List[str] = []
    unsupported_pairs = [
        ("OLE 개체", ["OLE", "OleObject"]),
        ("각주/미주", ["FootNote", "EndNote"]),
        ("변경 추적", ["TrackChange", "Revision"]),
        ("양식 컨트롤", ["FormObject", "Field"]),
    ]
    for label, tags in unsupported_pairs:
        if any(f"<{tag}" in xml_text for tag in tags):
            skipped_elements.append(label)

    if not paragraphs:
        try:
            fallback = extract_hwp_text(source)
        except HwpBinaryError:
            fallback = None
        if fallback and fallback.paragraphs:
            paragraphs = list(fallback.paragraphs)
            warnings.append(f"pyhwp XML 직접 매핑 실패로 {fallback.source} 텍스트를 사용했습니다.")

    document = HwpxDocument.new()
    for paragraph_text in paragraphs:
        document.add_paragraph(paragraph_text)

    for table_payload in tables:
        row_count = len(table_payload.rows)
        col_count = len(table_payload.rows[0]) if table_payload.rows else 0
        if row_count <= 0 or col_count <= 0:
            continue
        table = document.add_table(rows=row_count, cols=col_count)
        for row_index, row in enumerate(table_payload.rows):
            for col_index, text in enumerate(row):
                table.rows[row_index].cells[col_index].text = text

    document.save(target)

    if skipped_elements:
        warnings.append(
            "변환 제외 요소: " + ", ".join(skipped_elements)
        )

    return ConversionResult(
        success=True,
        output_path=str(target),
        paragraphs_converted=len(paragraphs),
        tables_converted=len(tables),
        skipped_elements=skipped_elements,
        warnings=warnings,
    )
