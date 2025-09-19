"""High level operations built on top of :mod:`python-hwpx`."""

from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hwpx import ObjectFinder
from hwpx.document import HwpxDocument, HwpxOxmlMemo, HwpxOxmlParagraph, HwpxOxmlTable
from hwpx.package import HwpxPackage
from hwpx.tools.text_extractor import AnnotationOptions, TextExtractor
from hwpx.tools.validator import ValidationReport, validate_document

from .fs import WorkdirGuard

HH_NS = "{http://www.hancom.co.kr/hwpml/2011/head}"

logger = logging.getLogger(__name__)


class HwpxOperationError(RuntimeError):
    """Raised when a document level operation fails."""


class HwpxOps:
    """Collection of safe high level helpers used by the MCP tools."""

    def __init__(
        self,
        guard: WorkdirGuard,
        *,
        paging_paragraph_limit: int = 2000,
        auto_backup: bool = False,
        enable_opc_write: bool = False,
    ) -> None:
        self.guard = guard
        self.paging_limit = max(1, paging_paragraph_limit)
        self.auto_backup = auto_backup
        self.enable_opc_write = enable_opc_write

    # ------------------------------------------------------------------
    # Basic helpers
    # ------------------------------------------------------------------
    def _resolve_path(self, path: str, *, must_exist: bool = True) -> Path:
        return self.guard.resolve_path(path, must_exist=must_exist)

    def _maybe_backup(self, path: Path) -> None:
        if self.auto_backup:
            backup = self.guard.ensure_backup(path)
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
            "path": self.guard.relative(resolved),
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

    def package_set_text(
        self,
        path: str,
        part_name: str,
        text: str,
        *,
        encoding: str | None = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        if not self.enable_opc_write:
            raise PermissionError("OPC write access is disabled by default")
        resolved = self._resolve_path(path)
        package = HwpxPackage.open(resolved)
        package.set_part(part_name, text.encode(encoding or "utf-8"))
        if dry_run:
            return {"updated": False}
        self._maybe_backup(resolved)
        package.save(resolved)
        return {"updated": True}

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
        effective_limit = limit if limit is not None else self.paging_limit
        effective_limit = max(1, min(effective_limit, self.paging_limit))
        annotations = None
        if with_highlights or with_footnotes:
            annotations = AnnotationOptions(
                highlight="markers" if with_highlights else "ignore",
                footnote="inline" if with_footnotes else "ignore",
                endnote="inline" if with_footnotes else "ignore",
            )
        paragraphs: List[str] = []
        next_offset: Optional[int] = None
        with TextExtractor(resolved) as extractor:
            all_paragraphs = list(extractor.iter_document_paragraphs())
            start = max(0, offset)
            slice_end = min(len(all_paragraphs), start + effective_limit)
            for para in all_paragraphs[start:slice_end]:
                paragraphs.append(
                    para.text(annotations=annotations, preserve_breaks=True)
                )
            if slice_end < len(all_paragraphs):
                next_offset = slice_end
        return {"textChunk": "\n".join(paragraphs), "nextOffset": next_offset}

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
    ) -> Dict[str, Any]:
        if not query:
            raise ValueError("query must be a non-empty string")
        resolved = self._resolve_path(path)
        matches: List[Dict[str, Any]] = []
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
                                "context": text,
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
                                "context": text,
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
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        border_fill = "0"
        if border_style == "none":
            border_fill = "0"
        table = document.add_table(
            rows,
            cols,
            section_index=section_index,
            border_fill_id_ref=border_fill,
        )
        tables = self._iter_tables(document)
        index = tables.index(table)
        self._save_document(document, resolved)
        return {"tableIndex": index, "cellCount": rows * cols}

    def set_table_cell_text(
        self,
        path: str,
        table_index: int,
        row: int,
        col: int,
        text: str,
        *,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        tables = self._iter_tables(document)
        try:
            table = tables[table_index]
        except IndexError as exc:
            raise HwpxOperationError("tableIndex out of range") from exc
        table.set_cell_text(row, col, text)
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
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        document, resolved = self._open_document(path)
        tables = self._iter_tables(document)
        try:
            table = tables[table_index]
        except IndexError as exc:
            raise HwpxOperationError("tableIndex out of range") from exc
        updated = 0
        for row_offset, row_values in enumerate(values):
            for col_offset, cell_text in enumerate(row_values):
                table.set_cell_text(start_row + row_offset, start_col + col_offset, cell_text)
                updated += 1
        if not dry_run:
            self._save_document(document, resolved)
        return {"updatedCells": updated}

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
        out_path = self.guard.resolve_output_path(out)
        document.save(out_path)
        return {"outPath": str(out_path)}

    def make_blank(self, out: str) -> Dict[str, Any]:
        document = HwpxDocument.new()
        out_path = self.guard.resolve_output_path(out)
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

    def package_set_xml(
        self,
        path: str,
        part_name: str,
        xml_string: str,
        *,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        if not self.enable_opc_write:
            raise PermissionError("OPC write access is disabled by default")
        resolved = self._resolve_path(path)
        from xml.etree import ElementTree as ET

        element = ET.fromstring(xml_string)
        package = HwpxPackage.open(resolved)
        package.set_xml(part_name, element)
        if dry_run:
            return {"updated": False}
        self._maybe_backup(resolved)
        package.save(resolved)
        return {"updated": True}
