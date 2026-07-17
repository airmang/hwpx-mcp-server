# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

from ..upstream import (
    HP_NS,
)

from .context import DocumentContext
from .save_policy import SavePolicy
from .transactions import TransactionService
from .memo_style import MemoStyleService

logger = logging.getLogger("hwpx_mcp_server.hwpx_ops")


class ContentLayoutService:
    def __init__(
        self,
        context: DocumentContext,
        save: SavePolicy,
        transactions: TransactionService,
        styles: MemoStyleService,
    ) -> None:
        self._context = context
        self._save = save
        self._transactions = transactions
        self._styles = styles

    def replace_text_in_runs(
        self,
        path: str,
        search: str,
        replacement: str,
        *,
        style_filter: Optional[Dict[str, Any]] = None,
        limit_per_run: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        document, resolved = self._context._open_document(path)
        filter_args: Dict[str, Any] = {}
        if style_filter:
            if "colorHex" in style_filter and style_filter["colorHex"]:
                filter_args["text_color"] = self._styles._normalize_color(
                    style_filter["colorHex"]
                )
            if "underline" in style_filter:
                filter_args["underline_type"] = (
                    "SOLID" if style_filter["underline"] else "NONE"
                )
            if "charPrIDRef" in style_filter and style_filter["charPrIDRef"]:
                filter_args["char_pr_id_ref"] = style_filter["charPrIDRef"]
        replaced = document.replace_text_in_runs(
            search,
            replacement,
            limit=limit_per_run,
            **filter_args,
        )
        if not dry_run and replaced:
            self._save._save_document(document, resolved)
        return {"replacedCount": replaced}

    def add_paragraph(
        self,
        path: str,
        text: str = "",
        *,
        section_index: Optional[int] = None,
        run_style: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        document, resolved = self._context._open_document(path)
        char_id = self._styles._ensure_char_style(document, run_style)
        paragraph = document.add_paragraph(
            text,
            section_index=section_index,
            char_pr_id_ref=char_id,
        )
        paragraphs = self._context._iter_paragraphs(document)
        index = len(paragraphs) - 1
        element_id = id(paragraph.element)
        for idx, candidate in enumerate(paragraphs):
            if id(candidate.element) == element_id:
                index = idx
                break
        self._save._save_document(document, resolved)
        return {"paragraphIndex": index}

    def insert_paragraphs_bulk(
        self,
        path: str,
        paragraphs: Sequence[str],
        *,
        section_index: Optional[int] = None,
        run_style: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        if not paragraphs:
            return {"added": 0}

        if dry_run:
            return {"added": len(paragraphs)}

        document, resolved = self._context._open_document(path)
        char_id = self._styles._ensure_char_style(document, run_style)
        count = 0
        for text in paragraphs:
            document.add_paragraph(
                text,
                section_index=section_index,
                char_pr_id_ref=char_id,
            )
            count += 1
        self._save._save_document(document, resolved)
        return {"added": count}

    def set_paragraph_format(
        self,
        path: str,
        *,
        paragraph_index: Optional[int] = None,
        paragraph_indexes: Optional[Sequence[int]] = None,
        alignment: Optional[str] = None,
        line_spacing_percent: Optional[float] = None,
        indent_left_mm: Optional[float] = None,
        indent_right_mm: Optional[float] = None,
        first_line_indent_mm: Optional[float] = None,
        spacing_before_pt: Optional[float] = None,
        spacing_after_pt: Optional[float] = None,
        outline_level: Optional[int] = None,
        keep_with_next: Optional[bool] = None,
        keep_lines: Optional[bool] = None,
        page_break_before: Optional[bool] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        document, resolved = self._context._open_document(path)
        result = document.set_paragraph_format(
            paragraph_index=paragraph_index,
            paragraph_indexes=paragraph_indexes,
            alignment=alignment,
            line_spacing_percent=line_spacing_percent,
            indent_left_mm=indent_left_mm,
            indent_right_mm=indent_right_mm,
            first_line_indent_mm=first_line_indent_mm,
            spacing_before_pt=spacing_before_pt,
            spacing_after_pt=spacing_after_pt,
            outline_level=outline_level,
            keep_with_next=keep_with_next,
            keep_lines=keep_lines,
            page_break_before=page_break_before,
        )
        result.update({"ok": True, "filename": path})
        return self._transactions._with_transaction_verification(
            result, document, resolved, dry_run=dry_run
        )

    def set_page_setup(
        self,
        path: str,
        *,
        paper_size: Optional[str] = None,
        width_mm: Optional[float] = None,
        height_mm: Optional[float] = None,
        orientation: Optional[str] = None,
        margins_mm: Optional[Dict[str, float]] = None,
        margin_left_mm: Optional[float] = None,
        margin_right_mm: Optional[float] = None,
        margin_top_mm: Optional[float] = None,
        margin_bottom_mm: Optional[float] = None,
        header_margin_mm: Optional[float] = None,
        footer_margin_mm: Optional[float] = None,
        gutter_mm: Optional[float] = None,
        columns: Optional[int] = None,
        column_gap_mm: Optional[float] = None,
        section_index: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        document, resolved = self._context._open_document(path)
        result = document.set_page_setup(
            paper_size=paper_size,
            width_mm=width_mm,
            height_mm=height_mm,
            orientation=orientation,
            margins_mm=margins_mm,
            margin_left_mm=margin_left_mm,
            margin_right_mm=margin_right_mm,
            margin_top_mm=margin_top_mm,
            margin_bottom_mm=margin_bottom_mm,
            header_margin_mm=header_margin_mm,
            footer_margin_mm=footer_margin_mm,
            gutter_mm=gutter_mm,
            columns=columns,
            column_gap_mm=column_gap_mm,
            section_index=section_index,
        )
        result.update({"ok": True, "filename": path})
        return self._transactions._with_transaction_verification(
            result, document, resolved, dry_run=dry_run
        )

    def _header_footer_payload(
        self,
        wrapper: Any,
        *,
        kind: str,
        page_type: str,
    ) -> Dict[str, Any]:
        element = getattr(wrapper, "element", None)
        page_number_count = 0
        if element is not None and hasattr(element, "iter"):
            page_number_count = sum(1 for _ in element.iter(f"{HP_NS}pageNum"))
        return {
            "kind": kind,
            "pageType": page_type,
            "id": getattr(wrapper, "id", None),
            "text": getattr(wrapper, "text", ""),
            "pageNumberCount": page_number_count,
        }

    def set_header_footer(
        self,
        path: str,
        *,
        kind: str,
        text: Optional[str] = None,
        content: Optional[Sequence[Dict[str, Any]]] = None,
        section_index: Optional[int] = None,
        page_type: str = "BOTH",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        document, resolved = self._context._open_document(path)
        wrapper = document.set_header_footer(
            kind=kind,
            text=text,
            content=content,
            section_index=section_index,
            page_type=page_type,
        )
        result = {
            "ok": True,
            "filename": path,
            "headerFooter": self._header_footer_payload(
                wrapper,
                kind=kind,
                page_type=page_type,
            ),
        }
        return self._transactions._with_transaction_verification(
            result, document, resolved, dry_run=dry_run
        )

    def set_page_number(
        self,
        path: str,
        *,
        target: str = "footer",
        page_type: str = "BOTH",
        format: str = "page",
        align: str = "CENTER",
        position: str = "BOTTOM_CENTER",
        prefix: str = "",
        suffix: str = "",
        format_type: Optional[str] = None,
        section_index: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        document, resolved = self._context._open_document(path)
        wrapper = document.set_page_number(
            target=target,
            page_type=page_type,
            format=format,
            align=align,
            position=position,
            prefix=prefix,
            suffix=suffix,
            format_type=format_type,
            section_index=section_index,
        )
        result = {
            "ok": True,
            "filename": path,
            "target": target,
            "format": format,
            "headerFooter": self._header_footer_payload(
                wrapper,
                kind=target,
                page_type=page_type,
            ),
        }
        return self._transactions._with_transaction_verification(
            result, document, resolved, dry_run=dry_run
        )

    def set_list_format(
        self,
        path: str,
        *,
        paragraph_index: Optional[int] = None,
        paragraph_indexes: Optional[Sequence[int]] = None,
        kind: str = "bullet",
        level: int = 1,
        bullet_char: Optional[str] = None,
        number_format: Optional[str] = None,
        start: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        document, resolved = self._context._open_document(path)
        result = document.set_list_format(
            paragraph_index=paragraph_index,
            paragraph_indexes=paragraph_indexes,
            kind=kind,
            level=level,
            bullet_char=bullet_char,
            number_format=number_format,
            start=start,
        )
        result.update({"ok": True, "filename": path})
        return self._transactions._with_transaction_verification(
            result, document, resolved, dry_run=dry_run
        )
