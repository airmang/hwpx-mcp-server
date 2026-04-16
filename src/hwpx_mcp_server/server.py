"""Stateless HWPX MCP 서버."""

from __future__ import annotations

import argparse
import base64
import binascii
import html
import json
import os
import re
from datetime import datetime
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
from .core.search import batch_replace_in_doc, find_in_doc, replace_in_doc
from .hwpx_ops import HwpxOps
from .upstream import HP_NS, create_text_extractor, open_document
from .utils.helpers import default_max_chars, resolve_path, truncate_response

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
_FIGURE_CAPTION_RE = re.compile(r"^\s*(?:Figure|Fig\.|그림)\s*\d*", re.IGNORECASE)


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
    create_blank(path)
    return {"filename": filename, "created": True}


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
def get_paragraph_text(filename: str, paragraph_index: int) -> dict:
    """특정 문단 텍스트를 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    try:
        text = doc.paragraphs[paragraph_index].text or ""
    except IndexError as exc:
        raise ValueError(f"유효하지 않은 paragraph_index: {paragraph_index}") from exc
    return {"paragraph_index": paragraph_index, "text": text}


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


@mcp.tool()
def search_and_replace(filename: str, find_text: str, replace_text: str) -> dict:
    """문서에서 텍스트를 치환하고 즉시 저장합니다. 스타일은 보존됩니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    replaced_count = replace_in_doc(doc, find_text=find_text, replace_text=replace_text)
    save_doc(doc, path)
    return {"replaced_count": replaced_count, "find_text": find_text, "replace_text": replace_text}


@mcp.tool()
def batch_replace(filename: str, replacements: list[dict[str, str]]) -> dict:
    """여러 치환 규칙을 순서대로 적용하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = batch_replace_in_doc(doc, replacements)
    save_doc(doc, path)
    return result


@mcp.tool()
def add_heading(filename: str, text: str, level: int = 1) -> dict:
    """문서 끝에 제목 문단을 추가하고 즉시 저장합니다. level: 1~6"""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = add_heading_to_doc(doc, text, level)
    save_doc(doc, path)
    return {"paragraph_index": idx}


@mcp.tool()
def add_paragraph(filename: str, text: str, style: str | None = None) -> dict:
    """문서 끝에 문단을 추가하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = add_paragraph_to_doc(doc, text, style)
    save_doc(doc, path)
    return {"paragraph_index": idx}


@mcp.tool()
def insert_paragraph(filename: str, paragraph_index: int, text: str, style: str | None = None) -> dict:
    """지정 위치 앞에 문단을 삽입하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = insert_paragraph_to_doc(doc, paragraph_index, text, style)
    save_doc(doc, path)
    return {"inserted_index": idx}


@mcp.tool()
def delete_paragraph(filename: str, paragraph_index: int) -> dict:
    """지정 문단을 삭제하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    remaining = delete_paragraph_from_doc(doc, paragraph_index)
    save_doc(doc, path)
    return {"deleted_index": paragraph_index, "remaining_paragraphs": remaining}


@mcp.tool()
def add_table(filename: str, rows: int, cols: int, data: list[list[str]] = None) -> dict:
    """문서 끝에 표를 추가하고 즉시 저장합니다. data가 없으면 빈 표를 생성합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = add_table_to_doc(doc, rows, cols, data)
    save_doc(doc, path)
    return {"table_index": idx}


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
def find_cell_by_label(filename: str, label_text: str, direction: str = "right") -> dict:
    """양식 문서에서 라벨 기준 인접 셀 후보를 조회합니다. direction: right 또는 down."""
    path = resolve_path(filename)
    doc = open_doc(path)
    safe_direction = _normalize_table_label_direction(direction)
    return find_cell_by_label_in_doc(doc, label_text, direction=safe_direction)


@mcp.tool()
def fill_by_path(filename: str, mappings: dict[str, str]) -> dict:
    """라벨 경로 문법으로 셀을 채우고 변경이 있으면 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = fill_by_path_in_doc(doc, _normalize_fill_mappings(mappings))
    if result.get("applied_count", 0) > 0:
        save_doc(doc, path)
    return result


@mcp.tool()
def set_table_cell_text(filename: str, table_index: int, row: int, col: int, text: str) -> dict:
    """표의 특정 셀 텍스트를 변경하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    set_cell_text(doc, table_index, row, col, text)
    save_doc(doc, path)
    return {"table_index": table_index, "row": row, "col": col, "text": text}


@mcp.tool()
def add_page_break(filename: str) -> dict:
    """문서 끝에 페이지 나누기를 추가하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    add_page_break_to_doc(doc)
    save_doc(doc, path)
    return {"success": True}


@mcp.tool()
def add_memo(filename: str, paragraph_index: int, text: str) -> dict:
    """문단에 메모를 추가하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    add_memo_to_doc(doc, paragraph_index, text)
    save_doc(doc, path)
    return {"memo_added": True, "paragraph_index": paragraph_index}


@mcp.tool()
def remove_memo(filename: str, paragraph_index: int) -> dict:
    """문단의 메모를 제거하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    remove_memo_from_doc(doc, paragraph_index)
    save_doc(doc, path)
    return {"memo_removed": True, "paragraph_index": paragraph_index}


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
) -> dict:
    """지정 범위 텍스트 서식을 변경하고 즉시 저장합니다. color는 hex 형식입니다."""
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
    save_doc(doc, path)
    return {"formatted": True, "paragraph_index": paragraph_index, "range": [start_pos, end_pos]}


@mcp.tool()
def create_custom_style(
    filename: str,
    style_name: str,
    bold: bool = None,
    italic: bool = None,
    font_size: float = None,
    font_name: str = None,
    color: str = None,
) -> dict:
    """문서에 커스텀 스타일을 생성하고 즉시 저장합니다."""
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
    save_doc(doc, path)
    return result


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
) -> dict:
    """표 셀 범위를 병합하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    merge_cells_in_table(doc, table_index, start_row, start_col, end_row, end_col)
    save_doc(doc, path)
    return {"merged": True, "range": f"({start_row},{start_col})~({end_row},{end_col})"}


@mcp.tool()
def split_table_cell(filename: str, table_index: int, row: int, col: int) -> dict:
    """병합된 셀을 분할하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    span_info = split_cell_in_table(doc, table_index, row, col)
    save_doc(doc, path)
    return {"split": True, "original_span": span_info}


@mcp.tool()
def format_table(filename: str, table_index: int, has_header_row: bool = None) -> dict:
    """표 서식을 적용하고 즉시 저장합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    format_table_in_doc(doc, table_index, has_header_row=has_header_row)
    save_doc(doc, path)
    return {"formatted": True, "table_index": table_index}


@mcp.tool()
def copy_document(source_filename: str, destination_filename: str = None) -> dict:
    """HWPX 문서를 새 경로로 복사합니다. 원본은 유지됩니다."""
    source = resolve_path(source_filename)
    destination = destination_filename
    if destination_filename is not None:
        destination = resolve_path(destination_filename)
    dest = copy_document_file(source, destination)
    return {"source": source_filename, "destination": os.path.basename(dest)}


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
