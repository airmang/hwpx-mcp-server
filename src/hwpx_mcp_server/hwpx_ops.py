""":mod:`python-hwpx` 위에 구축한 고수준 연산 모음."""

from __future__ import annotations

import copy
import dataclasses
import logging
import re
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

from hwpx import ObjectFinder
from hwpx.document import (
    HwpxDocument,
    HwpxOxmlMemo,
    HwpxOxmlParagraph,
    HwpxOxmlRun,
    HwpxOxmlTable,
)
from hwpx.package import HwpxPackage
from hwpx.tools.text_extractor import AnnotationOptions, TextExtractor
from hwpx.tools.validator import ValidationReport, validate_document

HH_NS = "{http://www.hancom.co.kr/hwpml/2011/head}"
HP_NS = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

logger = logging.getLogger(__name__)


DEFAULT_PAGING_PARAGRAPH_LIMIT = 200


class HwpxOperationError(RuntimeError):
    """문서 단위 작업이 실패했을 때 사용하는 예외."""


class HwpxOps:
    """MCP 도구에서 활용하는 안전한 고수준 헬퍼 모음."""

    def __init__(
        self,
        *,
        base_directory: Path | None = None,
        paging_paragraph_limit: int = DEFAULT_PAGING_PARAGRAPH_LIMIT,
        auto_backup: bool = False,
    ) -> None:
        self.base_directory = (base_directory or Path.cwd()).expanduser().resolve()
        self.paging_limit = max(1, paging_paragraph_limit)
        self.auto_backup = auto_backup

    # ------------------------------------------------------------------
    # Basic helpers
    # ------------------------------------------------------------------
    def _resolve_path(self, path: str, *, must_exist: bool = True) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = (self.base_directory / candidate).resolve(strict=False)
        else:
            candidate = candidate.resolve(strict=False)
        if must_exist and not candidate.exists():
            raise FileNotFoundError(f"Path '{candidate}' does not exist")
        return candidate

    def _resolve_output_path(self, path: str) -> Path:
        resolved = self._resolve_path(path, must_exist=False)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    def _ensure_backup(self, path: Path) -> Optional[Path]:
        if not path.exists():
            return None
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        return backup

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.base_directory))
        except ValueError:
            return str(path)

    def _maybe_backup(self, path: Path) -> None:
        if self.auto_backup:
            backup = self._ensure_backup(path)
            if backup is not None:
                logger.info("created backup", extra={"path": str(path), "backup": str(backup)})

    def _open_document(self, path: str) -> Tuple[HwpxDocument, Path]:
        resolved = self._resolve_path(path)
        try:
            document = HwpxDocument.open(resolved)
        except FileNotFoundError:
            raise
        except Exception as exc:  # pragma: no cover - delegated to python-hwpx
            raise HwpxOperationError(f"failed to open '{resolved}': {exc}") from exc
        return document, resolved

    def _save_document(self, document: HwpxDocument, target: Path) -> None:
        self._maybe_backup(target)
        document.save(target)

    def _iter_paragraphs(self, document: HwpxDocument) -> List[HwpxOxmlParagraph]:
        return list(document.paragraphs)

    def _iter_tables(self, document: HwpxDocument) -> List[HwpxOxmlTable]:
        tables: List[HwpxOxmlTable] = []
        for paragraph in document.paragraphs:
            tables.extend(paragraph.tables)
        return tables

    def _normalize_color(self, color: str | None) -> Optional[str]:
        if color is None:
            return None
        value = color.strip()
        if not value:
            return None
        if not value.startswith("#"):
            value = "#" + value
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("colorHex must be a 6-digit hexadecimal value")
        return value.upper()

    def _ensure_char_style(
        self,
        document: HwpxDocument,
        run_style: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not run_style:
            return None
        bold = bool(run_style.get("bold", False))
        italic = bool(run_style.get("italic", False))
        underline = bool(run_style.get("underline", False))
        color = self._normalize_color(run_style.get("colorHex"))
        base_id = document.ensure_run_style(bold=bold, italic=italic, underline=underline)
        if color is None:
            return base_id
        if not document.headers:
            raise HwpxOperationError("document does not contain any headers to host styles")
        header = document.headers[0]
        target_flags = (bold, italic, underline)

        def element_flags(element) -> Tuple[bool, bool, bool]:
            bold_present = element.find(f"{HH_NS}bold") is not None
            italic_present = element.find(f"{HH_NS}italic") is not None
            underline_element = element.find(f"{HH_NS}underline")
            underline_present = False
            if underline_element is not None:
                underline_present = (underline_element.get("type", "").upper() or "NONE") != "NONE"
            return bold_present, italic_present, underline_present

        normalized_color = color

        def predicate(element) -> bool:
            if element.get("textColor", "").upper() != normalized_color:
                return False
            return element_flags(element) == target_flags

        def modifier(element) -> None:
            element.set("textColor", normalized_color)
            underline_nodes = list(element.findall(f"{HH_NS}underline"))
            for node in underline_nodes:
                node.set("color", normalized_color)
                if underline:
                    node.set("type", node.get("type", "SOLID") or "SOLID")
                else:
                    node.set("type", "NONE")
            if underline and not underline_nodes:
                element.append(
                    self._create_underline_element(color=normalized_color)
                )

        char_element = header.ensure_char_property(
            predicate=predicate,
            modifier=modifier,
            base_char_pr_id=base_id,
        )
        char_id = char_element.get("id")
        if not char_id:
            raise HwpxOperationError("char property does not expose an identifier")
        return char_id

    def _create_underline_element(self, color: str) -> Any:
        from xml.etree import ElementTree as ET

        return ET.Element(
            f"{HH_NS}underline",
            {"type": "SOLID", "shape": "SOLID", "color": color},
        )

    def _ensure_table_border_fill(
        self,
        document: HwpxDocument,
        *,
        border_style: Optional[str] = None,
        border_color: Optional[str] = None,
        border_width: Optional[str | float | int] = None,
        fill_color: Optional[str] = None,
    ) -> str:
        normalized_style = (border_style or "").strip().lower() or None
        if normalized_style not in {None, "solid", "none"}:
            raise ValueError(f"Unsupported border style: {border_style}")

        normalized_border_color = self._normalize_color(border_color)
        normalized_fill_color = self._normalize_color(fill_color)

        if normalized_style == "none" and not any(
            [normalized_border_color, normalized_fill_color, border_width]
        ):
            return "0"

        if (
            normalized_style in {None, "solid"}
            and normalized_border_color is None
            and normalized_fill_color is None
            and border_width is None
        ):
            return document.oxml.ensure_basic_border_fill()

        if not document.headers:
            raise HwpxOperationError(
                "document does not contain any headers to host border fills"
            )

        header = document.headers[0]

        border_type = "NONE" if normalized_style == "none" else "SOLID"

        def normalize_length(value: Optional[str | float | int], default: str) -> str:
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return f"{value:g} mm"
            text = str(value).strip()
            if not text:
                return default
            match = re.fullmatch(r"([0-9]+(?:\\.[0-9]+)?)\\s*([A-Za-z]+)?", text)
            if match:
                number, unit = match.groups()
                unit = (unit or "mm").lower()
                return f"{number} {unit}"
            return text

        if border_type == "NONE":
            width_default = "0 mm"
            diag_default = "0 mm"
        else:
            width_default = "0.12 mm"
            diag_default = "0.1 mm"

        width_value = normalize_length(border_width, width_default)
        if border_width is not None:
            diagonal_width_value = normalize_length(border_width, width_default)
        else:
            diagonal_width_value = normalize_length(None, diag_default)

        def normalize_length_token(value: Optional[str]) -> str:
            if not value:
                return ""
            return re.sub(r"\s+", "", str(value)).lower()

        width_token = normalize_length_token(width_value)
        diagonal_width_token = normalize_length_token(diagonal_width_value)

        if border_type == "SOLID":
            edge_color = normalized_border_color or "#000000"
            diagonal_color = edge_color
        else:
            edge_color = normalized_border_color
            diagonal_color = normalized_border_color

        ref_list = header.element.find(f"{HH_NS}refList")
        if ref_list is None:
            ref_list = ET.SubElement(header.element, f"{HH_NS}refList")
            header.mark_dirty()

        border_fills_element = ref_list.find(f"{HH_NS}borderFills")
        if border_fills_element is None:
            border_fills_element = ET.SubElement(
                ref_list, f"{HH_NS}borderFills", {"itemCnt": "0"}
            )
            header.mark_dirty()

        def matches(existing: ET.Element) -> bool:
            if (existing.get("threeD") or "0") != "0":
                return False
            if (existing.get("shadow") or "0") != "0":
                return False
            if (existing.get("centerLine") or "NONE").upper() != "NONE":
                return False
            if (existing.get("breakCellSeparateLine") or "0") != "0":
                return False

            for slash_name in ("slash", "backSlash"):
                slash = existing.find(f"{HH_NS}{slash_name}")
                if slash is None:
                    return False
                if (slash.get("type") or "NONE").upper() != "NONE":
                    return False
                if slash.get("Crooked", "0") != "0":
                    return False
                if slash.get("isCounter", "0") != "0":
                    return False

            for child_name in ("leftBorder", "rightBorder", "topBorder", "bottomBorder"):
                border_child = existing.find(f"{HH_NS}{child_name}")
                if border_child is None:
                    return False
                if (border_child.get("type") or "").upper() != border_type:
                    return False
                if normalize_length_token(border_child.get("width")) != width_token:
                    return False
                if edge_color is not None:
                    if (border_child.get("color") or "").upper() != edge_color:
                        return False
                else:
                    if border_child.get("color") not in (None, ""):
                        return False

            diagonal_child = existing.find(f"{HH_NS}diagonal")
            if diagonal_child is None:
                return False
            expected_diagonal_type = "SOLID" if border_type == "SOLID" else "NONE"
            if (diagonal_child.get("type") or "").upper() != expected_diagonal_type:
                return False
            if normalize_length_token(diagonal_child.get("width")) != diagonal_width_token:
                return False
            if diagonal_color is not None:
                if (diagonal_child.get("color") or "").upper() != diagonal_color:
                    return False
            else:
                if diagonal_child.get("color") not in (None, ""):
                    return False

            fill_brush = existing.find(f"{HH_NS}fillBrush")
            if normalized_fill_color is None:
                if fill_brush is not None:
                    return False
            else:
                if fill_brush is None:
                    return False
                solid_brush = fill_brush.find(f"{HH_NS}solidBrush")
                if solid_brush is None:
                    return False
                if (solid_brush.get("type") or "SOLID").upper() != "SOLID":
                    return False
                if (solid_brush.get("color") or "").upper() != normalized_fill_color:
                    return False

            return True

        for candidate in border_fills_element.findall(f"{HH_NS}borderFill"):
            identifier = candidate.get("id")
            if not identifier:
                continue
            if matches(candidate):
                return identifier

        if not hasattr(header, "_allocate_border_fill_id"):
            raise HwpxOperationError("header does not expose ID allocation helpers")

        new_id = header._allocate_border_fill_id(border_fills_element)  # type: ignore[attr-defined]
        border_fill_element = ET.Element(
            f"{HH_NS}borderFill",
            {
                "id": new_id,
                "threeD": "0",
                "shadow": "0",
                "centerLine": "NONE",
                "breakCellSeparateLine": "0",
            },
        )

        for slash_name in ("slash", "backSlash"):
            ET.SubElement(
                border_fill_element,
                f"{HH_NS}{slash_name}",
                {"type": "NONE", "Crooked": "0", "isCounter": "0"},
            )

        def append_border(name: str, *, width: str, color: Optional[str], kind: str) -> None:
            attrs = {"type": kind}
            if width:
                attrs["width"] = width
            if color is not None:
                attrs["color"] = color
            ET.SubElement(border_fill_element, f"{HH_NS}{name}", attrs)

        for side in ("leftBorder", "rightBorder", "topBorder", "bottomBorder"):
            append_border(side, width=width_value, color=edge_color, kind=border_type)

        append_border(
            "diagonal",
            width=diagonal_width_value,
            color=diagonal_color,
            kind="SOLID" if border_type == "SOLID" else "NONE",
        )

        if normalized_fill_color is not None:
            fill_brush = ET.SubElement(border_fill_element, f"{HH_NS}fillBrush")
            ET.SubElement(
                fill_brush,
                f"{HH_NS}solidBrush",
                {"type": "SOLID", "color": normalized_fill_color, "alpha": "255"},
            )

        border_fills_element.append(border_fill_element)
        if hasattr(header, "_update_border_fills_item_count"):
            header._update_border_fills_item_count(border_fills_element)  # type: ignore[attr-defined]
        else:
            count = len(border_fills_element.findall(f"{HH_NS}borderFill"))
            border_fills_element.set("itemCnt", str(count))
        header.mark_dirty()
        return new_id

    # ------------------------------------------------------------------
    # Document information
    # ------------------------------------------------------------------
    def open_info(self, path: str) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        sections = document.sections
        section_count = len(sections)
        paragraph_count = sum(len(section.paragraphs) for section in sections)
        header_count = len(document.headers)
        stat = resolved.stat()
        meta = {
            "path": self._relative_path(resolved),
            "absolutePath": str(resolved),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
        return {
            "meta": meta,
            "sectionCount": section_count,
            "paragraphCount": paragraph_count,
            "headerCount": header_count,
        }

    def list_sections(self, path: str) -> Dict[str, Any]:
        document, _ = self._open_document(path)
        sections: List[Dict[str, Any]] = []
        for index, section in enumerate(document.sections):
            sections.append(
                {
                    "index": index,
                    "paragraphCount": len(section.paragraphs),
                    "partName": getattr(section, "part_name", None),
                }
            )
        return {"sections": sections}

    def list_headers(self, path: str) -> Dict[str, Any]:
        document, _ = self._open_document(path)
        headers: List[Dict[str, Any]] = []
        has_master_page = bool(document.master_pages)
        for index, header in enumerate(document.headers):
            headers.append(
                {
                    "index": index,
                    "styleCount": len(header.styles),
                    "bulletCount": len(header.bullets),
                    "hasMasterPage": has_master_page,
                    "partName": getattr(header, "part_name", None),
                }
            )
        return {"headers": headers}

    def package_parts(self, path: str) -> Dict[str, Any]:
        resolved = self._resolve_path(path)
        package = HwpxPackage.open(resolved)
        parts = sorted(package.part_names())
        return {"parts": parts}

    def package_get_text(self, path: str, part_name: str, encoding: str | None = None) -> Dict[str, Any]:
        resolved = self._resolve_path(path)
        package = HwpxPackage.open(resolved)
        text = package.get_text(part_name, encoding=encoding or "utf-8")
        return {"text": text}


    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------
    def read_text(
        self,
        path: str,
        *,
        offset: int = 0,
        limit: Optional[int] = None,
        with_highlights: bool = False,
        with_footnotes: bool = False,
    ) -> Dict[str, Any]:
        resolved = self._resolve_path(path)
        if limit is None:
            effective_limit = self.paging_limit
        else:
            effective_limit = max(1, limit)
        annotations = None
        if with_highlights or with_footnotes:
            annotations = AnnotationOptions(
                highlight="markers" if with_highlights else "ignore",
                footnote="inline" if with_footnotes else "ignore",
                endnote="inline" if with_footnotes else "ignore",
            )
        paragraphs: List[str] = []
        next_offset: Optional[int] = None
        start = max(0, offset)
        with TextExtractor(resolved) as extractor:
            paragraph_iter = extractor.iter_document_paragraphs()
            sentinel = object()

            skip_exhausted = False
            for _ in range(start):
                if next(paragraph_iter, sentinel) is sentinel:
                    skip_exhausted = True
                    break

            if not skip_exhausted:
                while len(paragraphs) < effective_limit:
                    paragraph = next(paragraph_iter, sentinel)
                    if paragraph is sentinel:
                        break
                    paragraphs.append(
                        paragraph.text(annotations=annotations, preserve_breaks=True)
                    )

                if len(paragraphs) == effective_limit:
                    if next(paragraph_iter, sentinel) is not sentinel:
                        next_offset = start + len(paragraphs)

        return {"textChunk": "\n".join(paragraphs), "nextOffset": next_offset}

    def get_paragraphs(
        self,
        path: str,
        paragraph_indexes: Sequence[int],
        *,
        with_highlights: bool = False,
        with_footnotes: bool = False,
    ) -> Dict[str, Any]:
        if not paragraph_indexes:
            return {"paragraphs": []}
        normalized_indexes: List[int] = []
        unique_indexes: set[int] = set()
        for index in paragraph_indexes:
            if index < 0:
                raise ValueError("paragraphIndexes must contain non-negative integers")
            normalized_indexes.append(int(index))
            unique_indexes.add(int(index))

        annotations = None
        if with_highlights or with_footnotes:
            annotations = AnnotationOptions(
                highlight="markers" if with_highlights else "ignore",
                footnote="inline" if with_footnotes else "ignore",
                endnote="inline" if with_footnotes else "ignore",
            )

        resolved = self._resolve_path(path)
        collected: Dict[int, str] = {}
        with TextExtractor(resolved) as extractor:
            for paragraph in extractor.iter_document_paragraphs():
                para_index = paragraph.index
                if para_index in unique_indexes and para_index not in collected:
                    collected[para_index] = paragraph.text(
                        annotations=annotations, preserve_breaks=True
                    )
                    if len(collected) == len(unique_indexes):
                        break

        missing = [index for index in normalized_indexes if index not in collected]
        if missing:
            raise ValueError(
                "paragraphIndexes out of range: " + ", ".join(str(idx) for idx in sorted(set(missing)))
            )

        return {
            "paragraphs": [
                {"paragraphIndex": index, "text": collected[index]}
                for index in normalized_indexes
            ]
        }

    def text_extract_report(self, path: str, mode: str = "plain") -> Dict[str, Any]:
        resolved = self._resolve_path(path)
        annotations = None
        if mode == "with_annotations":
            annotations = AnnotationOptions(
                highlight="markers",
                footnote="inline",
                endnote="inline",
                control="placeholder",
            )
        with TextExtractor(resolved) as extractor:
            content = extractor.extract_text(
                annotations=annotations,
                include_nested=True,
            )
        return {"content": content}

    # ------------------------------------------------------------------
    # Search & replace
    # ------------------------------------------------------------------
    def find(
        self,
        path: str,
        query: str,
        *,
        is_regex: bool = False,
        max_results: int = 100,
        context_radius: int = 80,
    ) -> Dict[str, Any]:
        if not query:
            raise ValueError("query must be a non-empty string")
        resolved = self._resolve_path(path)
        matches: List[Dict[str, Any]] = []
        radius = max(0, context_radius)

        def build_context(text: str, start: int, end: int) -> str:
            context_start = max(0, start - radius)
            context_end = min(len(text), end + radius)
            snippet = text[context_start:context_end]
            if context_start > 0:
                snippet = "..." + snippet
            if context_end < len(text):
                snippet = snippet + "..."
            return snippet

        pattern = re.compile(query) if is_regex else None
        with TextExtractor(resolved) as extractor:
            for paragraph in extractor.iter_document_paragraphs():
                text = paragraph.text()
                if is_regex:
                    for match in pattern.finditer(text):  # type: ignore[union-attr]
                        matches.append(
                            {
                                "paragraphIndex": paragraph.index,
                                "start": match.start(),
                                "end": match.end(),
                                "context": build_context(text, match.start(), match.end()),
                            }
                        )
                        if len(matches) >= max_results:
                            return {"matches": matches}
                else:
                    start = 0
                    while True:
                        found = text.find(query, start)
                        if found == -1:
                            break
                        matches.append(
                            {
                                "paragraphIndex": paragraph.index,
                                "start": found,
                                "end": found + len(query),
                                "context": build_context(text, found, found + len(query)),
                            }
                        )
                        if len(matches) >= max_results:
                            return {"matches": matches}
                        start = found + len(query)
        return {"matches": matches}

    def find_runs_by_style(
        self,
        path: str,
        *,
        filters: Optional[Dict[str, Any]] = None,
        max_results: int = 200,
    ) -> Dict[str, Any]:
        document, _ = self._open_document(path)
        filter_args: Dict[str, Any] = {}
        if filters:
            if "colorHex" in filters and filters["colorHex"]:
                filter_args["text_color"] = self._normalize_color(filters["colorHex"])
            if "underline" in filters:
                filter_args["underline_type"] = "SOLID" if filters["underline"] else "NONE"
            if "charPrIDRef" in filters and filters["charPrIDRef"]:
                filter_args["char_pr_id_ref"] = filters["charPrIDRef"]
        runs = document.find_runs_by_style(**filter_args)
        paragraph_index_map: Dict[int, int] = {}
        paragraphs = self._iter_paragraphs(document)
        for index, paragraph in enumerate(paragraphs):
            paragraph_index_map[id(paragraph.element)] = index
        results: List[Dict[str, Any]] = []
        for run in runs[:max_results]:
            paragraph = run.paragraph
            para_index = paragraph_index_map.get(id(paragraph.element), -1)
            style = {}
            if run.style is not None:
                style_data = run.style
                if dataclasses.is_dataclass(style_data):
                    style = asdict(style_data)
            results.append(
                {
                    "text": run.text,
                    "paragraphIndex": para_index,
                    "charPrIDRef": run.char_pr_id_ref,
                    "style": style,
                }
            )
        return {"runs": results}

    def replace_text_in_runs(
        self,
        path: str,
        search: str,
        replacement: str,
        *,
        style_filter: Optional[Dict[str, Any]] = None,
        limit_per_run: Optional[int] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        filter_args: Dict[str, Any] = {}
        if style_filter:
            if "colorHex" in style_filter and style_filter["colorHex"]:
                filter_args["text_color"] = self._normalize_color(style_filter["colorHex"])
            if "underline" in style_filter:
                filter_args["underline_type"] = "SOLID" if style_filter["underline"] else "NONE"
            if "charPrIDRef" in style_filter and style_filter["charPrIDRef"]:
                filter_args["char_pr_id_ref"] = style_filter["charPrIDRef"]
        replaced = document.replace_text_in_runs(
            search,
            replacement,
            limit=limit_per_run,
            **filter_args,
        )
        if not dry_run and replaced:
            self._save_document(document, resolved)
        return {"replacedCount": replaced}

    # ------------------------------------------------------------------
    # Paragraph and table editing
    # ------------------------------------------------------------------
    def add_paragraph(
        self,
        path: str,
        text: str = "",
        *,
        section_index: Optional[int] = None,
        run_style: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        char_id = self._ensure_char_style(document, run_style)
        paragraph = document.add_paragraph(
            text,
            section_index=section_index,
            char_pr_id_ref=char_id,
        )
        paragraphs = self._iter_paragraphs(document)
        index = len(paragraphs) - 1
        element_id = id(paragraph.element)
        for idx, candidate in enumerate(paragraphs):
            if id(candidate.element) == element_id:
                index = idx
                break
        self._save_document(document, resolved)
        return {"paragraphIndex": index}

    def insert_paragraphs_bulk(
        self,
        path: str,
        paragraphs: Sequence[str],
        *,
        section_index: Optional[int] = None,
        run_style: Optional[Dict[str, Any]] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        if not paragraphs:
            return {"added": 0}
        document, resolved = self._open_document(path)
        char_id = self._ensure_char_style(document, run_style)
        count = 0
        for text in paragraphs:
            document.add_paragraph(
                text,
                section_index=section_index,
                char_pr_id_ref=char_id,
            )
            count += 1
        if not dry_run:
            self._save_document(document, resolved)
        return {"added": count}

    def add_table(
        self,
        path: str,
        rows: int,
        cols: int,
        *,
        section_index: Optional[int] = None,
        border_style: str | None = None,
        border_color: Optional[str] = None,
        border_width: Optional[str | float | int] = None,
        fill_color: Optional[str] = None,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        border_fill = self._ensure_table_border_fill(
            document,
            border_style=border_style,
            border_color=border_color,
            border_width=border_width,
            fill_color=fill_color,
        )
        table = document.add_table(
            rows,
            cols,
            section_index=section_index,
            border_fill_id_ref=border_fill,
        )
        tables = self._iter_tables(document)
        element_id = id(table.element)
        index = len(tables) - 1
        for idx, candidate in enumerate(tables):
            if id(candidate.element) == element_id:
                index = idx
                break
        self._save_document(document, resolved)
        return {"tableIndex": index, "cellCount": rows * cols}

    def set_table_border_fill(
        self,
        path: str,
        table_index: int,
        *,
        border_style: str | None = None,
        border_color: Optional[str] = None,
        border_width: Optional[str | float | int] = None,
        fill_color: Optional[str] = None,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        tables = self._iter_tables(document)
        try:
            table = tables[table_index]
        except IndexError as exc:
            raise HwpxOperationError("tableIndex out of range") from exc

        border_fill = self._ensure_table_border_fill(
            document,
            border_style=border_style,
            border_color=border_color,
            border_width=border_width,
            fill_color=fill_color,
        )

        table.element.set("borderFillIDRef", border_fill)
        anchor_elements: set[int] = set()
        for position in table.iter_grid():
            if getattr(position, "is_anchor", False):
                cell_element = position.cell.element
                cell_element.set("borderFillIDRef", border_fill)
                anchor_elements.add(id(cell_element))

        table.mark_dirty()
        self._save_document(document, resolved)
        return {"borderFillIDRef": border_fill, "anchorCells": len(anchor_elements)}

    def get_table_cell_map(
        self,
        path: str,
        table_index: int,
    ) -> Dict[str, Any]:
        document, _ = self._open_document(path)
        tables = self._iter_tables(document)
        try:
            table = tables[table_index]
        except IndexError as exc:
            raise HwpxOperationError("tableIndex out of range") from exc

        grid_positions = table.get_cell_map()
        serialized: List[List[Dict[str, Any]]] = []
        for row in grid_positions:
            row_payload: List[Dict[str, Any]] = []
            for position in row:
                anchor_row, anchor_col = position.anchor
                row_span, col_span = position.span
                cell_text: Optional[str] = None
                cell = position.cell
                if cell is not None:
                    cell_text = cell.text
                row_payload.append(
                    {
                        "row": position.row,
                        "column": position.column,
                        "anchor": {"row": anchor_row, "column": anchor_col},
                        "rowSpan": row_span,
                        "colSpan": col_span,
                        "text": cell_text,
                    }
                )
            serialized.append(row_payload)
        row_count = len(serialized)
        column_count = len(serialized[0]) if serialized else 0
        return {"grid": serialized, "rowCount": row_count, "columnCount": column_count}

    def set_table_cell_text(
        self,
        path: str,
        table_index: int,
        row: int,
        col: int,
        text: str,
        *,
        dry_run: bool = False,
        logical: Optional[bool] = None,
        split_merged: Optional[bool] = None,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        tables = self._iter_tables(document)
        try:
            table = tables[table_index]
        except IndexError as exc:
            raise HwpxOperationError("tableIndex out of range") from exc
        kwargs: Dict[str, bool] = {}
        if logical is not None:
            kwargs["logical"] = logical
        if split_merged is not None:
            kwargs["split_merged"] = split_merged
        guidance = (
            "failed to update table cell; check indexes, enable logical addressing, "
            "or split merged cells first"
        )
        try:
            table.set_cell_text(row, col, text, **kwargs)
        except (IndexError, ValueError) as exc:
            raise HwpxOperationError(f"{guidance}: {exc}") from exc
        if not dry_run:
            self._save_document(document, resolved)
        return {"ok": True}

    def replace_table_region(
        self,
        path: str,
        table_index: int,
        start_row: int,
        start_col: int,
        values: Sequence[Sequence[str]],
        *,
        dry_run: bool = False,
        logical: Optional[bool] = None,
        split_merged: Optional[bool] = None,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        tables = self._iter_tables(document)
        try:
            table = tables[table_index]
        except IndexError as exc:
            raise HwpxOperationError("tableIndex out of range") from exc
        kwargs: Dict[str, bool] = {}
        if logical is not None:
            kwargs["logical"] = logical
        if split_merged is not None:
            kwargs["split_merged"] = split_merged
        guidance = (
            "failed to update table cell; check indexes, enable logical addressing, "
            "or split merged cells first"
        )
        updated = 0
        for row_offset, row_values in enumerate(values):
            for col_offset, cell_text in enumerate(row_values):
                logical_row = start_row + row_offset
                logical_col = start_col + col_offset
                try:
                    table.set_cell_text(logical_row, logical_col, cell_text, **kwargs)
                except (IndexError, ValueError) as exc:
                    message = (
                        f"{guidance} while writing cell ({logical_row}, {logical_col})"
                    )
                    raise HwpxOperationError(f"{message}: {exc}") from exc
                updated += 1
        if not dry_run:
            self._save_document(document, resolved)
        return {"updatedCells": updated}

    def split_table_cell(
        self,
        path: str,
        table_index: int,
        row: int,
        col: int,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        tables = self._iter_tables(document)
        try:
            table = tables[table_index]
        except IndexError as exc:
            raise HwpxOperationError("tableIndex out of range") from exc
        try:
            target = table.cell(row, col)
        except (IndexError, ValueError) as exc:
            raise HwpxOperationError(
                "table cell coordinates out of range; enable logical addressing to verify merged grids"
            ) from exc
        anchor_row, anchor_col = target.address
        span_row, span_col = target.span
        changed = span_row > 1 or span_col > 1
        guidance = (
            "failed to split merged cell; check indexes or split manually if logical addressing shows overlaps"
        )
        try:
            table.split_merged_cell(row, col)
        except (IndexError, ValueError) as exc:
            raise HwpxOperationError(f"{guidance}: {exc}") from exc
        if changed:
            self._save_document(document, resolved)
        return {
            "startRow": anchor_row,
            "startCol": anchor_col,
            "rowSpan": span_row,
            "colSpan": span_col,
        }

    def add_shape(
        self,
        path: str,
        *,
        shape_type: str = "RECTANGLE",
        section_index: Optional[int] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        shape = document.add_shape(shape_type, section_index=section_index)
        if not dry_run:
            self._save_document(document, resolved)
        return {"objectId": shape.element.get("id")}

    def add_control(
        self,
        path: str,
        *,
        control_type: str = "TEXTBOX",
        section_index: Optional[int] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        control = document.add_control(control_type=control_type, section_index=section_index)
        if not dry_run:
            self._save_document(document, resolved)
        return {"objectId": control.element.get("id")}

    # ------------------------------------------------------------------
    # Memo management
    # ------------------------------------------------------------------
    def add_memo(
        self,
        path: str,
        text: str,
        *,
        section_index: Optional[int] = None,
        author: str | None = None,
        timestamp: str | None = None,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        memo = document.add_memo(
            text,
            section_index=section_index,
            attributes={"author": author or "", "createDateTime": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        )
        self._save_document(document, resolved)
        return {"memoId": memo.id}

    def attach_memo_field(
        self,
        path: str,
        paragraph_index: int,
        memo_id: str,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        paragraphs = self._iter_paragraphs(document)
        try:
            paragraph = paragraphs[paragraph_index]
        except IndexError as exc:
            raise HwpxOperationError("paragraphIndex out of range") from exc
        memo = self._find_memo(document, memo_id)
        if memo is None:
            raise HwpxOperationError(f"memo '{memo_id}' not found")
        field_id = document.attach_memo_field(paragraph, memo)
        self._save_document(document, resolved)
        return {"fieldId": field_id}

    def add_memo_with_anchor(
        self,
        path: str,
        *,
        text: str,
        section_index: Optional[int] = None,
        memo_shape_id_ref: str | None = None,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        memo, paragraph, field_id = document.add_memo_with_anchor(
            text,
            section_index=section_index,
            memo_shape_id_ref=memo_shape_id_ref,
        )
        paragraphs = self._iter_paragraphs(document)
        paragraph_index = len(paragraphs) - 1
        paragraph_element_id = id(paragraph.element)
        for idx, candidate in enumerate(paragraphs):
            if id(candidate.element) == paragraph_element_id:
                paragraph_index = idx
                break
        self._save_document(document, resolved)
        return {"memoId": memo.id, "paragraphIndex": paragraph_index, "fieldId": field_id}

    def remove_memo(
        self,
        path: str,
        memo_id: str,
        *,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        memo = self._find_memo(document, memo_id)
        if memo is None:
            return {"removed": False}
        memo.remove()
        if not dry_run:
            self._save_document(document, resolved)
        return {"removed": True}

    def _find_memo(self, document: HwpxDocument, memo_id: str) -> Optional[HwpxOxmlMemo]:
        for section in document.sections:
            for memo in section.memos:
                if memo.id == memo_id:
                    return memo
        return None

    # ------------------------------------------------------------------
    # Style helpers
    # ------------------------------------------------------------------
    def ensure_run_style(self, path: str, **run_style: Any) -> Dict[str, Any]:
        document, _ = self._open_document(path)
        char_id = self._ensure_char_style(document, run_style)
        return {"charPrIDRef": char_id}

    def list_styles_and_bullets(self, path: str) -> Dict[str, Any]:
        document, _ = self._open_document(path)
        styles = [asdict(style) for style in document.styles.values() if dataclasses.is_dataclass(style)]
        bullets = [asdict(bullet) for bullet in document.bullets.values() if dataclasses.is_dataclass(bullet)]
        return {"styles": styles, "bullets": bullets}

    def apply_style_to_text_ranges(
        self,
        path: str,
        spans: Sequence[Dict[str, int]],
        char_pr_id_ref: str,
        *,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        if not char_pr_id_ref:
            raise ValueError("char_pr_id_ref must be provided")

        document, resolved = self._open_document(path)
        paragraphs = self._iter_paragraphs(document)

        class _Segment:
            __slots__ = ("element", "attr", "text")

            def __init__(self, element: ET.Element, attr: str, text: str) -> None:
                self.element = element
                self.attr = attr
                self.text = text

            def set(self, value: str) -> None:
                self.text = value
                if value:
                    setattr(self.element, self.attr, value)
                else:
                    setattr(self.element, self.attr, "")

        def _gather_segments(element: ET.Element) -> List[_Segment]:
            segments: List[_Segment] = []

            def visit(node: ET.Element) -> None:
                text_value = node.text or ""
                segments.append(_Segment(node, "text", text_value))
                for child in list(node):
                    visit(child)
                    tail_value = child.tail or ""
                    segments.append(_Segment(child, "tail", tail_value))

            for text_node in element.findall(f"{HP_NS}t"):
                visit(text_node)
            return segments

        def _slice_run(run_obj: HwpxOxmlRun, start: int, end: int) -> None:
            segments = _gather_segments(run_obj.element)
            if not segments:
                return
            total_length = sum(len(segment.text) for segment in segments)
            start = max(0, min(start, total_length))
            end = max(0, min(end, total_length))
            if start >= end:
                for segment in segments:
                    if segment.text:
                        segment.set("")
                run_obj.paragraph.section.mark_dirty()
                return
            changed = False
            offset = 0
            for segment in segments:
                seg_start = offset
                seg_end = seg_start + len(segment.text)
                offset = seg_end
                if end <= seg_start or start >= seg_end:
                    if segment.text:
                        segment.set("")
                        changed = True
                    continue
                local_start = max(start, seg_start) - seg_start
                local_end = min(end, seg_end) - seg_start
                new_value = segment.text[local_start:local_end]
                if segment.text != new_value:
                    segment.set(new_value)
                    changed = True
            if changed:
                run_obj.paragraph.section.mark_dirty()

        def _split_run(run_obj: HwpxOxmlRun, local_start: int, local_end: int) -> None:
            text_value = run_obj.text or ""
            length = len(text_value)
            if length == 0:
                return
            local_start = max(0, min(local_start, length))
            local_end = max(0, min(local_end, length))
            if local_start >= local_end:
                return
            if local_start == 0 and local_end == length:
                run_obj.char_pr_id_ref = char_pr_id_ref
                return

            segments: List[Tuple[int, int, Optional[str]]] = []
            original_char = run_obj.char_pr_id_ref
            if local_start > 0:
                segments.append((0, local_start, original_char))
            segments.append((local_start, local_end, char_pr_id_ref))
            if local_end < length:
                segments.append((local_end, length, original_char))

            parent = run_obj.paragraph.element
            run_children = list(parent)
            try:
                index = run_children.index(run_obj.element)
            except ValueError:  # pragma: no cover - defensive branch
                return

            new_elements: List[ET.Element] = []
            for seg_start, seg_end, char_id in segments:
                if seg_start >= seg_end:
                    continue
                element_copy = copy.deepcopy(run_obj.element)
                segment_run = HwpxOxmlRun(element_copy, run_obj.paragraph)
                _slice_run(segment_run, seg_start, seg_end)
                if char_id is None:
                    segment_run.char_pr_id_ref = None
                else:
                    segment_run.char_pr_id_ref = char_id
                new_elements.append(element_copy)

            if not new_elements:
                parent.remove(run_obj.element)
                run_obj.paragraph.section.mark_dirty()
                return

            for offset, element in enumerate(new_elements):
                parent.insert(index + offset, element)
            parent.remove(run_obj.element)
            run_obj.paragraph.section.mark_dirty()

        def _paragraph_length(paragraph: HwpxOxmlParagraph) -> int:
            return sum(len(run.text or "") for run in paragraph.runs)

        def _apply_span(paragraph: HwpxOxmlParagraph, span_start: int, span_end: int) -> bool:
            if span_start >= span_end:
                return False
            applied = False
            cursor = span_start
            while cursor < span_end:
                runs = list(paragraph.runs)
                offset = 0
                target: Tuple[HwpxOxmlRun, int, int, int] | None = None
                for candidate in runs:
                    text = candidate.text or ""
                    length = len(text)
                    run_start = offset
                    run_end = run_start + length
                    if run_end <= cursor:
                        offset = run_end
                        continue
                    if run_start >= span_end:
                        target = None
                        break
                    if length == 0:
                        offset = run_end
                        continue
                    target = (candidate, run_start, run_end, length)
                    break

                if target is None:
                    break

                run_obj, run_start, run_end, length = target
                local_start = max(0, cursor - run_start)
                local_end = min(length, span_end - run_start)
                if local_start >= local_end:
                    cursor = max(cursor + 1, run_end)
                    continue

                _split_run(run_obj, local_start, local_end)
                applied = True
                cursor = min(span_end, run_end)

            return applied

        styled = 0
        for span in spans:
            if isinstance(span, dict):
                paragraph_index = int(span.get("paragraph_index", -1))
                start = int(span.get("start", 0))
                end = int(span.get("end", 0))
            else:
                paragraph_index = int(getattr(span, "paragraph_index", getattr(span, "paragraphIndex", -1)))
                start = int(getattr(span, "start", 0))
                end = int(getattr(span, "end", 0))

            if paragraph_index < 0 or paragraph_index >= len(paragraphs):
                continue

            start = max(0, start)
            end = max(start, end)
            if start >= end:
                continue

            paragraph = paragraphs[paragraph_index]
            total_length = _paragraph_length(paragraph)
            if total_length == 0 or start >= total_length:
                continue
            clamped_end = min(end, total_length)

            if _apply_span(paragraph, start, clamped_end):
                styled += 1

        if not dry_run and styled:
            self._save_document(document, resolved)

        return {"styledSpans": styled}

    def apply_style_to_paragraphs(
        self,
        path: str,
        paragraph_indexes: Sequence[int],
        char_pr_id_ref: str,
        *,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        paragraphs = self._iter_paragraphs(document)
        updated = 0
        for index in paragraph_indexes:
            if index < 0 or index >= len(paragraphs):
                continue
            paragraph = paragraphs[index]
            paragraph.char_pr_id_ref = char_pr_id_ref
            for run in paragraph.runs:
                run.char_pr_id_ref = char_pr_id_ref
            updated += 1
        if not dry_run and updated:
            self._save_document(document, resolved)
        return {"updated": updated}

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def save(self, path: str) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        self._save_document(document, resolved)
        return {"ok": True}

    def save_as(self, path: str, out: str) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        out_path = self._resolve_output_path(out)
        document.save(out_path)
        return {"outPath": str(out_path)}

    def make_blank(self, out: str) -> Dict[str, Any]:
        document = HwpxDocument.new()
        out_path = self._resolve_output_path(out)
        document.save(out_path)
        return {"outPath": str(out_path)}

    # ------------------------------------------------------------------
    # Package & metadata queries
    # ------------------------------------------------------------------
    def list_master_pages_histories_versions(self, path: str) -> Dict[str, Any]:
        document, _ = self._open_document(path)
        master_pages = [getattr(page, "part_name", None) for page in document.master_pages]
        histories = [getattr(history, "part_name", None) for history in document.histories]
        version = document.version
        version_info = asdict(version) if version and dataclasses.is_dataclass(version) else None
        return {
            "masterPages": master_pages,
            "histories": histories,
            "versions": version_info,
        }

    # ------------------------------------------------------------------
    # Object finder
    # ------------------------------------------------------------------
    def object_find_by_tag(
        self,
        path: str,
        tag_name: str,
        *,
        max_results: int = 200,
    ) -> Dict[str, Any]:
        resolved = self._resolve_path(path)
        finder = ObjectFinder(resolved)
        objects = []
        for found in finder.iter(tag=tag_name, limit=max_results):
            element = found.element
            objects.append(
                {
                    "type": element.tag,
                    "text": element.text or "",
                    "attrs": dict(element.attrib),
                    "path": found.path,
                }
            )
        return {"objects": objects}

    def object_find_by_attr(
        self,
        path: str,
        element_type: str,
        attr: str,
        value: str,
        *,
        max_results: int = 200,
    ) -> Dict[str, Any]:
        resolved = self._resolve_path(path)
        finder = ObjectFinder(resolved)
        objects = []
        for found in finder.iter(tag=element_type, attrs={attr: value}, limit=max_results):
            element = found.element
            objects.append(
                {
                    "type": element.tag,
                    "text": element.text or "",
                    "attrs": dict(element.attrib),
                    "path": found.path,
                }
            )
        return {"objects": objects}

    # ------------------------------------------------------------------
    # Validation & linting
    # ------------------------------------------------------------------
    def validate_structure(self, path: str, level: str = "basic") -> Dict[str, Any]:
        resolved = self._resolve_path(path)
        report: ValidationReport = validate_document(resolved)
        issues = [
            {
                "part": issue.part_name,
                "message": issue.message,
            }
            for issue in report.issues
        ]
        return {"ok": not issues, "issues": issues}

    def lint_text_conventions(
        self,
        path: str,
        *,
        max_line_len: Optional[int] = None,
        forbid_patterns: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        resolved = self._resolve_path(path)
        patterns = [re.compile(pat) for pat in (forbid_patterns or [])]
        warnings: List[Dict[str, Any]] = []
        with TextExtractor(resolved) as extractor:
            for paragraph in extractor.iter_document_paragraphs():
                text = paragraph.text()
                if max_line_len is not None and len(text) > max_line_len:
                    warnings.append(
                        {
                            "paragraphIndex": paragraph.index,
                            "message": f"Paragraph exceeds {max_line_len} characters",
                        }
                    )
                for pattern in patterns:
                    if pattern.search(text):
                        warnings.append(
                            {
                                "paragraphIndex": paragraph.index,
                                "message": f"Pattern '{pattern.pattern}' found",
                            }
                        )
        return {"warnings": warnings}

    # ------------------------------------------------------------------
    # Raw package helpers
    # ------------------------------------------------------------------
    def package_get_xml(self, path: str, part_name: str) -> Dict[str, Any]:
        resolved = self._resolve_path(path)
        package = HwpxPackage.open(resolved)
        element = package.get_xml(part_name)
        from xml.etree import ElementTree as ET

        xml_string = ET.tostring(element, encoding="unicode")
        return {"xmlString": xml_string}

