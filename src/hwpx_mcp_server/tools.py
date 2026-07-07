# SPDX-License-Identifier: Apache-2.0
"""MCP 서버가 제공하는 도구 정의."""

from __future__ import annotations

import os
import logging

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Literal

import mcp.types as types
from pydantic import BaseModel, Field, ConfigDict, model_validator

from .core.plan import (
    ApplyEditInput,
    ContextOutput,
    GetContextInput,
    PlanEditInput,
    PreviewEditInput,
    SearchInput,
    SearchOutput,
    ServerResponse,
)
from .core.locator import (
    DocumentLocator,
    HandleLocator,
    document_locator_schema,
    normalize_locator_payload,
    locator_path,
)
from .schema.builder import build_tool_schema
from .hwpx_ops import HwpxOps


LOGGER = logging.getLogger(__name__)
ToolCategory = Literal["core", "tables", "styles", "pipeline", "debug"]
_TOOL_CATEGORY_SET: set[str] = {"core", "tables", "styles", "pipeline", "debug"}


class _BaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def _hardening_enabled() -> bool:
    value = os.getenv("HWPX_MCP_HARDENING", "0")
    return value.strip().lower() in {"1", "true", "yes", "on"}


class DocumentLocatorInput(_BaseModel):
    document: DocumentLocator = Field(alias="document")

    @model_validator(mode="before")
    @classmethod
    def _inflate_document(cls, data: object) -> object:
        if isinstance(data, dict):
            return normalize_locator_payload(dict(data), field_name="document")
        return data

    def to_hwpx_payload(self, *, require_path: bool = True) -> Dict[str, Any]:
        payload = self.model_dump(exclude={"document"})
        path = locator_path(self.document)
        if path is not None:
            payload["path"] = path
            return payload

        if isinstance(self.document, HandleLocator):
            payload["handleId"] = self.document.handle_id
            if require_path:
                payload["path"] = None
            return payload

        if require_path:
            raise ValueError("document locator must include a path, uri, or handleId")
        return payload

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        properties = schema.get("properties")
        if isinstance(properties, dict) and "document" in properties:
            properties["document"] = document_locator_schema()
        return schema


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


class PackageTextInput(DocumentLocatorInput):
    part_name: str = Field(alias="partName")
    encoding: Optional[str] = None


class PackageTextOutput(_BaseModel):
    text: str


class RepairHwpxInput(_BaseModel):
    source: str
    output: str
    recover: bool = False
    overwrite: bool = False
    max_entry_size: int = Field(64 * 1024 * 1024, alias="maxEntrySize")
    max_total_size: int = Field(512 * 1024 * 1024, alias="maxTotalSize")
    max_source_size: int = Field(512 * 1024 * 1024, alias="maxSourceSize")


class RepairHwpxOutput(_BaseModel):
    outputPath: str
    entries: List[str]
    entryCount: int
    reordered: bool
    crcOk: bool
    recovered: bool
    validatePackage: Dict[str, Any]


class ReadTextInput(DocumentLocatorInput):
    offset: int = 0
    limit: Optional[int] = None
    with_highlights: bool = Field(False, alias="withHighlights")
    with_footnotes: bool = Field(False, alias="withFootnotes")


class ReadTextOutput(_BaseModel):
    textChunk: str
    nextOffset: Optional[int]


class ReadParagraphsInput(DocumentLocatorInput):
    paragraph_indexes: Sequence[int] = Field(alias="paragraphIndexes")
    with_highlights: bool = Field(False, alias="withHighlights")
    with_footnotes: bool = Field(False, alias="withFootnotes")


class ParagraphText(_BaseModel):
    paragraphIndex: int
    text: str


class ReadParagraphsOutput(_BaseModel):
    paragraphs: List[ParagraphText]


class TextExtractReportInput(DocumentLocatorInput):
    mode: str = "plain"


class TextExtractReportOutput(_BaseModel):
    content: str


class AnalyzeTemplateInput(DocumentLocatorInput):
    placeholder_patterns: Optional[Sequence[str]] = Field(None, alias="placeholderPatterns")
    lock_keywords: Optional[Sequence[str]] = Field(None, alias="lockKeywords")


class TemplateRegion(_BaseModel):
    name: str
    startParagraph: int
    endParagraph: int
    editable: bool
    reason: str


class TemplatePlaceholder(_BaseModel):
    token: str
    paragraphIndex: int
    zone: str
    editable: bool
    context: str


class AnalyzeTemplateOutput(_BaseModel):
    summary: Dict[str, Any]
    regions: List[TemplateRegion]
    placeholders: List[TemplatePlaceholder]


class FindInput(DocumentLocatorInput):
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


class FindRunsInput(DocumentLocatorInput):
    filters: Optional[StyleFilter] = None
    max_results: int = Field(200, alias="maxResults")


class RunInfo(_BaseModel):
    text: str
    paragraphIndex: int
    charPrIDRef: Optional[str]
    style: Dict[str, Any]


class FindRunsOutput(_BaseModel):
    runs: List[RunInfo]


class ReplaceRunsInput(DocumentLocatorInput):
    search: str
    replacement: str
    style_filter: Optional[StyleFilter] = Field(None, alias="styleFilter")
    limit_per_run: Optional[int] = Field(None, alias="limitPerRun")
    dry_run: bool = Field(False, alias="dryRun")


class ReplaceRunsOutput(_BaseModel):
    replacedCount: int


class ApplyEditsInput(DocumentLocatorInput):
    operations: Sequence[Dict[str, Any]]
    dry_run: bool = Field(False, alias="dryRun")


class ApplyEditsOutput(_BaseModel):
    ok: bool
    rolledBack: bool
    dryRun: bool = False
    filename: Optional[str] = None
    operationsApplied: int
    operationResults: Optional[List[Dict[str, Any]]] = None
    failedOperationIndex: Optional[int] = None
    error: Optional[str] = None
    wouldSave: Optional[bool] = None
    verificationReport: Optional[Dict[str, Any]] = None
    openSafety: Optional[Dict[str, Any]] = None
    visualComplete: Optional[Dict[str, Any]] = None
    semanticDiff: Optional[Dict[str, Any]] = None
    backup: Optional[Dict[str, Any]] = None


class UndoLastEditOutput(_BaseModel):
    restored: bool
    filename: str
    backupPath: str
    verificationReport: Optional[Dict[str, Any]] = None
    openSafety: Optional[Dict[str, Any]] = None
    visualComplete: Optional[Dict[str, Any]] = None
    semanticDiff: Optional[Dict[str, Any]] = None


class RunStyleModel(_BaseModel):
    bold: Optional[bool] = False
    italic: Optional[bool] = False
    underline: Optional[bool] = False
    colorHex: Optional[str] = None


class AddParagraphInput(DocumentLocatorInput):
    text: str = ""
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    run_style: Optional[RunStyleModel] = Field(None, alias="runStyle")


class AddParagraphOutput(_BaseModel):
    paragraphIndex: int


class InsertParagraphsInput(DocumentLocatorInput):
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    paragraphs: Sequence[str]
    run_style: Optional[RunStyleModel] = Field(None, alias="runStyle")
    dry_run: bool = Field(False, alias="dryRun")


class InsertParagraphsOutput(_BaseModel):
    added: int


class FormattingEditOutput(_BaseModel):
    ok: Optional[bool] = None
    dryRun: bool = False
    filename: Optional[str] = None
    formatted: Optional[int] = None
    paragraphs: Optional[List[Dict[str, Any]]] = None
    pageSize: Optional[Dict[str, Any]] = None
    margins: Optional[Dict[str, Any]] = None
    columns: Optional[Dict[str, Any]] = None
    units: Optional[Dict[str, Any]] = None
    kind: Optional[str] = None
    level: Optional[int] = None
    paraPrIDRef: Optional[str] = None
    target: Optional[str] = None
    format: Optional[str] = None
    headerFooter: Optional[Dict[str, Any]] = None
    wouldSave: Optional[bool] = None
    verificationReport: Optional[Dict[str, Any]] = None
    openSafety: Optional[Dict[str, Any]] = None
    visualComplete: Optional[Dict[str, Any]] = None
    semanticDiff: Optional[Dict[str, Any]] = None
    backup: Optional[Dict[str, Any]] = None


