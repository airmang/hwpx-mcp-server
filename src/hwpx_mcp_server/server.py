# SPDX-License-Identifier: Apache-2.0
"""Stateless HWPX MCP 서버."""

from __future__ import annotations

import argparse
import base64
import binascii
import html
import json
import os
import re
import tempfile
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import mcp.types as mcp_types
from mcp.server.fastmcp import FastMCP

from .core.content import (
    add_heading_to_doc,
    add_memo_to_doc,
    add_page_break_to_doc,
    add_paragraph_to_doc,
    add_table_to_doc,
    collect_full_text,
    copy_document_file,
    delete_paragraph_from_doc,
    fill_by_path_in_doc,
    find_cell_by_label_in_doc,
    format_table_in_doc,
    get_paragraph_text_from_doc,
    get_table_map_in_doc,
    get_table_data,
    insert_paragraph_to_doc,
    merge_cells_in_table,
    remove_memo_from_doc,
    set_cell_text,
    split_cell_in_table,
)
from .core.document import create_blank, open_doc, save_doc
from .core.formatting import create_style_in_doc, format_text_range, list_styles_in_doc
from .core.locations import location_from_anchor, resolve_paragraph_reference
from .core.search import _replace_in_runs, batch_replace_in_doc, find_in_doc, replace_in_doc
from .core.transactions import (
    rotate_and_backup,
    save_dry_run,
    semantic_diff,
    undo_last_backup,
)
from .form_fill import analyze_form_fill_workflow, apply_form_fill_workflow
from .hwpx_ops import HwpxOps
from .quality_generation import (
    analyze_quality_generation_workflow,
    apply_quality_generation_workflow,
    create_quality_document_fallback,
    inspect_quality_fallback,
)
from .storage import build_hwpx_open_safety_report, build_hwpx_verification_report
from .upstream import HP_NS, create_text_extractor, open_document
from .utils.helpers import default_max_chars, resolve_path, truncate_response

