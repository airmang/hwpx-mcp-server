"""Stateless HWPX MCP 서버."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

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
    format_table_in_doc,
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


def _paragraph_count(doc) -> int:
    return len(doc.paragraphs)


def _table_count(doc) -> int:
    table_tag = "{http://www.hancom.co.kr/hwpml/2011/paragraph}tbl"
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
def get_document_text(filename: str, max_chars: int | None = None) -> dict:
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
def get_paragraphs_text(
    filename: str,
    start_index: int = 0,
    end_index: int = None,
    max_chars: int | None = None,
) -> dict:
    """지정 범위의 문단 텍스트를 반환합니다."""
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
def batch_replace(filename: str, replacements: list[dict[str, str]]) -> dict:
    """여러 텍스트를 순서대로 치환합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = batch_replace_in_doc(doc, replacements)
    save_doc(doc, path)
    return result


@mcp.tool()
def add_heading(filename: str, text: str, level: int = 1) -> dict:
    """문서 끝에 제목(헤딩)을 추가합니다. level: 1~6"""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = add_heading_to_doc(doc, text, level)
    save_doc(doc, path)
    return {"paragraph_index": idx}


@mcp.tool()
def add_paragraph(filename: str, text: str, style: str | None = None) -> dict:
    """문서 끝에 문단을 추가합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = add_paragraph_to_doc(doc, text, style)
    save_doc(doc, path)
    return {"paragraph_index": idx}


@mcp.tool()
def insert_paragraph(filename: str, paragraph_index: int, text: str, style: str | None = None) -> dict:
    """지정한 위치 앞에 문단을 삽입합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = insert_paragraph_to_doc(doc, paragraph_index, text, style)
    save_doc(doc, path)
    return {"inserted_index": idx}


@mcp.tool()
def delete_paragraph(filename: str, paragraph_index: int) -> dict:
    """지정한 문단을 삭제합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    remaining = delete_paragraph_from_doc(doc, paragraph_index)
    save_doc(doc, path)
    return {"deleted_index": paragraph_index, "remaining_paragraphs": remaining}


@mcp.tool()
def add_table(filename: str, rows: int, cols: int, data: list[list[str]] = None) -> dict:
    """문서 끝에 표를 추가합니다. data가 None이면 빈 표를 생성합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    idx = add_table_to_doc(doc, rows, cols, data)
    save_doc(doc, path)
    return {"table_index": idx}


@mcp.tool()
def get_table_text(filename: str, table_index: int = 0) -> dict:
    """표의 모든 셀 텍스트를 2D 배열로 반환합니다."""
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
def set_table_cell_text(filename: str, table_index: int, row: int, col: int, text: str) -> dict:
    """표의 특정 셀 텍스트를 변경합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    set_cell_text(doc, table_index, row, col, text)
    save_doc(doc, path)
    return {"table_index": table_index, "row": row, "col": col, "text": text}


@mcp.tool()
def add_page_break(filename: str) -> dict:
    """문서 끝에 페이지 나누기를 추가합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    add_page_break_to_doc(doc)
    save_doc(doc, path)
    return {"success": True}


@mcp.tool()
def add_memo(filename: str, paragraph_index: int, text: str) -> dict:
    """문단에 메모(코멘트)를 추가합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    add_memo_to_doc(doc, paragraph_index, text)
    save_doc(doc, path)
    return {"memo_added": True, "paragraph_index": paragraph_index}


@mcp.tool()
def remove_memo(filename: str, paragraph_index: int) -> dict:
    """문단의 메모를 제거합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    remove_memo_from_doc(doc, paragraph_index)
    save_doc(doc, path)
    return {"memo_removed": True, "paragraph_index": paragraph_index}


@mcp.tool()
def list_available_documents(directory: str = ".") -> dict:
    """지정 디렉토리의 HWPX 파일 목록을 반환합니다."""
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
    """지정 범위의 텍스트 서식을 변경합니다. color는 hex 형식 (예: "FF0000"은 빨간색)"""
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
    """문서에 커스텀 스타일을 생성합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    create_style_in_doc(
        doc,
        style_name,
        bold=bold,
        italic=italic,
        font_size=font_size,
        font_name=font_name,
        color=color,
    )
    save_doc(doc, path)
    return {"style_name": style_name, "created": True}


@mcp.tool()
def list_styles(filename: str) -> dict:
    """문서에 정의된 스타일 목록을 반환합니다."""
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
    """표의 셀을 병합합니다. (start_row, start_col) ~ (end_row, end_col) 범위."""
    path = resolve_path(filename)
    doc = open_doc(path)
    merge_cells_in_table(doc, table_index, start_row, start_col, end_row, end_col)
    save_doc(doc, path)
    return {"merged": True, "range": f"({start_row},{start_col})~({end_row},{end_col})"}


@mcp.tool()
def split_table_cell(filename: str, table_index: int, row: int, col: int) -> dict:
    """병합된 셀을 분할합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    span_info = split_cell_in_table(doc, table_index, row, col)
    save_doc(doc, path)
    return {"split": True, "original_span": span_info}