class ListFormFieldsOutput(_BaseModel):
    fieldCount: int
    fields: List[Dict[str, Any]]
    fallback: Optional[str] = None


class FillFormFieldInput(DocumentLocatorInput):
    value: str
    field_index: Optional[int] = Field(None, alias="fieldIndex")
    field_id: Optional[str] = Field(None, alias="fieldId")
    name: Optional[str] = None
    dry_run: bool = Field(False, alias="dryRun")


class FillFormFieldOutput(_BaseModel):
    ok: bool
    filename: Optional[str] = None
    field: Dict[str, Any]
    before_value: Optional[str] = Field(None, alias="beforeValue")
    after_value: Optional[str] = Field(None, alias="afterValue")
    style_before: Optional[List[Any]] = Field(None, alias="styleBefore")
    style_after: Optional[List[Any]] = Field(None, alias="styleAfter")
    style_preserved: Optional[bool] = Field(None, alias="stylePreserved")
    dryRun: bool = False
    wouldSave: Optional[bool] = None
    verificationReport: Optional[Dict[str, Any]] = None
    openSafety: Optional[Dict[str, Any]] = None
    visualComplete: Optional[Dict[str, Any]] = None
    semanticDiff: Optional[Dict[str, Any]] = None
    backup: Optional[Dict[str, Any]] = None


class SetParagraphFormatInput(DocumentLocatorInput):
    paragraph_index: Optional[int] = Field(None, alias="paragraphIndex")
    paragraph_indexes: Optional[Sequence[int]] = Field(None, alias="paragraphIndexes")
    alignment: Optional[str] = None
    line_spacing_percent: Optional[float] = Field(None, alias="lineSpacingPercent")
    indent_left_mm: Optional[float] = Field(None, alias="indentLeftMm")
    indent_right_mm: Optional[float] = Field(None, alias="indentRightMm")
    first_line_indent_mm: Optional[float] = Field(None, alias="firstLineIndentMm")
    spacing_before_pt: Optional[float] = Field(None, alias="spacingBeforePt")
    spacing_after_pt: Optional[float] = Field(None, alias="spacingAfterPt")
    outline_level: Optional[int] = Field(None, alias="outlineLevel")
    keep_with_next: Optional[bool] = Field(None, alias="keepWithNext")
    keep_lines: Optional[bool] = Field(None, alias="keepLines")
    page_break_before: Optional[bool] = Field(None, alias="pageBreakBefore")
    dry_run: bool = Field(False, alias="dryRun")


class SetPageSetupInput(DocumentLocatorInput):
    paper_size: Optional[str] = Field(None, alias="paperSize")
    width_mm: Optional[float] = Field(None, alias="widthMm")
    height_mm: Optional[float] = Field(None, alias="heightMm")
    orientation: Optional[str] = None
    margins_mm: Optional[Dict[str, float]] = Field(None, alias="marginsMm")
    margin_left_mm: Optional[float] = Field(None, alias="marginLeftMm")
    margin_right_mm: Optional[float] = Field(None, alias="marginRightMm")
    margin_top_mm: Optional[float] = Field(None, alias="marginTopMm")
    margin_bottom_mm: Optional[float] = Field(None, alias="marginBottomMm")
    header_margin_mm: Optional[float] = Field(None, alias="headerMarginMm")
    footer_margin_mm: Optional[float] = Field(None, alias="footerMarginMm")
    gutter_mm: Optional[float] = Field(None, alias="gutterMm")
    columns: Optional[int] = None
    column_gap_mm: Optional[float] = Field(None, alias="columnGapMm")
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    dry_run: bool = Field(False, alias="dryRun")


class SetHeaderFooterInput(DocumentLocatorInput):
    kind: str
    text: Optional[str] = None
    content: Optional[Sequence[Dict[str, Any]]] = None
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    page_type: str = Field("BOTH", alias="pageType")
    dry_run: bool = Field(False, alias="dryRun")


class SetPageNumberInput(DocumentLocatorInput):
    target: str = "footer"
    page_type: str = Field("BOTH", alias="pageType")
    format: str = "page"
    align: str = "CENTER"
    position: str = "BOTTOM_CENTER"
    prefix: str = ""
    suffix: str = ""
    format_type: Optional[str] = Field(None, alias="formatType")
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    dry_run: bool = Field(False, alias="dryRun")


class SetListFormatInput(DocumentLocatorInput):
    paragraph_index: Optional[int] = Field(None, alias="paragraphIndex")
    paragraph_indexes: Optional[Sequence[int]] = Field(None, alias="paragraphIndexes")
    kind: str = "bullet"
    level: int = 1
    bullet_char: Optional[str] = Field(None, alias="bulletChar")
    number_format: Optional[str] = Field(None, alias="numberFormat")
    start: Optional[int] = None
    dry_run: bool = Field(False, alias="dryRun")


class AddTableInput(DocumentLocatorInput):
    rows: int
    cols: int
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    border_style: Optional[Literal["solid", "none"]] = Field(None, alias="borderStyle")
    border_color: Optional[str] = Field(None, alias="borderColor")
    border_width: Optional[str | float | int] = Field(
        None,
        alias="borderWidth",
        description="Human border width. Prefer strings with units such as '0.1 mm' or '1 pt'; bare numbers are treated by python-hwpx compatibility code.",
    )
    fill_color: Optional[str] = Field(None, alias="fillColor")
    auto_fit: bool = Field(False, alias="autoFit")


class AddTableOutput(_BaseModel):
    tableIndex: int
    cellCount: int


class SetTableBorderFillInput(DocumentLocatorInput):
    table_index: int = Field(alias="tableIndex")
    border_style: Optional[Literal["solid", "none"]] = Field(None, alias="borderStyle")
    border_color: Optional[str] = Field(None, alias="borderColor")
    border_width: Optional[str | float | int] = Field(
        None,
        alias="borderWidth",
        description="Human border width. Prefer strings with units such as '0.1 mm' or '1 pt'; bare numbers are treated by python-hwpx compatibility code.",
    )
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


class GetTableCellMapInput(DocumentLocatorInput):
    table_index: int = Field(alias="tableIndex")


class TableCellMapOutput(_BaseModel):
    rowCount: int
    columnCount: int
    grid: List[List[TableCellPosition]]


class SetTableCellInput(DocumentLocatorInput):
    table_index: int = Field(alias="tableIndex")
    row: int
    col: int
    text: str
    logical: Optional[bool] = Field(None, alias="logical")
    split_merged: Optional[bool] = Field(None, alias="splitMerged")
    dry_run: bool = Field(False, alias="dryRun")
    auto_fit: bool = Field(False, alias="autoFit")


class SetTableCellOutput(_BaseModel):
    ok: bool


class ParagraphBytePatch(_BaseModel):
    section_path: str = Field("Contents/section0.xml", alias="sectionPath")
    paragraph_index: int = Field(alias="paragraphIndex")
    text: str


class BytePreservingPatchInput(DocumentLocatorInput):
    patches: Sequence[ParagraphBytePatch]
    output: Optional[str] = None

    def to_hwpx_payload(self, *, require_path: bool = True) -> Dict[str, Any]:
        payload = super().to_hwpx_payload(require_path=require_path)
        payload["patches"] = [
            patch.model_dump(by_alias=True) for patch in self.patches
        ]
        if self.output is not None:
            payload["output"] = self.output
        return payload


class BytePreservingPatchOutput(_BaseModel):
    ok: bool
    applied: List[Dict[str, Any]]
    skipped: List[Dict[str, Any]]
    changedParts: List[str]
    byteIdentical: bool
    zipMethod: str
    outputPath: str
    verificationReport: Optional[Dict[str, Any]] = None
    openSafety: Optional[Dict[str, Any]] = None
    visualComplete: Optional[Dict[str, Any]] = None


