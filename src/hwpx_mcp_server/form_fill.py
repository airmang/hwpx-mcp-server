"""High-level HWPX form-fill workflow helpers.

The public MCP surface is intentionally two-phase:
``analyze_form_fill`` is non-mutating and ``apply_form_fill`` owns copy,
mutation, re-read, and validation evidence.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from hwpx.tools.package_validator import validate_package

from .core.content import get_table_data, get_table_map_in_doc, set_cell_text
from .core.document import open_doc, save_doc
from .core.formatting import list_styles_in_doc
from .hwpx_ops import HwpxOps
from .upstream import HP_NS, validate_document_path
from .utils.helpers import resolve_path

_FORM_FILL_SCHEMA_VERSION = "hwpx.formfill.v1"
_DOCX_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TABLE_DIRECTIONS = {"right", "down"}
_FORM_FILL_PLANS: dict[str, dict[str, Any]] = {}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_form_fill_workflow(
    *,
    source_filename: str,
    input_json: dict[str, Any] | str | None = None,
    input_json_path: str | None = None,
    input_docx: str | None = None,
    destination_filename: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a non-mutating form-fill analysis and serializable plan."""

    source_path = resolve_path(source_filename)
    source = Path(source_path)
    before_hash = sha256_file(source)
    destination_path = resolve_path(destination_filename) if destination_filename else None

    canonical_input = _load_canonical_input(
        input_json=input_json,
        input_json_path=input_json_path,
        input_docx=input_docx,
    )
    doc = open_doc(source_path)
    table_map = get_table_map_in_doc(doc)
    styles = list_styles_in_doc(doc)
    outline = _document_outline(doc)
    mappings = _build_mapping_analysis(doc, canonical_input)
    plan_id = f"ff_{uuid.uuid4().hex[:16]}"

    analysis: dict[str, Any] = {
        "plan_id": plan_id,
        "schemaVersion": _FORM_FILL_SCHEMA_VERSION,
        "source": {
            "filename": source_filename,
            "path": source_path,
            "sha256": before_hash,
            "mtime_ns": source.stat().st_mtime_ns,
        },
        "destination": {
            "filename": destination_filename,
            "path": destination_path,
            "required": bool(destination_filename),
            "will_be_created_by": "apply_form_fill",
        },
        "canonicalInput": canonical_input,
        "document": {
            "info": {
                "sections": len(getattr(doc, "sections", [])),
                "paragraphs": len(getattr(doc, "paragraphs", [])),
                "tables": len(table_map.get("tables", [])),
            },
            "outline": outline,
            "tables": table_map.get("tables", []),
            "styles": styles,
            "styleCount": len(styles),
        },
        "mappings": mappings,
        "unresolved_count": len(mappings["unresolved"]),
        "resolved_count": len(mappings["resolved"]),
        "mutated": False,
        "next_tool": "apply_form_fill",
        "options": options or {},
    }
    _FORM_FILL_PLANS[plan_id] = copy.deepcopy(analysis)
    after_hash = sha256_file(source)
    analysis["source"]["unchanged_after_analysis"] = after_hash == before_hash
    return analysis


