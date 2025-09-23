"""MCP 서버가 제공하는 도구 정의."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Literal

import mcp.types as types
from pydantic import BaseModel, Field, ConfigDict

from .hwpx_ops import HwpxOps


class _BaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class _SchemaSanitizer:
    _DROP_KEYS = {"title", "description", "examples", "default"}

    def __init__(self, schema: Dict[str, Any]):
        self._raw_schema = schema
        self._ref_cache: Dict[str, Any] = {}
        self._resolving: set[str] = set()

    def sanitize(self) -> Dict[str, Any]:
        sanitized, _ = self._sanitize_schema(self._raw_schema)
        if not isinstance(sanitized, dict):
            raise TypeError("Expected sanitized schema to be a mapping")
        sanitized["type"] = "object"
        properties = sanitized.get("properties")
        if not isinstance(properties, dict):
            properties = {}
            sanitized["properties"] = properties
        if properties:
            sanitized.setdefault("additionalProperties", False)
        else:
            sanitized.pop("additionalProperties", None)
        return sanitized

    def _sanitize_schema(self, node: Any) -> tuple[Any, bool]:
        if isinstance(node, dict):
            working = dict(node)
            optional = False

            for key in list(working.keys()):
                if key in self._DROP_KEYS or key == "$defs":
                    working.pop(key, None)

            if "allOf" in working:
                merged: Dict[str, Any] = {}
                parts = working.pop("allOf")
                if isinstance(parts, list):
                    for part in parts:
                        sanitized_part, _ = self._sanitize_schema(part)
                        if isinstance(sanitized_part, dict):
                            merged = self._merge_schema_dicts(merged, sanitized_part)
                working = self._merge_schema_dicts(merged, working)

            for union_key in ("anyOf", "oneOf"):
                if union_key in working:
                    options = working.pop(union_key)
                    sanitized_options: List[Any] = []
                    found_null = False
                    if isinstance(options, list):
                        for option in options:
                            sanitized_option, _ = self._sanitize_schema(option)
                            if self._is_null_schema(sanitized_option):
                                found_null = True
                            else:
                                sanitized_options.append(sanitized_option)
                    if found_null and len(sanitized_options) == 1:
                        base_schema = sanitized_options[0]
                        if isinstance(base_schema, dict):
                            working = self._merge_schema_dicts(base_schema, working)
                        else:
                            working = base_schema
                        optional = True
                    else:
                        working = self._merge_union_into_node(working, sanitized_options)
                    break

            if "$ref" in working:
                ref = working.pop("$ref")
                resolved = self._resolve_ref(ref)
                working = self._merge_schema_dicts(resolved, working)

            optional_props: set[str] = set()
            properties_value = working.get("properties")
            if isinstance(properties_value, dict):
                sanitized_properties: Dict[str, Any] = {}
                optional_names: set[str] = set()
                for prop_name, prop_schema in properties_value.items():
                    sanitized_prop, prop_optional = self._sanitize_schema(prop_schema)
                    sanitized_properties[prop_name] = sanitized_prop
                    if prop_optional:
                        optional_names.add(prop_name)
                working["properties"] = sanitized_properties
                optional_props = optional_names

            if "required" in working:
                required_value = working["required"]
                if isinstance(required_value, list):
                    filtered: List[str] = []
                    seen: set[str] = set()
                    for item in required_value:
                        if not isinstance(item, str):
                            continue
                        if item in optional_props or item in seen:
                            continue
                        seen.add(item)
                        filtered.append(item)
                    if filtered:
                        working["required"] = filtered
                    else:
                        working.pop("required", None)
                else:
                    working.pop("required", None)

            for key, value in list(working.items()):
                if key in {"properties", "required"}:
                    continue
                sanitized_value, _ = self._sanitize_schema(value)
                working[key] = sanitized_value

            if working.get("type") == "object":
                props = working.get("properties")
                if not isinstance(props, dict):
                    props = {}
                    working["properties"] = props
                if props:
                    working.setdefault("additionalProperties", False)
                elif working.get("additionalProperties") is False:
                    working.pop("additionalProperties")

            return working, optional

        if isinstance(node, list):
            sanitized_items: List[Any] = []
            for item in node:
                sanitized_item, _ = self._sanitize_schema(item)
                sanitized_items.append(sanitized_item)
            return sanitized_items, False

        return node, False

    def _resolve_ref(self, ref: Any) -> Dict[str, Any]:
        if not isinstance(ref, str):
            raise TypeError("Schema reference must be a string")
        cached = self._ref_cache.get(ref)
        if cached is not None:
            return self._clone(cached)
        if ref in self._resolving:
            raise ValueError(f"Circular schema reference detected for {ref}")
        target = self._resolve_pointer(self._raw_schema, ref)
        self._resolving.add(ref)
        sanitized_target, _ = self._sanitize_schema(target)
        self._resolving.remove(ref)
        if not isinstance(sanitized_target, dict):
            raise TypeError("Referenced schema must resolve to a mapping")
        self._ref_cache[ref] = sanitized_target
        return self._clone(sanitized_target)

    def _resolve_pointer(self, schema: Any, pointer: str) -> Any:
        if pointer == "#":
            return schema
        if not pointer.startswith("#/"):
            raise ValueError(f"Unsupported schema reference: {pointer}")
        parts = pointer[2:].split("/")
        current = schema
        for raw_part in parts:
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                if part not in current:
                    raise KeyError(f"Cannot resolve pointer {pointer}")
                current = current[part]
            elif isinstance(current, list):
                index = int(part)
                current = current[index]
            else:
                raise KeyError(f"Cannot resolve pointer {pointer}")
        return current

    def _merge_schema_dicts(self, base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
        result = self._clone(base)
        for key, value in overlay.items():
            if key == "properties" and isinstance(value, dict):
                existing = result.get("properties")
                if isinstance(existing, dict):
                    merged = existing.copy()
                    merged.update(value)
                    result["properties"] = merged
                else:
                    result["properties"] = self._clone(value)
            else:
                result[key] = self._clone(value)
        return result

    def _merge_union_into_node(self, node: Dict[str, Any], options: List[Any]) -> Dict[str, Any]:
        if not options:
            return dict(node)
        simple_types: List[Any] = []
        complex_options: List[Any] = []
        for option in options:
            if isinstance(option, dict) and set(option.keys()) == {"type"}:
                simple_types.append(option["type"])
            else:
                complex_options.append(option)
        if complex_options:
            raise ValueError("Unsupported schema union with complex options")
        flattened: List[Any] = []
        for type_value in simple_types:
            if isinstance(type_value, list):
                for item in type_value:
                    if item not in flattened:
                        flattened.append(item)
            else:
                if type_value not in flattened:
                    flattened.append(type_value)
        merged = dict(node)
        if flattened:
            merged["type"] = flattened[0] if len(flattened) == 1 else flattened
        return merged

    def _clone(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self._clone(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._clone(item) for item in value]
        return value

    @staticmethod
    def _is_null_schema(schema: Any) -> bool:
        return isinstance(schema, dict) and schema.get("type") == "null" and len(schema) == 1


def _model_json_schema(model: type[_BaseModel], *, by_alias: bool) -> Dict[str, Any]:
    schema = model.model_json_schema(by_alias=by_alias)
    if not isinstance(schema, dict):
        raise TypeError("Expected model_json_schema to return a mapping")
    sanitizer = _SchemaSanitizer(schema)
    return sanitizer.sanitize()


class PathInput(_BaseModel):
    path: str


class OpenInfoOutput(_BaseModel):
    meta: Dict[str, Any]
    sectionCount: int
    paragraphCount: int
    headerCount: int


class SectionsOutput(_BaseModel):
    sections: List[Dict[str, Any]]


class HeadersOutput(_BaseModel):
    headers: List[Dict[str, Any]]


class PackagePartOutput(_BaseModel):
    parts: List[str]


class PackageTextInput(PathInput):
    part_name: str = Field(alias="partName")
    encoding: Optional[str] = None


class PackageTextOutput(_BaseModel):
    text: str


class ReadTextInput(PathInput):
    offset: int = 0
    limit: Optional[int] = None
    with_highlights: bool = Field(False, alias="withHighlights")
    with_footnotes: bool = Field(False, alias="withFootnotes")


class ReadTextOutput(_BaseModel):
    textChunk: str
    nextOffset: Optional[int]


class ReadParagraphsInput(PathInput):
    paragraph_indexes: Sequence[int] = Field(alias="paragraphIndexes")
    with_highlights: bool = Field(False, alias="withHighlights")
    with_footnotes: bool = Field(False, alias="withFootnotes")


class ParagraphText(_BaseModel):
    paragraphIndex: int
    text: str


class ReadParagraphsOutput(_BaseModel):
    paragraphs: List[ParagraphText]


class TextExtractReportInput(PathInput):
    mode: str = "plain"


class TextExtractReportOutput(_BaseModel):
    content: str


class FindInput(PathInput):
    query: str
    is_regex: bool = Field(False, alias="isRegex")
    max_results: int = Field(100, alias="maxResults")
    context_radius: int = Field(80, alias="contextRadius")


class MatchResult(_BaseModel):
    paragraphIndex: int
    start: int
    end: int
    context: str


class FindOutput(_BaseModel):
    matches: List[MatchResult]


class StyleFilter(_BaseModel):
    colorHex: Optional[str] = None
    underline: Optional[bool] = None
    charPrIDRef: Optional[str] = None


class FindRunsInput(PathInput):
    filters: Optional[StyleFilter] = None
    max_results: int = Field(200, alias="maxResults")


class RunInfo(_BaseModel):
    text: str
    paragraphIndex: int
    charPrIDRef: Optional[str]
    style: Dict[str, Any]


class FindRunsOutput(_BaseModel):
    runs: List[RunInfo]


class ReplaceRunsInput(PathInput):
    search: str
    replacement: str
    style_filter: Optional[StyleFilter] = Field(None, alias="styleFilter")
    limit_per_run: Optional[int] = Field(None, alias="limitPerRun")
    dry_run: bool = Field(True, alias="dryRun")


class ReplaceRunsOutput(_BaseModel):
    replacedCount: int


class RunStyleModel(_BaseModel):
    bold: Optional[bool] = False
    italic: Optional[bool] = False
    underline: Optional[bool] = False
    colorHex: Optional[str] = None


class AddParagraphInput(PathInput):
    text: str = ""
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    run_style: Optional[RunStyleModel] = Field(None, alias="runStyle")


class AddParagraphOutput(_BaseModel):
    paragraphIndex: int


class InsertParagraphsInput(PathInput):
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    paragraphs: Sequence[str]
    run_style: Optional[RunStyleModel] = Field(None, alias="runStyle")
    dry_run: bool = Field(True, alias="dryRun")


class InsertParagraphsOutput(_BaseModel):
    added: int


class AddTableInput(PathInput):
    rows: int
    cols: int
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    border_style: Optional[Literal["solid", "none"]] = Field(None, alias="borderStyle")
    border_color: Optional[str] = Field(None, alias="borderColor")
    border_width: Optional[str | float | int] = Field(None, alias="borderWidth")
    fill_color: Optional[str] = Field(None, alias="fillColor")


class AddTableOutput(_BaseModel):
    tableIndex: int
    cellCount: int


class SetTableBorderFillInput(PathInput):
    table_index: int = Field(alias="tableIndex")
    border_style: Optional[Literal["solid", "none"]] = Field(None, alias="borderStyle")
    border_color: Optional[str] = Field(None, alias="borderColor")
    border_width: Optional[str | float | int] = Field(None, alias="borderWidth")
    fill_color: Optional[str] = Field(None, alias="fillColor")


class SetTableBorderFillOutput(_BaseModel):
    borderFillIDRef: str
    anchorCells: int


class TableCellAnchor(_BaseModel):
    row: int
    column: int


class TableCellPosition(_BaseModel):
    row: int
    column: int
    anchor: TableCellAnchor
    rowSpan: int
    colSpan: int
    text: Optional[str] = None


class GetTableCellMapInput(PathInput):
    table_index: int = Field(alias="tableIndex")


class TableCellMapOutput(_BaseModel):
    rowCount: int
    columnCount: int
    grid: List[List[TableCellPosition]]


class SetTableCellInput(PathInput):
    table_index: int = Field(alias="tableIndex")
    row: int
    col: int
    text: str
    logical: Optional[bool] = Field(None, alias="logical")
    split_merged: Optional[bool] = Field(None, alias="splitMerged")
    dry_run: bool = Field(False, alias="dryRun")


class SetTableCellOutput(_BaseModel):
    ok: bool


class ReplaceTableRegionInput(PathInput):
    table_index: int = Field(alias="tableIndex")
    start_row: int = Field(alias="startRow")
    start_col: int = Field(alias="startCol")
    values: Sequence[Sequence[str]]
    logical: Optional[bool] = Field(None, alias="logical")
    split_merged: Optional[bool] = Field(None, alias="splitMerged")
    dry_run: bool = Field(False, alias="dryRun")


class ReplaceTableRegionOutput(_BaseModel):
    updatedCells: int


class SplitTableCellInput(PathInput):
    table_index: int = Field(alias="tableIndex")
    row: int
    col: int


class SplitTableCellOutput(_BaseModel):
    startRow: int
    startCol: int
    rowSpan: int
    colSpan: int


class AddShapeInput(PathInput):
    shape_type: str = Field("RECTANGLE", alias="shapeType")
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    dry_run: bool = Field(True, alias="dryRun")


class ObjectIdOutput(_BaseModel):
    objectId: Optional[str]


class AddControlInput(PathInput):
    control_type: str = Field("TEXTBOX", alias="controlType")
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    dry_run: bool = Field(True, alias="dryRun")


class AddMemoInput(PathInput):
    text: str
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    author: Optional[str] = None
    timestamp: Optional[str] = None


class AddMemoOutput(_BaseModel):
    memoId: Optional[str]


class AttachMemoFieldInput(PathInput):
    paragraph_index: int = Field(alias="paragraphIndex")
    memo_id: str = Field(alias="memoId")


class AttachMemoFieldOutput(_BaseModel):
    fieldId: str


class AddMemoWithAnchorInput(PathInput):
    text: str
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    memo_shape_id_ref: Optional[str] = Field(None, alias="memoShapeIdRef")


class AddMemoWithAnchorOutput(_BaseModel):
    memoId: Optional[str]
    paragraphIndex: int
    fieldId: str


class RemoveMemoInput(PathInput):
    memo_id: str = Field(alias="memoId")
    dry_run: bool = Field(True, alias="dryRun")


class RemoveMemoOutput(_BaseModel):
    removed: bool


class EnsureRunStyleInput(PathInput):
    bold: Optional[bool] = False
    italic: Optional[bool] = False
    underline: Optional[bool] = False
    colorHex: Optional[str] = None


class EnsureRunStyleOutput(_BaseModel):
    charPrIDRef: Optional[str]


class StylesAndBulletsOutput(_BaseModel):
    styles: List[Dict[str, Any]]
    bullets: List[Dict[str, Any]]


class TextSpanModel(_BaseModel):
    paragraph_index: int = Field(alias="paragraphIndex")
    start: int
    end: int


class ApplyStyleToTextInput(PathInput):
    spans: Sequence[TextSpanModel]
    char_pr_id_ref: str = Field(alias="charPrIDRef")
    dry_run: bool = Field(True, alias="dryRun")


class ApplyStyleToTextOutput(_BaseModel):
    styledSpans: int


class ApplyStyleInput(PathInput):
    paragraph_indexes: Sequence[int] = Field(alias="paragraphIndexes")
    char_pr_id_ref: str = Field(alias="charPrIDRef")
    dry_run: bool = Field(True, alias="dryRun")


class ApplyStyleOutput(_BaseModel):
    updated: int


class SaveOutput(_BaseModel):
    ok: bool


class SaveAsInput(PathInput):
    out: str


class OutPathOutput(_BaseModel):
    outPath: str


class MakeBlankInput(_BaseModel):
    out: str


class MasterHistoryVersionOutput(_BaseModel):
    masterPages: List[Any]
    histories: List[Any]
    versions: Optional[Dict[str, Any]]


class ObjectFindByTagInput(PathInput):
    tag_name: str = Field(alias="tagName")
    max_results: int = Field(200, alias="maxResults")


class ObjectFindByAttrInput(PathInput):
    element_type: str = Field(alias="elementType")
    attr: str
    value: str
    max_results: int = Field(200, alias="maxResults")


class ObjectsOutput(_BaseModel):
    objects: List[Dict[str, Any]]


class ValidateStructureInput(PathInput):
    level: str = "basic"


class ValidateStructureOutput(_BaseModel):
    ok: bool
    issues: List[Dict[str, Any]]


class LintRules(_BaseModel):
    max_line_len: Optional[int] = Field(None, alias="maxLineLen")
    forbid_patterns: Optional[Sequence[str]] = Field(None, alias="forbidPatterns")


class LintInput(PathInput):
    rules: LintRules = Field(default_factory=LintRules)


class LintOutput(_BaseModel):
    warnings: List[Dict[str, Any]]


class PackageXmlInput(PathInput):
    part_name: str = Field(alias="partName")


class PackageXmlOutput(_BaseModel):
    xmlString: str


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_model: type[_BaseModel]
    output_model: type[_BaseModel]
    func: Callable[[HwpxOps, _BaseModel], Dict[str, Any]]

    def to_tool(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            description=self.description,
            inputSchema=_model_json_schema(self.input_model, by_alias=True),
            outputSchema=_model_json_schema(self.output_model, by_alias=True),
        )

    def call(self, ops: HwpxOps, arguments: Dict[str, Any]) -> Dict[str, Any]:
        data = self.input_model.model_validate(arguments)
        raw = self.func(ops, data)
        return self.output_model.model_validate(raw).model_dump(by_alias=True)


def _simple(method_name: str) -> Callable[[HwpxOps, _BaseModel], Dict[str, Any]]:
    def caller(ops: HwpxOps, data: _BaseModel) -> Dict[str, Any]:
        method = getattr(ops, method_name)
        payload = data.model_dump()
        return method(**payload)

    return caller


def build_tool_definitions() -> List[ToolDefinition]:
    return [
        ToolDefinition(
            name="open_info",
            description="Return metadata about an HWPX document.",
            input_model=PathInput,
            output_model=OpenInfoOutput,
            func=_simple("open_info"),
        ),
        ToolDefinition(
            name="list_sections",
            description="List sections within a document.",
            input_model=PathInput,
            output_model=SectionsOutput,
            func=_simple("list_sections"),
        ),
        ToolDefinition(
            name="list_headers",
            description="List header references used by the document.",
            input_model=PathInput,
            output_model=HeadersOutput,
            func=_simple("list_headers"),
        ),
        ToolDefinition(
            name="package_parts",
            description="List OPC package part names.",
            input_model=PathInput,
            output_model=PackagePartOutput,
            func=_simple("package_parts"),
        ),
        ToolDefinition(
            name="package_get_text",
            description="Read the raw text payload of an OPC part.",
            input_model=PackageTextInput,
            output_model=PackageTextOutput,
            func=_simple("package_get_text"),
        ),
        ToolDefinition(
            name="read_text",
            description="Read document text using pagination.",
            input_model=ReadTextInput,
            output_model=ReadTextOutput,
            func=_simple("read_text"),
        ),
        ToolDefinition(
            name="read_paragraphs",
            description="Read specific paragraphs by index.",
            input_model=ReadParagraphsInput,
            output_model=ReadParagraphsOutput,
            func=_simple("get_paragraphs"),
        ),
        ToolDefinition(
            name="text_extract_report",
            description="Extract the full text of the document.",
            input_model=TextExtractReportInput,
            output_model=TextExtractReportOutput,
            func=_simple("text_extract_report"),
        ),
        ToolDefinition(
            name="find",
            description="Search for text occurrences.",
            input_model=FindInput,
            output_model=FindOutput,
            func=_simple("find"),
        ),
        ToolDefinition(
            name="find_runs_by_style",
            description="Find runs filtered by style attributes.",
            input_model=FindRunsInput,
            output_model=FindRunsOutput,
            func=_simple("find_runs_by_style"),
        ),
        ToolDefinition(
            name="replace_text_in_runs",
            description="Replace text within runs matching a style filter.",
            input_model=ReplaceRunsInput,
            output_model=ReplaceRunsOutput,
            func=_simple("replace_text_in_runs"),
        ),
        ToolDefinition(
            name="add_paragraph",
            description="Append a new paragraph to the document.",
            input_model=AddParagraphInput,
            output_model=AddParagraphOutput,
            func=_simple("add_paragraph"),
        ),
        ToolDefinition(
            name="insert_paragraphs_bulk",
            description="Insert multiple paragraphs efficiently.",
            input_model=InsertParagraphsInput,
            output_model=InsertParagraphsOutput,
            func=_simple("insert_paragraphs_bulk"),
        ),
        ToolDefinition(
            name="add_table",
            description="Add a table to the document.",
            input_model=AddTableInput,
            output_model=AddTableOutput,
            func=_simple("add_table"),
        ),
        ToolDefinition(
            name="set_table_border_fill",
            description="Update a table's border fill and anchor cells.",
            input_model=SetTableBorderFillInput,
            output_model=SetTableBorderFillOutput,
            func=_simple("set_table_border_fill"),
        ),
        ToolDefinition(
            name="get_table_cell_map",
            description="Return the logical grid coverage for a table, including merged spans.",
            input_model=GetTableCellMapInput,
            output_model=TableCellMapOutput,
            func=_simple("get_table_cell_map"),
        ),
        ToolDefinition(
            name="set_table_cell_text",
            description="Update the text of a table cell.",
            input_model=SetTableCellInput,
            output_model=SetTableCellOutput,
            func=_simple("set_table_cell_text"),
        ),
        ToolDefinition(
            name="replace_table_region",
            description="Replace a region of table cells.",
            input_model=ReplaceTableRegionInput,
            output_model=ReplaceTableRegionOutput,
            func=_simple("replace_table_region"),
        ),
        ToolDefinition(
            name="split_table_cell",
            description="Split a merged table cell back into individual cells and report the original span.",
            input_model=SplitTableCellInput,
            output_model=SplitTableCellOutput,
            func=_simple("split_table_cell"),
        ),
        ToolDefinition(
            name="add_shape",
            description="Insert a basic shape object.",
            input_model=AddShapeInput,
            output_model=ObjectIdOutput,
            func=_simple("add_shape"),
        ),
        ToolDefinition(
            name="add_control",
            description="Insert a control object.",
            input_model=AddControlInput,
            output_model=ObjectIdOutput,
            func=_simple("add_control"),
        ),
        ToolDefinition(
            name="add_memo",
            description="Create a memo entry.",
            input_model=AddMemoInput,
            output_model=AddMemoOutput,
            func=_simple("add_memo"),
        ),
        ToolDefinition(
            name="attach_memo_field",
            description="Attach a memo to a paragraph via field.",
            input_model=AttachMemoFieldInput,
            output_model=AttachMemoFieldOutput,
            func=_simple("attach_memo_field"),
        ),
        ToolDefinition(
            name="add_memo_with_anchor",
            description="Create a memo and insert an anchor paragraph.",
            input_model=AddMemoWithAnchorInput,
            output_model=AddMemoWithAnchorOutput,
            func=_simple("add_memo_with_anchor"),
        ),
        ToolDefinition(
            name="remove_memo",
            description="Remove a memo by identifier.",
            input_model=RemoveMemoInput,
            output_model=RemoveMemoOutput,
            func=_simple("remove_memo"),
        ),
        ToolDefinition(
            name="ensure_run_style",
            description="Ensure a run style exists and return its identifier.",
            input_model=EnsureRunStyleInput,
            output_model=EnsureRunStyleOutput,
            func=_simple("ensure_run_style"),
        ),
        ToolDefinition(
            name="list_styles_and_bullets",
            description="List style and bullet definitions.",
            input_model=PathInput,
            output_model=StylesAndBulletsOutput,
            func=_simple("list_styles_and_bullets"),
        ),
        ToolDefinition(
            name="apply_style_to_text_ranges",
            description="Apply a charPr style to specific text spans.",
            input_model=ApplyStyleToTextInput,
            output_model=ApplyStyleToTextOutput,
            func=_simple("apply_style_to_text_ranges"),
        ),
        ToolDefinition(
            name="apply_style_to_paragraphs",
            description="Apply a charPr style to paragraphs and runs.",
            input_model=ApplyStyleInput,
            output_model=ApplyStyleOutput,
            func=_simple("apply_style_to_paragraphs"),
        ),
        ToolDefinition(
            name="save",
            description="Persist in-memory changes to disk.",
            input_model=PathInput,
            output_model=SaveOutput,
            func=_simple("save"),
        ),
        ToolDefinition(
            name="save_as",
            description="Save the document to a new path.",
            input_model=SaveAsInput,
            output_model=OutPathOutput,
            func=_simple("save_as"),
        ),
        ToolDefinition(
            name="make_blank",
            description="Create a new blank HWPX file.",
            input_model=MakeBlankInput,
            output_model=OutPathOutput,
            func=_simple("make_blank"),
        ),
        ToolDefinition(
            name="list_master_pages_histories_versions",
            description="List master pages, histories and version info.",
            input_model=PathInput,
            output_model=MasterHistoryVersionOutput,
            func=_simple("list_master_pages_histories_versions"),
        ),
        ToolDefinition(
            name="object_find_by_tag",
            description="Find objects by tag name.",
            input_model=ObjectFindByTagInput,
            output_model=ObjectsOutput,
            func=_simple("object_find_by_tag"),
        ),
        ToolDefinition(
            name="object_find_by_attr",
            description="Find objects by attribute value.",
            input_model=ObjectFindByAttrInput,
            output_model=ObjectsOutput,
            func=_simple("object_find_by_attr"),
        ),
        ToolDefinition(
            name="validate_structure",
            description="Validate document structure using schema checks.",
            input_model=ValidateStructureInput,
            output_model=ValidateStructureOutput,
            func=_simple("validate_structure"),
        ),
        ToolDefinition(
            name="lint_text_conventions",
            description="Run lightweight lint checks against paragraphs.",
            input_model=LintInput,
            output_model=LintOutput,
            func=lambda ops, data: ops.lint_text_conventions(
                data.path,
                **(data.rules.model_dump()),
            ),
        ),
        ToolDefinition(
            name="package_get_xml",
            description="Read an OPC part as XML string.",
            input_model=PackageXmlInput,
            output_model=PackageXmlOutput,
            func=_simple("package_get_xml"),
        ),
    ]