class ApplyTableOpsInput(DocumentLocatorInput):
    ops: List[Dict[str, Any]]
    output: Optional[str] = None
    render_check: str = Field("off", alias="renderCheck")
    dry_run: bool = Field(False, alias="dryRun")

    def to_hwpx_payload(self, *, require_path: bool = True) -> Dict[str, Any]:
        payload = super().to_hwpx_payload(require_path=require_path)
        payload["ops"] = [dict(op) for op in self.ops]
        if self.output is not None:
            payload["output"] = self.output
        payload["render_check"] = self.render_check
        payload["dry_run"] = self.dry_run
        return payload


class ApplyTableOpsOutput(_BaseModel):
    ok: bool
    applied: List[Dict[str, Any]]
    skipped: List[Dict[str, Any]]
    changedParts: List[str]
    byteIdentical: bool
    zipMethod: str
    outputPath: Optional[str] = None  # dry-run이면 None(아무것도 안 씀)
    dryRun: Optional[bool] = None
    transcript: Optional[List[Dict[str, Any]]] = None
    verificationReport: Optional[Dict[str, Any]] = None
    openSafety: Optional[Dict[str, Any]] = None
    renderVerdict: Optional[Dict[str, Any]] = None


class VerifyFormFillInput(DocumentLocatorInput):
    before_path: str = Field(alias="beforePath")
    require: bool = False

    def to_hwpx_payload(self, *, require_path: bool = True) -> Dict[str, Any]:
        payload = super().to_hwpx_payload(require_path=require_path)
        payload["before_path"] = self.before_path
        payload["require"] = self.require
        return payload


class VerifyFormFillOutput(_BaseModel):
    renderChecked: bool
    ok: bool
    overflowDetected: Optional[bool] = None
    overlapDetected: Optional[bool] = None
    pageCountChanged: Optional[Any] = None
    warnings: List[str]
    errors: List[str]


class ScoreFormFillInput(DocumentLocatorInput):
    gold_path: str = Field(alias="goldPath")
    blank_path: str = Field(alias="blankPath")
    run_render: bool = Field(True, alias="runRender")
    expected_pages: Optional[int] = Field(None, alias="expectedPages")

    def to_hwpx_payload(self, *, require_path: bool = True) -> Dict[str, Any]:
        payload = super().to_hwpx_payload(require_path=require_path)
        payload["gold_path"] = self.gold_path
        payload["blank_path"] = self.blank_path
        payload["run_render"] = self.run_render
        payload["expected_pages"] = self.expected_pages
        return payload


class ScoreFormFillOutput(_BaseModel):
    total: float
    renderChecked: bool = Field(alias="render_checked")
    axes: List[Dict[str, Any]]
    lowestAxis: str = Field(alias="lowest_axis")

    model_config = ConfigDict(populate_by_name=True)


class ApplyBodyOpsInput(DocumentLocatorInput):
    ops: List[Dict[str, Any]]
    output: Optional[str] = None
    dry_run: bool = Field(False, alias="dryRun")

    def to_hwpx_payload(self, *, require_path: bool = True) -> Dict[str, Any]:
        payload = super().to_hwpx_payload(require_path=require_path)
        payload["ops"] = [dict(op) for op in self.ops]
        if self.output is not None:
            payload["output"] = self.output
        payload["dry_run"] = self.dry_run
        return payload


class ApplyBodyOpsOutput(_BaseModel):
    ok: bool
    skipped: List[Dict[str, Any]]
    transcript: List[Dict[str, Any]]
    changedParts: List[str]
    byteIdentical: bool
    openSafety: Dict[str, Any]
    outputPath: Optional[str] = None
    dryRun: Optional[bool] = None


class InspectFillResidueInput(DocumentLocatorInput):
    blank_path: Optional[str] = Field(None, alias="blankPath")

    def to_hwpx_payload(self, *, require_path: bool = True) -> Dict[str, Any]:
        payload = super().to_hwpx_payload(require_path=require_path)
        if self.blank_path is not None:
            payload["blank_path"] = self.blank_path
        return payload


class InspectFillResidueOutput(_BaseModel):
    ok: bool
    errors: List[Dict[str, Any]]
    needsReview: List[Dict[str, Any]]
    stats: Dict[str, int]


class ScanFormGuidanceInput(DocumentLocatorInput):
    max_items: int = Field(60, alias="maxItems")

    def to_hwpx_payload(self, *, require_path: bool = True) -> Dict[str, Any]:
        payload = super().to_hwpx_payload(require_path=require_path)
        payload["max_items"] = self.max_items
        return payload


class ScanFormGuidanceOutput(_BaseModel):
    legend: List[Dict[str, Any]]
    colorInventory: Dict[str, Any]
    deleteCandidates: List[Dict[str, Any]]
    deleteCandidatesTotal: int
    modifyCandidatesByTable: Dict[str, Any]
    emptyCellCandidates: List[Dict[str, Any]]
    emptyCellTotal: int
    placeholderCandidates: List[Dict[str, Any]]
    conditionalChoices: List[Dict[str, Any]]
    questions: List[str]
    stats: Dict[str, int]
    limitations: List[str]
    markdownReport: str


class ApplyEvalplanFillInput(DocumentLocatorInput):
    review_md: str = Field(alias="reviewMd")
    output: Optional[str] = None
    render_check: str = Field("off", alias="renderCheck")
    score_gold_path: Optional[str] = Field(None, alias="scoreGoldPath")
    expected_pages: Optional[int] = Field(None, alias="expectedPages")

    def to_hwpx_payload(self, *, require_path: bool = True) -> Dict[str, Any]:
        payload = super().to_hwpx_payload(require_path=require_path)
        payload["review_md"] = self.review_md
        if self.output is not None:
            payload["output"] = self.output
        payload["render_check"] = self.render_check
        payload["score_gold_path"] = self.score_gold_path
        payload["expected_pages"] = self.expected_pages
        return payload


class ApplyEvalplanFillOutput(_BaseModel):
    ok: bool
    outputPath: str
    byteIdentical: bool
    transcript: List[str]
    expectedSkeleton: Optional[Dict[str, Any]] = None
    contentReport: Dict[str, Any]
    rubricNeedsReview: int
    needsReviewNotes: List[str]
    changedParts: Optional[List[str]] = None
    skipped: Optional[List[Any]] = None
    openSafety: Optional[Dict[str, Any]] = None
    verificationReport: Optional[Dict[str, Any]] = None
    renderVerdict: Optional[Dict[str, Any]] = None
    scorecard: Optional[Dict[str, Any]] = None


class ReplaceTableRegionInput(DocumentLocatorInput):
    table_index: int = Field(alias="tableIndex")
    start_row: int = Field(alias="startRow")
    start_col: int = Field(alias="startCol")
    values: Sequence[Sequence[str]]
    logical: Optional[bool] = Field(None, alias="logical")
    split_merged: Optional[bool] = Field(None, alias="splitMerged")
    dry_run: bool = Field(False, alias="dryRun")
    auto_fit: bool = Field(False, alias="autoFit")


class ReplaceTableRegionOutput(_BaseModel):
    updatedCells: int


class SplitTableCellInput(DocumentLocatorInput):
    table_index: int = Field(alias="tableIndex")
    row: int
    col: int


class SplitTableCellOutput(_BaseModel):
    startRow: int
    startCol: int
    rowSpan: int
    colSpan: int


class AddShapeInput(DocumentLocatorInput):
    shape_type: str = Field("RECTANGLE", alias="shapeType")
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    dry_run: bool = Field(True, alias="dryRun")


class ObjectIdOutput(_BaseModel):
    objectId: Optional[str]


class AddControlInput(DocumentLocatorInput):
    control_type: str = Field("TEXTBOX", alias="controlType")
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    dry_run: bool = Field(True, alias="dryRun")


class InsertPictureInput(DocumentLocatorInput):
    image_base64: str = Field(alias="imageBase64")
    image_format: str = Field("png", alias="imageFormat")
    width: Optional[int] = None
    height: Optional[int] = None
    width_mm: Optional[float] = Field(None, alias="widthMm")
    height_mm: Optional[float] = Field(None, alias="heightMm")
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    align: Optional[str] = None
    output: Optional[str] = None
    dry_run: bool = Field(False, alias="dryRun")


