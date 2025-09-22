"""MCP 서버가 제공하는 도구 정의."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

import mcp.types as types
from pydantic import BaseModel, Field, ConfigDict
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue

from .hwpx_ops import HwpxOps


class _BaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class _Draft7JsonSchemaGenerator(GenerateJsonSchema):
    schema_dialect = "http://json-schema.org/draft-07/schema#"
    ref_template = "#/definitions/{model}"


def _is_null_schema(schema: JsonSchemaValue) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get("type") != "null":
        return False
    allowed_keys = {"type", "title", "description", "default", "examples"}
    return set(schema).issubset(allowed_keys)


def _collapse_nullable(schema: Dict[str, Any]) -> Dict[str, Any]:
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        null_entries = [item for item in any_of if _is_null_schema(item)]
        non_null_entries = [item for item in any_of if not _is_null_schema(item)]
        if len(non_null_entries) == 1 and null_entries:
            base: Dict[str, Any] = {k: v for k, v in schema.items() if k != "anyOf"}
            non_null = non_null_entries[0]
            if isinstance(non_null, dict):
                for key, value in non_null.items():
                    if key in {"title", "description"} and key in base:
                        continue
                    base[key] = value
            base["nullable"] = True
            return base

    type_value = schema.get("type")
    if isinstance(type_value, list):
        non_null_types = [t for t in type_value if t != "null"]
        if len(non_null_types) == 1 and len(non_null_types) != len(type_value):
            schema["type"] = non_null_types[0]
            schema["nullable"] = True

    return schema


def _normalize_draft7_schema(value: JsonSchemaValue) -> JsonSchemaValue:
    if isinstance(value, dict):
        normalized: Dict[str, Any] = {}
        for key, item in value.items():
            if key == "$schema":
                continue
            if key == "$defs" and isinstance(item, dict):
                normalized["definitions"] = {
                    name: _normalize_draft7_schema(sub_schema)
                    for name, sub_schema in item.items()
                }
                continue
            if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/"):
                normalized["$ref"] = "#/definitions/" + item[len("#/$defs/"):]
                continue
            normalized[key] = _normalize_draft7_schema(item)

        if "anyOf" in normalized:
            normalized = _collapse_nullable(normalized)

        return normalized

    if isinstance(value, list):
        return [_normalize_draft7_schema(item) for item in value]

    return value


def _model_json_schema(model: type[_BaseModel], *, by_alias: bool) -> Dict[str, Any]:
    raw = model.model_json_schema(
        by_alias=by_alias,
        schema_generator=_Draft7JsonSchemaGenerator,
    )
    schema = _normalize_draft7_schema(raw)
    if not isinstance(schema, dict):
        raise TypeError("Expected model_json_schema to return a mapping")
    if schema.get("type") != "object":
        schema["type"] = "object"
    schema.setdefault("properties", {})
    return schema


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
    border_style: Optional[str] = Field(None, alias="borderStyle")


class AddTableOutput(_BaseModel):
    tableIndex: int
    cellCount: int


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