def apply_form_fill_workflow(
    *,
    plan_id: str | None = None,
    analysis: dict[str, Any] | None = None,
    source_filename: str | None = None,
    destination_filename: str | None = None,
    canonical_input: dict[str, Any] | str | None = None,
    confirm: bool = True,
) -> dict[str, Any]:
    """Apply a resolved form-fill plan to a copied destination and validate it."""

    if not confirm:
        raise ValueError("confirm must be true to apply form-fill mutations")

    plan = _resolve_analysis(plan_id=plan_id, analysis=analysis)
    if canonical_input is not None:
        plan["canonicalInput"] = _load_canonical_input(input_json=canonical_input)
        doc_for_mapping = open_doc(_source_path_from_plan(plan, source_filename))
        plan["mappings"] = _build_mapping_analysis(doc_for_mapping, plan["canonicalInput"])

    source_path = _source_path_from_plan(plan, source_filename)
    destination_path = _destination_path_from_plan(plan, destination_filename)
    if Path(source_path).resolve(strict=False) == Path(destination_path).resolve(strict=False):
        raise ValueError("apply_form_fill refuses source-in-place edits; destination_filename must differ from source")

    unresolved = list(plan.get("mappings", {}).get("unresolved", []))
    if unresolved:
        return {
            "handoff_status": "blocked",
            "reason": "unresolved mappings remain",
            "unresolved": unresolved,
            "applied": [],
            "source": {"path": source_path, "sha256": sha256_file(source_path)},
            "destination": {"path": destination_path},
        }

    source = Path(source_path)
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_before_hash = sha256_file(source)
    source_before_mtime = source.stat().st_mtime_ns
    shutil.copy2(source, destination)
    copied_hash = sha256_file(destination)

    doc = open_doc(str(destination))
    applied: list[dict[str, Any]] = []
    for mapping in plan.get("mappings", {}).get("resolved", []):
        if mapping.get("kind") == "cell":
            table_index = int(mapping["table_index"])
            row = int(mapping["row"])
            col = int(mapping["col"])
            before_style = _cell_style_snapshot(doc, table_index, row, col)
            before_text = _cell_text(doc, table_index, row, col)
            set_cell_text(doc, table_index, row, col, str(mapping.get("value", "")))
            after_style = _cell_style_snapshot(doc, table_index, row, col)
            applied.append(
                {
                    **mapping,
                    "before_text": before_text,
                    "after_text": str(mapping.get("value", "")),
                    "style_before": before_style,
                    "style_after": after_style,
                    "style_preserved": before_style == after_style,
                }
            )
        elif mapping.get("kind") == "placeholder":
            token = str(mapping["token"])
            value = str(mapping.get("value", ""))
            replacements = _replace_placeholder(doc, token, value)
            applied.append(
                {
                    **mapping,
                    "replaced_count": sum(item["replace_count"] for item in replacements),
                    "replacements": replacements,
                    "style_preserved": all(item["style_preserved"] for item in replacements),
                }
            )

    save_doc(doc, str(destination))
    reread_doc = open_doc(str(destination))
    touched = _reread_touched(reread_doc, applied)
    validation = _runtime_validation(str(destination))
    source_after_hash = sha256_file(source)
    source_after_mtime = source.stat().st_mtime_ns
    output_hash = sha256_file(destination)
    ok = bool(validation["validate_structure"]["ok"] and validation["validate_package"]["ok"] and validation["validate_document"]["ok"])

    return {
        "handoff_status": "ready" if ok else "blocked",
        "plan_id": plan.get("plan_id"),
        "source": {
            "path": str(source),
            "sha256_before": source_before_hash,
            "sha256_after": source_after_hash,
            "mtime_ns_before": source_before_mtime,
            "mtime_ns_after": source_after_mtime,
            "preserved": source_before_hash == source_after_hash and source_before_mtime == source_after_mtime,
        },
        "destination": {
            "path": str(destination),
            "sha256_after_copy": copied_hash,
            "sha256_after_apply": output_hash,
            "changed": copied_hash != output_hash,
        },
        "lineage_id": _lineage_id(source_before_hash, str(destination)),
        "applied": applied,
        "unresolved": [],
        "touched": touched,
        "validation": validation,
        "persisted": True,
    }


def _load_canonical_input(
    *,
    input_json: dict[str, Any] | str | None = None,
    input_json_path: str | None = None,
    input_docx: str | None = None,
) -> dict[str, Any]:
    provided = [value is not None for value in (input_json, input_json_path, input_docx)].count(True)
    if provided != 1:
        raise ValueError("provide exactly one of input_json, input_json_path, or input_docx")

    if input_json_path is not None:
        payload = json.loads(Path(resolve_path(input_json_path)).read_text(encoding="utf-8"))
    elif input_docx is not None:
        payload = _canonical_input_from_docx(resolve_path(input_docx))
    elif isinstance(input_json, str):
        stripped = input_json.strip()
        possible_path = Path(stripped).expanduser()
        if possible_path.suffix.lower() == ".json" and possible_path.exists():
            payload = json.loads(possible_path.read_text(encoding="utf-8"))
        else:
            payload = json.loads(stripped)
    elif isinstance(input_json, dict):
        payload = copy.deepcopy(input_json)
    else:  # pragma: no cover - defensive, provided count catches this
        raise ValueError("input_json must be an object, JSON string, or JSON path")

    return _normalize_canonical_input(payload)