class ReplacePictureInput(DocumentLocatorInput):
    image_base64: str = Field(alias="imageBase64")
    image_format: str = Field("png", alias="imageFormat")
    picture_index: int = Field(0, alias="pictureIndex")
    binary_item_id_ref: Optional[str] = Field(None, alias="binaryItemIDRef")
    remove_orphaned: bool = Field(True, alias="removeOrphaned")
    output: Optional[str] = None
    dry_run: bool = Field(False, alias="dryRun")


class PictureEditOutput(_BaseModel):
    ok: bool
    dryRun: bool = False
    filename: Optional[str] = None
    outputPath: Optional[str] = None
    picture: Optional[Dict[str, Any]] = None
    replacement: Optional[Dict[str, Any]] = None
    pictureReferences: Optional[List[Dict[str, Any]]] = None
    idIntegrity: Optional[Dict[str, Any]] = None
    wouldSave: Optional[bool] = None
    verificationReport: Optional[Dict[str, Any]] = None
    openSafety: Optional[Dict[str, Any]] = None
    visualComplete: Optional[Dict[str, Any]] = None
    semanticDiff: Optional[Dict[str, Any]] = None
    backup: Optional[Dict[str, Any]] = None


class AddMemoInput(DocumentLocatorInput):
    text: str
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    author: Optional[str] = None
    timestamp: Optional[str] = None


class AddMemoOutput(_BaseModel):
    memoId: Optional[str]


class AttachMemoFieldInput(DocumentLocatorInput):
    paragraph_index: int = Field(alias="paragraphIndex")
    memo_id: str = Field(alias="memoId")


class AttachMemoFieldOutput(_BaseModel):
    fieldId: str


class AddMemoWithAnchorInput(DocumentLocatorInput):
    text: str
    section_index: Optional[int] = Field(None, alias="sectionIndex")
    memo_shape_id_ref: Optional[str] = Field(None, alias="memoShapeIdRef")


class AddMemoWithAnchorOutput(_BaseModel):
    memoId: Optional[str]
    paragraphIndex: int
    fieldId: str


class RemoveMemoInput(DocumentLocatorInput):
    memo_id: str = Field(alias="memoId")
    dry_run: bool = Field(True, alias="dryRun")


class RemoveMemoOutput(_BaseModel):
    removed: bool


class EnsureRunStyleInput(DocumentLocatorInput):
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


class ApplyStyleToTextInput(DocumentLocatorInput):
    spans: Sequence[TextSpanModel]
    char_pr_id_ref: str = Field(alias="charPrIDRef")
    dry_run: bool = Field(True, alias="dryRun")


class ApplyStyleToTextOutput(_BaseModel):
    styledSpans: int


class ApplyStyleInput(DocumentLocatorInput):
    paragraph_indexes: Sequence[int] = Field(alias="paragraphIndexes")
    char_pr_id_ref: str = Field(alias="charPrIDRef")
    dry_run: bool = Field(True, alias="dryRun")


class ApplyStyleOutput(_BaseModel):
    updated: int




class OpenDocumentHandleOutput(_BaseModel):
    handle: Dict[str, Any]


class ListOpenDocumentsOutput(_BaseModel):
    documents: List[Dict[str, Any]]
    sessionPolicy: Dict[str, Any]


class CloseDocumentHandleInput(_BaseModel):
    handle_id: str = Field(alias="handleId")


class CloseDocumentHandleOutput(_BaseModel):
    closed: bool


