"""Stateless HWPX MCP 서버."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .core.content import collect_full_text
from .core.document import create_blank, open_doc, save_doc
from .core.search import batch_replace_in_doc, find_in_doc, replace_in_doc
from .utils.helpers import resolve_path, truncate_response

mcp = FastMCP("hwpx-mcp-server")


def _advanced_enabled() -> bool:
    return os.environ.get("HWPX_MCP_ADVANCED", "0").strip().lower() in {"1", "true", "yes", "on"}


def _paragraph_count(doc) -> int:
    return len(doc.paragraphs)


def _table_count(doc) -> int:
    count = 0
    seen: set[int] = set()
    for paragraph in doc.paragraphs:
        for table in getattr(paragraph, "tables", []):
            key = id(table)
            if key not in seen:
                seen.add(key)
                count += 1
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
    """새 HWPX 문서를 생성합니다."""
    del title, author
    path = resolve_path(filename)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    create_blank(path)
    return {"filename": filename, "created": True}


@mcp.tool()
def get_document_info(filename: str) -> dict:
    """HWPX 문서의 메타데이터와 구조 정보를 반환합니다."""
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
def get_document_text(filename: str, max_chars: int = 10000) -> dict:
    """HWPX 문서의 전체 텍스트를 추출합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    return truncate_response(collect_full_text(doc), max_chars=max_chars)


@mcp.tool()
def get_document_outline(filename: str) -> dict:
    """문서의 헤더와 섹션 구조를 반환합니다."""
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
def get_paragraph_text(filename: str, paragraph_index: int) -> dict:
    """지정한 문단의 텍스트를 반환합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    try:
        text = doc.paragraphs[paragraph_index].text or ""
    except IndexError as exc:
        raise ValueError(f"유효하지 않은 paragraph_index: {paragraph_index}") from exc
    return {"paragraph_index": paragraph_index, "text": text}


@mcp.tool()
def get_paragraphs_text(filename: str, start_index: int = 0, end_index: int = None, max_chars: int = 10000) -> dict:
    """지정 범위의 문단 텍스트를 반환합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
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
    """문서에서 텍스트를 검색합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    return find_in_doc(doc, text_to_find=text_to_find, match_case=match_case, max_results=max_results)


@mcp.tool()
def search_and_replace(filename: str, find_text: str, replace_text: str) -> dict:
    """문서에서 텍스트를 찾아 모두 치환합니다. 스타일은 보존됩니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    replaced_count = replace_in_doc(doc, find_text=find_text, replace_text=replace_text)
    save_doc(doc, path)
    return {"replaced_count": replaced_count, "find_text": find_text, "replace_text": replace_text}


@mcp.tool()
def batch_replace(filename: str, replacements: list[dict]) -> dict:
    """여러 텍스트를 순서대로 치환합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = batch_replace_in_doc(doc, replacements)
    save_doc(doc, path)
    return result


def main() -> None:
    if _advanced_enabled():
        from .legacy_server import main as legacy_main

        legacy_main()
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