try:  # python-hwpx >= proposal preset feature
    from hwpx.presets import (
        create_proposal_document as build_proposal_document,
        inspect_proposal_quality as inspect_proposal_document_quality,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    build_proposal_document = None
    inspect_proposal_document_quality = None

try:  # python-hwpx >= document-plan authoring feature
    from hwpx import (
        create_document_from_plan as build_document_from_plan,
        inspect_document_authoring_quality as inspect_authoring_document_quality,
        inspect_operating_plan_quality as inspect_operating_plan_document_quality,
        normalize_document_plan as normalize_hwpx_document_plan,
        validate_document_plan as validate_hwpx_document_plan,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    build_document_from_plan = None
    inspect_authoring_document_quality = None
    inspect_operating_plan_document_quality = None
    normalize_hwpx_document_plan = None
    validate_hwpx_document_plan = None

try:  # python-hwpx >= government-report tools
    from hwpx.tools import report_utils as hwpx_report_utils
    from hwpx.tools.report_parser import (
        parse_government_report_text as parse_hwpx_government_report_text,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    hwpx_report_utils = None
    parse_hwpx_government_report_text = None

try:  # python-hwpx >= template form-fit feature
    from hwpx import (
        analyze_template_formfit as analyze_hwpx_template_formfit,
        apply_template_formfit as apply_hwpx_template_formfit,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    analyze_hwpx_template_formfit = None
    apply_hwpx_template_formfit = None

try:  # python-hwpx >= byte-preserving patch feature
    from hwpx.patch import paragraph_patch as hwpx_paragraph_patch
except Exception:  # pragma: no cover - optional dependency compatibility
    hwpx_paragraph_patch = None

mcp = FastMCP("hwpx-mcp-server")


def _error_data(
    message: str,
    *,
    tool_name: str | None = None,
    arguments: dict | None = None,
    code: int = -32000,
) -> mcp_types.ErrorData:
    data: dict[str, object] = {}
    if tool_name is not None:
        data["tool"] = tool_name
    if arguments is not None:
        data["arguments"] = arguments
    return mcp_types.ErrorData(code=code, message=message, data=data)


def _first_text_content(content: object) -> str | None:
    if not isinstance(content, list):
        return None
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            stripped = text.strip()
            if stripped:
                return stripped
        if isinstance(item, dict):
            value = item.get("text")
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
    return None


async def _strict_call_tool_handler(req: mcp_types.CallToolRequest):
    tool_name = req.params.name
    arguments = req.params.arguments or {}
    try:
        result = await mcp.call_tool(tool_name, arguments)
    except Exception as exc:
        return _error_data(str(exc), tool_name=tool_name, arguments=arguments)

    if isinstance(result, mcp_types.CreateTaskResult):
        return mcp_types.ServerResult(result)

    if isinstance(result, mcp_types.CallToolResult):
        if bool(result.isError):
            text = _first_text_content(result.content) or f"Tool '{tool_name}' returned an error"
            return _error_data(text, tool_name=tool_name, arguments=arguments)
        return mcp_types.ServerResult(result)

    if isinstance(result, tuple) and len(result) == 2:
        unstructured_content = list(result[0])
        structured_content = result[1]
    elif isinstance(result, dict):
        structured_content = result
        unstructured_content = [
            mcp_types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))
        ]
    elif isinstance(result, str):
        structured_content = None
        unstructured_content = [mcp_types.TextContent(type="text", text=result)]
    elif hasattr(result, "__iter__"):
        structured_content = None
        unstructured_content = list(result)
    else:
        return _error_data(
            f"Unexpected return type from tool '{tool_name}': {type(result).__name__}",
            tool_name=tool_name,
            arguments=arguments,
        )

    return mcp_types.ServerResult(
        mcp_types.CallToolResult(
            content=unstructured_content,
            structuredContent=structured_content,
            isError=False,
        )
    )


# FastMCP default call_tool handler serializes exceptions as result.isError.
# Replace it so real tool failures surface as JSON-RPC error objects.
mcp._mcp_server.request_handlers[mcp_types.CallToolRequest] = _strict_call_tool_handler


def _advanced_enabled() -> bool:
    return os.environ.get("HWPX_MCP_ADVANCED", "0") == "1"


_OPS = HwpxOps(auto_backup=False)

_OUTPUT_MODES = {"full", "chunks"}
_CHUNK_STRATEGIES = {"section", "paragraph"}
_TABLE_LABEL_DIRECTIONS = ("right", "down")
_DEFAULT_MAX_CHARS_PER_CHUNK = 8000
_DEFAULT_MAX_INPUT_BYTES = 20 * 1024 * 1024
_DEFAULT_FETCH_TIMEOUT_SECONDS = 20.0
_EXPECTED_FASTMCP_TOOL_COUNT = 59
_EXPECTED_LEGACY_TOOL_COUNT = 54
_KEY_TOOL_NAMES = (
    "create_document_from_plan",
    "create_government_report_document",
    "repair_hwpx",
    "replace_by_anchor",
    "add_memo_by_anchor",
    "byte_preserving_patch",
    "render_preview",
    "apply_edits",
    "undo_last_edit",
)
_FIGURE_CAPTION_RE = re.compile(r"^\s*(?:Figure|Fig\.|그림)\s*\d*", re.IGNORECASE)


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unknown"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(1, parsed)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return max(0.1, parsed)


def _fastmcp_tool_names() -> list[str]:
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:
        return []
    tools = getattr(manager, "list_tools", lambda: [])()
    return sorted(getattr(tool, "name", "") for tool in tools if getattr(tool, "name", ""))


def _legacy_tool_names() -> list[str]:
    try:
        from .tools import build_tool_definitions

        return sorted(definition.name for definition in build_tool_definitions())
    except Exception:  # pragma: no cover - reported through health, not hidden
        return []


def _normalize_output_mode(output: str | None) -> str:
    value = (output or "full").strip().lower()
    if value not in _OUTPUT_MODES:
        expected = ", ".join(sorted(_OUTPUT_MODES))
        raise ValueError(f"output must be one of: {expected}")
    return value


def _normalize_chunk_strategy(chunk_strategy: str | None) -> str:
    value = (chunk_strategy or "section").strip().lower()
    if value not in _CHUNK_STRATEGIES:
        expected = ", ".join(sorted(_CHUNK_STRATEGIES))
        raise ValueError(f"chunk_strategy must be one of: {expected}")
    return value


def _resolve_chunk_size(max_chars_per_chunk: int | None) -> int:
    if max_chars_per_chunk is None:
        return _env_int("HWPX_MCP_MAX_CHARS_PER_CHUNK", _DEFAULT_MAX_CHARS_PER_CHUNK)
    if max_chars_per_chunk <= 0:
        raise ValueError("max_chars_per_chunk must be greater than 0")
    return max_chars_per_chunk


def _normalize_heading_text(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("#"):
        return stripped.lstrip("#").strip()
    return stripped


def _normalize_table_label_direction(direction: str | None) -> str:
    value = (direction or "right").strip().lower()
    if value not in _TABLE_LABEL_DIRECTIONS:
        expected = ", ".join(_TABLE_LABEL_DIRECTIONS)
        raise ValueError(f"direction must be one of: {expected}")
    return value


def _normalize_fill_mappings(mappings: dict[str, str]) -> dict[str, str]:
    if not isinstance(mappings, dict):
        raise ValueError("mappings must be an object mapping path strings to text values")
    if not mappings:
        raise ValueError("mappings must not be empty")

    normalized: dict[str, str] = {}
    for path, value in mappings.items():
        if not isinstance(path, str) or not path.strip():
            raise ValueError("mappings keys must be non-empty strings")
        normalized[path] = value if isinstance(value, str) else str(value)
    return normalized


def _looks_like_figure_caption(text: str) -> bool:
    return bool(_FIGURE_CAPTION_RE.match((text or "").strip()))


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


def _download_hwpx_from_url(url: str, *, max_input_bytes: int) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("url must use https://")

    request = Request(url, headers={"User-Agent": "hwpx-mcp-server/2"})
    timeout = _env_float("HWPX_MCP_FETCH_TIMEOUT_SECONDS", _DEFAULT_FETCH_TIMEOUT_SECONDS)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(max_input_bytes + 1)
    except HTTPError as exc:
        raise ValueError(f"failed to download url: HTTP {exc.code}") from exc
    except URLError as exc:
        raise ValueError(f"failed to download url: {exc.reason}") from exc

    if len(payload) > max_input_bytes:
        raise ValueError(f"input is too large: limit is {max_input_bytes} bytes")
    if not payload:
        raise ValueError("downloaded payload is empty")
    return payload


def _load_hwpx_payload(hwpx_base64: str | None, url: str | None) -> tuple[bytes, dict[str, Any]]:
    use_base64 = bool(hwpx_base64 and hwpx_base64.strip())
    use_url = bool(url and url.strip())
    if use_base64 == use_url:
        raise ValueError("provide exactly one of hwpx_base64 or url")

    max_input_bytes = _env_int("HWPX_MCP_MAX_INPUT_BYTES", _DEFAULT_MAX_INPUT_BYTES)
    if use_base64:
        try:
            payload = base64.b64decode((hwpx_base64 or "").strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid hwpx_base64 payload") from exc
        if len(payload) > max_input_bytes:
            raise ValueError(f"input is too large: limit is {max_input_bytes} bytes")
        if not payload:
            raise ValueError("hwpx_base64 decoded to empty payload")
        return payload, {"source_type": "base64", "size_bytes": len(payload)}

    source_url = (url or "").strip()
    payload = _download_hwpx_from_url(source_url, max_input_bytes=max_input_bytes)
    return payload, {"source_type": "url", "source_url": source_url, "size_bytes": len(payload)}


def _open_hwpx_from_payload(hwpx_base64: str | None, url: str | None):
    payload, source_meta = _load_hwpx_payload(hwpx_base64, url)
    try:
        doc = open_document(BytesIO(payload))
    except Exception as exc:  # pragma: no cover - delegated to parser
        raise ValueError(f"failed to parse hwpx payload: {exc}") from exc
    return doc, source_meta


def _table_rows(table: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in getattr(table, "rows", []):
        rows.append([(cell.text or "") for cell in getattr(row, "cells", [])])
    return rows


def _table_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max((len(row) for row in rows), default=0)
    if width <= 0:
        return ""

    def _pad(row: list[str]) -> list[str]:
        return row + [""] * (width - len(row))

    normalized = [_pad([str(cell) for cell in row]) for row in rows]
    header = normalized[0]
    divider = ["---"] * width

    def _render(cells: list[str]) -> str:
        escaped = [cell.replace("|", r"\|").replace("\n", "<br>") for cell in cells]
        return f"| {' | '.join(escaped)} |"

    lines = [_render(header), _render(divider)]
    for row in normalized[1:]:
        lines.append(_render(row))
    return "\n".join(lines)


def _table_to_html(rows: list[list[str]]) -> str:
    if not rows:
        return "<table></table>"
    width = max((len(row) for row in rows), default=0)
    if width <= 0:
        return "<table></table>"

    def _pad(row: list[str]) -> list[str]:
        return row + [""] * (width - len(row))

    normalized = [_pad([str(cell) for cell in row]) for row in rows]
    header = normalized[0]
    body_rows = normalized[1:]

    head_html = "".join(f"<th>{html.escape(cell)}</th>" for cell in header)
    body_html = []
    for row in body_rows:
        cells = "".join(f"<td>{html.escape(cell)}</td>" for cell in row)
        body_html.append(f"<tr>{cells}</tr>")

    if body_html:
        body = "<tbody>" + "".join(body_html) + "</tbody>"
    else:
        body = ""
    return f"<table><thead><tr>{head_html}</tr></thead>{body}</table>"


def _build_read_model(doc: Any) -> dict[str, Any]:
    toc: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    current_section: dict[str, Any] = {
        "index": 0,
        "title": None,
        "level": 0,
        "paragraphs": [],
        "tables": [],
        "figures": [],
    }

    def _flush_current_section() -> None:
        if not current_section["paragraphs"] and not current_section["tables"] and not current_section["figures"]:
            return
        sections.append(
            {
                "index": len(sections),
                "title": current_section["title"],
                "level": current_section["level"],
                "paragraphs": list(current_section["paragraphs"]),
                "tables": list(current_section["tables"]),
                "figures": list(current_section["figures"]),
            }
        )

    table_index = 0
    for paragraph_index, paragraph in enumerate(doc.paragraphs):
        text = (paragraph.text or "").strip()
        level = _outline_level(text)

        if level > 0 and text:
            _flush_current_section()
            current_section = {
                "index": len(sections),
                "title": _normalize_heading_text(text),
                "level": level,
                "paragraphs": [],
                "tables": [],
                "figures": [],
            }
            heading_text = _normalize_heading_text(text)
            toc.append({"level": level, "text": heading_text, "paragraph_index": paragraph_index})
            items.append(
                {
                    "type": "heading",
                    "level": level,
                    "text": heading_text,
                    "paragraph_index": paragraph_index,
                }
            )
        elif text:
            items.append({"type": "paragraph", "text": text, "paragraph_index": paragraph_index})

        if text:
            current_section["paragraphs"].append({"index": paragraph_index, "text": text})
            if _looks_like_figure_caption(text):
                figure = {
                    "figure_index": len(figures),
                    "paragraph_index": paragraph_index,
                    "caption": text,
                }
                figures.append(figure)
                current_section["figures"].append(figure)

        for table in getattr(paragraph, "tables", []):
            rows = _table_rows(table)
            table_payload = {
                "table_index": table_index,
                "paragraph_index": paragraph_index,
                "rows": len(rows),
                "cols": max((len(row) for row in rows), default=0),
                "data": rows,
            }
            tables.append(table_payload)
            current_section["tables"].append(table_payload)
            items.append(
                {
                    "type": "table",
                    "table_index": table_index,
                    "paragraph_index": paragraph_index,
                    "data": rows,
                }
            )
            table_index += 1

    _flush_current_section()
    return {
        "title": toc[0]["text"] if toc else None,
        "toc": toc,
        "sections": sections,
        "tables": tables,
        "figures": figures,
        "items": items,
    }


def _render_markdown(model: dict[str, Any]) -> str:
    blocks: list[str] = []
    for item in model["items"]:
        kind = item["type"]
        if kind == "heading":
            level = max(1, min(6, int(item["level"])))
            blocks.append(f"{'#' * level} {item['text']}")
        elif kind == "paragraph":
            blocks.append(item["text"])
        elif kind == "table":
            table_markdown = _table_to_markdown(item["data"])
            if table_markdown:
                blocks.append(table_markdown)
    return "\n\n".join(blocks).strip()


def _render_html(model: dict[str, Any]) -> str:
    body: list[str] = ['<article class="hwpx-document">']
    for item in model["items"]:
        kind = item["type"]
        if kind == "heading":
            level = max(1, min(6, int(item["level"])))
            body.append(f"<h{level}>{html.escape(item['text'])}</h{level}>")
        elif kind == "paragraph":
            body.append(f"<p>{html.escape(item['text'])}</p>")
        elif kind == "table":
            body.append(_table_to_html(item["data"]))
    body.append("</article>")
    return (
        "<!doctype html>"
        "<html><head><meta charset='utf-8'><title>HWPX Document</title></head>"
        f"<body>{''.join(body)}</body></html>"
    )


def _section_markdown(section: dict[str, Any]) -> str:
    blocks: list[str] = []
    title = section.get("title")
    level = max(1, min(6, int(section.get("level") or 1)))
    paragraphs = list(section.get("paragraphs") or [])
    if title:
        blocks.append(f"{'#' * level} {title}")
        if paragraphs and paragraphs[0].get("text") == title:
            paragraphs = paragraphs[1:]
    for paragraph in paragraphs:
        text = (paragraph.get("text") or "").strip()
        if text:
            blocks.append(text)
    for table in section.get("tables") or []:
        markdown_table = _table_to_markdown(table.get("data") or [])
        if markdown_table:
            blocks.append(markdown_table)
    return "\n\n".join(blocks).strip()


def _section_html(section: dict[str, Any]) -> str:
    parts: list[str] = ["<section>"]
    title = section.get("title")
    level = max(1, min(6, int(section.get("level") or 1)))
    paragraphs = list(section.get("paragraphs") or [])
    if title:
        parts.append(f"<h{level}>{html.escape(title)}</h{level}>")
        if paragraphs and paragraphs[0].get("text") == title:
            paragraphs = paragraphs[1:]
    for paragraph in paragraphs:
        text = (paragraph.get("text") or "").strip()
        if text:
            parts.append(f"<p>{html.escape(text)}</p>")
    for table in section.get("tables") or []:
        parts.append(_table_to_html(table.get("data") or []))
    parts.append("</section>")
    return "".join(parts)


def _chunk_paragraphs(paragraphs: list[str], max_chars_per_chunk: int) -> list[str]:
    chunks: list[str] = []
    buffer: list[str] = []
    used = 0
    for paragraph in paragraphs:
        text = (paragraph or "").strip()
        if not text:
            continue
        additional = len(text) + (2 if buffer else 0)
        if buffer and used + additional > max_chars_per_chunk:
            chunks.append("\n\n".join(buffer))
            buffer = []
            used = 0
        if len(text) > max_chars_per_chunk:
            if buffer:
                chunks.append("\n\n".join(buffer))
                buffer = []
                used = 0
            for start in range(0, len(text), max_chars_per_chunk):
                chunks.append(text[start : start + max_chars_per_chunk])
            continue
        buffer.append(text)
        used += additional
    if buffer:
        chunks.append("\n\n".join(buffer))
    return chunks


def _markdown_chunks(model: dict[str, Any], *, chunk_strategy: str, max_chars_per_chunk: int) -> list[str]:
    if chunk_strategy == "section":
        return [chunk for section in model["sections"] if (chunk := _section_markdown(section))]

    paragraphs = [item["text"] for item in model["items"] if item["type"] in {"heading", "paragraph"}]
    return _chunk_paragraphs(paragraphs, max_chars_per_chunk)


def _html_chunks(model: dict[str, Any], *, chunk_strategy: str, max_chars_per_chunk: int) -> list[str]:
    if chunk_strategy == "section":
        chunks = [_section_html(section) for section in model["sections"]]
        return [chunk for chunk in chunks if chunk]

    paragraphs = [f"<p>{html.escape(item['text'])}</p>" for item in model["items"] if item["type"] in {"heading", "paragraph"}]
    plain_chunks = _chunk_paragraphs(paragraphs, max_chars_per_chunk)
    return [f"<article class='hwpx-document'>{chunk}</article>" for chunk in plain_chunks]


def _json_chunks(model: dict[str, Any], *, chunk_strategy: str, max_chars_per_chunk: int) -> list[dict[str, Any]]:
    if chunk_strategy == "section":
        return [
            {"chunk_index": index, "strategy": "section", "section": section}
            for index, section in enumerate(model["sections"])
        ]

    paragraphs = []
    for section in model["sections"]:
        for paragraph in section.get("paragraphs") or []:
            paragraphs.append(
                {
                    "section_index": section.get("index"),
                    "paragraph_index": paragraph.get("index"),
                    "text": paragraph.get("text") or "",
                }
            )
    groups = _chunk_paragraphs([item["text"] for item in paragraphs], max_chars_per_chunk)
    chunks: list[dict[str, Any]] = []
    offset = 0
    for chunk_index, chunk in enumerate(groups):
        consumed = len(chunk.split("\n\n")) if chunk else 0
        selected = paragraphs[offset : offset + consumed]
        offset += consumed
        chunks.append(
            {
                "chunk_index": chunk_index,
                "strategy": "paragraph",
                "paragraphs": selected,
            }
        )
    return chunks


def _build_conversion_meta(model: dict[str, Any], source_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_type": source_meta.get("source_type"),
        "source_url": source_meta.get("source_url"),
        "size_bytes": source_meta.get("size_bytes"),
        "section_count": len(model["sections"]),
        "paragraph_count": sum(1 for item in model["items"] if item["type"] in {"heading", "paragraph"}),
        "table_count": len(model["tables"]),
        "figure_caption_count": len(model["figures"]),
    }


def _paragraph_count(doc) -> int:
    return len(doc.paragraphs)


def _table_count(doc) -> int:
    table_tag = f"{HP_NS}tbl"
    count = 0
    for section in getattr(doc, "sections", []):
        section_element = getattr(section, "element", None)
        if section_element is None or not hasattr(section_element, "iter"):
            continue
        count += sum(1 for _ in section_element.iter(table_tag))
    return count


def _outline_level(text: str) -> int:
    stripped = (text or "").strip()
    if not stripped:
        return 0
    if stripped.startswith("#"):
        return min(6, len(stripped) - len(stripped.lstrip("#")))
    if stripped[:2].isdigit() and "." in stripped[:6]:
        return 2
    if stripped[:1].isdigit() and "." in stripped[:4]:
        return 1
    return 1 if len(stripped) < 60 else 0


@mcp.tool()
def create_document(filename: str, title: str = None, author: str = None) -> dict:
    """새 HWPX 문서를 생성하고 즉시 저장합니다."""
    del title, author
    path = resolve_path(filename)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    verification = create_blank(path)
    return {
        "filename": filename,
        "created": True,
        "verification": verification,
        "openSafety": verification["openSafety"],
    }


def _save_generated_document(doc: Any, path: str) -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.stem}.",
        suffix=target.suffix or ".hwpx",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        os.close(fd)
        doc.save_to_path(tmp_path)
        verification = build_hwpx_verification_report(tmp_path)
        if not verification["openSafety"]["ok"]:
            raise RuntimeError(
                "generated HWPX failed open-safety verification: "
                + verification["openSafety"]["summary"]
            )
        os.replace(tmp_path, target)
        verification["filePath"] = str(target)
        return verification
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _save_doc_verification(doc: Any, path: str) -> dict[str, Any]:
    target = Path(path)
    backup = rotate_and_backup(target)
    verification = save_doc(doc, path)
    if not isinstance(verification, dict):
        verification = build_hwpx_verification_report(target)
    verification["filePath"] = str(target)
    verification["backup"] = backup.to_dict()
    if backup.backup_path is not None:
        try:
            verification["semanticDiff"] = semantic_diff(backup.backup_path, target)
        except Exception as exc:  # pragma: no cover - diagnostic fallback
            verification["semanticDiff"] = {
                "schemaVersion": "hwpx.semantic-diff.v1",
                "changed": True,
                "summary": f"Semantic diff unavailable: {exc}",
                "items": [],
                "error": str(exc),
            }
    return verification


def _with_save_verification(result: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    payload.setdefault("dryRun", False)
    payload["verificationReport"] = verification
    payload.setdefault("openSafety", verification.get("openSafety"))
    if "semanticDiff" in verification:
        payload.setdefault("semanticDiff", verification["semanticDiff"])
    if "backup" in verification:
        payload.setdefault("backup", verification["backup"])
    return payload


def _with_dry_run_verification(result: dict[str, Any], doc: Any, path: str) -> dict[str, Any]:
    payload = dict(result)
    dry_run = save_dry_run(doc, path)
    payload.update(dry_run)
    return payload


def _write_verified_patch_result(target: Path, payload: bytes) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = rotate_and_backup(target)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.stem}.",
        suffix=target.suffix or ".hwpx",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        os.close(fd)
        tmp_path.write_bytes(payload)
        verification = build_hwpx_verification_report(tmp_path)
        if not verification["openSafety"]["ok"]:
            raise RuntimeError(
                "patched HWPX failed open-safety verification: "
                + verification["openSafety"]["summary"]
            )
        os.replace(tmp_path, target)
        verification["filePath"] = str(target)
        verification["backup"] = backup.to_dict()
        if backup.backup_path is not None:
            verification["semanticDiff"] = semantic_diff(backup.backup_path, target)
        return verification
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _quality_profile_argument(
    quality_profile: str | dict | None,
    profile: dict | None = None,
) -> str | dict | None:
    """Normalize MCP quality-profile arguments for python-hwpx."""

    if profile:
        merged = dict(profile)
        if quality_profile:
            merged.setdefault("name", quality_profile)
        return merged
    return quality_profile


def _inspect_authoring_quality(
    source: str | Any,
    *,
    document_plan: dict | None,
    quality_profile: str | dict | None = None,
    profile: dict | None = None,
) -> dict:
    if inspect_authoring_document_quality is None:
        raise RuntimeError("installed python-hwpx does not provide document-plan authoring")
    profile_arg = _quality_profile_argument(quality_profile, profile)
    try:
        if profile_arg is None:
            return inspect_authoring_document_quality(source, plan=document_plan)
        return inspect_authoring_document_quality(
            source,
            plan=document_plan,
            quality_profile=profile_arg,
        )
    except TypeError as exc:
        if profile_arg is not None:
            raise RuntimeError(
                "installed python-hwpx does not support document-plan quality profiles"
            ) from exc
        raise


def _handoff_status(quality: dict) -> str:
    return "ready" if bool(quality.get("pass")) else "needs_revision"


def _next_action(quality: dict) -> str:
    if bool(quality.get("pass")):
        return "structural handoff is ready; complete visual review before final submission"
    return "review quality.gaps and profile repair_hints, then rerun validate/analyze/create"


@mcp.tool()
def validate_document_plan(document_plan: dict) -> dict:
    """선언형 hwpx.document_plan.v1 생성 계획을 검증합니다. 파일은 쓰지 않습니다."""
    if validate_hwpx_document_plan is None or normalize_hwpx_document_plan is None:
        raise RuntimeError("installed python-hwpx does not provide document-plan authoring")
    report = validate_hwpx_document_plan(document_plan or {})
    result = report.to_dict()
    if report.ok:
        result["can_create"] = True
        result["normalizedPlan"] = normalize_hwpx_document_plan(document_plan or {}).to_dict()
        result["next_tool"] = "create_document_from_plan"
    else:
        result["can_create"] = False
        result["next_tool"] = "validate_document_plan"
        result["next_action"] = (
            "repair document_plan using repairHints, then rerun validate_document_plan"
        )
    return result


@mcp.tool()
def analyze_document_plan(
    document_plan: dict,
    destination_filename: str = None,
    style_preset: str = "standard_korean_business",
    quality_profile: str = None,
    profile: dict = None,
) -> dict:
    """선언형 document_plan을 파일 쓰기 없이 분석하고 품질 미리보기를 반환합니다."""
    if (
        build_document_from_plan is None
        or validate_hwpx_document_plan is None
        or normalize_hwpx_document_plan is None
    ):
        raise RuntimeError("installed python-hwpx does not provide document-plan authoring")
    validation = validate_hwpx_document_plan(document_plan or {})
    validation_payload = validation.to_dict()
    result = {
        **validation_payload,
        "mutated": False,
        "destination": {
            "filename": destination_filename,
            "path": resolve_path(destination_filename) if destination_filename else None,
            "required_for_create": bool(destination_filename),
        },
        "style_preset": style_preset,
        "quality_profile": _quality_profile_argument(quality_profile, profile),
    }
    if not validation.ok:
        result.update(
            {
                "can_create": False,
                "handoff_status": "needs_revision",
                "next_tool": "validate_document_plan",
                "next_action": (
                    "repair document_plan using repairHints, then rerun analyze_document_plan"
                ),
            }
        )
        return result

    normalized = normalize_hwpx_document_plan(document_plan or {})
    quality: dict | None = None
    doc = build_document_from_plan(document_plan or {}, preset=style_preset)
    try:
        quality = _inspect_authoring_quality(
            doc,
            document_plan=document_plan or {},
            quality_profile=quality_profile,
            profile=profile,
        )
    finally:
        doc.close()

    result.update(
        {
            "can_create": True,
            "normalizedPlan": normalized.to_dict(),
            "quality_preview": quality,
            "handoff_status": _handoff_status(quality),
            "next_tool": "create_document_from_plan",
            "next_action": _next_action(quality),
        }
    )
    return result


@mcp.tool()
def create_document_from_plan(
    filename: str,
    document_plan: dict,
    style_preset: str = "standard_korean_business",
    quality_profile: str = None,
    profile: dict = None,
) -> dict:
    """선언형 document_plan으로 HWPX를 생성하고 즉시 저장/검증합니다."""
    return _create_document_from_plan_impl(
        filename,
        document_plan,
        style_preset=style_preset,
        quality_profile=quality_profile,
        profile=profile,
    )


def _create_document_from_plan_impl(
    filename: str,
    document_plan: dict,
    *,
    style_preset: str = "standard_korean_business",
    quality_profile: str | dict | None = None,
    profile: dict | None = None,
) -> dict:
    if (
        build_document_from_plan is None
        or inspect_authoring_document_quality is None
        or validate_hwpx_document_plan is None
    ):
        raise RuntimeError("installed python-hwpx does not provide document-plan authoring")
    validation = validate_hwpx_document_plan(document_plan or {})
    if not validation.ok:
        return {
            "filename": filename,
            "created": False,
            "error": "document plan failed validation",
            "plan_validation": validation.to_dict(),
            "handoff_status": "needs_revision",
            "next_tool": "validate_document_plan",
            "next_action": (
                "repair document_plan using repairHints, then rerun validate_document_plan"
            ),
        }
    path = resolve_path(filename)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc = build_document_from_plan(document_plan or {}, preset=style_preset)
    try:
        verification = _save_generated_document(doc, path)
    finally:
        doc.close()
    report = _inspect_authoring_quality(
        path,
        document_plan=document_plan or {},
        quality_profile=quality_profile,
        profile=profile,
    )
    return {
        "filename": filename,
        "created": True,
        "style_preset": style_preset,
        "quality_profile": _quality_profile_argument(quality_profile, profile),
        "plan_validation": validation.to_dict(),
        "handoff_status": _handoff_status(report),
        "next_action": _next_action(report),
        "quality": report,
        "verification": verification,
    }


@mcp.tool()
def create_government_report_document(
    filename: str,
    document_plan: dict,
    profile: dict = None,
) -> dict:
    """정부보고서 프리셋으로 document_plan을 생성하고 즉시 저장/검증합니다."""
    return _create_document_from_plan_impl(
        filename,
        document_plan,
        style_preset="government_report",
        quality_profile="government_report",
        profile=profile,
    )


def _report_values(values: list | tuple | dict | None) -> tuple[list, dict]:
    if isinstance(values, dict):
        args = values.get("args")
        if args is None:
            args = values.get("values")
        if args is None:
            args = []
        if not isinstance(args, list | tuple):
            args = [args]
        kwargs = {
            str(key): value
            for key, value in values.items()
            if key not in {"args", "values"}
        }
        return list(args), kwargs
    if isinstance(values, list | tuple):
        args = list(values)
        if args and isinstance(args[-1], dict):
            kwargs = dict(args.pop())
        else:
            kwargs = {}
        return args, kwargs
    if values is None:
        return [], {}
    return [values], {}


def _optional_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


@mcp.tool()
def compute_report_value(operation: str, values: list | dict = None) -> dict:
    """정부보고서 표/문장에 넣을 계산값을 python-hwpx report_utils로 계산합니다."""
    if hwpx_report_utils is None:
        raise RuntimeError("installed python-hwpx does not provide report_utils")

    normalized = str(operation or "").strip().lower().replace("-", "_")
    args, kwargs = _report_values(values)
    try:
        if normalized in {"krw_hangul", "hangul_krw"}:
            value = hwpx_report_utils.format_krw_hangul(args[0])
        elif normalized in {"commas", "number_commas"}:
            value = hwpx_report_utils.format_number_commas(args[0])
        elif normalized == "age":
            today_arg = kwargs.get("today")
            today = date.fromisoformat(today_arg) if today_arg else None
            value = hwpx_report_utils.calculate_age(args[0], today=today)
        elif normalized == "delta":
            value = hwpx_report_utils.format_delta(
                args[0],
                negative_prefix=str(kwargs.get("negative_prefix", "△")),
            )
        elif normalized == "delta_percent":
            value = hwpx_report_utils.format_delta_percent(
                args[0],
                args[1],
                digits=_optional_int(kwargs.get("digits"), 1),
            )
        elif normalized in {"ratio", "ratios"}:
            value = hwpx_report_utils.calculate_ratios(
                args[0],
                args[1],
                digits=_optional_int(kwargs.get("digits"), 1),
            )
        elif normalized in {"date", "normalize_date", "korean_date"}:
            value = hwpx_report_utils.normalize_korean_date(args[0])
        else:
            return {
                "operation": operation,
                "value": None,
                "warnings": [f"unsupported report value operation: {operation}"],
            }
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        return {"operation": operation, "value": None, "warnings": [str(exc)]}

    return {"operation": normalized, "value": value, "warnings": []}


@mcp.tool()
def parse_government_report_text(text: str, title: str = "") -> dict:
    """붙여넣은 정부보고서 텍스트를 document_plan으로 파싱하고 검증합니다."""
    if parse_hwpx_government_report_text is None or validate_hwpx_document_plan is None:
        raise RuntimeError("installed python-hwpx does not provide government-report parsing")

    document_plan = parse_hwpx_government_report_text(text or "", title=title or "")
    validation = validate_hwpx_document_plan(document_plan)
    result = {
        "document_plan": document_plan,
        "plan_validation": validation.to_dict(),
        "can_create": bool(validation.ok),
    }
    if validation.ok:
        result["next_tool"] = "create_government_report_document"
        result["next_action"] = (
            "review document_plan, then call create_government_report_document"
        )
    else:
        result["next_tool"] = "parse_government_report_text"
        result["next_action"] = "repair source text or document_plan using plan_validation"
    return result


@mcp.tool()
def inspect_document_authoring_quality(
    filename: str,
    document_plan: dict = None,
    quality_profile: str = None,
    profile: dict = None,
) -> dict:
    """document-plan 기반 생성물의 reopen/package/schema 품질 근거를 조회합니다."""
    path = resolve_path(filename)
    return _inspect_authoring_quality(
        path,
        document_plan=document_plan,
        quality_profile=quality_profile,
        profile=profile,
    )


@mcp.tool()
def inspect_operating_plan_quality(
    filename: str,
    document_plan: dict = None,
    profile: dict = None,
) -> dict:
    """운영 계획서 제출 후보의 file-only 품질 프로필을 반환합니다."""
    path = resolve_path(filename)
    if inspect_operating_plan_document_quality is not None:
        return inspect_operating_plan_document_quality(path, plan=document_plan, profile=profile)
    report = _inspect_authoring_quality(
        path,
        document_plan=document_plan,
        quality_profile={"name": "operating_plan", **dict(profile or {})},
    )
    return report.get("profiles", {}).get("operating_plan", report)


def _template_formfit_baseline_arg(baseline: dict | str) -> dict | str:
    if isinstance(baseline, dict):
        return baseline
    text = str(baseline or "").strip()
    if text.endswith(".json") or Path(text).exists():
        return resolve_path(text)
    return text


@mcp.tool()
def analyze_template_formfit(
    source_filename: str,
    baseline: dict | str,
    content: dict,
    destination_filename: str = None,
    options: dict = None,
) -> dict:
    """P6 baseline 기반 양식 보존 생성 계획을 비파괴 분석합니다."""
    if analyze_hwpx_template_formfit is None:
        raise RuntimeError("installed python-hwpx does not provide template form-fit")
    return analyze_hwpx_template_formfit(
        resolve_path(source_filename),
        baseline=_template_formfit_baseline_arg(baseline),
        content=content or {},
        destination=resolve_path(destination_filename) if destination_filename else None,
        options=options,
    )


@mcp.tool()
def apply_template_formfit(
    analysis: dict = None,
    source_filename: str = None,
    baseline: dict | str = None,
    content: dict = None,
    destination_filename: str = None,
    confirm: bool = True,
) -> dict:
    """양식 보존 생성 계획을 복사본 destination에만 적용하고 검증합니다."""
    if apply_hwpx_template_formfit is None:
        raise RuntimeError("installed python-hwpx does not provide template form-fit")
    return apply_hwpx_template_formfit(
        analysis=analysis,
        source=resolve_path(source_filename) if source_filename else None,
        baseline=_template_formfit_baseline_arg(baseline) if baseline is not None else None,
        content=content,
        destination=resolve_path(destination_filename) if destination_filename else None,
        confirm=confirm,
    )


@mcp.tool()
def create_proposal_document(
    filename: str,
    proposal_spec: dict,
    style_preset: str = "clean_korean_proposal",
) -> dict:
    """자연어에서 추출한 proposal_spec으로 제안서형 HWPX 문서를 생성합니다."""
    path = resolve_path(filename)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc = (
        build_proposal_document(proposal_spec or {}, preset=style_preset)
        if build_proposal_document is not None
        else create_quality_document_fallback(proposal_spec or {})
    )
    try:
        verification = _save_generated_document(doc, path)
    finally:
        doc.close()

    report = (
        inspect_proposal_document_quality(path)
        if inspect_proposal_document_quality is not None
        else _proposal_quality_fallback(path)
    )
    return {
        "filename": filename,
        "created": True,
        "style_preset": style_preset,
        "quality": report,
        "verification": verification,
    }


@mcp.tool()
def inspect_document_quality(filename: str, rubric: str = "proposal") -> dict:
    """생성된 HWPX 문서를 제안서 품질 루브릭으로 점검합니다."""
    if rubric != "proposal":
        raise ValueError("rubric must be 'proposal'")
    path = resolve_path(filename)
    if inspect_proposal_document_quality is not None:
        return inspect_proposal_document_quality(path)
    return _proposal_quality_fallback(path)


def _proposal_quality_fallback(path: str) -> dict:
    """Compatibility report when installed python-hwpx lacks proposal presets."""

    report = inspect_quality_fallback(path)
    table_checks = dict(report.get("table_checks") or {})
    table_checks.setdefault("has_budget_table", bool(table_checks.get("has_structured_tables")))
    report["table_checks"] = table_checks
    report["report_version"] = "proposal-quality-v2"
    return report


@mcp.tool()
def analyze_quality_generation(
    form_filename: str,
    idea_brief: str | dict,
    destination_filename: str = None,
    quality_profile: str = "korean_ai_school_application_v1",
    options: dict = None,
) -> dict:
    """양식+아이디어만으로 고품질 HWPX 생성을 준비하는 비파괴 분석을 수행합니다."""
    return analyze_quality_generation_workflow(
        form_filename=form_filename,
        idea_brief=idea_brief,
        destination_filename=destination_filename,
        quality_profile=quality_profile,
        options=options,
    )


@mcp.tool()
def apply_quality_generation(
    plan_id: str = None,
    analysis: dict = None,
    form_filename: str = None,
    destination_filename: str = None,
    idea_brief: str | dict = None,
    max_revision_rounds: int = 1,
    confirm: bool = True,
) -> dict:
    """MCP 품질 파이프라인으로 HWPX를 생성하고 검수/개선 루프 결과를 반환합니다."""
    return apply_quality_generation_workflow(
        plan_id=plan_id,
        analysis=analysis,
        form_filename=form_filename,
        destination_filename=destination_filename,
        idea_brief=idea_brief,
        max_revision_rounds=max_revision_rounds,
        confirm=confirm,
    )


@mcp.tool()
def get_document_info(filename: str) -> dict:
    """문서 메타데이터와 구조 요약을 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    file_size = Path(path).stat().st_size
    return {
        "filename": filename,
        "sections": len(doc.sections),
        "paragraphs": _paragraph_count(doc),
        "tables": _table_count(doc),
        "file_size": str(file_size),
    }


@mcp.tool()
def get_document_text(filename: str, max_chars: int | None = None) -> dict:
    """문서 전체 텍스트를 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    return truncate_response(collect_full_text(doc), max_chars=max_chars)


@mcp.tool()
def get_document_outline(filename: str) -> dict:
    """문단 기준 제목/개요 구조를 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    outline: list[dict] = []
    for index, para in enumerate(doc.paragraphs):
        text = (para.text or "").strip()
        level = _outline_level(text)
        if level > 0 and text:
            outline.append({"level": level, "text": text, "paragraph_index": index})
    return {"outline": outline}


@mcp.tool()
def hwpx_to_markdown(
    hwpx_base64: str | None = None,
    url: str | None = None,
    output: str = "full",
    chunk_strategy: str = "section",
    max_chars_per_chunk: int | None = None,
) -> dict:
    """HWPX payload 또는 URL을 Markdown으로 변환합니다."""
    mode = _normalize_output_mode(output)
    strategy = _normalize_chunk_strategy(chunk_strategy)
    chunk_size = _resolve_chunk_size(max_chars_per_chunk)

    doc, source_meta = _open_hwpx_from_payload(hwpx_base64, url)
    model = _build_read_model(doc)
    markdown = _render_markdown(model)

    result: dict[str, Any] = {
        "markdown": markdown,
        "meta": _build_conversion_meta(model, source_meta),
    }
    if mode == "chunks":
        result["chunks"] = _markdown_chunks(
            model,
            chunk_strategy=strategy,
            max_chars_per_chunk=chunk_size,
        )
        result["meta"]["chunk_strategy"] = strategy
        result["meta"]["max_chars_per_chunk"] = chunk_size
    return result


@mcp.tool()
def hwpx_to_html(
    hwpx_base64: str | None = None,
    url: str | None = None,
    output: str = "full",
    chunk_strategy: str = "section",
    max_chars_per_chunk: int | None = None,
) -> dict:
    """HWPX payload 또는 URL을 HTML로 변환합니다."""
    mode = _normalize_output_mode(output)
    strategy = _normalize_chunk_strategy(chunk_strategy)
    chunk_size = _resolve_chunk_size(max_chars_per_chunk)

    doc, source_meta = _open_hwpx_from_payload(hwpx_base64, url)
    model = _build_read_model(doc)
    payload = {
        "html": _render_html(model),
        "meta": _build_conversion_meta(model, source_meta),
    }
    payload["meta"]["image_policy"] = "omitted"

    if mode == "chunks":
        payload["chunks"] = _html_chunks(
            model,
            chunk_strategy=strategy,
            max_chars_per_chunk=chunk_size,
        )
        payload["meta"]["chunk_strategy"] = strategy
        payload["meta"]["max_chars_per_chunk"] = chunk_size
    return payload


@mcp.tool()
def render_preview(
    filename: str,
    output_dir: str | None = None,
    mode: str = "pages",
    screenshot: str = "auto",
    max_pages: int | None = None,
) -> dict:
    """레이아웃 충실 HTML과 headless browser PNG 프리뷰 산출물을 생성합니다."""
    return _OPS.render_preview(
        path=filename,
        output_dir=output_dir,
        mode=mode,
        screenshot=screenshot,
        max_pages=max_pages,
    )


@mcp.tool()
def hwpx_extract_json(
    hwpx_base64: str | None = None,
    url: str | None = None,
    output: str = "full",
    chunk_strategy: str = "section",
    max_chars_per_chunk: int | None = None,
) -> dict:
    """HWPX payload 또는 URL에서 구조화된 JSON을 추출합니다."""
    mode = _normalize_output_mode(output)
    strategy = _normalize_chunk_strategy(chunk_strategy)
    chunk_size = _resolve_chunk_size(max_chars_per_chunk)

    doc, source_meta = _open_hwpx_from_payload(hwpx_base64, url)
    model = _build_read_model(doc)
    doc_payload = {
        "title": model["title"],
        "toc": model["toc"],
        "sections": model["sections"],
        "tables": model["tables"],
        "figures": model["figures"],
    }
    result: dict[str, Any] = {
        "doc": doc_payload,
        "meta": _build_conversion_meta(model, source_meta),
    }
    if mode == "chunks":
        result["chunks"] = _json_chunks(
            model,
            chunk_strategy=strategy,
            max_chars_per_chunk=chunk_size,
        )
        result["meta"]["chunk_strategy"] = strategy
        result["meta"]["max_chars_per_chunk"] = chunk_size
    return result


@mcp.tool()
def get_paragraph_text(
    filename: str,
    paragraph_index: int | None = None,
    location: dict[str, Any] | None = None,
) -> dict:
    """본문 문단 또는 표 셀 문단 텍스트를 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = get_paragraph_text_from_doc(doc, paragraph_index=paragraph_index, location=location)
    if result["location"].get("kind") == "body_paragraph":
        result["paragraph_index"] = result["location"]["paragraph_index"]
    return result


@mcp.tool()
def get_location_text(filename: str, location: dict[str, Any]) -> dict:
    """get_table_map/find_text가 반환한 location으로 텍스트를 조회합니다."""
    return get_paragraph_text(filename, location=location)


@mcp.tool()
def get_paragraphs_text(
    filename: str,
    start_index: int = 0,
    end_index: int = None,
    max_chars: int | None = None,
) -> dict:
    """문단 범위 텍스트를 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    if max_chars is None:
        max_chars = default_max_chars()

    total = len(doc.paragraphs)
    end = total if end_index is None else min(end_index, total)
    start = max(0, start_index)
    picked = []
    used = 0
    truncated = False
    for index in range(start, end):
        text = doc.paragraphs[index].text or ""
        next_size = used + len(text)
        if next_size > max_chars:
            remaining = max(0, max_chars - used)
            picked.append({"index": index, "text": text[:remaining]})
            truncated = True
            break
        picked.append({"index": index, "text": text})
        used = next_size
    return {"paragraphs": picked, "truncated": truncated}


@mcp.tool()
def find_text(filename: str, text_to_find: str, match_case: bool = True, max_results: int = 50) -> dict:
    """문서에서 텍스트를 검색합니다. 원본은 수정하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    return find_in_doc(doc, text_to_find=text_to_find, match_case=match_case, max_results=max_results)


def _operation_value(operation: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in operation:
            return operation[name]
    return default


def _apply_edit_operation(doc: Any, operation: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(operation, dict):
        raise TypeError(f"operation {index} must be an object")
    raw_type = _operation_value(operation, "type", "op", "operation")
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise ValueError(f"operation {index} must include a type")
    op_type = raw_type.strip().replace("-", "_")

    if op_type == "replace_text":
        find = _operation_value(operation, "findText", "find_text", "find")
        replace = _operation_value(operation, "replaceText", "replace_text", "replace", default="")
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
        paragraph_index = _operation_value(operation, "paragraphIndex", "paragraph_index")
        if paragraph_index is None:
            raise ValueError("insert_paragraph requires paragraphIndex")
        text = _operation_value(operation, "text", default="")
        style = _operation_value(operation, "style")
        inserted = insert_paragraph_to_doc(doc, int(paragraph_index), str(text), style)
        return {"type": op_type, "inserted_index": inserted}

    if op_type == "delete_paragraph":
        paragraph_index = _operation_value(operation, "paragraphIndex", "paragraph_index")
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
        table_index = _operation_value(operation, "tableIndex", "table_index", default=0)
        row = _operation_value(operation, "row")
        col = _operation_value(operation, "col", "column")
        text = _operation_value(operation, "text", default="")
        if row is None or col is None:
            raise ValueError("set_table_cell_text requires row and col")
        preserve_format = bool(_operation_value(operation, "preserveFormat", "preserve_format", default=True))
        split_paragraphs = bool(_operation_value(operation, "splitParagraphs", "split_paragraphs", default=False))
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


@mcp.tool()
def search_and_replace(filename: str, find_text: str, replace_text: str, dry_run: bool = False) -> dict:
    """문서에서 텍스트를 치환합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    replaced_count = replace_in_doc(doc, find_text=find_text, replace_text=replace_text)
    result = {"replaced_count": replaced_count, "find_text": find_text, "replace_text": replace_text}
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


@mcp.tool()
def batch_replace(filename: str, replacements: list[dict[str, str]], dry_run: bool = False) -> dict:
    """여러 치환 규칙을 순서대로 적용합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = batch_replace_in_doc(doc, replacements)
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


@mcp.tool()
def apply_edits(filename: str, operations: list[dict[str, Any]], dry_run: bool = False) -> dict:
    """여러 편집 operation을 원자적으로 적용합니다. 실패 시 원본 파일은 변경하지 않습니다."""
    path = resolve_path(filename)
    if not isinstance(operations, list):
        raise TypeError("operations must be a list")

    doc = open_doc(path)
    operation_results: list[dict[str, Any]] = []
    try:
        for index, operation in enumerate(operations):
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
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


@mcp.tool()
def undo_last_edit(filename: str) -> dict:
    """마지막 저장 전 .bak 백업과 현재 문서를 교체해 직전 편집을 되돌립니다."""
    path = resolve_path(filename)
    return undo_last_backup(path)


@mcp.tool()
def byte_preserving_patch(
    filename: str,
    patches: list[dict[str, Any]],
    output: str | None = None,
) -> dict:
    """section XML 바이트 splice 기반 문단 텍스트 패치를 적용합니다."""
    if hwpx_paragraph_patch is None:
        raise RuntimeError("installed python-hwpx does not provide hwpx.patch.paragraph_patch")
    path = Path(resolve_path(filename))
    target = Path(resolve_path(output)) if output else path
    result = hwpx_paragraph_patch(path, patches)
    payload = result.to_dict()
    payload["outputPath"] = str(target)
    verification = {
        "ok": bool(payload["openSafety"]["ok"]) and not payload["skipped"],
        "filePath": str(target),
        "openSafety": payload["openSafety"],
        "byteIdentical": payload["byteIdentical"],
        "changedParts": payload["changedParts"],
        "skipped": payload["skipped"],
    }
    if payload["skipped"]:
        payload["verificationReport"] = verification
        return payload
    verification = _write_verified_patch_result(target, result.data)
    verification["byteIdentical"] = payload["byteIdentical"]
    verification["changedParts"] = payload["changedParts"]
    verification["skipped"] = payload["skipped"]
    payload["verificationReport"] = verification
    payload["openSafety"] = verification["openSafety"]
    return payload


@mcp.tool()
def add_heading(filename: str, text: str, level: int = 1, dry_run: bool = False) -> dict:
    """문서 끝에 제목 문단을 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = add_heading_to_doc(doc, text, level)
    if dry_run:
        return _with_dry_run_verification({"paragraph_index": idx}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"paragraph_index": idx}, verification)


@mcp.tool()
def add_paragraph(filename: str, text: str, style: str | None = None, dry_run: bool = False) -> dict:
    """문서 끝에 문단을 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = add_paragraph_to_doc(doc, text, style)
    if dry_run:
        return _with_dry_run_verification({"paragraph_index": idx}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"paragraph_index": idx}, verification)


@mcp.tool()
def insert_paragraph(
    filename: str,
    paragraph_index: int,
    text: str,
    style: str | None = None,
    dry_run: bool = False,
) -> dict:
    """지정 위치 앞에 문단을 삽입합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = insert_paragraph_to_doc(doc, paragraph_index, text, style)
    if dry_run:
        return _with_dry_run_verification({"inserted_index": idx}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"inserted_index": idx}, verification)


@mcp.tool()
def delete_paragraph(filename: str, paragraph_index: int, dry_run: bool = False) -> dict:
    """지정 문단을 삭제합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    remaining = delete_paragraph_from_doc(doc, paragraph_index)
    result = {"deleted_index": paragraph_index, "remaining_paragraphs": remaining}
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


@mcp.tool()
def add_table(
    filename: str,
    rows: int,
    cols: int,
    data: list[list[str]] = None,
    dry_run: bool = False,
) -> dict:
    """문서 끝에 표를 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = add_table_to_doc(doc, rows, cols, data)
    if dry_run:
        return _with_dry_run_verification({"table_index": idx}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"table_index": idx}, verification)


@mcp.tool()
def get_table_text(filename: str, table_index: int = 0) -> dict:
    """표 셀 텍스트를 2D 배열로 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = get_table_data(doc, table_index)
    return {
        "table_index": table_index,
        "rows": result["rows"],
        "cols": result["cols"],
        "data": result["data"],
    }


@mcp.tool()
def get_table_map(filename: str) -> dict:
    """문서 내 표 위치, 크기, 문맥 요약을 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    return get_table_map_in_doc(doc)


@mcp.tool()
def mcp_server_health() -> dict:
    """MCP 서버 transport와 timeout/keepalive 점검 정보를 반환합니다."""
    transport = os.environ.get("HWPX_MCP_TRANSPORT", "stdio")
    sandbox_root = os.environ.get("HWPX_MCP_SANDBOX_ROOT")
    fastmcp_tool_names = _fastmcp_tool_names()
    legacy_tool_names = _legacy_tool_names()
    expected_fastmcp = _env_int("HWPX_EXPECTED_FASTMCP_TOOL_COUNT", _EXPECTED_FASTMCP_TOOL_COUNT)
    expected_legacy = _env_int("HWPX_EXPECTED_LEGACY_TOOL_COUNT", _EXPECTED_LEGACY_TOOL_COUNT)
    missing_key_tools = [name for name in _KEY_TOOL_NAMES if name not in fastmcp_tool_names]
    skew_detected = (
        len(fastmcp_tool_names) < expected_fastmcp
        or len(legacy_tool_names) < expected_legacy
        or bool(missing_key_tools)
    )
    return {
        "server": "hwpx-mcp-server",
        "version": _package_version("hwpx-mcp-server"),
        "pythonHwpxVersion": _package_version("python-hwpx"),
        "skillBundleVersion": os.environ.get("HWPX_SKILL_VERSION", "unknown"),
        "pluginRoot": os.environ.get("HWPX_PLUGIN_ROOT"),
        "transport": transport,
        "streamable_http_available": callable(getattr(mcp, "streamable_http_app", None)),
        "toolSurface": {
            "status": "skewed" if skew_detected else "ok",
            "expectedFastMcpToolCount": expected_fastmcp,
            "actualFastMcpToolCount": len(fastmcp_tool_names),
            "expectedLegacyToolCount": expected_legacy,
            "actualLegacyToolCount": len(legacy_tool_names),
            "missingKeyTools": missing_key_tools,
            "keyTools": list(_KEY_TOOL_NAMES),
            "diagnosis": (
                "Installed MCP surface is missing expected tools; reinstall the hwpx plugin, "
                "remove stale plugin venv/cache, then start a fresh host session."
                if skew_detected
                else "Installed MCP surface matches the expected tool count and key tools."
            ),
        },
        "unitPolicy": {
            "status": "audited",
            "fontSize": "points",
            "borderWidth": "human value: number/string accepted; prefer pt or mm suffix when supported",
            "fileSizeLimits": "bytes",
            "pageAndTableInternals": "HWP units are internal implementation details; MCP tools should prefer mm/pt/% labels.",
            "auditReport": "tests/unit_audit_report.md",
        },
        "fetch_timeout_seconds": _env_float(
            "HWPX_MCP_FETCH_TIMEOUT_SECONDS",
            _DEFAULT_FETCH_TIMEOUT_SECONDS,
        ),
        "max_chars": default_max_chars(),
        "sandbox": {
            "root": sandbox_root,
            "absolute_paths_inside_root_allowed": bool(sandbox_root),
            "path_guidance": (
                "Use relative paths under HWPX_MCP_SANDBOX_ROOT or absolute paths inside that root."
                if sandbox_root
                else "No HWPX_MCP_SANDBOX_ROOT is configured; paths resolve from the current working directory."
            ),
        },
        "disconnect_diagnostics": {
            "likely_conditions": [
                "large document extraction exceeding client/tool timeout",
                "idle stdio client session termination",
                "remote URL fetch timeout",
            ],
            "keepalive_check": "streamable_http_app constructibility is covered by smoke tests; stdio keepalive is client-controlled.",
        },
    }


@mcp.tool()
def find_cell_by_label(filename: str, label_text: str, direction: str = "right") -> dict:
    """양식 문서에서 라벨 기준 인접 셀 후보를 조회합니다. direction: right 또는 down."""
    path = resolve_path(filename)
    doc = open_doc(path)
    safe_direction = _normalize_table_label_direction(direction)
    return find_cell_by_label_in_doc(doc, label_text, direction=safe_direction)


@mcp.tool()
def fill_by_path(filename: str, mappings: dict[str, str], dry_run: bool = False) -> dict:
    """라벨 경로 문법으로 셀을 채웁니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = fill_by_path_in_doc(doc, _normalize_fill_mappings(mappings))
    if result.get("applied_count", 0) > 0:
        if dry_run:
            return _with_dry_run_verification(result, doc, path)
        verification = _save_doc_verification(doc, path)
        return _with_save_verification(result, verification)
    result["dryRun"] = dry_run
    return result


@mcp.tool()
def set_table_cell_text(
    filename: str,
    table_index: int,
    row: int,
    col: int,
    text: str,
    preserve_format: bool = True,
    split_paragraphs: bool = False,
    dry_run: bool = False,
) -> dict:
    """표 셀 텍스트를 변경합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
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
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


@mcp.tool()
def add_page_break(filename: str, dry_run: bool = False) -> dict:
    """문서 끝에 페이지 나누기를 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    add_page_break_to_doc(doc)
    if dry_run:
        return _with_dry_run_verification({"success": True}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"success": True}, verification)


@mcp.tool()
def add_memo(
    filename: str,
    paragraph_index: int | None = None,
    text: str = "",
    location: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict:
    """본문 문단 또는 표 셀 문단에 메모를 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = add_memo_to_doc(doc, paragraph_index, text, location=location)
    if result["location"].get("kind") == "body_paragraph":
        result["paragraph_index"] = result["location"]["paragraph_index"]
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


@mcp.tool()
def add_memo_by_anchor(
    filename: str,
    anchor: dict[str, Any] | str,
    text: str,
    dry_run: bool = False,
) -> dict:
    """find_text가 반환한 anchor로 메모 위치를 지정해 메모를 추가합니다."""
    return add_memo(filename, text=text, location=location_from_anchor(anchor), dry_run=dry_run)


@mcp.tool()
def remove_memo(
    filename: str,
    paragraph_index: int | None = None,
    location: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict:
    """본문 문단 또는 표 셀 문단의 메모를 제거합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = remove_memo_from_doc(doc, paragraph_index, location=location)
    if result["location"].get("kind") == "body_paragraph":
        result["paragraph_index"] = result["location"]["paragraph_index"]
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
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


@mcp.tool()
def replace_in_paragraph(
    filename: str,
    old_text: str,
    new_text: str,
    paragraph_index: int | None = None,
    location: dict[str, Any] | None = None,
    count: int | None = None,
    dry_run: bool = False,
) -> dict:
    """본문/표 셀 문단 하나에서 run 서식을 유지하며 부분 텍스트를 치환합니다."""
    if old_text == "":
        raise ValueError("old_text는 빈 문자열일 수 없습니다.")
    if count is not None and count <= 0:
        return {"replaced_count": 0, "location": location or {"paragraph_index": paragraph_index}, "dryRun": dry_run}

    path = resolve_path(filename)
    doc = open_doc(path)
    resolved = resolve_paragraph_reference(doc, paragraph_index=paragraph_index, location=location)
    paragraph = resolved.paragraph
    runs = list(getattr(paragraph, "runs", []))

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
            replaced = before.count(old_text) if count is None else min(before.count(old_text), count)

    if replaced:
        result = {"replaced_count": replaced, "location": resolved.location}
        if dry_run:
            return _with_dry_run_verification(result, doc, path)
        verification = _save_doc_verification(doc, path)
        return _with_save_verification(result, verification)
    return {"replaced_count": replaced, "location": resolved.location, "dryRun": dry_run}


@mcp.tool()
def replace_by_anchor(
    filename: str,
    anchor: dict[str, Any] | str,
    old_text: str,
    new_text: str,
    dry_run: bool = False,
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
        )

    path = resolve_path(filename)
    doc = open_doc(path)
    resolved = resolve_paragraph_reference(doc, location=location)
    paragraph = resolved.paragraph
    before = paragraph.text or ""
    end = position + len(old_text)
    if before[position:end] != old_text:
        raise ValueError("anchor position does not match old_text")

    runs = list(getattr(paragraph, "runs", []))
    if runs:
        replaced = _replace_visible_span_in_runs(runs, position, end, new_text)
    else:
        paragraph.text = before[:position] + new_text + before[end:]
        replaced = 1

    result = {"replaced_count": replaced, "location": resolved.location, "position": position}
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


@mcp.tool()
def list_available_documents(directory: str = ".") -> dict:
    """지정 디렉토리의 .hwpx 파일 목록을 조회합니다."""
    import glob

    path = resolve_path(directory)
    files = glob.glob(os.path.join(path, "*.hwpx"))
    docs = []
    for file_path in sorted(files):
        stat = os.stat(file_path)
        docs.append(
            {
                "filename": os.path.basename(file_path),
                "size": f"{stat.st_size / 1024:.1f}KB",
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )
    return {"directory": directory, "documents": docs, "count": len(docs)}


@mcp.tool()
def format_text(
    filename: str,
    paragraph_index: int,
    start_pos: int,
    end_pos: int,
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    font_size: float = None,
    font_name: str = None,
    color: str = None,
    dry_run: bool = False,
) -> dict:
    """지정 범위 텍스트 서식을 변경합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    format_text_range(
        doc,
        paragraph_index,
        start_pos,
        end_pos,
        bold=bold,
        italic=italic,
        underline=underline,
        font_size=font_size,
        font_name=font_name,
        color=color,
    )
    result = {"formatted": True, "paragraph_index": paragraph_index, "range": [start_pos, end_pos]}
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


@mcp.tool()
def create_custom_style(
    filename: str,
    style_name: str,
    bold: bool = None,
    italic: bool = None,
    font_size: float = None,
    font_name: str = None,
    color: str = None,
    dry_run: bool = False,
) -> dict:
    """문서에 커스텀 스타일을 생성합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = create_style_in_doc(
        doc,
        style_name,
        bold=bold,
        italic=italic,
        font_size=font_size,
        font_name=font_name,
        color=color,
    )
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


@mcp.tool()
def list_styles(filename: str) -> dict:
    """문서에 정의된 스타일 목록을 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    styles = list_styles_in_doc(doc)
    return {"styles": styles, "count": len(styles)}


@mcp.tool()
def merge_table_cells(
    filename: str,
    table_index: int,
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
    dry_run: bool = False,
) -> dict:
    """표 셀 범위를 병합합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    merge_cells_in_table(doc, table_index, start_row, start_col, end_row, end_col)
    result = {"merged": True, "range": f"({start_row},{start_col})~({end_row},{end_col})"}
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


@mcp.tool()
def split_table_cell(filename: str, table_index: int, row: int, col: int, dry_run: bool = False) -> dict:
    """병합된 셀을 분할합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    span_info = split_cell_in_table(doc, table_index, row, col)
    if dry_run:
        return _with_dry_run_verification({"split": True, "original_span": span_info}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"split": True, "original_span": span_info}, verification)


@mcp.tool()
def format_table(
    filename: str,
    table_index: int,
    has_header_row: bool = None,
    dry_run: bool = False,
) -> dict:
    """표 서식을 적용합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    format_table_in_doc(doc, table_index, has_header_row=has_header_row)
    if dry_run:
        return _with_dry_run_verification({"formatted": True, "table_index": table_index}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"formatted": True, "table_index": table_index}, verification)


@mcp.tool()
def copy_document(source_filename: str, destination_filename: str = None) -> dict:
    """HWPX 문서를 새 경로로 복사합니다. 원본은 유지됩니다."""
    source = resolve_path(source_filename)
    destination = destination_filename
    if destination_filename is not None:
        destination = resolve_path(destination_filename)
    dest = copy_document_file(source, destination)
    open_safety = build_hwpx_open_safety_report(Path(dest))
    return {
        "source": source_filename,
        "destination": os.path.basename(dest),
        "openSafety": open_safety,
    }


@mcp.tool()
def repair_hwpx(
    source_filename: str,
    output_filename: str,
    recover: bool = False,
    overwrite: bool = False,
    max_entry_size: int = 64 * 1024 * 1024,
    max_total_size: int = 512 * 1024 * 1024,
    max_source_size: int = 512 * 1024 * 1024,
) -> dict:
    """HWPX ZIP 패키지를 repair-repack하거나, recover=true일 때 Local File Header 스캔으로 복구합니다."""
    return _OPS.repair_hwpx(
        source=resolve_path(source_filename),
        output=resolve_path(output_filename),
        recover=recover,
        overwrite=overwrite,
        max_entry_size=max_entry_size,
        max_total_size=max_total_size,
        max_source_size=max_source_size,
    )


@mcp.tool()
def analyze_form_fill(
    source_filename: str,
    input_json: dict = None,
    input_json_path: str = None,
    input_docx: str = None,
    destination_filename: str = None,
    options: dict = None,
) -> dict:
    """HWPX 양식 채움 계획을 분석합니다. 파일 복사/채움 변경은 하지 않습니다."""
    return analyze_form_fill_workflow(
        source_filename=source_filename,
        input_json=input_json,
        input_json_path=input_json_path,
        input_docx=input_docx,
        destination_filename=destination_filename,
        options=options,
    )


@mcp.tool()
def apply_form_fill(
    plan_id: str = None,
    analysis: dict = None,
    source_filename: str = None,
    destination_filename: str = None,
    canonical_input: dict = None,
    confirm: bool = True,
) -> dict:
    """분석된 HWPX 양식 채움 계획을 복사본에만 적용하고 구조/패키지를 검증합니다."""
    return apply_form_fill_workflow(
        plan_id=plan_id,
        analysis=analysis,
        source_filename=source_filename,
        destination_filename=destination_filename,
        canonical_input=canonical_input,
        confirm=confirm,
    )


if _advanced_enabled():

    @mcp.tool()
    def package_parts(filename: str) -> dict:
        """[고급] HWPX 패키지 파트 목록을 조회합니다."""
        path = resolve_path(filename)
        return _OPS.package_parts(path)

    @mcp.tool()
    def package_get_xml(filename: str, part_name: str, max_chars: int = 5000) -> dict:
        """[고급] 특정 패키지 파트의 XML을 조회합니다."""
        path = resolve_path(filename)
        result = _OPS.package_get_xml(path, part_name)
        return truncate_response(result.get("xmlString", ""), max_chars=max_chars)

    @mcp.tool()
    def package_get_text(filename: str, part_name: str, max_chars: int = 5000) -> dict:
        """[고급] 특정 패키지 파트의 텍스트를 조회합니다."""
        path = resolve_path(filename)
        result = _OPS.package_get_text(path, part_name)
        return truncate_response(result.get("text", ""), max_chars=max_chars)

    @mcp.tool()
    def object_find_by_tag(filename: str, tag_name: str, max_results: int = 20) -> dict:
        """[고급] 문서 XML에서 태그를 검색합니다."""
        path = resolve_path(filename)
        return _OPS.object_find_by_tag(path, tag_name, max_results=max_results)

    @mcp.tool()
    def object_find_by_attr(filename: str, attr_name: str, attr_value: str = None, max_results: int = 20) -> dict:
        """[고급] 문서 XML에서 속성을 검색합니다."""
        path = resolve_path(filename)
        return _OPS.object_find_by_attr(path, None, attr_name, attr_value, max_results=max_results)

    @mcp.tool()
    def plan_edit(filename: str, instruction: str) -> dict:
        """[고급] instruction 기준 검증용 편집 계획을 생성합니다."""
        path = resolve_path(filename)
        operation = _build_verification_plan_operation(path, instruction)
        return _OPS.plan_edit(path=path, operations=[operation])

    @mcp.tool()
    def preview_edit(filename: str, plan_id: str) -> dict:
        """[고급] plan_edit 결과 미리보기를 조회합니다."""
        del filename
        return _OPS.preview_edit(plan_id=plan_id)

    @mcp.tool()
    def apply_edit(filename: str, plan_id: str) -> dict:
        """[고급] 검증 계획을 적용합니다. 원본 HWPX는 직접 수정하지 않습니다."""
        del filename
        return _OPS.apply_edit(plan_id=plan_id, confirm=True)

    @mcp.tool()
    def validate_structure(filename: str) -> dict:
        """[고급] HWPX 구조 유효성을 검사합니다."""
        return _OPS.validate_structure(resolve_path(filename))

    @mcp.tool()
    def lint_text_conventions(filename: str) -> dict:
        """[고급] 텍스트 규칙 위반 여부를 검사합니다."""
        return _OPS.lint_text_conventions(resolve_path(filename))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hwpx-mcp-server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "http"),
        default=os.environ.get("HWPX_MCP_TRANSPORT", "stdio"),
        help="MCP transport to use",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HWPX_MCP_HOST", "127.0.0.1"),
        help="Host interface for streamable HTTP transport",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_env_int("HWPX_MCP_PORT", 8000),
        help="TCP port for streamable HTTP transport",
    )
    args = parser.parse_args(argv)

    os.environ.setdefault("HWPX_MCP_SANDBOX_ROOT", str(Path.cwd()))

    selected_transport = args.transport
    if selected_transport == "http":
        selected_transport = "streamable-http"

    if selected_transport == "stdio":
        mcp.run(transport="stdio")
        return

    # TODO: add pluggable auth middleware/headers for production HTTP deployments.
    import uvicorn

    app = mcp.streamable_http_app()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