class CopyTableBetweenDocumentsInput(_BaseModel):
    source_document: DocumentLocator = Field(alias="sourceDocument")
    source_table_index: int = Field(alias="sourceTableIndex")
    target_document: DocumentLocator = Field(alias="targetDocument")
    target_section_index: Optional[int] = Field(None, alias="targetSectionIndex")
    auto_fit: bool = Field(False, alias="autoFit")

    @model_validator(mode="before")
    @classmethod
    def _inflate_documents(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        source_data = dict(data)
        if "sourceDocument" not in source_data:
            source_data = normalize_locator_payload(source_data, field_name="sourceDocument")

        target_payload = source_data.get("targetDocument")
        if target_payload is None:
            target_payload = {}
            for key in ("targetPath", "targetUri", "targetHandleId", "targetBackend"):
                if key in source_data:
                    target_payload[key] = source_data.pop(key)
            if target_payload:
                normalized_target: Dict[str, Any] = {}
                if "targetPath" in target_payload:
                    normalized_target["path"] = target_payload["targetPath"]
                    normalized_target["type"] = "path"
                if "targetUri" in target_payload:
                    normalized_target["uri"] = target_payload["targetUri"]
                    normalized_target["type"] = "uri"
                if "targetHandleId" in target_payload:
                    normalized_target["handleId"] = target_payload["targetHandleId"]
                    normalized_target["type"] = "handle"
                if "targetBackend" in target_payload:
                    normalized_target["backend"] = target_payload["targetBackend"]
                source_data["targetDocument"] = normalized_target
        return source_data

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            if "sourceDocument" in properties:
                properties["sourceDocument"] = document_locator_schema()
            if "targetDocument" in properties:
                properties["targetDocument"] = document_locator_schema()
        return schema

    def to_hwpx_payload(self, ops: HwpxOps) -> Dict[str, Any]:
        source_locator = DocumentLocatorInput(document=self.source_document).to_hwpx_payload(require_path=False)
        source_path = ops.resolve_document_path(
            path=source_locator.get("path"),
            handle_id=source_locator.get("handleId"),
        )
        target_locator = DocumentLocatorInput(document=self.target_document).to_hwpx_payload(require_path=False)
        target_path = ops.resolve_document_path(
            path=target_locator.get("path"),
            handle_id=target_locator.get("handleId"),
        )
        return {
            "source_path": source_path,
            "source_table_index": self.source_table_index,
            "target_path": target_path,
            "target_section_index": self.target_section_index,
            "auto_fit": self.auto_fit,
        }


class CopyTableBetweenDocumentsOutput(_BaseModel):
    targetTableIndex: int
    copiedCells: int
    rowCount: int
    columnCount: int

class SaveOutput(_BaseModel):
    ok: bool
    verificationReport: Optional[Dict[str, Any]] = None


class SaveAsInput(DocumentLocatorInput):
    out: str


class OutPathOutput(_BaseModel):
    outPath: str
    verificationReport: Optional[Dict[str, Any]] = None


class ExportOutput(_BaseModel):
    content: str
    format: str


class RenderPreviewInput(DocumentLocatorInput):
    output_dir: Optional[str] = Field(None, alias="outputDir")
    mode: Literal["pages", "long"] = "pages"
    screenshot: Literal["auto", "require", "off"] = "auto"
    max_pages: Optional[int] = Field(None, alias="maxPages")


class RenderPreviewOutput(_BaseModel):
    status: str
    schemaVersion: str
    generatedAt: str
    sourcePath: str
    outputDir: str
    htmlPath: str
    manifestPath: str
    visualReviewPath: str
    mode: str
    pageCount: int
    pages: List[Dict[str, Any]]
    screenshots: List[Dict[str, Any]]
    screenshotEngine: Dict[str, Any]
    warnings: List[str]
    suggestion: Optional[str] = None


class ConvertHwpToHwpxInput(_BaseModel):
    source: str
    output: Optional[str] = None


class ConvertHwpToHwpxOutput(_BaseModel):
    success: bool
    outputPath: str
    paragraphsConverted: int
    tablesConverted: int
    skippedElements: List[str]
    warnings: List[str]
    verification: Optional[Dict[str, Any]] = None
    openSafety: Optional[Dict[str, Any]] = None


class FillTemplateInput(_BaseModel):
    source: str
    output: str
    replacements: Dict[str, str]
    preserve_style: bool = Field(True, alias="preserveStyle")
    split_newlines: bool = Field(True, alias="splitNewlines")


class FillTemplateOutput(_BaseModel):
    outPath: str
    replacedCount: int
    verificationReport: Optional[Dict[str, Any]] = None


class MakeBlankInput(_BaseModel):
    out: str


class MasterHistoryVersionOutput(_BaseModel):
    masterPages: List[Any]
    histories: List[Any]
    versions: Optional[Dict[str, Any]]


class ObjectFindByTagInput(DocumentLocatorInput):
    tag_name: str = Field(alias="tagName")
    max_results: int = Field(200, alias="maxResults")


class ObjectFindByAttrInput(DocumentLocatorInput):
    element_type: str = Field(alias="elementType")
    attr: str
    value: str
    max_results: int = Field(200, alias="maxResults")


class ObjectsOutput(_BaseModel):
    objects: List[Dict[str, Any]]


class ValidateStructureInput(DocumentLocatorInput):
    level: str = "basic"


class ValidateStructureOutput(_BaseModel):
    ok: bool
    issues: List[Dict[str, Any]]


class LintRules(_BaseModel):
    max_line_len: Optional[int] = Field(None, alias="maxLineLen")
    forbid_patterns: Optional[Sequence[str]] = Field(None, alias="forbidPatterns")


class LintInput(DocumentLocatorInput):
    rules: LintRules = Field(default_factory=LintRules)


class LintOutput(_BaseModel):
    warnings: List[Dict[str, Any]]


class PackageXmlInput(DocumentLocatorInput):
    part_name: str = Field(alias="partName")


class PackageXmlOutput(_BaseModel):
    xmlString: str


class GetToolGuideInput(_BaseModel):
    workflow: Optional[str] = Field(
        None,
        description="Workflow name (read, repair, edit, template, export, table, style, form). If omitted, returns the full guide.",
    )


class GetToolGuideOutput(_BaseModel):
    guide: str


_TOOL_GUIDE: Dict[str, str] = {
    "read": (
        "## 문서 읽기 워크플로\n"
        "1. `open_info` — 문서 메타(문단 수, 표 수, 섹션) 파악\n"
        "2. `read_text` — 페이지네이션된 텍스트 읽기\n"
        "3. `read_paragraphs` — 특정 문단 인덱스로 상세 읽기\n"
        "4. `text_extract_report` — 전체 텍스트 한번에 추출\n"
        "5. `export_text` / `export_html` / `export_markdown` — 형식별 내보내기\n"
    ),
    "repair": (
        "## HWPX repair/recover 워크플로\n"
        "1. 한컴에서 열리지 않거나 ZIP 오류가 의심되면 원본을 직접 덮어쓰지 말고 `repair_hwpx`로 새 output을 만든다.\n"
        "2. 일반 패키지 재정렬/CRC self-check는 `recover=false`를 사용한다.\n"
        "3. central directory 손상처럼 일반 ZIP open이 실패하면 `recover=true`로 Local File Header 복구를 시도한다.\n"
        "4. 반환값의 `crcOk == true`, `validatePackage.ok == true`, `openSafety.ok == true`, `reordered/recovered`를 evidence로 기록한다.\n"
        "5. 결과 파일은 가능하면 Hancom Office HWP 또는 viewer로 실제 열람 확인한다.\n"
    ),
    "edit": (
        "## 문서 편집 워크플로\n"
        "1. `open_info`로 문서 구조 확인\n"
        "2. `read_paragraphs`로 편집 대상 문단 확인\n"
        "3. `find`로 편집할 텍스트 위치 검색\n"
        "4. 문단/페이지/머리글/쪽번호/목록 서식은 `set_paragraph_format`, `set_page_setup`, `set_header_footer`, `set_page_number`, `set_list_format` 사용\n"
        "5. 여러 변경은 `apply_edits`와 `dryRun=true`로 먼저 semanticDiff를 확인\n"
        "6. 실제 저장은 `apply_edits` 또는 개별 편집 도구의 `dryRun=false`로 수행\n"
        "7. 저장 후 문제가 있으면 `undo_last_edit`로 직전 `.bak` 백업 복원\n"
        "⚠️ 항상 편집 전 `read_paragraphs`로 현재 상태를 확인하세요.\n"
    ),
    "template": (
        "## 템플릿 워크플로\n"
        "1. `analyze_template_structure`로 템플릿 구조/플레이스홀더 분석\n"
        "2. `fill_template`로 일괄 치환 (source → output 복사 후 치환)\n"
        "3. 결과 파일을 `read_text`로 검증\n"
    ),
    "form": (
        "## 양식 채움 워크플로\n"
        "1. 먼저 `list_form_fields`로 누름틀/FORM 필드가 있는지 확인한다.\n"
        "2. 필드가 있으면 `fill_form_field` 또는 `analyze_form_fill`의 form-field 매핑을 우선 사용한다.\n"
        "3. `analyze_form_fill`의 `confidenceGrade`가 `label-fuzzy` 또는 `position-guess`이면 적용 전 사용자 확인을 받는다.\n"
        "4. `formFields.available=false`이면 명시된 `fallback=table-label`에 따라 기존 표 라벨 경로를 사용한다.\n"
    ),
    "export": (
        "## 내보내기 워크플로\n"
        "- `export_text` — 순수 텍스트 (python-hwpx 2.4 네이티브)\n"
        "- `export_html` — HTML 변환\n"
        "- `export_markdown` — Markdown 변환\n"
        "- `text_extract_report` — 구조화된 텍스트 추출\n"
    ),
    "table": (
        "## 표 편집 워크플로\n"
        "1. `get_table_cell_map`으로 셀 구조(grid) 파악\n"
        "2. `read_table_cell`로 특정 셀 내용 읽기\n"
        "3. `write_table_cell` / `insert_table_row` / `delete_table_row` 등 편집\n"
        "4. `merge_table_cells` / `split_table_cell`로 병합/분할\n"
        "5. `set_cell_border`로 테두리 스타일 설정\n"
    ),
    "style": (
        "## 스타일 워크플로\n"
        "1. `list_styles_and_bullets`로 현재 스타일 목록 확인\n"
        "2. `set_paragraph_format`으로 정렬/줄간격/들여쓰기/문단 간격/개요 수준 적용\n"
        "3. `set_list_format`으로 불릿/번호 목록 적용\n"
        "4. `apply_style_to_paragraphs`로 문단 스타일 적용\n"
        "5. `apply_style_to_text_ranges`로 텍스트 범위 스타일 적용\n"
    ),
}


def _get_tool_guide(ops: HwpxOps, data: GetToolGuideInput) -> Dict[str, Any]:
    if data.workflow and data.workflow in _TOOL_GUIDE:
        return {"guide": _TOOL_GUIDE[data.workflow]}
    # Return full guide
    full = "# HWPX MCP 도구 사용 가이드\n\n"
    full += "\n".join(_TOOL_GUIDE.values())
    return {"guide": full}


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_model: type[_BaseModel]
    output_model: type[_BaseModel]
    func: Callable[[HwpxOps, _BaseModel], Dict[str, Any]]
    category: ToolCategory = "core"

    def to_tool(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            description=self.description,
            inputSchema=build_tool_schema(self.input_model),
            outputSchema=build_tool_schema(self.output_model),
        )

    def call(self, ops: HwpxOps, arguments: Dict[str, Any]) -> Dict[str, Any]:
        data = self.input_model.model_validate(arguments)
        raw = self.func(ops, data)
        return self.output_model.model_validate(raw).model_dump(
            by_alias=True, exclude_none=True
        )


def _simple(
    method_name: str,
    *,
    require_path: bool = True,
) -> Callable[[HwpxOps, _BaseModel], Dict[str, Any]]:
    def caller(ops: HwpxOps, data: _BaseModel) -> Dict[str, Any]:
        method = getattr(ops, method_name)
        to_payload = getattr(data, "to_hwpx_payload", None)
        if callable(to_payload):
            try:
                payload = to_payload(require_path=require_path)
            except TypeError:  # pragma: no cover - defensive guard
                payload = to_payload()
        else:
            payload = data.model_dump()

        if payload.get("path") is None and payload.get("handleId"):
            payload["path"] = ops.resolve_document_path(handle_id=payload["handleId"])
        payload.pop("handleId", None)
        return method(**payload)

    return caller




def _copy_table_between_documents(ops: HwpxOps, data: CopyTableBetweenDocumentsInput) -> Dict[str, Any]:
    payload = data.to_hwpx_payload(ops)
    return ops.copy_table_between_documents(**payload)


def _parse_toolset_env() -> set[str] | None:
    raw = os.getenv("HWPX_MCP_TOOLSET")
    if raw is None or not raw.strip():
        return None

    requested = {item.strip().lower() for item in raw.split(",") if item.strip()}
    unknown = sorted(requested - _TOOL_CATEGORY_SET)
    if unknown:
        LOGGER.warning(
            "Ignoring unknown tool categories from HWPX_MCP_TOOLSET: %s",
            ", ".join(unknown),
        )
    return requested & _TOOL_CATEGORY_SET


def build_tool_definitions() -> List[ToolDefinition]:
    tools = [
        ToolDefinition(
            name="open_document_handle",
            description="문서 로케이터를 등록하고 handleId를 반환합니다.",
            input_model=DocumentLocatorInput,
            output_model=OpenDocumentHandleOutput,
            func=_simple("open_document_handle"),
        ),
        ToolDefinition(
            name="list_open_documents",
            description="현재 세션에 등록된 문서 handle 목록을 반환합니다.",
            input_model=_BaseModel,
            output_model=ListOpenDocumentsOutput,
            func=_simple("list_open_documents", require_path=False),
        ),
        ToolDefinition(
            name="get_tool_guide",
            description="Return workflow guidance for HWPX MCP tools. Specify a workflow name (read/repair/edit/template/export/table/style/form) or omit for the full guide.",
            input_model=GetToolGuideInput,
            output_model=GetToolGuideOutput,
            func=_get_tool_guide,
        ),
        ToolDefinition(
            name="list_form_fields",
            description="List native HWPX click-here/FORM fields with name, prompt/instruction, and current value.",
            input_model=DocumentLocatorInput,
            output_model=ListFormFieldsOutput,
            func=_simple("list_form_fields"),
        ),
        ToolDefinition(
            name="fill_form_field",
            description="Fill one native HWPX click-here/FORM field while preserving formatting and returning open-safety evidence.",
            input_model=FillFormFieldInput,
            output_model=FillFormFieldOutput,
            func=_simple("fill_form_field"),
        ),
        ToolDefinition(
            name="close_document_handle",
            description="지정한 handleId를 세션 레지스트리에서 제거합니다.",
            input_model=CloseDocumentHandleInput,
            output_model=CloseDocumentHandleOutput,
            func=_simple("close_document_handle", require_path=False),
        ),
        ToolDefinition(
            name="open_info",
            description="Return metadata about an HWPX document.",
            input_model=DocumentLocatorInput,
            output_model=OpenInfoOutput,
            func=_simple("open_info"),
        ),
        ToolDefinition(
            name="list_sections",
            description="List sections within a document.",
            input_model=DocumentLocatorInput,
            output_model=SectionsOutput,
            func=_simple("list_sections"),
        ),
        ToolDefinition(
            name="list_headers",
            description="List header references used by the document.",
            input_model=DocumentLocatorInput,
            output_model=HeadersOutput,
            func=_simple("list_headers"),
        ),
        ToolDefinition(
            name="package_parts",
            description="List OPC package part names.",
            input_model=DocumentLocatorInput,
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
            name="repair_hwpx",
            description="Repair an HWPX archive into a new output path. Use recover=true to rebuild from ZIP Local File Headers when the central directory is damaged.",
            input_model=RepairHwpxInput,
            output_model=RepairHwpxOutput,
            func=_simple("repair_hwpx", require_path=False),
            category="pipeline",
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
            name="analyze_template_structure",
            description="Analyze template-like regions and placeholder candidates.",
            input_model=AnalyzeTemplateInput,
            output_model=AnalyzeTemplateOutput,
            func=_simple("analyze_template_structure"),
            category="pipeline",
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
            category="styles",
        ),
        ToolDefinition(
            name="replace_text_in_runs",
            description="Replace text within runs matching a style filter.",
            input_model=ReplaceRunsInput,
            output_model=ReplaceRunsOutput,
            func=_simple("replace_text_in_runs"),
            category="styles",
        ),
        ToolDefinition(
            name="apply_edits",
            description="Atomically apply an ordered list of edit operations. Use dryRun=true to preview without saving.",
            input_model=ApplyEditsInput,
            output_model=ApplyEditsOutput,
            func=_simple("apply_edits"),
        ),
        ToolDefinition(
            name="undo_last_edit",
            description="Restore the last .bak backup for a local HWPX document and swap the current file into .bak.",
            input_model=DocumentLocatorInput,
            output_model=UndoLastEditOutput,
            func=_simple("undo_last_edit"),
        ),
        ToolDefinition(
            name="add_paragraph",
            description="Append a new paragraph to the document.",
            input_model=AddParagraphInput,
            output_model=AddParagraphOutput,
            func=_simple("add_paragraph"),
        ),
        ToolDefinition(
            name="byte_preserving_patch",
            description="Patch paragraph text with section XML byte splices and skipped unsupported edits.",
            input_model=BytePreservingPatchInput,
            output_model=BytePreservingPatchOutput,
            func=_simple("byte_preserving_patch"),
            category="pipeline",
        ),
        ToolDefinition(
            name="apply_table_ops",
            description=(
                "Byte-preserving structural form-fill: apply a list of ops "
                "(fill_cell / delete_column / delete_row / delete_table / "
                "insert_row_by_clone / insert_block_by_clone / set_column_widths / "
                "autofit_columns / set_row_heights) to a form, PRESERVING the original table formatting "
                "and every untouched byte (never rebuild). ops: "
                "[{op, tableIndex?, tableAnchor?, row?, col?, cellAnchor?, cols?, "
                "ref_row?, ref_rows?, count?, text?, widths?, max_lines?}]. "
                "insert_block_by_clone clones a contiguous VERTICAL-MERGE block "
                "(ref_rows:[r0,r1] — e.g. a 성취기준 A~E unit whose leading cell is "
                "rowSpan=N) count times, formatting preserved (insert_row_by_clone "
                "stays for the flat rowSpan==1 case). delete_column now handles "
                "fully-merged tables with no uniform colSpan==1 row (widths derived "
                "from the merged grid; refuses with a reason if unreconcilable). "
                "ADDRESS BY ANCHOR to survive index shifts: tableAnchor = the text of "
                "the table's preceding heading/label; cellAnchor = {label, dir} of an "
                "adjacent cell — resolves to a UNIQUE target or is skipped with a "
                "reason (never guessed). set_column_widths sets explicit logical "
                "column widths; autofit_columns rebalances widths to content. A "
                "fill_cell with max_lines shrinks the cell font (to a floor) to fit. "
                "delete_table shifts later indices — sequence deletes in reverse "
                "order (or address by anchor). set_row_heights{heights:{row:HWPUNIT}} "
                "resizes rows explicitly (1pt=100; merged cells get the sum of covered "
                "rows; refuses when a current row height is underivable) — the human "
                "editor's 'redistribute row heights to fit the page' move. Structure edits are grid-validated "
                "(fail-closed). Set renderCheck='required' to gate on a real Hancom "
                "render, 'auto' to attach a render verdict when Hancom is reachable. "
                "dryRun=true: run the IDENTICAL pipeline but write NOTHING — returns "
                "transcript (per-op resolution + dims before→after) and applied "
                "old→new texts as approval evidence. USE THIS FIRST in the user "
                "consult loop; apply for real only after the user approves the plan."
            ),
            input_model=ApplyTableOpsInput,
            output_model=ApplyTableOpsOutput,
            func=_simple("apply_table_ops"),
            category="pipeline",
        ),
        ToolDefinition(
            name="verify_form_fill",
            description=(
                "Render before/after in REAL Hancom and judge the fill: "
                "renderChecked + overflowDetected + overlapDetected (글자겹침) + "
                "pageCountChanged. Honest degrade (renderChecked=false) when no "
                "Hancom is reachable; set require=true to fail closed. Never treat "
                "open-safety or an HTML preview as Hancom acceptance."
            ),
            input_model=VerifyFormFillInput,
            output_model=VerifyFormFillOutput,
            func=_simple("verify_form_fill"),
            category="pipeline",
        ),
        ToolDefinition(
            name="score_form_fill",
            description=(
                "Score a filled 평가계획 form against a gold reference on 5 axes -> "
                "weighted 0-100 total + per-axis GAP REPORT (the fitness function of "
                "the form-fill quality loop). path = produced fill, goldPath = an "
                "accepted submission of the same form family, blankPath = the empty "
                "province form. Axes: A render_cleanliness(30, REAL-Hancom render: "
                "text crossing a cell border = overflow; no oracle -> unverified, "
                "never a silent pass) · B format_fidelity(25, byte preservation vs "
                "blank — regeneration scores ~0; gold itself scores LOW here by "
                "design) · C structure_conformance(20, vs gold skeleton: delete/keep "
                "policy + 성취기준/루브릭 block counts) · D content_completeness(15) · "
                "E compliance(10, gold-calibrated lint; manual rules -> needs_review). "
                "Each axis reports status (measured/unverified/needs_review) and "
                "findings with locations; lowestAxis names the weakest axis to fix "
                "next. Set runRender=false for a fast structural-only pass (A -> "
                "unverified). expectedPages penalises page-count overflow when set."
            ),
            input_model=ScoreFormFillInput,
            output_model=ScoreFormFillOutput,
            func=_simple("score_form_fill"),
            category="pipeline",
        ),
        ToolDefinition(
            name="apply_body_ops",
            description=(
                "Byte-preserving BODY ops for paragraphs OUTSIDE tables (the body-"
                "text counterpart of apply_table_ops). ops: replace_text{find, "
                "replace, count=1 — matches ONLY inside <hp:t> text content; refuses "
                "on count mismatch or run-spanning strings} · delete_paragraph"
                "{index — refuses paragraphs that wrap a table} · "
                "insert_paragraph_by_clone{ref_index, count, texts? — clones the "
                "reference paragraph verbatim (formatting inherited from the "
                "NEIGHBOUR, ids rewritten, layout cache stripped) then fills texts} "
                "· reorder_paragraphs{start, end, order — contiguous permutation}. "
                "Indices are section-direct paragraph order AT OP TIME. dryRun=true "
                "runs the identical pipeline, writes NOTHING, and returns the "
                "transcript as approval evidence — use in the consult loop first. "
                "Proven by byte-identical replay of the KACE citation-renumbering "
                "conversion (17 in-text replacements + 18-entry reorder)."
            ),
            input_model=ApplyBodyOpsInput,
            output_model=ApplyBodyOpsOutput,
            func=_simple("apply_body_ops"),
            category="pipeline",
        ),
        ToolDefinition(
            name="inspect_fill_residue",
            description=(
                "채움본 잔존물 zero-체크(비변형) — 제출 전 기계 게이트. blankPath를 "
                "주면 blank의 색 범례로 신호를 만든다: 삭제색 텍스트 잔존(빨간 지시문 "
                "류)·미수정 샘플(수정색 텍스트가 blank와 동일 = 코드 없는 prose 샘플 "
                "미교체 — 타과목 샘플을 잡는 일반 신호) = ERROR. placeholder "
                "◯◯◯/□□□ = ERROR, 리터럴 ** = 각주 표식과 중의적이라 needsReview, "
                "목록 마커만 있는 문단 = needsReview(의도된 빈 자리일 수 있음). "
                "ok=true는 필요조건일 뿐 — 제출 확언은 렌더 PDF를 사람이 전 페이지 "
                "확인한 뒤에만 한다. fill 실행 후·검수 요청 전에 반드시 돌려라."
            ),
            input_model=InspectFillResidueInput,
            output_model=InspectFillResidueOutput,
            func=_simple("inspect_fill_residue"),
            category="pipeline",
        ),
        ToolDefinition(
            name="scan_form_guidance",
            description=(
                "임의 양식 정찰(비변형) — universal form-fill Stage 1. 표 셀 내부·캡션 "
                "포함 전체 run을 순회해 양식 스스로 선언한 색 범례(검정 유지/파랑 수정/"
                "빨강 삭제 류)를 파싱하고, table_patch 좌표로 후보를 보고한다: 지울 것"
                "(삭제색+안내 키워드), 수정 대상(표별 집계), placeholder(◯◯◯/**/□□□), "
                "조건부 선택 블록('2개 중 하나만 남기고 삭제'), 빈 셀(인접 라벨+charPr "
                "서식 컨텍스트), 질문 목록(확신 없는 곳). 처음 보는 양식은 이 도구부터 "
                "실행해 사용자와 fill-plan을 상의하라. 후보는 제안일 뿐 — 삭제/구조 op는 "
                "사용자 승인 후 apply_table_ops로. markdownReport가 사람이 읽는 리포트."
            ),
            input_model=ScanFormGuidanceInput,
            output_model=ScanFormGuidanceOutput,
            func=_simple("scan_form_guidance"),
            category="pipeline",
        ),
        ToolDefinition(
            name="apply_evalplan_fill",
            description=(
                "Whole-form 평가계획(교수학습운영 및 평가계획) fill in ONE call: "
                "{blank province form + structured review markdown} -> byte-preserving "
                "gold-quality 채움본. path = blank form; reviewMd = the review markdown "
                "(Ⅰ 운영계획 + [1]~[11]: 목적/방향/방침/성취기준·성취수준/성취율/반영비율/"
                "수행평가 세부기준 rubrics/정의적/결시자/유의사항/결과분석). Runs the "
                "structure-driven recipe: delete the red/optional tables + 정기시험 column "
                "+ surplus example tables, then fill 운영계획 21주 · 성취기준(상/중/하 or "
                "A~E) · 성취수준 · 성취율(3단계 or 5단계) · 반영비율(영역·비율·성취기준·"
                "평가요소) · rubrics incl. the 채점기준 배점 ladder · 가/나/다 prose "
                "sections — PRESERVING the original table formatting byte-for-byte "
                "(NEVER regenerates). Auto-handles BOTH the 2015-개정 and 2022-개정 form "
                "families. Returns outputPath + per-region contentReport + "
                "rubricNeedsReview/needsReviewNotes (honest-defer count — a region with "
                "no byte-preserving map to the review is reported, never silently faked). "
                "renderCheck='required' gates on a real Hancom render; scoreGoldPath (an "
                "accepted form of the same family) also returns the 5-axis scorecard. Do "
                "NOT rebuild the form by hand — this is the byte-preserving path."
            ),
            input_model=ApplyEvalplanFillInput,
            output_model=ApplyEvalplanFillOutput,
            func=_simple("apply_evalplan_fill"),
            category="pipeline",
        ),
        ToolDefinition(
            name="insert_paragraphs_bulk",
            description="Insert multiple paragraphs efficiently.",
            input_model=InsertParagraphsInput,
            output_model=InsertParagraphsOutput,
            func=_simple("insert_paragraphs_bulk"),
        ),
        ToolDefinition(
            name="set_paragraph_format",
            description="Apply paragraph alignment, line spacing %, indents in mm, paragraph spacing in pt, and outline level to existing paragraphs.",
            input_model=SetParagraphFormatInput,
            output_model=FormattingEditOutput,
            func=_simple("set_paragraph_format"),
            category="styles",
        ),
        ToolDefinition(
            name="set_page_setup",
            description="Set paper size, orientation, margins in mm, and optional columns for an existing document section.",
            input_model=SetPageSetupInput,
            output_model=FormattingEditOutput,
            func=_simple("set_page_setup"),
        ),
        ToolDefinition(
            name="set_header_footer",
            description="Add or modify header/footer text or rich content on an existing document section.",
            input_model=SetHeaderFooterInput,
            output_model=FormattingEditOutput,
            func=_simple("set_header_footer"),
        ),
        ToolDefinition(
            name="set_page_number",
            description="Add or modify an automatic page-number field in a header or footer.",
            input_model=SetPageNumberInput,
            output_model=FormattingEditOutput,
            func=_simple("set_page_number"),
        ),
        ToolDefinition(
            name="set_list_format",
            description="Apply bullet or numbered-list formatting to existing paragraphs.",
            input_model=SetListFormatInput,
            output_model=FormattingEditOutput,
            func=_simple("set_list_format"),
            category="styles",
        ),
        ToolDefinition(
            name="add_table",
            description="Add a table to the document.",
            input_model=AddTableInput,
            output_model=AddTableOutput,
            func=_simple("add_table"),
            category="tables",
        ),
        ToolDefinition(
            name="set_table_border_fill",
            description="Update a table's border fill and anchor cells.",
            input_model=SetTableBorderFillInput,
            output_model=SetTableBorderFillOutput,
            func=_simple("set_table_border_fill"),
            category="tables",
        ),
        ToolDefinition(
            name="get_table_cell_map",
            description="Return the logical grid coverage for a table, including merged spans.",
            input_model=GetTableCellMapInput,
            output_model=TableCellMapOutput,
            func=_simple("get_table_cell_map"),
            category="tables",
        ),
        ToolDefinition(
            name="set_table_cell_text",
            description="Update the text of a table cell.",
            input_model=SetTableCellInput,
            output_model=SetTableCellOutput,
            func=_simple("set_table_cell_text"),
            category="tables",
        ),
        ToolDefinition(
            name="replace_table_region",
            description="Replace a region of table cells.",
            input_model=ReplaceTableRegionInput,
            output_model=ReplaceTableRegionOutput,
            func=_simple("replace_table_region"),
            category="tables",
        ),
        ToolDefinition(
            name="split_table_cell",
            description="Split a merged table cell back into individual cells and report the original span.",
            input_model=SplitTableCellInput,
            output_model=SplitTableCellOutput,
            func=_simple("split_table_cell"),
            category="tables",
        ),
        ToolDefinition(
            name="copy_table_between_documents",
            description="원본 문서의 표를 대상 문서로 복사합니다.",
            input_model=CopyTableBetweenDocumentsInput,
            output_model=CopyTableBetweenDocumentsOutput,
            func=_copy_table_between_documents,
            category="tables",
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
            name="insert_picture",
            description="Insert a body picture and update binaryItemIDRef, content.hpf manifest, and BinData consistently.",
            input_model=InsertPictureInput,
            output_model=PictureEditOutput,
            func=_simple("insert_picture"),
        ),
        ToolDefinition(
            name="replace_picture",
            description="Replace a body picture asset while preserving the existing hp:pic geometry.",
            input_model=ReplacePictureInput,
            output_model=PictureEditOutput,
            func=_simple("replace_picture"),
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
            category="styles",
        ),
        ToolDefinition(
            name="list_styles_and_bullets",
            description="List style and bullet definitions.",
            input_model=DocumentLocatorInput,
            output_model=StylesAndBulletsOutput,
            func=_simple("list_styles_and_bullets"),
            category="styles",
        ),
        ToolDefinition(
            name="apply_style_to_text_ranges",
            description="Apply a charPr style to specific text spans.",
            input_model=ApplyStyleToTextInput,
            output_model=ApplyStyleToTextOutput,
            func=_simple("apply_style_to_text_ranges"),
            category="styles",
        ),
        ToolDefinition(
            name="apply_style_to_paragraphs",
            description="Apply a charPr style to paragraphs and runs.",
            input_model=ApplyStyleInput,
            output_model=ApplyStyleOutput,
            func=_simple("apply_style_to_paragraphs"),
            category="styles",
        ),
        ToolDefinition(
            name="save",
            description="Persist in-memory changes to disk.",
            input_model=DocumentLocatorInput,
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
            name="export_text",
            description="Export document content as plain text.",
            input_model=DocumentLocatorInput,
            output_model=ExportOutput,
            func=_simple("export_text"),
        ),
        ToolDefinition(
            name="export_html",
            description="Export document content as HTML.",
            input_model=DocumentLocatorInput,
            output_model=ExportOutput,
            func=_simple("export_html"),
        ),
        ToolDefinition(
            name="render_preview",
            description="Generate layout-aware HTML and headless browser PNG preview artifacts.",
            input_model=RenderPreviewInput,
            output_model=RenderPreviewOutput,
            func=_simple("render_preview"),
        ),
        ToolDefinition(
            name="export_markdown",
            description="Export document content as Markdown.",
            input_model=DocumentLocatorInput,
            output_model=ExportOutput,
            func=_simple("export_markdown"),
        ),
        ToolDefinition(
            name="fill_template",
            description="Copy a template and apply multiple text replacements in one call.",
            input_model=FillTemplateInput,
            output_model=FillTemplateOutput,
            func=_simple("fill_template", require_path=False),
        ),
        ToolDefinition(
            name="make_blank",
            description="Create a new blank HWPX file.",
            input_model=MakeBlankInput,
            output_model=OutPathOutput,
            func=_simple("make_blank"),
        ),
        ToolDefinition(
            name="convert_hwp_to_hwpx",
            description="Convert a .hwp binary document into .hwpx.",
            input_model=ConvertHwpToHwpxInput,
            output_model=ConvertHwpToHwpxOutput,
            func=_simple("convert_hwp_to_hwpx", require_path=False),
        ),
        ToolDefinition(
            name="list_master_pages_histories_versions",
            description="List master pages, histories and version info.",
            input_model=DocumentLocatorInput,
            output_model=MasterHistoryVersionOutput,
            func=_simple("list_master_pages_histories_versions"),
            category="debug",
        ),
        ToolDefinition(
            name="object_find_by_tag",
            description="Find objects by tag name.",
            input_model=ObjectFindByTagInput,
            output_model=ObjectsOutput,
            func=_simple("object_find_by_tag"),
            category="debug",
        ),
        ToolDefinition(
            name="object_find_by_attr",
            description="Find objects by attribute value.",
            input_model=ObjectFindByAttrInput,
            output_model=ObjectsOutput,
            func=_simple("object_find_by_attr"),
            category="debug",
        ),
        ToolDefinition(
            name="validate_structure",
            description="Validate document structure using schema checks.",
            input_model=ValidateStructureInput,
            output_model=ValidateStructureOutput,
            func=_simple("validate_structure"),
            category="debug",
        ),
        ToolDefinition(
            name="lint_text_conventions",
            description="Run lightweight lint checks against paragraphs.",
            input_model=LintInput,
            output_model=LintOutput,
            func=lambda ops, data: ops.lint_text_conventions(
                data.to_hwpx_payload()["path"],
                **(data.rules.model_dump()),
            ),
            category="debug",
        ),
        ToolDefinition(
            name="package_get_xml",
            description="Read an OPC part as XML string.",
            input_model=PackageXmlInput,
            output_model=PackageXmlOutput,
            func=_simple("package_get_xml"),
            category="debug",
        ),
    ]
    if _hardening_enabled():
        tools.extend([
            ToolDefinition(
                name="hwpx.plan_edit",
                description="Plan hardened edits for preview/apply.",
                input_model=PlanEditInput,
                output_model=ServerResponse,
                func=_simple("plan_edit", require_path=False),
                category="pipeline",
            ),
            ToolDefinition(
                name="hwpx.preview_edit",
                description="Preview a hardened edit plan.",
                input_model=PreviewEditInput,
                output_model=ServerResponse,
                func=_simple("preview_edit"),
                category="pipeline",
            ),
            ToolDefinition(
                name="hwpx.apply_edit",
                description="Apply a hardened edit plan (preview required).",
                input_model=ApplyEditInput,
                output_model=ServerResponse,
                func=_simple("apply_edit"),
                category="pipeline",
            ),
            ToolDefinition(
                name="hwpx.search",
                description="Search document content using hardened handles.",
                input_model=SearchInput,
                output_model=SearchOutput,
                func=_simple("search", require_path=False),
                category="pipeline",
            ),
            ToolDefinition(
                name="hwpx.get_context",
                description="Return paragraph context around a hardened target.",
                input_model=GetContextInput,
                output_model=ContextOutput,
                func=_simple("get_context", require_path=False),
                category="pipeline",
            ),
        ])
    selected_categories = _parse_toolset_env()
    if selected_categories is None:
        return tools
    return [tool for tool in tools if tool.category in selected_categories]