def _normalize_canonical_input(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("canonical input must be a JSON object")
    normalized = copy.deepcopy(payload)
    normalized.setdefault("schemaVersion", _FORM_FILL_SCHEMA_VERSION)
    if normalized["schemaVersion"] != _FORM_FILL_SCHEMA_VERSION:
        raise ValueError(f"unsupported form-fill schemaVersion: {normalized['schemaVersion']}")
    normalized.setdefault("source", {"type": "structured"})
    fields = normalized.setdefault("fields", [])
    if not isinstance(fields, list):
        raise ValueError("fields must be a list")
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            raise ValueError(f"fields[{index}] must be an object")
        label = str(field.get("label") or field.get("key") or "").strip()
        if not label:
            raise ValueError(f"fields[{index}] requires label or key")
        field.setdefault("key", label)
        field.setdefault("label", label)
        field.setdefault("value", "")
        field.setdefault("stylePolicy", "preserve-target")
        field.setdefault("target", {"kind": "label-path", "path": f"{label} > right"})
    paragraphs = normalized.setdefault("paragraphs", [])
    if not isinstance(paragraphs, list):
        raise ValueError("paragraphs must be a list")
    return normalized


def _canonical_input_from_docx(path: str) -> dict[str, Any]:
    docx_path = Path(path)
    if docx_path.suffix.lower() != ".docx":
        raise ValueError("input_docx must point to a .docx file")
    if not zipfile.is_zipfile(docx_path):
        raise ValueError("input_docx is not a valid DOCX zip package; provide input_json instead")
    try:
        with zipfile.ZipFile(docx_path) as archive:
            xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise ValueError("input_docx does not contain word/document.xml; provide input_json instead") from exc

    root = ET.fromstring(xml)
    body = root.find(f"{_DOCX_W_NS}body")
    if body is None:
        raise ValueError("input_docx does not contain a Word document body; provide input_json instead")

    fields: list[dict[str, Any]] = []
    for child in body:
        if child.tag == f"{_DOCX_W_NS}tbl":
            for row in child.iter(f"{_DOCX_W_NS}tr"):
                cells = [_element_text(cell).strip() for cell in row.findall(f"{_DOCX_W_NS}tc")]
                cells = [cell for cell in cells if cell]
                if len(cells) >= 2:
                    label, value = cells[0], cells[1]
                    fields.append(_field_from_label_value(label, value, source="docx-table"))
        elif child.tag == f"{_DOCX_W_NS}p":
            text = _element_text(child).strip()
            if not text or "=" not in text and ":" not in text:
                continue
            label, value = re.split(r"[:=]", text, maxsplit=1)
            if label.strip() and value.strip():
                fields.append(_field_from_label_value(label.strip(), value.strip(), source="docx-paragraph"))
    if not fields:
        raise ValueError("input_docx did not contain key/value fields; provide canonical input_json")
    return {
        "schemaVersion": _FORM_FILL_SCHEMA_VERSION,
        "source": {"type": "docx", "path": path, "sha256": sha256_file(path)},
        "fields": fields,
        "paragraphs": [],
    }


def _field_from_label_value(label: str, value: str, *, source: str) -> dict[str, Any]:
    return {
        "key": _slug_key(label),
        "label": label,
        "value": value,
        "target": {"kind": "label-path", "path": f"{label} > right"},
        "stylePolicy": "preserve-target",
        "provenance": {"source": source},
    }


def _element_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{_DOCX_W_NS}t"))


def _slug_key(label: str) -> str:
    slug = re.sub(r"\W+", "_", label.strip(), flags=re.UNICODE).strip("_")
    return slug or "field"


def _document_outline(doc: Any) -> list[dict[str, Any]]:
    outline = []
    for index, para in enumerate(getattr(doc, "paragraphs", [])):
        text = (getattr(para, "text", None) or "").strip()
        if not text:
            continue
        level = 1 if len(text) < 80 else 0
        if text.startswith("#"):
            level = min(6, len(text) - len(text.lstrip("#")))
        if level:
            outline.append({"level": level, "text": text, "paragraph_index": index})
    return outline


def _build_mapping_analysis(doc: Any, canonical_input: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for field in _iter_fill_items(canonical_input):
        target = field.get("target") or {}
        kind = target.get("kind", "label-path")
        if kind in {"cell", "coordinate"} or {"table_index", "row", "col"}.issubset(target):
            resolved.append(_resolved_cell_mapping(field, target, method="explicit-coordinate"))
            continue
        if kind == "label-path":
            label, direction = _label_and_direction(field, target)
            matches = doc.find_cell_by_label(label, direction=direction).get("matches", [])
            if len(matches) == 1:
                match = matches[0]
                cell = match["target_cell"]
                resolved.append(
                    {
                        "kind": "cell",
                        "key": field.get("key"),
                        "label": label,
                        "value": str(field.get("value", "")),
                        "table_index": match["table_index"],
                        "row": cell["row"],
                        "col": cell["col"],
                        "current_text": cell.get("text", ""),
                        "stylePolicy": field.get("stylePolicy", "preserve-target"),
                        "confidence": "high",
                        "method": "label-path",
                    }
                )
            else:
                unresolved.append(
                    {
                        "kind": "cell",
                        "key": field.get("key"),
                        "label": label,
                        "value": str(field.get("value", "")),
                        "reason": "label not found" if not matches else "ambiguous label",
                        "candidates": matches,
                        "candidate_count": len(matches),
                        "next_action": "provide explicit table_index/row/col target",
                    }
                )
            continue
        if kind == "placeholder":
            token = target.get("token")
            if not token:
                unresolved.append({"kind": "placeholder", "key": field.get("key"), "reason": "missing token"})
            else:
                resolved.append(
                    {
                        "kind": "placeholder",
                        "key": field.get("key"),
                        "token": str(token),
                        "value": str(field.get("value", "")),
                        "stylePolicy": field.get("stylePolicy", "preserve-placeholder"),
                        "method": "placeholder",
                    }
                )
            continue
        unresolved.append({"kind": kind, "key": field.get("key"), "reason": f"unsupported target kind: {kind}"})
    return {"resolved": resolved, "unresolved": unresolved}


def _iter_fill_items(canonical_input: dict[str, Any]) -> list[dict[str, Any]]:
    items = list(canonical_input.get("fields", []))
    for paragraph in canonical_input.get("paragraphs", []):
        if isinstance(paragraph, dict):
            items.append(
                {
                    "key": paragraph.get("key"),
                    "label": paragraph.get("label") or paragraph.get("key"),
                    "value": paragraph.get("value", paragraph.get("text", "")),
                    "target": paragraph.get("target"),
                    "stylePolicy": paragraph.get("stylePolicy", "preserve-placeholder"),
                }
            )
    return items


def _resolved_cell_mapping(field: dict[str, Any], target: dict[str, Any], *, method: str) -> dict[str, Any]:
    return {
        "kind": "cell",
        "key": field.get("key"),
        "label": field.get("label"),
        "value": str(field.get("value", "")),
        "table_index": int(target.get("table_index", target.get("tableIndex", 0))),
        "row": int(target["row"]),
        "col": int(target["col"]),
        "stylePolicy": field.get("stylePolicy", "preserve-target"),
        "confidence": "explicit",
        "method": method,
    }


def _label_and_direction(field: dict[str, Any], target: dict[str, Any]) -> tuple[str, str]:
    path = str(target.get("path") or f"{field.get('label')} > right")
    parts = [part.strip() for part in path.split(">") if part.strip()]
    label = parts[0] if parts else str(field.get("label") or field.get("key"))
    direction = parts[1].casefold() if len(parts) > 1 else "right"
    if direction not in _TABLE_DIRECTIONS:
        raise ValueError("label-path target currently supports right or down as the first direction")
    return label, direction


def _resolve_analysis(*, plan_id: str | None, analysis: dict[str, Any] | None) -> dict[str, Any]:
    if analysis is not None:
        return copy.deepcopy(analysis)
    if plan_id is None:
        raise ValueError("provide plan_id or analysis")
    try:
        return copy.deepcopy(_FORM_FILL_PLANS[plan_id])
    except KeyError as exc:
        raise ValueError(f"unknown form-fill plan_id: {plan_id}") from exc


def _source_path_from_plan(plan: dict[str, Any], override: str | None) -> str:
    if override:
        return resolve_path(override)
    source = plan.get("source") or {}
    path = source.get("path") or source.get("filename")
    if not path:
        raise ValueError("source_filename is required")
    return resolve_path(str(path))


def _destination_path_from_plan(plan: dict[str, Any], override: str | None) -> str:
    if override:
        return resolve_path(override)
    destination = plan.get("destination") or {}
    path = destination.get("path") or destination.get("filename")
    if not path:
        raise ValueError("destination_filename is required")
    return resolve_path(str(path))


def _cell_style_snapshot(doc: Any, table_index: int, row: int, col: int) -> dict[str, Any]:
    cell = _cell(doc, table_index, row, col)
    paragraph = cell.paragraphs[0] if getattr(cell, "paragraphs", []) else None
    return _paragraph_style_snapshot(paragraph)


def _paragraph_style_snapshot(paragraph: Any) -> dict[str, Any]:
    run = paragraph.runs[0] if paragraph is not None and getattr(paragraph, "runs", []) else None
    return {
        "para_pr_id_ref": getattr(paragraph, "para_pr_id_ref", None),
        "style_id_ref": getattr(paragraph, "style_id_ref", None),
        "char_pr_id_ref": getattr(paragraph, "char_pr_id_ref", None),
        "run_char_pr_id_ref": getattr(run, "char_pr_id_ref", None),
    }


def _cell_text(doc: Any, table_index: int, row: int, col: int) -> str:
    return _cell(doc, table_index, row, col).text or ""


def _cell(doc: Any, table_index: int, row: int, col: int) -> Any:
    tables = []
    for paragraph in doc.paragraphs:
        tables.extend(getattr(paragraph, "tables", []))
    return tables[table_index].rows[row].cells[col]


def _replace_placeholder(doc: Any, token: str, value: str) -> list[dict[str, Any]]:
    replacements: list[dict[str, Any]] = []
    for paragraph_index, paragraph in enumerate(doc.paragraphs):
        before_text = paragraph.text or ""
        if token not in before_text:
            continue
        before_style = _paragraph_style_snapshot(paragraph)
        replace_count = 0
        for run in paragraph.runs:
            text = run.text or ""
            if token in text:
                replace_count += text.count(token)
                run.text = text.replace(token, value)
        after_style = _paragraph_style_snapshot(paragraph)
        replacements.append(
            {
                "paragraph_index": paragraph_index,
                "before_text": before_text,
                "after_text": paragraph.text or "",
                "replace_count": replace_count,
                "style_before": before_style,
                "style_after": after_style,
                "style_preserved": before_style == after_style,
            }
        )
    return replacements


def _reread_touched(doc: Any, applied: list[dict[str, Any]]) -> list[dict[str, Any]]:
    touched = []
    for item in applied:
        if item.get("kind") == "cell":
            table_index = int(item["table_index"])
            row = int(item["row"])
            col = int(item["col"])
            data = get_table_data(doc, table_index)
            touched.append(
                {
                    "kind": "cell",
                    "table_index": table_index,
                    "row": row,
                    "col": col,
                    "text": data["data"][row][col],
                    "style": _cell_style_snapshot(doc, table_index, row, col),
                }
            )
        elif item.get("kind") == "placeholder":
            for replacement in item.get("replacements", []):
                paragraph_index = int(replacement["paragraph_index"])
                paragraph = doc.paragraphs[paragraph_index]
                touched.append(
                    {
                        "kind": "placeholder",
                        "paragraph_index": paragraph_index,
                        "text": paragraph.text or "",
                        "style": _paragraph_style_snapshot(paragraph),
                    }
                )
    return touched


def _runtime_validation(path: str) -> dict[str, Any]:
    ops = HwpxOps(auto_backup=False)
    structure = ops.validate_structure(path)
    package_report = validate_package(path)
    document_report = validate_document_path(path)
    return {
        "validate_structure": structure,
        "validate_package": _package_report(package_report),
        "validate_document": _document_report(document_report),
    }


def _package_report(report: Any) -> dict[str, Any]:
    return {
        "ok": bool(getattr(report, "ok", False)),
        "checked_parts": list(getattr(report, "checked_parts", ())),
        "issues": [_issue_payload(issue) for issue in getattr(report, "issues", ())],
    }


def _document_report(report: Any) -> dict[str, Any]:
    return {
        "ok": bool(getattr(report, "ok", False)),
        "validated_parts": list(getattr(report, "validated_parts", ())),
        "issues": [_issue_payload(issue) for issue in getattr(report, "issues", ())],
    }


def _issue_payload(issue: Any) -> dict[str, Any]:
    return {
        "part": getattr(issue, "part_name", None),
        "message": getattr(issue, "message", str(issue)),
        "level": getattr(issue, "level", "error"),
    }


def _lineage_id(source_hash: str, destination: str) -> str:
    return hashlib.sha256(f"{source_hash}:{destination}".encode("utf-8")).hexdigest()[:16]
