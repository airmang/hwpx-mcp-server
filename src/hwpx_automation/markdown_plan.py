# SPDX-License-Identifier: Apache-2.0
"""Markdown-to-document-plan bridge."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class MarkdownPlanResult:
    plan: dict[str, Any]
    warnings: list[str]


def markdown_to_document_plan(
    markdown: str,
    *,
    title: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    style_preset: str = "standard_korean_business",
) -> MarkdownPlanResult:
    """Convert a conservative Markdown subset into ``hwpx.document_plan.v1``."""

    warnings: list[str] = []
    blocks: list[dict[str, Any]] = []
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    resolved_title = (title or "").strip()
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        if table := _parse_table(lines, index):
            table_block, next_index = table
            blocks.append(table_block)
            index = next_index
            continue

        heading = _parse_heading(stripped)
        if heading is not None:
            level, text = heading
            if not resolved_title and level == 1 and not blocks:
                resolved_title = text
            else:
                blocks.append({"type": "heading", "level": min(level, 3), "text": text})
                if level > 3:
                    warnings.append(f"Heading level {level} was clamped to document_plan level 3.")
            index += 1
            continue

        if _is_list_item(stripped):
            items, next_index, ordered = _collect_list(lines, index)
            blocks.append({"type": "bullets", "items": items})
            if ordered:
                warnings.append("Ordered Markdown lists were converted to bullet blocks.")
            index = next_index
            continue

        paragraph, next_index = _collect_paragraph(lines, index)
        if paragraph:
            blocks.append({"type": "paragraph", "text": paragraph})
        index = next_index

    if not resolved_title:
        resolved_title = _title_from_blocks(blocks) or "Markdown Import"

    if not blocks:
        blocks.append({"type": "paragraph", "text": "작성 필요"})
        warnings.append("Markdown had no content blocks; inserted an empty paragraph placeholder.")

    quality_gates = {
        "validatePackage": True,
        "validateDocument": True,
        "reopen": True,
        "minNonEmptyParagraphs": max(1, _non_empty_block_count(blocks)),
        "visualReviewRequired": True,
    }
    return MarkdownPlanResult(
        plan={
            "schemaVersion": "hwpx.document_plan.v1",
            "title": resolved_title,
            "metadata": _string_metadata(metadata or {}),
            "stylePreset": style_preset,
            "blocks": blocks,
            "qualityGates": quality_gates,
        },
        warnings=warnings,
    )


def _parse_heading(stripped: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def _is_list_item(stripped: str) -> bool:
    return bool(re.match(r"^([-+*])\s+.+", stripped) or re.match(r"^\d+[.)]\s+.+", stripped))


def _collect_list(lines: list[str], index: int) -> tuple[list[str], int, bool]:
    items: list[str] = []
    ordered = False
    while index < len(lines):
        stripped = lines[index].strip()
        unordered = re.match(r"^[-+*]\s+(.+)$", stripped)
        numbered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if unordered:
            items.append(unordered.group(1).strip())
        elif numbered:
            ordered = True
            items.append(numbered.group(1).strip())
        else:
            break
        index += 1
    return items, index, ordered


def _collect_paragraph(lines: list[str], index: int) -> tuple[str, int]:
    parts: list[str] = []
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or _parse_heading(stripped) or _is_list_item(stripped) or _parse_table(lines, index):
            break
        parts.append(stripped)
        index += 1
    return " ".join(parts).strip(), index


def _parse_table(lines: list[str], index: int) -> tuple[dict[str, Any], int] | None:
    if index + 1 >= len(lines):
        return None
    header = lines[index].strip()
    divider = lines[index + 1].strip()
    if not _looks_like_table_row(header) or not _looks_like_divider(divider):
        return None

    labels = _split_table_row(header)
    if not labels:
        return None
    keys = [f"col{col_index + 1}" for col_index in range(len(labels))]
    rows: list[dict[str, str]] = []
    index += 2
    while index < len(lines) and _looks_like_table_row(lines[index].strip()):
        cells = _split_table_row(lines[index].strip())
        row = {
            key: cells[col_index].strip() if col_index < len(cells) else ""
            for col_index, key in enumerate(keys)
        }
        rows.append(row)
        index += 1

    return {
        "type": "table",
        "columns": [
            {"key": key, "label": label.strip() or key}
            for key, label in zip(keys, labels)
        ],
        "rows": rows,
    }, index


def _looks_like_table_row(line: str) -> bool:
    return "|" in line and len(_split_table_row(line)) >= 2


def _looks_like_divider(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.match(r"^:?-{3,}:?$", cell.strip()) for cell in cells)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.replace(r"\|", "|").strip() for cell in stripped.split("|")]


def _title_from_blocks(blocks: list[dict[str, Any]]) -> str:
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if block.get("type") == "heading" and text:
            return text
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if text:
            return text[:80]
    return ""


def _string_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in metadata.items()
        if value is not None and str(value).strip()
    }


def _non_empty_block_count(blocks: list[dict[str, Any]]) -> int:
    count = 0
    for block in blocks:
        kind = block.get("type")
        if kind in {"heading", "paragraph"} and str(block.get("text") or "").strip():
            count += 1
        elif kind == "bullets":
            count += len(block.get("items") or [])
    return count