@mcp.tool()
def format_table(filename: str, table_index: int, has_header_row: bool = None) -> dict:
    """표 서식을 변경합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    format_table_in_doc(doc, table_index, has_header_row=has_header_row)
    save_doc(doc, path)
    return {"formatted": True, "table_index": table_index}


@mcp.tool()
def copy_document(source_filename: str, destination_filename: str = None) -> dict:
    """HWPX 문서를 복사합니다."""
    source = resolve_path(source_filename)
    destination = destination_filename
    if destination_filename is not None:
        destination = resolve_path(destination_filename)
    dest = copy_document_file(source, destination)
    return {"source": source_filename, "destination": os.path.basename(dest)}


if _advanced_enabled():

    @mcp.tool()
    def package_parts(filename: str) -> dict:
        """[고급] HWPX 패키지의 파트(파일) 목록을 반환합니다."""
        path = resolve_path(filename)
        return _OPS.package_parts(path)

    @mcp.tool()
    def package_get_xml(filename: str, part_name: str, max_chars: int = 5000) -> dict:
        """[고급] HWPX 패키지의 특정 XML 파트 내용을 반환합니다."""
        path = resolve_path(filename)
        result = _OPS.package_get_xml(path, part_name)
        return truncate_response(result.get("xmlString", ""), max_chars=max_chars)

    @mcp.tool()
    def package_get_text(filename: str, part_name: str, max_chars: int = 5000) -> dict:
        """[고급] HWPX 패키지의 특정 파트에서 텍스트만 추출합니다."""
        path = resolve_path(filename)
        result = _OPS.package_get_text(path, part_name)
        return truncate_response(result.get("text", ""), max_chars=max_chars)

    @mcp.tool()
    def object_find_by_tag(filename: str, tag_name: str, max_results: int = 20) -> dict:
        """[고급] 문서 XML에서 특정 태그를 검색합니다."""
        path = resolve_path(filename)
        return _OPS.object_find_by_tag(path, tag_name, max_results=max_results)

    @mcp.tool()
    def object_find_by_attr(filename: str, attr_name: str, attr_value: str = None, max_results: int = 20) -> dict:
        """[고급] 문서 XML에서 특정 속성을 검색합니다."""
        path = resolve_path(filename)
        return _OPS.object_find_by_attr(path, "*", attr_name, attr_value or "", max_results=max_results)

    @mcp.tool()
    def plan_edit(filename: str, instruction: str) -> dict:
        """[고급/하드닝] 편집 계획을 생성합니다. preview_edit → apply_edit 순으로 사용하세요."""
        operation = {"op": "searchReplace", "args": {"find": instruction, "replace": instruction}}
        return _OPS.plan_edit(path=resolve_path(filename), operations=[operation])

    @mcp.tool()
    def preview_edit(filename: str, plan_id: str) -> dict:
        """[고급/하드닝] 편집 계획의 미리보기를 반환합니다."""
        del filename
        return _OPS.preview_edit(plan_id=plan_id)

    @mcp.tool()
    def apply_edit(filename: str, plan_id: str) -> dict:
        """[고급/하드닝] 편집 계획을 적용합니다."""
        del filename
        return _OPS.apply_edit(plan_id=plan_id, confirm=True)

    @mcp.tool()
    def validate_structure(filename: str) -> dict:
        """[고급] HWPX 문서 구조의 유효성을 검사합니다."""
        return _OPS.validate_structure(resolve_path(filename))

    @mcp.tool()
    def lint_text_conventions(filename: str) -> dict:
        """[고급] 텍스트 규칙(띄어쓰기, 맞춤법 등)을 검사합니다."""
        return _OPS.lint_text_conventions(resolve_path(filename))


def main() -> None:
    os.environ.setdefault("HWPX_MCP_SANDBOX_ROOT", str(Path.cwd()))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
