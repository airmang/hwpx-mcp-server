# SPDX-License-Identifier: Apache-2.0
"""Stateless HWPX MCP 서버."""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import html
import json
import os
import re
import tempfile
from datetime import date, datetime
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
from .core.formatting import (
    create_style_in_doc,
    format_text_range,
    list_styles_in_doc,
    outline_style_levels,
)
from .core.locations import location_from_anchor, resolve_paragraph_reference
from .core.search import _replace_in_runs, batch_replace_in_doc, find_in_doc, replace_in_doc
from .core.transactions import (
    rotate_and_backup,
    save_dry_run,
    semantic_diff,
    undo_last_backup,
)
from .document_state import document_state_payload, revision_mismatch_response
from .form_fill import analyze_form_fill_workflow, apply_form_fill_workflow
from . import quality as quality_contract
from .hwpx_ops import HwpxOps
from hwpx.tools.pii import DEFAULT_POLICY, detect_pii, mask_pii, mask_value
from hwpx.tools import read_fidelity as _read_fidelity


def _mask_pii_text(value: str, mask: bool = True) -> str:
    """Mask machine-set PII (rrn/phone/email/card) in user-facing extract output.

    On by default (safe-by-default per 개인정보 보호법); contextual types stay
    label-gated low-confidence inside ``mask_pii`` so free text isn't over-masked.
    """
    if not mask or not value:
        return value
    return mask_pii(value, DEFAULT_POLICY)


def _deep_mask_pii(obj: Any, mask: bool = True) -> Any:
    """Recursively mask PII in string VALUES of a nested JSON-able structure.

    Dict keys are left untouched; only string values are masked (``mask_pii`` only
    rewrites machine-set PII + label-gated contextual, so normal text is unchanged).
    """
    if not mask:
        return obj
    if isinstance(obj, str):
        return mask_pii(obj, DEFAULT_POLICY)
    if isinstance(obj, list):
        return [_deep_mask_pii(item, True) for item in obj]
    if isinstance(obj, dict):
        return {key: _deep_mask_pii(val, True) for key, val in obj.items()}
    return obj
from .quality_generation import (
    analyze_quality_generation_workflow,
    apply_quality_generation_workflow,
    create_quality_document_fallback,
    inspect_quality_fallback,
)
from .storage import build_hwpx_open_safety_report, build_hwpx_verification_report
from .tool_contract import (
    contract_hash as tool_contract_hash,
    expected_tool_names,
    register_fastmcp_tools,
    skill_required_tool_names,
)
from .upstream import HP_NS, create_text_extractor, open_document
from .utils.helpers import default_max_chars, resolve_path, truncate_response

from hwpx.tools.id_integrity import check_id_integrity
from hwpx.form_fit import seal as seal_ops
from hwpx.form_fit.wordbox import (
    OracleUnavailable,
    extract_image_boxes,
    render_glyph_boxes,
)

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
        get_document_plan_schema as get_hwpx_document_plan_schema,
        inspect_document_authoring_quality as inspect_authoring_document_quality,
        inspect_operating_plan_quality as inspect_operating_plan_document_quality,
        normalize_document_plan as normalize_hwpx_document_plan,
        validate_document_plan as validate_hwpx_document_plan,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    build_document_from_plan = None
    get_hwpx_document_plan_schema = None
    inspect_authoring_document_quality = None
    inspect_operating_plan_document_quality = None
    normalize_hwpx_document_plan = None
    validate_hwpx_document_plan = None

try:  # python-hwpx >= MarkItDown-style ingest gateway
    from hwpx.ingest import (
        DocumentIngestError,
        DocumentIngestor,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    DocumentIngestError = None
    DocumentIngestor = None

try:  # python-hwpx >= official-document style lint feature
    from hwpx import (
        inspect_official_document_style as inspect_hwpx_official_document_style,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    inspect_hwpx_official_document_style = None

try:  # python-hwpx >= govoffice advanced generators
    from hwpx import (
        build_image_grid as build_hwpx_image_grid,
        build_meeting_nameplates as build_hwpx_meeting_nameplates,
        build_organization_chart as build_hwpx_organization_chart,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    build_hwpx_image_grid = None
    build_hwpx_meeting_nameplates = None
    build_hwpx_organization_chart = None

try:  # python-hwpx >= clean-room document diff feature
    from hwpx import (
        build_comparison_table_plan as build_hwpx_comparison_table_plan,
        doc_diff as build_hwpx_doc_diff,
        inspect_reference_consistency as inspect_hwpx_reference_consistency,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    build_hwpx_comparison_table_plan = None
    build_hwpx_doc_diff = None
    inspect_hwpx_reference_consistency = None

try:  # python-hwpx >= mail-merge and table-compute productivity tools
    from hwpx import (
        inspect_mail_merge_placeholders as inspect_hwpx_mail_merge_placeholders,
        mail_merge as build_hwpx_mail_merge,
        table_compute as build_hwpx_table_compute,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    inspect_hwpx_mail_merge_placeholders = None
    build_hwpx_mail_merge = None
    build_hwpx_table_compute = None

try:  # python-hwpx >= style-profile and template registry tools
    from hwpx import (
        apply_style_profile_to_plan as apply_hwpx_style_profile_to_plan,
        compare_style_profiles as compare_hwpx_style_profiles,
        describe_template as describe_hwpx_template,
        extract_style_profile as extract_hwpx_style_profile,
        list_templates as list_hwpx_templates,
        register_template as register_hwpx_template,
    )
except Exception:  # pragma: no cover - optional dependency compatibility
    apply_hwpx_style_profile_to_plan = None
    compare_hwpx_style_profiles = None
    describe_hwpx_template = None
    extract_hwpx_style_profile = None
    list_hwpx_templates = None
    register_hwpx_template = None

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

try:  # python-hwpx >= exam typesetting composer (S-056 시험지 조판)
    from hwpx.exam import (
        ExamParseError,
        FormProfileError,
        compose_exam_into_form,
        measure_question_splits,
    )
    from hwpx.visual.oracle import NullOracle, resolve_oracle
except Exception:  # pragma: no cover - optional dependency compatibility
    ExamParseError = None
    FormProfileError = None
    compose_exam_into_form = None
    measure_question_splits = None
    NullOracle = None
    resolve_oracle = None

try:  # python-hwpx >= redline authoring verification (S-058/FR-009)
    from hwpx.tools.redline import verify_redline as verify_hwpx_redline
except Exception:  # pragma: no cover - optional dependency compatibility
    verify_hwpx_redline = None

mcp = FastMCP("hwpx-mcp-server")


def _error_data(
    message: str,
    *,
    tool_name: str | None = None,
    arguments: dict | None = None,
    code: int = -32000,
    extra_data: dict | None = None,
) -> mcp_types.ErrorData:
    data: dict[str, object] = {}
    if tool_name is not None:
        data["tool"] = tool_name
    if arguments is not None:
        data["arguments"] = arguments
    if extra_data:
        data.update(extra_data)
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


def _gate_or_plain_error(text: str, tool_name: str, arguments: dict) -> mcp_types.ErrorData:
    """Rebuild a structured gate/skew error from the stash, else a plain error."""

    gate = quality_contract.take_last_gate_error()
    if isinstance(gate, quality_contract.CapabilitySkewError):
        return _error_data(
            f"CAPABILITY_SKEW: {text}", tool_name=tool_name, arguments=arguments,
            extra_data={"errorCode": gate.code, "capability": gate.state},
        )
    if isinstance(gate, quality_contract.QualityGateError):
        return _error_data(
            f"{gate.code}: {text}", tool_name=tool_name, arguments=arguments,
            extra_data={
                "errorCode": gate.code,
                "visualComplete": gate.block,
                "suggestedRetry": gate.block.get("suggestedRetry"),
            },
        )
    return _error_data(text, tool_name=tool_name, arguments=arguments)


async def _strict_call_tool_handler(req: mcp_types.CallToolRequest):
    tool_name = req.params.name
    arguments = req.params.arguments or {}
    quality_contract.clear_last_gate_error()
    try:
        result = await mcp.call_tool(tool_name, arguments)
    except Exception as exc:
        # FastMCP wraps a tool's exception in ToolError, so the structured gate/
        # skew error never matches a specific `except` here — recover it from the
        # stash the exception left on construction (plan §2 Phase F).
        return _gate_or_plain_error(str(exc), tool_name, arguments)

    if isinstance(result, mcp_types.CreateTaskResult):
        return mcp_types.ServerResult(result)

    if isinstance(result, mcp_types.CallToolResult):
        if bool(result.isError):
            text = _first_text_content(result.content) or f"Tool '{tool_name}' returned an error"
            return _gate_or_plain_error(text, tool_name, arguments)
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


_ACTIVE_ADVANCED = _advanced_enabled()


_OPS = HwpxOps(auto_backup=False)

_OUTPUT_MODES = {"full", "chunks"}
_VERBOSITY_MODES = {"compact", "full"}
_CHUNK_STRATEGIES = {"section", "paragraph"}
_TABLE_LABEL_DIRECTIONS = ("right", "down")
_DEFAULT_MAX_CHARS_PER_CHUNK = 8000
_DEFAULT_MAX_INPUT_BYTES = 20 * 1024 * 1024
_DEFAULT_FETCH_TIMEOUT_SECONDS = 20.0
_FIGURE_CAPTION_RE = re.compile(r"^\s*(?:Figure|Fig\.|그림)\s*\d*", re.IGNORECASE)
_IDEMPOTENCY_CACHE: dict[str, dict[str, Any]] = {}
_MAX_IDEMPOTENCY_CACHE_ENTRIES = 512


def _package_version(package: str) -> str:
    return quality_contract.package_version(package)


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


def _normalize_verbosity(verbosity: str | None) -> str:
    value = (verbosity or "compact").strip().lower()
    if value not in _VERBOSITY_MODES:
        expected = ", ".join(sorted(_VERBOSITY_MODES))
        raise ValueError(f"verbosity must be one of: {expected}")
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


def _compact_open_safety(open_safety: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(open_safety, dict):
        return None
    compact: dict[str, Any] = {
        "ok": bool(open_safety.get("ok")),
        "summary": open_safety.get("summary"),
    }
    for key in ("validatePackage", "validateDocument", "reopen"):
        value = open_safety.get(key)
        if isinstance(value, dict):
            compact[key] = {"ok": bool(value.get("ok"))}
    return compact


def _verification_is_successful(verification: dict[str, Any] | None) -> bool:
    if not isinstance(verification, dict):
        return False
    open_safety = verification.get("openSafety")
    if isinstance(open_safety, dict) and not bool(open_safety.get("ok")):
        return False
    return bool(verification.get("ok", True))


def _compact_verification_report(verification: dict[str, Any]) -> dict[str, Any]:
    if not _verification_is_successful(verification):
        return verification
    compact: dict[str, Any] = {
        "ok": bool(verification.get("ok", True)),
        "summary": verification.get("summary", "verification passed"),
    }
    for key in ("filePath", "fileSizeBytes", "warnings"):
        if key in verification:
            compact[key] = verification[key]
    open_safety = _compact_open_safety(verification.get("openSafety"))
    if open_safety is not None:
        compact["openSafety"] = open_safety
    return compact


def _apply_write_verbosity(payload: dict[str, Any], verbosity: str | None) -> dict[str, Any]:
    if _normalize_verbosity(verbosity) == "full":
        return payload
    compacted = dict(payload)
    for key in ("verification", "verificationReport"):
        verification = compacted.get(key)
        if isinstance(verification, dict):
            compacted[key] = _compact_verification_report(verification)
    open_safety = compacted.get("openSafety")
    if isinstance(open_safety, dict) and bool(open_safety.get("ok")):
        compacted["openSafety"] = _compact_open_safety(open_safety)
    return compacted


def _idempotency_scope(tool_name: str, path: str, idempotency_key: str | None) -> str | None:
    key = (idempotency_key or "").strip()
    if not key:
        return None
    resolved = str(Path(path).resolve())
    return f"{tool_name}:{resolved}:{key}"


def _idempotency_fingerprint(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)


def _idempotency_replay(
    scope: str | None,
    *,
    fingerprint: str,
) -> dict[str, Any] | None:
    if scope is None:
        return None
    cached = _IDEMPOTENCY_CACHE.get(scope)
    if cached is None:
        return None
    if cached.get("fingerprint") != fingerprint:
        raise ValueError("idempotency_key was reused with different arguments")
    payload = copy.deepcopy(cached["payload"])
    payload["idempotentReplay"] = True
    return payload


def _idempotency_store(
    scope: str | None,
    *,
    fingerprint: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if scope is None:
        return payload
    stored = copy.deepcopy(payload)
    stored["idempotentReplay"] = False
    _IDEMPOTENCY_CACHE[scope] = {"fingerprint": fingerprint, "payload": stored}
    while len(_IDEMPOTENCY_CACHE) > _MAX_IDEMPOTENCY_CACHE_ENTRIES:
        _IDEMPOTENCY_CACHE.pop(next(iter(_IDEMPOTENCY_CACHE)))
    return copy.deepcopy(stored)


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


def _decode_image_base64(image_base64: str) -> bytes:
    try:
        payload = base64.b64decode((image_base64 or "").strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid image_base64 payload") from exc
    if not payload:
        raise ValueError("image_base64 decoded to empty payload")
    return payload


def _id_integrity_payload(doc: Any) -> dict[str, Any]:
    report = check_id_integrity(doc)
    return {
        "ok": report.ok,
        "dangling": [str(item) for item in report.dangling],
        "orphanBinData": [str(item) for item in report.orphan_bin_data],
        "ignored": [str(item) for item in report.ignored],
    }


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


def _run_format_detail(run: Any, fontfaces: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """Resolved inline formatting for one run.

    Named fields (bold/italic/underline/strikeout/color/fontSize/fontName/
    super-subscript) come from the canonical :mod:`hwpx.tools.read_fidelity`
    resolver so this surface and the fidelity harness agree by construction.
    ``strikeout`` is shape-normalised (the always-present
    ``<hh:strikeout shape="NONE"/>`` previously read as always-on); ``underline``
    is the type or ``None`` when off. Legacy keys are preserved for compat.
    """
    style = getattr(run, "style", None)
    span = _read_fidelity.run_span(getattr(run, "text", "") or "", style, fontfaces or {})
    return {
        "text": span.text,
        "charPrIDRef": getattr(run, "char_pr_id_ref", None),
        "bold": span.bold,
        "italic": span.italic,
        "underline": span.underline,
        "strikeout": span.strikeout,
        "color": span.color,
        "fontSize": span.size_pt,
        "fontName": span.font,
        "superscript": span.superscript,
        "subscript": span.subscript,
        # legacy back-compat keys
        "textColor": span.color,
        "underlineType": style.underline_type() if style is not None else None,
        "underlineColor": style.underline_color() if style is not None else None,
        "attributes": dict(getattr(style, "attributes", {}) or {}),
    }


def _paragraph_format_detail(paragraph: Any, fontfaces: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "paraPrIDRef": getattr(paragraph, "para_pr_id_ref", None),
        "styleIDRef": getattr(paragraph, "style_id_ref", None),
        "charPrIDRef": getattr(paragraph, "char_pr_id_ref", None),
        "runs": [_run_format_detail(run, fontfaces) for run in getattr(paragraph, "runs", [])],
    }


def _cell_format_detail(cell: Any) -> dict[str, Any]:
    return {
        "width": getattr(cell, "width", None),
        "height": getattr(cell, "height", None),
        "span": list(getattr(cell, "span", ()) or ()),
        "address": list(getattr(cell, "address", ()) or ()),
        "borderFillIDRef": getattr(getattr(cell, "element", None), "get", lambda _name, _default=None: _default)("borderFillIDRef"),
    }


def _table_format_detail(table: Any) -> dict[str, Any]:
    rows = []
    for row in getattr(table, "rows", []):
        cells = [_cell_format_detail(cell) for cell in getattr(row, "cells", [])]
        rows.append(cells)
    return {
        "columnCount": getattr(table, "column_count", None),
        "rowCount": getattr(table, "row_count", None),
        "cells": rows,
    }


def _build_read_model(doc: Any, *, format_detail: bool = False) -> dict[str, Any]:
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
    style_levels = outline_style_levels(doc)
    fontfaces = _read_fidelity.fontface_maps(doc) if format_detail else {}
    for paragraph_index, paragraph in enumerate(doc.paragraphs):
        text = (paragraph.text or "").strip()
        level = _paragraph_outline_level(paragraph, text, style_levels)
        paragraph_payload = {"index": paragraph_index, "text": text}
        if format_detail:
            paragraph_payload["format"] = _paragraph_format_detail(paragraph, fontfaces)

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
            item = {"type": "heading", "level": level, "text": heading_text, "paragraph_index": paragraph_index}
            if format_detail:
                item["format"] = _paragraph_format_detail(paragraph, fontfaces)
            items.append(item)
        elif text:
            item = {"type": "paragraph", "text": text, "paragraph_index": paragraph_index}
            if format_detail:
                item["format"] = _paragraph_format_detail(paragraph, fontfaces)
            items.append(item)

        if text:
            current_section["paragraphs"].append(paragraph_payload)
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
            if format_detail:
                table_payload["format"] = _table_format_detail(table)
            tables.append(table_payload)
            current_section["tables"].append(table_payload)
            item = {"type": "table", "table_index": table_index, "paragraph_index": paragraph_index, "data": rows}
            if format_detail:
                item["format"] = table_payload["format"]
            items.append(item)
            table_index += 1

    _flush_current_section()
    try:
        notes = [note.to_dict() for note in _read_fidelity.collect_notes(doc)]
    except Exception:  # pragma: no cover - defensive: never break a read
        notes = []
    return {
        "title": toc[0]["text"] if toc else None,
        "toc": toc,
        "sections": sections,
        "tables": tables,
        "figures": figures,
        "items": items,
        "notes": notes,
    }


def _append_notes_markdown(markdown: str, notes: list[dict[str, Any]], mask: bool) -> str:
    """Append a footnote/endnote definition appendix (reference-style).

    The reading surfaces used to drop note bodies entirely; this preserves them
    at the installed surface as ``[^fn1]: body`` lines under a rule.
    """
    if not notes:
        return markdown
    fn_i = en_i = 0
    lines: list[str] = []
    for note in notes:
        if note.get("kind") == "footNote":
            fn_i += 1
            marker, label = f"[^fn{fn_i}]", "각주"
        else:
            en_i += 1
            marker, label = f"[^en{en_i}]", "미주"
        body = _mask_pii_text(note.get("bodyText", "") or "", mask)
        lines.append(f"{marker}: ({label}) {body}")
    appendix = "\n".join(lines)
    return f"{markdown}\n\n---\n\n{appendix}" if markdown else appendix


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


def _ingest_markdown_chunks(markdown: str, *, max_chars_per_chunk: int) -> list[str]:
    paragraphs = [part for part in re.split(r"\n{2,}", markdown or "") if part.strip()]
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


def _ingest_local_document(filename: str):
    if DocumentIngestor is None:
        raise RuntimeError("installed python-hwpx does not provide document ingest")
    from .ingest_adapters import MarkItDownAdapter, MissingMarkItDownDependency

    path = resolve_path(filename)
    ingestor = DocumentIngestor.default()
    ingestor.register_converter(MarkItDownAdapter(), priority=100.0)
    try:
        return path, ingestor.convert(path)
    except DocumentIngestError as exc:
        for attempt in getattr(exc, "attempts", []) or []:
            if getattr(attempt, "error_type", None) == "MissingMarkItDownDependency":
                raise MissingMarkItDownDependency(str(attempt.message), attempts=exc.attempts) from exc
        raise


def _document_ingest_error_payload(exc: Exception, filename: str) -> dict[str, Any]:
    if DocumentIngestError is not None and isinstance(exc, DocumentIngestError):
        payload = exc.as_dict()
    else:
        payload = {"error": type(exc).__name__, "message": str(exc), "attempts": []}
    payload.update({"ok": False, "filename": filename})
    return payload


def _document_ingest_meta(result: Any) -> dict[str, Any]:
    source_info = getattr(result, "source_info", None)
    meta = {
        "source_format": getattr(result, "source_format", None),
        "engine": getattr(result, "engine", None),
        "engine_version": getattr(result, "engine_version", None),
        "lossiness": getattr(result, "lossiness", None),
    }
    if source_info is not None:
        meta["source_info"] = {
            "mimetype": getattr(source_info, "mimetype", None),
            "extension": getattr(source_info, "extension", None),
            "charset": getattr(source_info, "charset", None),
            "filename": getattr(source_info, "filename", None),
            "local_path": getattr(source_info, "local_path", None),
            "url": getattr(source_info, "url", None),
        }
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, dict):
        meta.update(metadata)
    return {key: value for key, value in meta.items() if value is not None}


def _attempts_payload(result: Any) -> list[dict[str, Any]]:
    attempts = getattr(result, "attempts", []) or []
    return [
        attempt.as_dict() if hasattr(attempt, "as_dict") else dict(attempt)
        for attempt in attempts
    ]


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


def _paragraph_outline_level(
    paragraph: Any, text: str, style_levels: dict[str, int]
) -> int:
    """개요 문단 스타일을 우선하고, 구버전 '#' 헤딩만 fallback으로 인식한다."""
    if not (text or "").strip():
        return 0
    ref = getattr(paragraph, "style_id_ref", None)
    if ref is not None and str(ref) in style_levels:
        return style_levels[str(ref)]
    if text.strip().startswith("#"):
        return _outline_level(text)
    if style_levels:
        return 0
    return _outline_level(text)


def create_document(
    filename: str,
    title: str = None,
    author: str = None,
    expected_revision: str = None,
) -> dict:
    """새 HWPX 문서를 생성하고 즉시 저장합니다."""
    del title, author
    path = resolve_path(filename)
    if Path(path).exists():
        guard = _revision_guard(path, expected_revision)
        if guard is not None:
            return guard
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    verification = create_blank(path)
    return {
        "filename": filename,
        "created": True,
        "verification": verification,
        "openSafety": verification["openSafety"],
        **document_state_payload(path),
    }


def _save_generated_document(doc: Any, path: str, *, quality: Any = None) -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Phase F: generation also funnels through the one SavePipeline gate.
    quality_contract.assert_write_capability()
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.stem}.",
        suffix=target.suffix or ".hwpx",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        os.close(fd)
        report = quality_contract.save_through_pipeline(doc, tmp_path, quality=quality)
        verification = build_hwpx_verification_report(tmp_path)
        if not verification["openSafety"]["ok"]:
            raise RuntimeError(
                "generated HWPX failed open-safety verification: "
                + verification["openSafety"]["summary"]
            )
        os.replace(tmp_path, target)
        verification["filePath"] = str(target)
        verification["visualComplete"] = quality_contract.visual_complete_block(report)
        return verification
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _save_doc_verification(doc: Any, path: str, *, quality: Any = None) -> dict[str, Any]:
    target = Path(path)
    backup = rotate_and_backup(target)
    verification = save_doc(doc, path, quality=quality)
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


def _with_document_state(result: dict[str, Any], path: str) -> dict[str, Any]:
    payload = dict(result)
    payload.update(document_state_payload(path))
    return payload


def _revision_guard(path: str, expected_revision: str | None) -> dict[str, Any] | None:
    return revision_mismatch_response(path, expected_revision)


def _with_save_verification(result: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    payload.setdefault("dryRun", False)
    payload["verificationReport"] = verification
    payload.setdefault("openSafety", verification.get("openSafety"))
    if "visualComplete" in verification:
        payload.setdefault("visualComplete", verification["visualComplete"])
    if "semanticDiff" in verification:
        payload.setdefault("semanticDiff", verification["semanticDiff"])
    if "backup" in verification:
        payload.setdefault("backup", verification["backup"])
    file_path = verification.get("filePath")
    if isinstance(file_path, str):
        payload.update(document_state_payload(file_path))
    return payload


def _with_dry_run_verification(
    result: dict[str, Any], doc: Any, path: str, *, quality: Any = None
) -> dict[str, Any]:
    payload = dict(result)
    dry_run = save_dry_run(doc, path, quality=quality)
    payload.update(dry_run)
    payload.update(document_state_payload(path))
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
    verify_render: bool = False,
) -> dict:
    if inspect_authoring_document_quality is None:
        raise RuntimeError("installed python-hwpx does not provide document-plan authoring")
    profile_arg = _quality_profile_argument(quality_profile, profile)
    kwargs: dict[str, Any] = {"plan": document_plan}
    if profile_arg is not None:
        kwargs["quality_profile"] = profile_arg
    if verify_render:
        kwargs["verify_render"] = True
    try:
        return inspect_authoring_document_quality(source, **kwargs)
    except TypeError as exc:
        # An older installed python-hwpx may predate verify_render and/or the
        # quality_profile kwarg; retry without the unsupported argument(s).
        if verify_render:
            kwargs.pop("verify_render", None)
            try:
                return inspect_authoring_document_quality(source, **kwargs)
            except TypeError:
                pass
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


def get_document_plan_schema() -> dict:
    """document_plan(생성 계획)의 JSON Schema를 반환합니다.

    Structured Outputs/외부 검증 계약으로 직접 사용하세요 — 자유형 JSON을 만들고
    validate_document_plan에서 거부당하는 대신, 이 스키마로 생성을 제약합니다.
    """
    if get_hwpx_document_plan_schema is None:
        raise RuntimeError("installed python-hwpx does not provide document-plan authoring")
    return get_hwpx_document_plan_schema()


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


def markdown_to_document_plan(
    markdown: str,
    title: str = None,
    metadata: dict = None,
    style_preset: str = "standard_korean_business",
) -> dict:
    """Markdown을 검증 가능한 hwpx.document_plan.v1 초안으로 변환합니다. 파일은 쓰지 않습니다."""
    if validate_hwpx_document_plan is None or normalize_hwpx_document_plan is None:
        raise RuntimeError("installed python-hwpx does not provide document-plan authoring")
    from .markdown_plan import markdown_to_document_plan as _build_markdown_document_plan

    converted = _build_markdown_document_plan(
        markdown or "",
        title=title,
        metadata=metadata or {},
        style_preset=style_preset,
    )
    plan = converted.plan
    report = validate_hwpx_document_plan(plan)
    validation = report.to_dict()
    result: dict[str, Any] = {
        "ok": report.ok,
        "can_create": report.ok,
        "document_plan": plan,
        "validation": validation,
        "warnings": list(converted.warnings),
        "next_tool": "create_document_from_plan" if report.ok else "markdown_to_document_plan",
    }
    if report.ok:
        result["normalizedPlan"] = normalize_hwpx_document_plan(plan).to_dict()
    else:
        result["next_action"] = (
            "repair Markdown or document_plan using validation.repairHints, then rerun markdown_to_document_plan"
        )
    return result


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


def create_document_from_plan(
    filename: str,
    document_plan: dict,
    style_preset: str = "standard_korean_business",
    quality_profile: str = None,
    profile: dict = None,
    verify_render: bool = False,
    verbosity: str = "compact",
) -> dict:
    """선언형 document_plan으로 HWPX를 생성하고 즉시 저장/검증합니다.

    document_plan.metadata.document_type 이 '공문'/'보고서'/'가정통신문'이면 실제
    한컴-harvest 프로파일로 생성됩니다. 공문은 결문 메타
    document_plan.gyeolmun = {issuer, productionNumber, enforcementDate,
    disclosure} 를 지원하고, 공문서 작성규정 구조 hard-gate 결과가
    quality.gongmun_structure(structure_pass)로 반환됩니다. 맞춤법은
    quality.korean_proofing_status(정직 'unverified', 거짓 통과 없음)로 보고합니다.
    출력은 **HWPX 전용**입니다(.odt/.docx/.pdf 등 비-HWPX 미지원; 기안문 ODT는
    별도 트랙). verify_render=True 이고 Mac 한컴 오라클이 가용하면
    quality.render_checked/visual_complete 가 실제 렌더 영수증이 됩니다.
    """
    return _create_document_from_plan_impl(
        filename,
        document_plan,
        style_preset=style_preset,
        quality_profile=quality_profile,
        profile=profile,
        verify_render=verify_render,
        verbosity=verbosity,
    )


def _create_document_from_plan_impl(
    filename: str,
    document_plan: dict,
    *,
    style_preset: str = "standard_korean_business",
    quality_profile: str | dict | None = None,
    profile: dict | None = None,
    verify_render: bool = False,
    verbosity: str | None = "compact",
) -> dict:
    if (
        build_document_from_plan is None
        or inspect_authoring_document_quality is None
        or validate_hwpx_document_plan is None
    ):
        raise RuntimeError("installed python-hwpx does not provide document-plan authoring")
    # FR-011: HWPX-only output. Non-HWPX formats (ODT/기안문, docx, pdf) are a
    # separate track — never silently attempt them.
    suffix = Path(filename).suffix.lower()
    if suffix and suffix != ".hwpx":
        return {
            "filename": filename,
            "created": False,
            "error": f"unsupported output format {suffix!r}; only .hwpx is supported",
            "handoff_status": "unsupported_format",
            "next_action": "use a .hwpx filename (ODT 기안문 등 비-HWPX 포맷은 별도 트랙)",
        }
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
        verify_render=verify_render,
    )
    result = {
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
    return _apply_write_verbosity(result, verbosity)


def create_government_report_document(
    filename: str,
    document_plan: dict,
    profile: dict = None,
    verbosity: str = "compact",
) -> dict:
    """정부보고서 프리셋으로 document_plan을 생성하고 즉시 저장/검증합니다."""
    return _create_document_from_plan_impl(
        filename,
        document_plan,
        style_preset="government_report",
        quality_profile="government_report",
        profile=profile,
        verbosity=verbosity,
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


def inspect_mail_merge_placeholders(filename: str) -> dict:
    """메일머지 템플릿의 placeholder key를 확인합니다."""
    if inspect_hwpx_mail_merge_placeholders is None:
        raise RuntimeError("installed python-hwpx does not provide mail merge tools")
    return inspect_hwpx_mail_merge_placeholders(resolve_path(filename))


def _mail_merge_data_source(data_rows: list | dict | None, data_filename: str | None) -> Any:
    if data_rows is not None:
        return data_rows
    if data_filename:
        return resolve_path(data_filename)
    raise ValueError("provide data_rows or data_filename")


def _mail_merge_open_safety_summary(report: dict) -> dict:
    row_reports = list(report.get("rows") or [])
    checked = 0
    failures: list[dict[str, Any]] = []
    for row in row_reports:
        open_safety = row.get("openSafety")
        if not isinstance(open_safety, dict):
            continue
        checked += 1
        if not bool(open_safety.get("ok")):
            failures.append(
                {
                    "rowIndex": row.get("rowIndex"),
                    "filename": row.get("filename"),
                    "summary": open_safety.get("summary"),
                }
            )
    return {
        "ok": checked == int(report.get("createdCount", 0)) and not failures,
        "checkedCount": checked,
        "failureCount": len(failures),
        "failures": failures,
    }


def mail_merge(
    template_filename: str,
    data_rows: list | dict = None,
    data_filename: str = None,
    output_dir: str = None,
    filename_pattern: str = "{index:03d}.hwpx",
    zip_filename: str = None,
    strict: bool = False,
    split_newlines: bool = True,
    fit_mode: str | None = None,
    max_lines: int = 1,
) -> dict:
    """템플릿 HWPX와 CSV/JSON/XLSX(명부)/rows 데이터로 N부를 생성합니다.

    ``fit_mode`` (keep·wrap·shrink·wrap_then_shrink·expand_row·truncate_with_report·
    fail_on_overflow)를 주면 **fit-aware** 배치가 됩니다: 각 placeholder 슬롯을 템플릿에서
    한 번 측정(template-once-measure)하고, 슬롯을 넘치거나 필수값이 빠진 레코드를
    ``needsReview[]``/``skipped[]`` 로 격리합니다(나머지 배치는 안전). zip/openSafety
    evidence 동봉.
    """
    if build_hwpx_mail_merge is None:
        raise RuntimeError("installed python-hwpx does not provide mail merge tools")
    fit_policy = None
    if fit_mode:
        from hwpx.form_fit import FitMode, FitPolicy

        valid_modes = set(getattr(FitMode, "__args__", ()))
        if valid_modes and fit_mode not in valid_modes:
            raise ValueError(
                f"unknown fit_mode {fit_mode!r}; expected one of {sorted(valid_modes)}"
            )
        fit_policy = FitPolicy(mode=fit_mode, max_lines=max_lines)
    data_source = _mail_merge_data_source(data_rows, data_filename)
    report = build_hwpx_mail_merge(
        resolve_path(template_filename),
        data_source,
        output_dir=resolve_path(output_dir) if output_dir else None,
        filename_pattern=filename_pattern,
        zip_path=resolve_path(zip_filename) if zip_filename else None,
        strict=strict,
        split_newlines=split_newlines,
        fit_policy=fit_policy,
        max_lines=max_lines,
    )
    open_safety = _mail_merge_open_safety_summary(report)
    report["openSafety"] = open_safety
    report["verification"] = {
        "openSafety": open_safety,
        "createdCount": report.get("createdCount", 0),
        "rowCount": report.get("rowCount", 0),
        "rowsWithIssues": report.get("rowsWithIssues", []),
        "zip": report.get("zip"),
    }
    return report


def table_compute(
    table: dict | list,
    value_columns: list = None,
    operations: list = None,
    append: str = "rows",
    group_by: str | int = None,
    label_column: str | int = None,
    labels: dict = None,
) -> dict:
    """일반 표에 합계·평균·소계 행/열을 추가하고 계산 근거를 반환합니다."""
    if build_hwpx_table_compute is None:
        raise RuntimeError("installed python-hwpx does not provide table compute tools")
    return build_hwpx_table_compute(
        table,
        value_columns=value_columns,
        operations=operations,
        append=append,
        group_by=group_by,
        label_column=label_column,
        labels=labels,
    )


def extract_style_profile(filename: str) -> dict:
    """참조 HWPX의 페이지·폰트·표 프로파일을 plan 적용용 JSON으로 추출합니다."""
    if extract_hwpx_style_profile is None:
        raise RuntimeError("installed python-hwpx does not provide style profile tools")
    return extract_hwpx_style_profile(resolve_path(filename))


def apply_style_profile_to_plan(
    document_plan: dict,
    style_profile: dict = None,
    reference_filename: str = None,
    overwrite: bool = True,
) -> dict:
    """style_profile 또는 reference HWPX 서식을 document_plan v2에 적용합니다."""
    if apply_hwpx_style_profile_to_plan is None:
        raise RuntimeError("installed python-hwpx does not provide style profile tools")
    profile = style_profile
    if profile is None:
        if not reference_filename:
            raise ValueError("provide style_profile or reference_filename")
        if extract_hwpx_style_profile is None:
            raise RuntimeError("installed python-hwpx does not provide style profile extraction")
        profile = extract_hwpx_style_profile(resolve_path(reference_filename))
    return {
        "document_plan": apply_hwpx_style_profile_to_plan(
            document_plan or {},
            profile,
            overwrite=overwrite,
        ),
        "style_profile": profile,
        "next_tool": "create_document_from_plan",
    }


def compare_style_profiles(
    reference_filename: str = None,
    candidate_filename: str = None,
    reference_profile: dict = None,
    candidate_profile: dict = None,
    margin_tolerance_mm: float = 1.0,
    table_weight_tolerance: float = 0.10,
) -> dict:
    """참조/후보 HWPX 또는 style_profile의 페이지·표 프로파일 유사성을 비교합니다."""
    if compare_hwpx_style_profiles is None:
        raise RuntimeError("installed python-hwpx does not provide style profile comparison")
    reference = reference_profile or (resolve_path(reference_filename) if reference_filename else None)
    candidate = candidate_profile or (resolve_path(candidate_filename) if candidate_filename else None)
    if reference is None or candidate is None:
        raise ValueError("provide reference/candidate filenames or profiles")
    return compare_hwpx_style_profiles(
        reference,
        candidate,
        margin_tolerance_mm=margin_tolerance_mm,
        table_weight_tolerance=table_weight_tolerance,
    )


def register_template(
    name: str,
    source_filename: str,
    registry_path: str = None,
    description: str = "",
    tags: list[str] = None,
) -> dict:
    """사용자 템플릿을 등록하고 style profile과 placeholder contract를 저장합니다."""
    if register_hwpx_template is None:
        raise RuntimeError("installed python-hwpx does not provide template registry tools")
    return register_hwpx_template(
        name,
        resolve_path(source_filename),
        registry_path=resolve_path(registry_path) if registry_path else None,
        description=description,
        tags=tags or [],
    )


def list_templates(registry_path: str = None) -> dict:
    """등록된 템플릿 목록을 반환합니다."""
    if list_hwpx_templates is None:
        raise RuntimeError("installed python-hwpx does not provide template registry tools")
    return list_hwpx_templates(registry_path=resolve_path(registry_path) if registry_path else None)


def describe_template(
    name: str,
    registry_path: str = None,
    values: dict = None,
) -> dict:
    """등록 템플릿 상세와 placeholder 미충전 리포트를 반환합니다."""
    if describe_hwpx_template is None:
        raise RuntimeError("installed python-hwpx does not provide template registry tools")
    return describe_hwpx_template(
        name,
        registry_path=resolve_path(registry_path) if registry_path else None,
        values=values or {},
    )


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


def inspect_official_document_style(
    filename: str = None,
    paragraphs: list[str] = None,
    document_plan: dict = None,
) -> dict:
    """공문서 작성규정 lint를 실행하고 위반별 수정 제안을 반환합니다."""
    if inspect_hwpx_official_document_style is None:
        raise RuntimeError("installed python-hwpx does not provide official-document lint")
    if filename:
        path = resolve_path(filename)
        return _with_document_state(inspect_hwpx_official_document_style(path), path)
    if document_plan is not None:
        return inspect_hwpx_official_document_style(document_plan or {})
    if paragraphs is not None:
        return inspect_hwpx_official_document_style(paragraphs or [])
    raise ValueError("filename, document_plan, or paragraphs is required")


def _single_block_plan(block: dict, *, title: str = "") -> dict:
    return {
        "schemaVersion": "hwpx.document_plan.v2",
        "title": title,
        "sections": [{"blocks": [block]}],
    }


def build_image_grid(
    images: list,
    columns: int = 2,
    image_width_mm: float = None,
    title: str = "사진대지",
) -> dict:
    """사진 목록을 plan v2 image_grid block과 생성 가능한 document_plan으로 변환합니다."""
    if build_hwpx_image_grid is None:
        raise RuntimeError("installed python-hwpx does not provide image_grid generator")
    block = build_hwpx_image_grid(
        images or [],
        columns=columns,
        image_width_mm=image_width_mm,
    )
    return {
        "block": block,
        "document_plan": _single_block_plan(block, title=title),
        "next_tool": "create_document_from_plan",
    }


def build_meeting_nameplates(
    names: list[str],
    size: str = "150x70",
    columns: int = 2,
    title: str = "회의 명패",
) -> dict:
    """참석자 명단을 회의 명패 table block과 document_plan으로 변환합니다."""
    if build_hwpx_meeting_nameplates is None:
        raise RuntimeError("installed python-hwpx does not provide meeting nameplate generator")
    block = build_hwpx_meeting_nameplates(names or [], size=size, columns=columns)
    return {
        "block": block,
        "document_plan": _single_block_plan(block, title=title),
        "next_tool": "create_document_from_plan",
    }


def build_organization_chart(
    hierarchy: dict | list,
    max_depth: int = 3,
    title: str = "조직도",
) -> dict:
    """2~3단 계층 데이터를 표 기반 조직도 block과 document_plan으로 변환합니다."""
    if build_hwpx_organization_chart is None:
        raise RuntimeError("installed python-hwpx does not provide organization chart generator")
    block = build_hwpx_organization_chart(hierarchy or {}, max_depth=max_depth)
    return {
        "block": block,
        "document_plan": _single_block_plan(block, title=title),
        "next_tool": "create_document_from_plan",
    }


def _diff_sources(
    *,
    old_filename: str | None = None,
    new_filename: str | None = None,
    old_paragraphs: list[str] | None = None,
    new_paragraphs: list[str] | None = None,
) -> tuple[Any, Any]:
    if old_filename and new_filename:
        return resolve_path(old_filename), resolve_path(new_filename)
    if old_paragraphs is not None and new_paragraphs is not None:
        return old_paragraphs, new_paragraphs
    raise ValueError("provide old_filename/new_filename or old_paragraphs/new_paragraphs")


def doc_diff(
    old_filename: str = None,
    new_filename: str = None,
    old_paragraphs: list[str] = None,
    new_paragraphs: list[str] = None,
) -> dict:
    """두 문서 또는 문단 목록의 LCS 기반 신구 paragraph diff를 반환합니다."""
    if build_hwpx_doc_diff is None:
        raise RuntimeError("installed python-hwpx does not provide doc_diff")
    old_source, new_source = _diff_sources(
        old_filename=old_filename,
        new_filename=new_filename,
        old_paragraphs=old_paragraphs,
        new_paragraphs=new_paragraphs,
    )
    return build_hwpx_doc_diff(old_source, new_source)


def create_comparison_table_document(
    filename: str,
    old_filename: str = None,
    new_filename: str = None,
    old_paragraphs: list[str] = None,
    new_paragraphs: list[str] = None,
    title: str = "신구대조표",
    include_equal: bool = True,
    verbosity: str = "compact",
) -> dict:
    """두 문서/문단을 좌우 신구대조표 HWPX로 생성하고 검증합니다."""
    if build_hwpx_comparison_table_plan is None or build_document_from_plan is None or validate_hwpx_document_plan is None:
        raise RuntimeError("installed python-hwpx does not provide comparison table generation")
    old_source, new_source = _diff_sources(
        old_filename=old_filename,
        new_filename=new_filename,
        old_paragraphs=old_paragraphs,
        new_paragraphs=new_paragraphs,
    )
    document_plan = build_hwpx_comparison_table_plan(
        old_source,
        new_source,
        title=title,
        include_equal=include_equal,
    )
    validation = validate_hwpx_document_plan(document_plan)
    if not validation.ok:
        return {
            "filename": filename,
            "created": False,
            "error": "comparison table plan failed validation",
            "plan_validation": validation.to_dict(),
        }
    path = resolve_path(filename)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc = build_document_from_plan(document_plan, preset="government_report")
    try:
        verification = _save_generated_document(doc, path)
    finally:
        doc.close()
    result = {
        "filename": filename,
        "created": True,
        "document_plan": document_plan,
        "plan_validation": validation.to_dict(),
        "verification": verification,
        "openSafety": verification.get("openSafety"),
    }
    return _apply_write_verbosity(result, verbosity)


def inspect_reference_consistency(
    filename: str = None,
    paragraphs: list[str] = None,
    document_plan: dict = None,
) -> dict:
    """붙임 참조와 표/그림 번호 연속성의 의미 수준 정합성을 검사합니다."""
    if inspect_hwpx_reference_consistency is None:
        raise RuntimeError("installed python-hwpx does not provide reference consistency lint")
    if filename:
        path = resolve_path(filename)
        return _with_document_state(inspect_hwpx_reference_consistency(path), path)
    if document_plan is not None:
        return inspect_hwpx_reference_consistency(document_plan or {})
    if paragraphs is not None:
        return inspect_hwpx_reference_consistency(paragraphs or [])
    raise ValueError("filename, document_plan, or paragraphs is required")


def _template_formfit_baseline_arg(baseline: dict | str) -> dict | str:
    if isinstance(baseline, dict):
        return baseline
    text = str(baseline or "").strip()
    if text.endswith(".json") or Path(text).exists():
        return resolve_path(text)
    return text


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


def create_proposal_document(
    filename: str,
    proposal_spec: dict,
    style_preset: str = "clean_korean_proposal",
    verbosity: str = "compact",
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
    result = {
        "filename": filename,
        "created": True,
        "style_preset": style_preset,
        "quality": report,
        "verification": verification,
    }
    return _apply_write_verbosity(result, verbosity)


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


def get_document_info(filename: str) -> dict:
    """문서 메타데이터와 구조 요약을 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    file_size = Path(path).stat().st_size
    return _with_document_state({
        "filename": filename,
        "sections": len(doc.sections),
        "paragraphs": _paragraph_count(doc),
        "tables": _table_count(doc),
        "file_size": str(file_size),
    }, path)


def get_document_text(filename: str, max_chars: int | None = None, mask: bool = True) -> dict:
    """문서 전체 텍스트를 조회합니다. (기본: 기계검증 PII 마스킹 ON — `mask=False`로 원본)"""
    path = resolve_path(filename)
    doc = open_doc(path)
    text = _mask_pii_text(collect_full_text(doc), mask)
    return _with_document_state(truncate_response(text, max_chars=max_chars), path)


def scan_personal_info(filename: str | None = None, text: str | None = None) -> dict:
    """문서/텍스트의 개인정보(PII)를 탐지하는 read-only 감사 (원본값 미노출).

    기계검증 세트(주민등록번호·휴대폰·이메일·카드)는 항상 high-confidence, 맥락형(계좌·주소·이름)은
    라벨 게이트 low-confidence. 반환은 유형별 건수 + **마스킹된 예시만** — 원본 PII는 절대 반환하지 않습니다.
    마스킹은 따로 하지 않고 이 도구는 탐지/감사만 합니다(마스킹은 fill/merge/extract 도구의 `mask` 기본 ON).
    """
    if text is None:
        if not filename:
            return {"error": "filename 또는 text 중 하나가 필요합니다."}
        text = collect_full_text(open_doc(resolve_path(filename)))
    spans = detect_pii(text or "", DEFAULT_POLICY)
    by_type: dict[str, dict] = {}
    for span in spans:
        kind = span["type"]
        bucket = by_type.setdefault(
            kind, {"type": kind, "confidence": span["confidence"], "count": 0, "maskedExamples": []}
        )
        bucket["count"] += 1
        if len(bucket["maskedExamples"]) < 3:
            bucket["maskedExamples"].append(mask_value(span["value"], kind, DEFAULT_POLICY))
    return {
        "report_version": "pii-scan-v1",
        "total": len(spans),
        "byType": list(by_type.values()),
        "machineSet": ["rrn", "phone", "email", "card"],
        "note": "기계세트=high-confidence 항상 탐지; 맥락형(account/address/name)=라벨게이트 low-confidence. 원본값 미반환(마스킹 예시만).",
    }


def get_document_outline(filename: str) -> dict:
    """문단 기준 제목/개요 구조를 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    outline: list[dict] = []
    style_levels = outline_style_levels(doc)
    for index, para in enumerate(doc.paragraphs):
        text = (para.text or "").strip()
        level = _paragraph_outline_level(para, text, style_levels)
        if level > 0 and text:
            outline.append(
                {
                    "level": level,
                    "text": _normalize_heading_text(text),
                    "paragraph_index": index,
                }
            )
    return _with_document_state({"outline": outline}, path)


def _summary_table_map(path) -> dict:
    """Compact per-table map (FR-006) via python-hwpx table_summary — bounded."""
    try:
        from hwpx.table_patch import table_summary
    except Exception as exc:  # pragma: no cover - dependency compatibility
        return {"tables": [], "count": 0, "note": f"summary unavailable: {exc}"}
    tables = table_summary(path)
    return {"tables": tables, "count": len(tables), "detail": "summary"}


def get_document_map(
    filename: str,
    max_preview_chars: int = 80,
    detail: str = "full",
) -> dict:
    """문서 개요, 표, 양식 필드, 앵커를 한 번에 조회합니다.

    detail="summary" (FR-006): 표는 셀 덤프 없이 표당 {tableIndex, rows, cols,
    merges, heading, firstRow} 요약만 반환 — 37표 양식도 토큰한도 내 1콜. 헤딩은
    apply_table_ops/fill_cells의 tableAnchor로 그대로 쓸 수 있는 텍스트.
    detail="full"(기본)은 종전대로 셀 단위 표 지도를 반환한다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    model = _build_read_model(doc)
    preview_limit = max(0, int(max_preview_chars))
    summary_mode = str(detail).lower() == "summary"

    paragraph_anchors = []
    for item in model["items"]:
        if item.get("type") not in {"heading", "paragraph"}:
            continue
        text = str(item.get("text") or "")
        paragraph_index = item.get("paragraph_index")
        paragraph_anchors.append(
            {
                "kind": item.get("type"),
                "paragraphIndex": paragraph_index,
                "textPreview": text[:preview_limit],
                "anchor": {
                    "kind": "body_paragraph",
                    "paragraphIndex": paragraph_index,
                },
            }
        )

    table_anchors = [
        {
            "kind": "table",
            "tableIndex": table.get("table_index"),
            "paragraphIndex": table.get("paragraph_index"),
            "rows": table.get("rows"),
            "cols": table.get("cols"),
            "anchor": {
                "kind": "table",
                "tableIndex": table.get("table_index"),
                "paragraphIndex": table.get("paragraph_index"),
            },
        }
        for table in model["tables"]
    ]

    try:
        form_fields = _OPS.list_form_fields(path)
    except Exception as exc:  # pragma: no cover - diagnostic fallback
        form_fields = {"fields": [], "error": str(exc)}

    result = {
        "filename": filename,
        "info": {
            "sections": len(doc.sections),
            "paragraphs": _paragraph_count(doc),
            "tables": _table_count(doc),
        },
        "outline": model["toc"],
        "sections": [
            {
                "index": section.get("index"),
                "title": section.get("title"),
                "level": section.get("level"),
                "paragraphCount": len(section.get("paragraphs") or []),
                "tableCount": len(section.get("tables") or []),
                "figureCount": len(section.get("figures") or []),
            }
            for section in model["sections"]
        ],
        "tables": _summary_table_map(path) if summary_mode else get_table_map_in_doc(doc),
        "formFields": form_fields,
        "anchors": {
            "paragraphs": paragraph_anchors,
            "tables": table_anchors,
            "figures": model["figures"],
        },
        "sourceTools": [
            "get_document_info",
            "get_document_outline",
            "get_table_map",
            "list_form_fields",
            "hwpx_extract_json",
        ],
    }
    return _with_document_state(result, path)


def hwpx_to_markdown(
    hwpx_base64: str | None = None,
    url: str | None = None,
    output: str = "full",
    chunk_strategy: str = "section",
    max_chars_per_chunk: int | None = None,
    mask: bool = True,
) -> dict:
    """HWPX payload 또는 URL을 Markdown으로 변환합니다. (기본: 기계검증 PII 마스킹 ON)"""
    mode = _normalize_output_mode(output)
    strategy = _normalize_chunk_strategy(chunk_strategy)
    chunk_size = _resolve_chunk_size(max_chars_per_chunk)

    doc, source_meta = _open_hwpx_from_payload(hwpx_base64, url)
    model = _build_read_model(doc)
    markdown = _mask_pii_text(_render_markdown(model), mask)
    markdown = _append_notes_markdown(markdown, model.get("notes", []), mask)

    result: dict[str, Any] = {
        "markdown": markdown,
        "meta": _build_conversion_meta(model, source_meta),
    }
    if mode == "chunks":
        result["chunks"] = _deep_mask_pii(_markdown_chunks(
            model,
            chunk_strategy=strategy,
            max_chars_per_chunk=chunk_size,
        ), mask)
        result["meta"]["chunk_strategy"] = strategy
        result["meta"]["max_chars_per_chunk"] = chunk_size
    return result


def document_to_markdown(
    filename: str,
    output: str = "full",
    chunk_strategy: str = "section",
    max_chars_per_chunk: int | None = None,
    mask: bool = True,
) -> dict:
    """로컬 문서를 Markdown으로 변환합니다. 현재 HWPX는 python-hwpx ingest 엔진으로 처리합니다."""
    mode = _normalize_output_mode(output)
    strategy = _normalize_chunk_strategy(chunk_strategy)
    chunk_size = _resolve_chunk_size(max_chars_per_chunk)

    try:
        path, ingest_result = _ingest_local_document(filename)
    except Exception as exc:
        return _document_ingest_error_payload(exc, filename)

    markdown = _mask_pii_text(ingest_result.markdown, mask)
    payload: dict[str, Any] = {
        "ok": True,
        "filename": str(path),
        "markdown": markdown,
        "meta": _document_ingest_meta(ingest_result),
        "warnings": list(getattr(ingest_result, "warnings", []) or []),
        "attempts": _attempts_payload(ingest_result),
    }
    if mode == "chunks":
        payload["chunks"] = _deep_mask_pii(
            _ingest_markdown_chunks(markdown, max_chars_per_chunk=chunk_size),
            mask,
        )
        payload["meta"]["chunk_strategy"] = strategy
        payload["meta"]["max_chars_per_chunk"] = chunk_size
    return payload


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


def render_preview(
    filename: str,
    output_dir: str | None = None,
    mode: str = "pages",
    screenshot: str = "auto",
    max_pages: int | None = None,
    embed_images: bool = True,
    max_image_bytes: int | None = None,
) -> mcp_types.CallToolResult:
    """레이아웃 충실 HTML과 headless browser PNG 프리뷰 산출물을 생성합니다.

    embed_images 가 참이면 각 페이지 PNG 를 인라인 이미지 콘텐츠 블록으로 함께
    반환해 (한컴/ComputerUse 없이) 모델이 레이아웃을 직접 볼 수 있습니다. 구조화
    매니페스트(JSON)는 structuredContent 로 그대로 유지됩니다.
    """
    manifest = _OPS.render_preview(
        path=filename,
        output_dir=output_dir,
        mode=mode,
        screenshot=screenshot,
        max_pages=max_pages,
        embed_images=embed_images,
        max_image_bytes=max_image_bytes,
    )

    images: list[mcp_types.ImageContent] = []
    for shot in manifest.get("screenshots", []):
        data = shot.pop("imageBase64", None)
        mime = shot.pop("imageMime", "image/png")
        if data:
            images.append(mcp_types.ImageContent(type="image", data=data, mimeType=mime))
            shot["imageEmbedded"] = True
        elif embed_images:
            shot["imageEmbedded"] = False

    content: list[mcp_types.ContentBlock] = [
        mcp_types.TextContent(
            type="text", text=json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    ]
    content.extend(images)
    return mcp_types.CallToolResult(
        content=content,
        structuredContent=manifest,
        isError=False,
    )


def hwpx_extract_json(
    hwpx_base64: str | None = None,
    url: str | None = None,
    output: str = "full",
    chunk_strategy: str = "section",
    max_chars_per_chunk: int | None = None,
    format_detail: bool = False,
    mask: bool = True,
) -> dict:
    """HWPX payload 또는 URL에서 구조화된 JSON을 추출합니다. (기본: 기계검증 PII 마스킹 ON)"""
    mode = _normalize_output_mode(output)
    strategy = _normalize_chunk_strategy(chunk_strategy)
    chunk_size = _resolve_chunk_size(max_chars_per_chunk)

    doc, source_meta = _open_hwpx_from_payload(hwpx_base64, url)
    model = _build_read_model(doc, format_detail=bool(format_detail))
    doc_payload = _deep_mask_pii({
        "title": model["title"],
        "toc": model["toc"],
        "sections": model["sections"],
        "tables": model["tables"],
        "figures": model["figures"],
        "notes": model.get("notes", []),
    }, mask)
    result: dict[str, Any] = {
        "doc": doc_payload,
        "meta": _build_conversion_meta(model, source_meta),
    }
    if format_detail:
        result["meta"]["format_detail"] = True
    if mode == "chunks":
        result["chunks"] = _deep_mask_pii(_json_chunks(
            model,
            chunk_strategy=strategy,
            max_chars_per_chunk=chunk_size,
        ), mask)
        result["meta"]["chunk_strategy"] = strategy
        result["meta"]["max_chars_per_chunk"] = chunk_size
    return result


def document_extract_json(
    filename: str,
    output: str = "full",
    chunk_strategy: str = "section",
    max_chars_per_chunk: int | None = None,
    format_detail: bool = False,
    mask: bool = True,
) -> dict:
    """로컬 문서에서 Markdown과 구조화된 JSON을 함께 추출합니다. 현재 HWPX ingest를 우선 사용합니다."""
    del format_detail
    mode = _normalize_output_mode(output)
    strategy = _normalize_chunk_strategy(chunk_strategy)
    chunk_size = _resolve_chunk_size(max_chars_per_chunk)

    try:
        path, ingest_result = _ingest_local_document(filename)
    except Exception as exc:
        return _document_ingest_error_payload(exc, filename)

    markdown = _mask_pii_text(ingest_result.markdown, mask)
    doc_payload = _deep_mask_pii(
        {
            "title": getattr(ingest_result, "title", None),
            "markdown": markdown,
            "sections": getattr(ingest_result, "sections", []) or [],
            "tables": getattr(ingest_result, "tables", []) or [],
            "metadata": getattr(ingest_result, "metadata", {}) or {},
        },
        mask,
    )
    payload: dict[str, Any] = {
        "ok": True,
        "filename": str(path),
        "doc": doc_payload,
        "meta": _document_ingest_meta(ingest_result),
        "warnings": list(getattr(ingest_result, "warnings", []) or []),
        "attempts": _attempts_payload(ingest_result),
    }
    if mode == "chunks":
        payload["chunks"] = _deep_mask_pii(
            _ingest_markdown_chunks(markdown, max_chars_per_chunk=chunk_size),
            mask,
        )
        payload["meta"]["chunk_strategy"] = strategy
        payload["meta"]["max_chars_per_chunk"] = chunk_size
    return payload


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
    return _with_document_state(result, path)


def get_location_text(filename: str, location: dict[str, Any]) -> dict:
    """get_table_map/find_text가 반환한 location으로 텍스트를 조회합니다."""
    return get_paragraph_text(filename, location=location)


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
    return _with_document_state({"paragraphs": picked, "truncated": truncated}, path)


def find_text(filename: str, text_to_find: str, match_case: bool = True, max_results: int = 50) -> dict:
    """문서에서 텍스트를 검색합니다. 원본은 수정하지 않습니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    return _with_document_state(
        find_in_doc(doc, text_to_find=text_to_find, match_case=match_case, max_results=max_results),
        path,
    )


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


def search_and_replace(
    filename: str,
    find_text: str,
    replace_text: str,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """문서에서 텍스트를 치환합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("search_and_replace", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "find_text": find_text,
            "replace_text": replace_text,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    replaced_count = replace_in_doc(doc, find_text=find_text, replace_text=replace_text)
    result = {"replaced_count": replaced_count, "find_text": find_text, "replace_text": replace_text}
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification(result, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification(result, verification),
    )


def batch_replace(
    filename: str,
    replacements: list[dict[str, str]],
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """여러 치환 규칙을 순서대로 적용합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("batch_replace", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "replacements": replacements,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    result = batch_replace_in_doc(doc, replacements)
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification(result, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification(result, verification),
    )


def apply_edits(
    filename: str,
    operations: list[dict[str, Any]],
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
    quality: dict[str, Any] | str | None = None,
) -> dict:
    """여러 편집 operation을 원자적으로 적용합니다. 실패 시 원본 파일은 변경하지 않습니다.

    ``quality``는 저장 게이트 정책입니다(생략 시 transparent = 열림안전만). ``"strict"``
    또는 ``{"mode":"strict","overflowPolicy":"fail","layoutLint":"strict"}`` 처럼 올리면
    SavePipeline이 FormFit/레이아웃/시각 게이트를 적용하고, 실패 시 저장을 보류하며
    ``visualComplete`` 블록과 구조화된 오류 코드를 반환합니다.
    """
    path = resolve_path(filename)
    scope = _idempotency_scope("apply_edits", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "operations": operations,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
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
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification(result, doc, path, quality=quality),
        )
    verification = _save_doc_verification(doc, path, quality=quality)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification(result, verification),
    )


def undo_last_edit(filename: str) -> dict:
    """마지막 저장 전 .bak 백업과 현재 문서를 교체해 직전 편집을 되돌립니다."""
    path = resolve_path(filename)
    return undo_last_backup(path)


def byte_preserving_patch(
    filename: str,
    patches: list[dict[str, Any]],
    output: str | None = None,
) -> dict:
    """section XML 바이트 splice 기반 문단 텍스트 패치를 적용합니다.

    바이트 보존 fast path: python-hwpx의 ``patch`` → SavePipeline(open-safety)로 게이트되고
    capability handshake로 fail-closed 됩니다. 단, 바이트를 보존하므로 전체 재렌더(VisualComplete
    render) 게이트는 적용되지 않습니다(설계상 카브아웃).
    """
    if hwpx_paragraph_patch is None:
        raise RuntimeError("installed python-hwpx does not provide hwpx.patch.paragraph_patch")
    quality_contract.assert_write_capability()  # fail-closed on capability skew
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


def apply_table_ops(
    filename: str,
    ops: list[dict[str, Any]],
    output: str | None = None,
    render_check: str = "off",
    dry_run: bool = False,
) -> dict:
    """바이트 보존으로 표 셀/행/열/표 구조 연산을 원자 적용합니다."""

    if not dry_run:
        quality_contract.assert_write_capability()
    return _OPS.apply_table_ops(
        resolve_path(filename),
        ops,
        output=resolve_path(output) if output else None,
        render_check=render_check,
        dry_run=dry_run,
    )


def verify_form_fill(
    filename: str,
    before_path: str,
    require: bool = False,
) -> dict:
    """실제 한컴 렌더로 양식 채움의 넘침·겹침·페이지 변화를 검증합니다."""

    return _OPS.verify_form_fill(
        resolve_path(filename),
        resolve_path(before_path),
        require=require,
    )


def score_form_fill(
    filename: str,
    gold_path: str,
    blank_path: str,
    run_render: bool = True,
    expected_pages: int | None = None,
) -> dict:
    """채움본을 gold/blank와 비교해 렌더·서식·구조·내용·규정 5축으로 채점합니다."""

    return _OPS.score_form_fill(
        resolve_path(filename),
        resolve_path(gold_path),
        resolve_path(blank_path),
        run_render=run_render,
        expected_pages=expected_pages,
    )


def apply_body_ops(
    filename: str,
    ops: list[dict[str, Any]],
    output: str | None = None,
    dry_run: bool = False,
) -> dict:
    """표 밖 본문 문단에 바이트 보존 연산을 적용합니다."""

    if not dry_run:
        quality_contract.assert_write_capability()
    return _OPS.apply_body_ops(
        resolve_path(filename),
        ops,
        output=resolve_path(output) if output else None,
        dry_run=dry_run,
    )


def inspect_fill_residue(
    filename: str,
    blank_path: str | None = None,
) -> dict:
    """채움본의 삭제색 안내문·미수정 샘플·placeholder 잔존을 검사합니다."""

    return _OPS.inspect_fill_residue(
        resolve_path(filename),
        blank_path=resolve_path(blank_path) if blank_path else None,
    )


def scan_form_guidance(filename: str, max_items: int = 60) -> dict:
    """처음 보는 양식의 색 범례·삭제/수정 후보·빈 셀·질문을 비변형 정찰합니다."""

    return _OPS.scan_form_guidance(resolve_path(filename), max_items=max_items)


def apply_evalplan_fill(
    filename: str,
    review_md: str,
    output: str | None = None,
    render_check: str = "off",
    score_gold_path: str | None = None,
    expected_pages: int | None = None,
) -> dict:
    """빈 평가계획 양식과 검토용 Markdown을 바이트 보존 채움본으로 만듭니다."""

    quality_contract.assert_write_capability()
    return _OPS.apply_evalplan_fill(
        resolve_path(filename),
        resolve_path(review_md),
        output=resolve_path(output) if output else None,
        render_check=render_check,
        score_gold_path=resolve_path(score_gold_path) if score_gold_path else None,
        expected_pages=expected_pages,
    )


_REDLINE_RECEIPT_FIELDS = (
    "changeCount",
    "marksLinked",
    "displayEnabled",
    "opensClean",
    "render_checked",
)


class _RedlineVerificationError(RuntimeError):
    def __init__(self, failures: list[dict[str, Any]], receipt: dict[str, Any]) -> None:
        self.failures = failures
        self.receipt = receipt
        messages = "; ".join(str(item.get("message", "")) for item in failures if item.get("message"))
        super().__init__(messages or "redline verification failed")


def _tracked_edit_format_error(
    filename: str,
    *,
    field: str,
    role: str,
    dry_run: bool,
) -> dict[str, Any] | None:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix == ".hwpx":
        return None
    return {
        "ok": False,
        "applied": False,
        "dryRun": dry_run,
        field: filename,
        "error": f"unsupported {role} format {suffix!r}; only .hwpx is supported",
        "handoff_status": "unsupported_format",
        "next_action": "use a .hwpx filename (ODT 기안문 등 비-HWPX 포맷은 별도 트랙)",
    }


def _tracked_paragraph_text(paragraph: Any) -> str:
    value = getattr(paragraph, "text", "")
    return value if isinstance(value, str) else str(value or "")


def _tracked_edit_error(
    index: int,
    code: str,
    message: str,
    *,
    edit: Any,
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "editIndex": index,
        "code": code,
        "message": message,
        "edit": edit,
    }
    payload.update(extra)
    return payload


def _tracked_edit_paragraph_index(edit: dict[str, Any]) -> int:
    raw = edit.get("paragraph_index", edit.get("paragraphIndex"))
    if isinstance(raw, bool):
        raise ValueError("paragraph_index must be an integer")
    return int(raw)


def _validate_tracked_edits(edits: Any, paragraphs: list[Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []
    paragraph_count = len(paragraphs)

    if not isinstance(edits, list):
        return {
            "ok": False,
            "errors": [
                {
                    "code": "invalid_edits",
                    "message": "edits must be a list",
                    "edit": edits,
                }
            ],
            "previews": [],
            "wouldChangeCount": 0,
        }
    if not edits:
        return {
            "ok": False,
            "errors": [
                {
                    "code": "empty_edits",
                    "message": "edits must contain at least one tracked edit",
                    "edit": edits,
                }
            ],
            "previews": [],
            "wouldChangeCount": 0,
        }

    for index, edit in enumerate(edits):
        edit_error_count = len(errors)
        if not isinstance(edit, dict):
            errors.append(
                _tracked_edit_error(
                    index,
                    "invalid_edit",
                    "each edit must be an object",
                    edit=edit,
                )
            )
            continue

        edit_type = str(edit.get("type", edit.get("op", ""))).strip().lower()
        try:
            paragraph_index = _tracked_edit_paragraph_index(edit)
        except (TypeError, ValueError):
            errors.append(
                _tracked_edit_error(
                    index,
                    "invalid_paragraph_index",
                    "paragraph_index must be an integer",
                    edit=edit,
                )
            )
            continue

        paragraph_text = ""
        if paragraph_index < 0 or paragraph_index >= paragraph_count:
            errors.append(
                _tracked_edit_error(
                    index,
                    "paragraph_index_out_of_range",
                    f"paragraph_index {paragraph_index} is outside 0..{max(paragraph_count - 1, 0)}",
                    edit=edit,
                    paragraphIndex=paragraph_index,
                    paragraphCount=paragraph_count,
                )
            )
        else:
            paragraph_text = _tracked_paragraph_text(paragraphs[paragraph_index])

        change_count = 0
        if edit_type == "insert":
            text = edit.get("text")
            if not isinstance(text, str) or text == "":
                errors.append(
                    _tracked_edit_error(
                        index,
                        "insert_text_required",
                        "insert edits require non-empty text",
                        edit=edit,
                    )
                )
            change_count = 1
        elif edit_type == "delete":
            match = edit.get("match")
            if match is not None and (not isinstance(match, str) or match == ""):
                errors.append(
                    _tracked_edit_error(
                        index,
                        "delete_match_invalid",
                        "delete match must be a non-empty string when provided",
                        edit=edit,
                    )
                )
            elif paragraph_index >= 0 and paragraph_index < paragraph_count:
                if match is None and not paragraph_text:
                    errors.append(
                        _tracked_edit_error(
                            index,
                            "delete_target_empty",
                            "delete edits without match require a paragraph with text",
                            edit=edit,
                            paragraphIndex=paragraph_index,
                        )
                    )
                elif match is not None and match not in paragraph_text:
                    errors.append(
                        _tracked_edit_error(
                            index,
                            "delete_match_not_found",
                            "delete match text was not found in the paragraph; no output was written",
                            edit=edit,
                            paragraphIndex=paragraph_index,
                            match=match,
                            warning=True,
                        )
                    )
            change_count = 1
        elif edit_type == "replace":
            old = edit.get("old")
            new = edit.get("new")
            if not isinstance(old, str) or old == "":
                errors.append(
                    _tracked_edit_error(
                        index,
                        "replace_old_required",
                        "replace edits require non-empty old text",
                        edit=edit,
                    )
                )
            elif paragraph_index >= 0 and paragraph_index < paragraph_count and old not in paragraph_text:
                errors.append(
                    _tracked_edit_error(
                        index,
                        "replace_old_not_found",
                        "replace old text was not found in the paragraph; no output was written",
                        edit=edit,
                        paragraphIndex=paragraph_index,
                        old=old,
                    )
                )
            if not isinstance(new, str) or new == "":
                errors.append(
                    _tracked_edit_error(
                        index,
                        "replace_new_required",
                        "replace edits require non-empty new text",
                        edit=edit,
                    )
                )
            change_count = 2
        else:
            errors.append(
                _tracked_edit_error(
                    index,
                    "unknown_edit_type",
                    "edit type must be one of: insert, delete, replace",
                    edit=edit,
                    type=edit_type,
                )
            )

        if len(errors) == edit_error_count:
            previews.append(
                {
                    "editIndex": index,
                    "type": edit_type,
                    "paragraphIndex": paragraph_index,
                    "targetParagraphText": paragraph_text,
                    "changeCount": change_count,
                }
            )

    return {
        "ok": not errors,
        "errors": errors,
        "previews": previews,
        "wouldChangeCount": sum(item["changeCount"] for item in previews),
    }


def _normalize_tracked_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _redline_receipt(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report.get(key) for key in _REDLINE_RECEIPT_FIELDS}


def _redline_receipt_failures(
    report: dict[str, Any],
    *,
    expected_change_count: int,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    actual_change_count = int(report.get("changeCount") or 0)
    if actual_change_count < expected_change_count:
        failures.append(
            {
                "code": "redline_change_count_mismatch",
                "message": (
                    f"verify_redline reported {actual_change_count} changes; "
                    f"expected at least {expected_change_count}"
                ),
                "expectedChangeCount": expected_change_count,
                "actualChangeCount": actual_change_count,
            }
        )
    if report.get("marksLinked") is not True:
        failures.append(
            {
                "code": "redline_marks_unlinked",
                "message": "verify_redline reported unlinked body/header track-change marks",
            }
        )
    if report.get("displayEnabled") is not True:
        failures.append(
            {
                "code": "redline_display_disabled",
                "message": "verify_redline reported track-change display is not enabled",
            }
        )
    if report.get("opensClean") is False:
        failures.append(
            {
                "code": "redline_does_not_open_clean",
                "message": "verify_redline reported the destination does not open cleanly",
            }
        )
    return failures


def _save_verified_redline_document(
    doc: Any,
    *,
    source_path: str,
    destination_path: str,
    expected_change_count: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if verify_hwpx_redline is None:
        raise RuntimeError("installed python-hwpx does not provide hwpx.tools.redline.verify_redline")

    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=".hwpx",
        dir=str(destination.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        os.close(fd)
        save_report = quality_contract.save_through_pipeline(doc, tmp_path)
        verification = build_hwpx_verification_report(tmp_path)
        if not verification["openSafety"]["ok"]:
            raise RuntimeError(
                "tracked-edit HWPX failed open-safety verification: "
                + verification["openSafety"]["summary"]
            )
        redline_report = verify_hwpx_redline(source_path, tmp_path)
        failures = _redline_receipt_failures(
            redline_report,
            expected_change_count=expected_change_count,
        )
        if failures:
            raise _RedlineVerificationError(failures, redline_report)
        os.replace(tmp_path, destination)
        verification["filePath"] = str(destination)
        verification["visualComplete"] = quality_contract.visual_complete_block(save_report)
        return verification, redline_report
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def add_tracked_edit(
    source_filename: str,
    destination_filename: str,
    edits: list[dict[str, Any]],
    author: str = "AI Agent",
    date: Any = None,
    dry_run: bool = False,
) -> dict:
    """원본 HWPX를 보존하고 destination에 추적 삽입/삭제/치환(redline)을 작성합니다."""
    source_path = resolve_path(source_filename)
    guard = _tracked_edit_format_error(
        source_filename,
        field="source_filename",
        role="source",
        dry_run=dry_run,
    )
    if guard is not None:
        return guard

    destination_path = resolve_path(destination_filename)
    guard = _tracked_edit_format_error(
        destination_filename,
        field="destination_filename",
        role="output",
        dry_run=dry_run,
    )
    if guard is not None:
        return guard

    if Path(source_path).resolve(strict=False) == Path(destination_path).resolve(strict=False):
        return {
            "ok": False,
            "applied": False,
            "dryRun": dry_run,
            "error": "add_tracked_edit refuses source-in-place edits; destination_filename must differ from source",
            "reason": "source equals destination",
            "source": {"filename": source_filename, "path": source_path},
            "destination": {"filename": destination_filename, "path": destination_path},
            "errors": [
                {
                    "code": "source_destination_same",
                    "message": "destination_filename must differ from source_filename",
                }
            ],
        }

    validation_doc = open_doc(source_path)
    try:
        validation = _validate_tracked_edits(edits, list(getattr(validation_doc, "paragraphs", [])))
    finally:
        validation_doc.close()

    if not validation["ok"]:
        return {
            "ok": False,
            "applied": False,
            "dryRun": dry_run,
            "error": "tracked edit validation failed",
            "handoff_status": "blocked",
            "source": {"filename": source_filename, "path": source_path},
            "destination": {"filename": destination_filename, "path": destination_path},
            "errors": validation["errors"],
            "editPreviews": validation["previews"],
            "written": False,
        }

    if dry_run:
        return {
            "ok": True,
            "applied": False,
            "dryRun": True,
            "handoff_status": "dry_run",
            "source": {"filename": source_filename, "path": source_path},
            "destination": {
                "filename": destination_filename,
                "path": destination_path,
                "exists": Path(destination_path).exists(),
            },
            "editsValidated": len(validation["previews"]),
            "wouldChangeCount": validation["wouldChangeCount"],
            "wouldWrite": True,
            "written": False,
            "editPreviews": validation["previews"],
        }

    normalized_date = _normalize_tracked_date(date)
    doc = open_doc(source_path)
    edit_results: list[dict[str, Any]] = []
    try:
        for index, edit in enumerate(edits):
            edit_type = str(edit.get("type", edit.get("op", ""))).strip().lower()
            paragraph_index = _tracked_edit_paragraph_index(edit)
            paragraph = doc.paragraphs[paragraph_index]
            before_text = _tracked_paragraph_text(paragraph)
            if edit_type == "insert":
                change_id = doc.add_tracked_insert(
                    paragraph,
                    edit["text"],
                    author=author,
                    date=normalized_date,
                )
                change_ids = [change_id]
            elif edit_type == "delete":
                change_id = doc.add_tracked_delete(
                    paragraph,
                    match=edit.get("match"),
                    author=author,
                    date=normalized_date,
                )
                change_ids = [change_id]
            elif edit_type == "replace":
                replace_ids = doc.add_tracked_replace(
                    paragraph,
                    edit["old"],
                    edit["new"],
                    author=author,
                    date=normalized_date,
                )
                change_ids = list(replace_ids)
            else:  # pragma: no cover - validation prevents this branch
                raise ValueError(f"unsupported tracked edit type: {edit_type}")
            edit_results.append(
                {
                    "editIndex": index,
                    "type": edit_type,
                    "paragraphIndex": paragraph_index,
                    "beforeText": before_text,
                    "changeIds": change_ids,
                    "changeCount": len(change_ids),
                }
            )
    except Exception as exc:
        return {
            "ok": False,
            "applied": False,
            "dryRun": False,
            "error": "tracked edit application failed before save",
            "handoff_status": "blocked",
            "source": {"filename": source_filename, "path": source_path},
            "destination": {"filename": destination_filename, "path": destination_path},
            "errors": [
                {
                    "code": "tracked_edit_apply_failed",
                    "message": str(exc),
                    "failedOperationIndex": len(edit_results),
                }
            ],
            "editResults": edit_results,
            "written": False,
        }

    expected_change_count = sum(item["changeCount"] for item in edit_results)
    try:
        verification, redline_report = _save_verified_redline_document(
            doc,
            source_path=source_path,
            destination_path=destination_path,
            expected_change_count=expected_change_count,
        )
    except _RedlineVerificationError as exc:
        return {
            "ok": False,
            "applied": False,
            "dryRun": False,
            "error": "redline verification failed; destination was not written",
            "handoff_status": "blocked",
            "source": {"filename": source_filename, "path": source_path},
            "destination": {"filename": destination_filename, "path": destination_path},
            "errors": exc.failures,
            "editResults": edit_results,
            "redlineReceipt": _redline_receipt(exc.receipt),
            "redlineVerification": exc.receipt,
            "written": False,
        }
    finally:
        doc.close()

    return {
        "ok": True,
        "applied": True,
        "dryRun": False,
        "handoff_status": "ready",
        "source": {"filename": source_filename, "path": source_path},
        "destination": {"filename": destination_filename, "path": destination_path},
        "author": author,
        "date": normalized_date,
        "editsApplied": len(edit_results),
        "changeCount": expected_change_count,
        "editResults": edit_results,
        "redlineReceipt": _redline_receipt(redline_report),
        "redlineVerification": redline_report,
        "verification": verification,
        "openSafety": verification.get("openSafety"),
        "visualComplete": verification.get("visualComplete"),
        "written": True,
    }


# ── M7: 네이티브 자동 차례 / 상호참조 (S-062) ─────────────────────────
def _toc_format_guard(filename: str, path: str) -> dict | None:
    if str(path).lower().endswith((".hwpx", ".hwpxml")):
        return None
    return {
        "ok": False,
        "error": "native TOC/cross-reference tools support HWPX only",
        "filename": filename,
        "path": path,
        "errors": [{"code": "unsupported_format", "message": "HWPX(.hwpx)만 지원합니다"}],
    }


def add_toc(
    filename: str,
    level: int = 2,
    leader: int = 3,
    hyperlink: bool = False,
    at_index: int = 0,
    dry_run: bool = False,
) -> dict:
    """개요 스타일 제목들로 한컴 네이티브 차례(TABLEOFCONTENTS 필드)를 삽입합니다.

    고정 텍스트 목차가 아니라 한컴이 인식·재계산하는 필드입니다. dirty=1로
    방출되므로 한컴이 처음 여는 순간 항목·스타일·쪽번호를 스스로 재계산합니다
    (방출 시점의 쪽번호는 추정치 — 응답의 cachedPagesAreEstimates 참조).
    본문 문단은 본문(스타일 1) 등 비수집 스타일이어야 항목으로 끌려가지
    않습니다(바탕글=스타일 0은 수집 대상)."""
    from hwpx.tools.toc_author import add_native_toc

    path = resolve_path(filename)
    guard = _toc_format_guard(filename, path)
    if guard is not None:
        return guard
    doc = open_doc(path)
    try:
        summary = add_native_toc(
            doc, at_index=at_index, level=level, leader=leader, hyperlink=hyperlink
        )
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "hint": "개요 1~10 스타일의 제목 문단이 필요합니다 (add_heading 사용)",
            "errors": [{"code": "no_outline_headings", "message": str(exc)}],
        }
    if dry_run:
        return _with_dry_run_verification({"ok": True, "dryRun": True, **summary}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"ok": True, **summary}, verification)


def add_cross_reference(
    filename: str,
    paragraph_index: int,
    target_heading_text: str,
    cached_page: int = 1,
    dry_run: bool = False,
) -> dict:
    """지정 문단 끝에 특정 제목의 '쪽 번호' 상호참조(CROSSREF 필드)를 추가합니다.

    캐시 쪽번호는 추정치여도 됩니다 — 한컴이 문서를 열거나 편집/저장할 때
    자동으로 재계산합니다(실측 의미론)."""
    from hwpx.tools.toc_author import add_page_crossref

    path = resolve_path(filename)
    guard = _toc_format_guard(filename, path)
    if guard is not None:
        return guard
    doc = open_doc(path)
    paragraphs = [p for s in doc.sections for p in s.paragraphs]
    if not (0 <= paragraph_index < len(paragraphs)):
        return {
            "ok": False,
            "error": f"paragraph_index {paragraph_index} out of range (total {len(paragraphs)})",
            "errors": [{"code": "paragraph_index_out_of_range", "message": str(paragraph_index)}],
        }
    needle = target_heading_text.strip()
    target = next((p for p in paragraphs if (p.text or "").strip() == needle), None)
    if target is None:
        target = next((p for p in paragraphs if needle and needle in (p.text or "")), None)
    if target is None:
        return {
            "ok": False,
            "error": f"target heading not found: {target_heading_text!r}",
            "errors": [{"code": "target_not_found", "message": target_heading_text}],
        }
    result = add_page_crossref(doc, paragraphs[paragraph_index], target, cached_page=cached_page)
    if dry_run:
        return _with_dry_run_verification({"ok": True, "dryRun": True, **result}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"ok": True, **result}, verification)


def verify_toc(
    filename: str,
    refresh: bool = False,
    verify_render: bool = False,
) -> dict:
    """네이티브 차례/상호참조의 캐시 쪽번호를 검증합니다.

    verify_render=True면 실제 한컴 렌더로 캐시 vs 실제 페이지를 대조해
    toc_correctness_ratio를 산출합니다(오라클 없으면 정직하게 unverified).
    refresh=True면 검증 전에 한컴을 열어 dirty 필드를 재계산·저장합니다
    (macOS GUI 오라클 필요; dirty 재생성 직후 같은 세션 렌더는 이 한컴
    빌드가 크래시하므로 refresh와 render는 별도 세션으로 수행됩니다)."""
    from hwpx.tools.toc_fidelity import structural_report, toc_verify

    path = resolve_path(filename)
    guard = _toc_format_guard(filename, path)
    if guard is not None:
        return guard

    oracle = None
    refreshed = None
    if refresh or verify_render:
        try:
            from hwpx.visual.oracle import resolve_oracle

            oracle = resolve_oracle()
        except Exception:  # pragma: no cover - oracle stack unavailable
            oracle = None
    if refresh:
        refresher = getattr(oracle, "refresh_document", None)
        refreshed = bool(refresher and refresher(path))

    if verify_render and oracle is not None and oracle.available():
        report = toc_verify(path, oracle=oracle)
    else:
        structural = structural_report(path)
        report = {
            "structural": structural,
            "render_checked": False,
            "toc_correctness_ratio": None,
            "stale_entries": [],
            "crossref_correctness_ratio": None,
            "verdict": (
                "stale_detected_structurally"
                if not structural["internally_consistent"]
                else "unverified"
            ),
        }
    if refreshed is not None:
        report["refreshed"] = refreshed
    report["ok"] = report["verdict"] in ("verified", "unverified")
    return report


def add_heading(
    filename: str,
    text: str,
    level: int = 1,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """문서 끝에 제목 문단을 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("add_heading", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "text": text,
            "level": level,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    idx = add_heading_to_doc(doc, text, level)
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification({"paragraph_index": idx}, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification({"paragraph_index": idx}, verification),
    )


def add_paragraph(
    filename: str,
    text: str,
    style: str | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """문서 끝에 문단을 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("add_paragraph", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "text": text,
            "style": style,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    idx = add_paragraph_to_doc(doc, text, style)
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification({"paragraph_index": idx}, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification({"paragraph_index": idx}, verification),
    )


def insert_paragraph(
    filename: str,
    paragraph_index: int,
    text: str,
    style: str | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """지정 위치 앞에 문단을 삽입합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("insert_paragraph", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "paragraph_index": paragraph_index,
            "text": text,
            "style": style,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    idx = insert_paragraph_to_doc(doc, paragraph_index, text, style)
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification({"inserted_index": idx}, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification({"inserted_index": idx}, verification),
    )


def delete_paragraph(
    filename: str,
    paragraph_index: int,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """지정 문단을 삭제합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("delete_paragraph", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "paragraph_index": paragraph_index,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    remaining = delete_paragraph_from_doc(doc, paragraph_index)
    result = {"deleted_index": paragraph_index, "remaining_paragraphs": remaining}
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification(result, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification(result, verification),
    )


def add_table(
    filename: str,
    rows: int,
    cols: int,
    data: list[list[str]] = None,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """문서 끝에 표를 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("add_table", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "rows": rows,
            "cols": cols,
            "data": data,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    idx = add_table_to_doc(doc, rows, cols, data)
    if dry_run:
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification({"table_index": idx}, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification({"table_index": idx}, verification),
    )


def get_table_text(filename: str, table_index: int = 0) -> dict:
    """표 셀 텍스트를 2D 배열로 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    result = get_table_data(doc, table_index)
    return _with_document_state({
        "table_index": table_index,
        "rows": result["rows"],
        "cols": result["cols"],
        "data": result["data"],
    }, path)


def get_table_map(filename: str) -> dict:
    """문서 내 표 위치, 크기, 문맥 요약을 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    return _with_document_state(get_table_map_in_doc(doc), path)


def _capability_block(tool_surface_skew: bool, surface_details: list[str]) -> dict:
    """Core/mcp/plugin capability handshake (plan §2 Phase F).

    Versions + a fingerprint hash + skew. Writes fail closed on a *version* skew
    (the SavePipeline gate would otherwise be unavailable). Tool-surface skew is
    part of the installed capability verdict and is never reported healthy.
    """

    state = quality_contract.capability_state()
    skew = list(state["skew"])
    if tool_surface_skew:
        detail = "; ".join(surface_details) or "ToolSpec mismatch"
        skew.append(f"MCP tool surface skew: {detail}")
    fail_closed = quality_contract.fail_closed_enabled()
    return {
        "handshake": "hwpx.capability.v1",
        "versions": state["versions"],
        "minPythonHwpx": state["minPythonHwpx"],
        "minMcpVersion": state["minMcpVersion"],
        "minSkillVersion": state["minSkillVersion"],
        "savePipelineAvailable": state["savePipelineAvailable"],
        "hash": state["hash"],
        "toolContractHash": state["toolContractHash"],
        "skew": skew,
        "ok": not skew,
        "failClosed": fail_closed,
        "writesBlocked": fail_closed and bool(skew),
        "diagnosis": (
            "Capability handshake OK; every write funnels through the SavePipeline gate."
            if not skew
            else "Capability skew: install the contract-required core/MCP/plugin versions and restart the host."
        ),
    }


def describe_capabilities(domain: str | None = None) -> dict:
    """이 HWPX 툴킷이 무엇을 할 수 있는지 작업 종류별로 요약한 능력 지도를 반환합니다.

    도구 등록을 단일 ToolSpec 계약에서 생성하므로 실제 FastMCP 표면과
    capability map이 항상 같은 진실을 반영합니다. 이 도구를 한 번
    부르면 작업군(읽기·양식채움·생성·편집·표·서식·차례·PII·레드라인·시험지·직인·
    대량생산·메모·검증·패키지)별 intent + 언제 쓰는지 + 진입점 도구가 나옵니다.
    domain 인자로 한 작업군 상세만 필터할 수 있습니다(예: domain="form_fill").
    처음 이 서버로 HWPX 작업을 시작하는 에이전트는 이걸 먼저 부르면 오리엔테이션이
    됩니다. coverage에 등록 도구 대비 미매핑이 표시되면 그건 이 지도의 드리프트입니다."""
    from .capabilities import build_capability_report, coverage_against

    advanced = _ACTIVE_ADVANCED
    report = build_capability_report(domain if domain else None, advanced=advanced)
    live = set(_fastmcp_tool_names())
    report["toolCount"] = len(live)
    report["coverage"] = coverage_against(live, advanced=advanced)
    return report


def mcp_server_health() -> dict:
    """MCP 서버 transport와 timeout/keepalive 점검 정보를 반환합니다."""
    transport = os.environ.get("HWPX_MCP_TRANSPORT", "stdio")
    sandbox_root = os.environ.get("HWPX_MCP_SANDBOX_ROOT")
    advanced = _ACTIVE_ADVANCED
    fastmcp_tool_names = set(_fastmcp_tool_names())
    legacy_tool_names = _legacy_tool_names()
    expected = expected_tool_names(advanced=advanced)
    missing_expected = sorted(expected - fastmcp_tool_names)
    unexpected_registered = sorted(fastmcp_tool_names - expected)
    missing_required = sorted(skill_required_tool_names() - fastmcp_tool_names)
    skew_detected = bool(missing_expected or unexpected_registered or missing_required)
    surface_details = []
    if missing_expected:
        surface_details.append(f"missing expected: {', '.join(missing_expected)}")
    if unexpected_registered:
        surface_details.append(f"unexpected registered: {', '.join(unexpected_registered)}")
    if missing_required:
        surface_details.append(f"missing skill-required: {', '.join(missing_required)}")
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
            "profile": "advanced" if advanced else "default",
            "contractHash": tool_contract_hash(),
            "expectedFastMcpToolCount": len(expected),
            "actualFastMcpToolCount": len(fastmcp_tool_names),
            "actualLegacyToolCount": len(legacy_tool_names),
            "missingExpectedTools": missing_expected,
            "unexpectedRegisteredTools": unexpected_registered,
            "missingSkillRequiredTools": missing_required,
            # Compatibility alias for older skill startup checks.
            "missingKeyTools": missing_required,
            "keyTools": sorted(skill_required_tool_names()),
            "diagnosis": (
                "Installed MCP surface is missing expected tools; reinstall the hwpx plugin, "
                "remove stale plugin venv/cache, then start a fresh host session."
                if skew_detected
                else "Installed MCP surface exactly matches the active ToolSpec contract."
            ),
        },
        "capability": _capability_block(skew_detected, surface_details),
        "unitPolicy": {
            "status": "audited",
            "fontSize": "points",
            "paragraphLineSpacing": "percent",
            "paragraphIndent": "millimeters",
            "paragraphSpacing": "points",
            "pageSizeAndMargins": "millimeters",
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


def find_cell_by_label(filename: str, label_text: str, direction: str = "right") -> dict:
    """양식 문서에서 라벨 기준 인접 셀 후보를 조회합니다. direction: right 또는 down."""
    path = resolve_path(filename)
    doc = open_doc(path)
    safe_direction = _normalize_table_label_direction(direction)
    return _with_document_state(find_cell_by_label_in_doc(doc, label_text, direction=safe_direction), path)


def fill_by_path(
    filename: str,
    mappings: dict[str, str],
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """라벨 경로 문법으로 셀을 채웁니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("fill_by_path", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "mappings": mappings,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    result = fill_by_path_in_doc(doc, _normalize_fill_mappings(mappings))
    if result.get("applied_count", 0) > 0:
        if dry_run:
            return _idempotency_store(
                scope,
                fingerprint=fingerprint,
                payload=_with_dry_run_verification(result, doc, path),
            )
        verification = _save_doc_verification(doc, path)
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_save_verification(result, verification),
        )
    result["dryRun"] = dry_run
    return _idempotency_store(scope, fingerprint=fingerprint, payload=result)


def set_table_cell_text(
    filename: str,
    table_index: int,
    row: int,
    col: int,
    text: str,
    preserve_format: bool = True,
    split_paragraphs: bool = False,
    dry_run: bool = False,
    expected_revision: str = None,
    idempotency_key: str = None,
) -> dict:
    """표 셀 텍스트를 변경합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    scope = _idempotency_scope("set_table_cell_text", path, idempotency_key)
    fingerprint = _idempotency_fingerprint(
        {
            "filename": filename,
            "table_index": table_index,
            "row": row,
            "col": col,
            "text": text,
            "preserve_format": preserve_format,
            "split_paragraphs": split_paragraphs,
            "dry_run": dry_run,
            "expected_revision": expected_revision,
        }
    )
    replay = _idempotency_replay(scope, fingerprint=fingerprint)
    if replay is not None:
        return replay
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
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
        return _idempotency_store(
            scope,
            fingerprint=fingerprint,
            payload=_with_dry_run_verification(result, doc, path),
        )
    verification = _save_doc_verification(doc, path)
    return _idempotency_store(
        scope,
        fingerprint=fingerprint,
        payload=_with_save_verification(result, verification),
    )


def add_page_break(
    filename: str,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """문서 끝에 페이지 나누기를 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    add_page_break_to_doc(doc)
    if dry_run:
        return _with_dry_run_verification({"success": True}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"success": True}, verification)


def set_paragraph_format(
    filename: str,
    paragraph_index: int | None = None,
    paragraph_indexes: list[int] | None = None,
    alignment: str | None = None,
    line_spacing_percent: float | None = None,
    indent_left_mm: float | None = None,
    indent_right_mm: float | None = None,
    first_line_indent_mm: float | None = None,
    spacing_before_pt: float | None = None,
    spacing_after_pt: float | None = None,
    outline_level: int | None = None,
    keep_with_next: bool | None = None,
    keep_lines: bool | None = None,
    page_break_before: bool | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """기존 문단의 정렬, 줄간격(%), 들여쓰기(mm), 문단 간격(pt), 개요 수준을 변경합니다.

    keep_with_next/keep_lines/page_break_before로 문단의 keep-together(다음 문단과
    붙임·문단 줄바꿈 금지·앞에서 쪽 나눔) 플래그를 설정합니다 — 시험지 조판 등에서
    한 문항이 단/쪽 경계에서 잘리지 않게 할 때 씁니다.
    """
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    result = doc.set_paragraph_format(
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
    result["filename"] = filename
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


def set_page_setup(
    filename: str,
    paper_size: str | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    orientation: str | None = None,
    margins_mm: dict[str, float] | None = None,
    margin_left_mm: float | None = None,
    margin_right_mm: float | None = None,
    margin_top_mm: float | None = None,
    margin_bottom_mm: float | None = None,
    header_margin_mm: float | None = None,
    footer_margin_mm: float | None = None,
    gutter_mm: float | None = None,
    columns: int | None = None,
    column_gap_mm: float | None = None,
    section_index: int | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """용지/방향/여백을 mm 단위로 설정하고 선택적으로 단 구성을 적용합니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    result = doc.set_page_setup(
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
    result["filename"] = filename
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


def _header_footer_payload(wrapper: Any, *, kind: str, page_type: str) -> dict[str, Any]:
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
    filename: str,
    kind: str,
    text: str | None = None,
    content: list[dict[str, Any]] | None = None,
    section_index: int | None = None,
    page_type: str = "BOTH",
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """머리글 또는 바닥글을 텍스트나 rich content spec으로 추가/수정합니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    wrapper = doc.set_header_footer(
        kind=kind,
        text=text,
        content=content,
        section_index=section_index,
        page_type=page_type,
    )
    result = {
        "filename": filename,
        "headerFooter": _header_footer_payload(wrapper, kind=kind, page_type=page_type),
    }
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


def set_page_number(
    filename: str,
    target: str = "footer",
    page_type: str = "BOTH",
    format: str = "page",
    align: str = "CENTER",
    position: str = "BOTTOM_CENTER",
    prefix: str = "",
    suffix: str = "",
    format_type: str | None = None,
    section_index: int | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """머리글/바닥글에 자동 쪽번호 필드를 추가하거나 수정합니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    wrapper = doc.set_page_number(
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
        "filename": filename,
        "target": target,
        "format": format,
        "headerFooter": _header_footer_payload(wrapper, kind=target, page_type=page_type),
    }
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


def set_list_format(
    filename: str,
    paragraph_index: int | None = None,
    paragraph_indexes: list[int] | None = None,
    kind: str = "bullet",
    level: int = 1,
    bullet_char: str | None = None,
    number_format: str | None = None,
    start: int | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """기존 문단에 불릿 또는 번호 목록 서식을 적용합니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    result = doc.set_list_format(
        paragraph_index=paragraph_index,
        paragraph_indexes=paragraph_indexes,
        kind=kind,
        level=level,
        bullet_char=bullet_char,
        number_format=number_format,
        start=start,
    )
    result["filename"] = filename
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


def insert_picture(
    filename: str,
    image_base64: str,
    image_format: str = "png",
    width: int | None = None,
    height: int | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    section_index: int | None = None,
    align: str | None = None,
    output: str | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """본문에 그림 객체를 삽입하고 BinData/manifest 참조를 함께 저장합니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    target_path = resolve_path(output) if output else path
    doc = open_doc(path)
    image_data = _decode_image_base64(image_base64)
    doc.add_picture(
        image_data,
        image_format,
        width=width,
        height=height,
        width_mm=width_mm,
        height_mm=height_mm,
        section_index=section_index,
        align=align,
    )
    picture_refs = doc.picture_references()
    result = {
        "ok": True,
        "filename": filename,
        "outputPath": target_path,
        "picture": picture_refs[-1] if picture_refs else None,
        "pictureReferences": picture_refs,
        "idIntegrity": _id_integrity_payload(doc),
    }
    if dry_run:
        return _with_dry_run_verification(result, doc, target_path)
    verification = _save_doc_verification(doc, target_path)
    return _with_save_verification(result, verification)


def replace_picture(
    filename: str,
    image_base64: str,
    image_format: str = "png",
    picture_index: int = 0,
    binary_item_id_ref: str | None = None,
    remove_orphaned: bool = True,
    output: str | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """그림 객체의 geometry를 유지하고 연결된 이미지 asset만 교체합니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    target_path = resolve_path(output) if output else path
    doc = open_doc(path)
    image_data = _decode_image_base64(image_base64)
    replacement = doc.replace_picture(
        image_data,
        image_format,
        picture_index=picture_index,
        binary_item_id_ref=binary_item_id_ref,
        remove_orphaned=remove_orphaned,
    )
    result = {
        "ok": True,
        "filename": filename,
        "outputPath": target_path,
        "replacement": replacement,
        "pictureReferences": doc.picture_references(),
        "idIntegrity": _id_integrity_payload(doc),
    }
    if dry_run:
        return _with_dry_run_verification(result, doc, target_path)
    verification = _save_doc_verification(doc, target_path)
    return _with_save_verification(result, verification)


# ------------------------------------------------------------------
# 직인/관인 placement + compliance (M2 P3 / FR-003) — oracle-bound
# ------------------------------------------------------------------


def _nearest_rect(rects: list, center: tuple[float, float]):
    cx, cy = center
    return min(
        rects,
        key=lambda r: ((r.x0 + r.x1) / 2 - cx) ** 2 + ((r.y0 + r.y1) / 2 - cy) ** 2,
    )


def _check_seal_compliance_impl(
    path: str,
    sender_text: str,
    *,
    tol_pt: float,
    expected_center: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Render *path* via the Hancom oracle and decide the 직인 rule (pass/fail).

    The seal is a picture, so it is located with ``extract_image_boxes`` (not text);
    the 발신명의 anchor is found in the same render. Degrades to
    ``renderChecked=False`` when no oracle is reachable — never a silent pass.
    """

    fd, pdf = tempfile.mkstemp(prefix="seal_verify_", suffix=".pdf")
    os.close(fd)
    try:
        boxes, _sizes, backend = render_glyph_boxes(path, out_pdf=pdf)
        seal_rects = extract_image_boxes(pdf)
    except OracleUnavailable as exc:
        return {
            "ok": False,
            "renderChecked": False,
            "note": "한컴 오라클이 없어 직인 배치를 검증할 수 없습니다.",
            "reason": str(exc),
        }
    finally:
        try:
            os.unlink(pdf)
        except OSError:
            pass

    if not seal_rects:
        return {
            "ok": False,
            "renderChecked": True,
            "note": "렌더에 직인 이미지가 없습니다 (isEmbeded 누락 또는 미배치).",
        }
    anchor = seal_ops.find_seal_anchor(boxes, sender_text)
    center = expected_center or (anchor.center if anchor is not None else None)
    seal_rect = _nearest_rect(seal_rects, center) if center is not None else seal_rects[-1]
    verdict = seal_ops.check_seal_placement(boxes, seal_rect, sender_text, tol_pt=tol_pt)
    out: dict[str, Any] = {"ok": verdict.ok, "renderChecked": True, "backend": backend}
    out.update(verdict.to_dict())
    return out


def place_seal(
    filename: str,
    sender_text: str,
    image_base64: str,
    image_format: str = "png",
    seal_width_mm: float = 25.0,
    seal_height_mm: float | None = None,
    anchor_x: float | None = None,
    anchor_y: float | None = None,
    anchor_page: int | None = None,
    verify: bool = True,
    tol_pt: float = 6.0,
    output: str | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """발신명의(issuer line) 끝글자에 직인/관인을 floating으로 찍습니다 (FR-003).

    한컴 렌더 오라클로 발신명의 위치(앵커)를 찾아 직인을 그 위에 스탬프합니다
    (textWrap=IN_FRONT_OF_TEXT — 겹친 글자를 밀지 않음). 오라클이 없으면 ``anchor_x``/
    ``anchor_y`` 로 PDF 포인트 앵커를 직접 지정할 수 있고, 둘 다 없으면
    ``renderChecked=false`` 로 정직하게 degrade 합니다(임의 배치 금지). ``verify=True``
    이면 저장 후 재렌더로 직인이 발신명의에 규칙대로 찍혔는지 검증합니다.
    """
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    target_path = resolve_path(output) if output else path
    image_data = _decode_image_base64(image_base64)

    # 1) locate the 발신명의 anchor — explicit override, else the render oracle.
    if anchor_x is not None and anchor_y is not None:
        anchor_center = (float(anchor_x), float(anchor_y))
        anchor_page_resolved = anchor_page
        anchor_source = "explicit"
    else:
        try:
            boxes, _sizes, backend = render_glyph_boxes(path)
        except OracleUnavailable as exc:
            return {
                "ok": False,
                "filename": filename,
                "renderChecked": False,
                "note": "한컴 오라클이 없어 발신명의 위치를 찾을 수 없습니다. anchor_x/anchor_y(PDF pt)로 직접 지정하세요.",
                "reason": str(exc),
            }
        anchor = seal_ops.find_seal_anchor(boxes, sender_text)
        if anchor is None:
            return {
                "ok": False,
                "filename": filename,
                "renderChecked": True,
                "note": f"발신명의 '{sender_text}'를 렌더에서 찾지 못했습니다.",
            }
        anchor_center = anchor.center
        anchor_page_resolved = anchor.glyph.page
        anchor_source = backend

    # 2) stamp the floating seal on the anchor.
    doc = open_doc(path)
    placement = seal_ops.place_seal(
        doc,
        image_data=image_data,
        image_format=image_format,
        sender_text=sender_text,
        anchor_center_pt=anchor_center,
        seal_width_mm=seal_width_mm,
        seal_height_mm=seal_height_mm,
        page=anchor_page_resolved,
    )
    if not placement.placed:
        return {
            "ok": False,
            "filename": filename,
            "placement": placement.to_dict(),
            "note": placement.note or "발신명의 문단을 찾지 못했습니다.",
        }

    result = {
        "ok": True,
        "filename": filename,
        "outputPath": target_path,
        "anchorSource": anchor_source,
        "placement": placement.to_dict(),
        "idIntegrity": _id_integrity_payload(doc),
    }
    if dry_run:
        return _with_dry_run_verification(result, doc, target_path)
    verification = _save_doc_verification(doc, target_path)
    result = _with_save_verification(result, verification)

    # 3) optional oracle re-verify against the realized seal center.
    if verify:
        expected_center = (
            (placement.horz_offset + placement.seal_width_hu / 2) / 100.0,
            (placement.vert_offset + placement.seal_height_hu / 2) / 100.0,
        )
        result["sealVerdict"] = _check_seal_compliance_impl(
            target_path, sender_text, tol_pt=tol_pt, expected_center=expected_center
        )
    return result


def check_seal_compliance(
    filename: str,
    sender_text: str,
    tol_pt: float = 6.0,
) -> dict:
    """직인이 발신명의 끝글자에 규칙대로 찍혔는지 pass/fail 검사 (FR-003).

    한컴으로 렌더해 직인 이미지의 실제 위치를 발신명의 앵커와 비교합니다(중심 tol 이내 +
    의도치 않은 글자 가림 없음). 평가자가 그대로 돌릴 수 있는 차별적 검사 — 잘 찍힌 직인은
    pass, 어긋난 직인은 fail. 오라클이 없으면 ``renderChecked=false`` 로 degrade 합니다.
    """
    path = resolve_path(filename)
    return _check_seal_compliance_impl(path, sender_text, tol_pt=tol_pt)


def compose_exam(
    form_filename: str,
    output: str,
    exam_md: str | None = None,
    exam_md_filename: str | None = None,
    max_rounds: int = 2,
    verify: bool = True,
    role_style_names: dict[str, str] | None = None,
) -> dict:
    """출제 md를 학교 양식 .hwpx에 그 양식의 기존 스타일로 재조판합니다 (S-056 시험지 조판).

    각 문항이 단/쪽 경계에서 잘리지 않게(keep-together) 하고, 관리박스·머리글/꼬리글은
    무손실 보존하며, [그림N]/[표N]/[식N]은 텍스트 placeholder로 남깁니다(사람이 그림을
    나중에 삽입). ``exam_md``(인라인 문자열) 또는 ``exam_md_filename``(경로) 중 정확히
    하나를 줍니다. ``verify=True``(기본)면 한컴 렌더로 문항-split/overflow/placeholder를
    검증하고 — 오라클이 없으면 ``renderChecked=false`` 로 정직하게 degrade — ``verify=False``
    면 렌더 없이 조판만 합니다. 한컴이 본문을 벡터 커브로 export 하는 양식은
    ``splits=null`` + ``needsReview=true`` 로 정직 보고합니다(렌더 이미지로 시각 검증 필요).
    잘못된 md/양식은 조용히 틀린 문서를 내지 않고 ``ok=false`` 로 실패합니다.
    """
    if compose_exam_into_form is None:
        return {
            "ok": False,
            "filename": form_filename,
            "renderChecked": False,
            "needsReview": True,
            "note": "이 python-hwpx 빌드에는 hwpx.exam 조판 모듈이 없습니다.",
        }
    if (exam_md is None) == (exam_md_filename is None):
        return {
            "ok": False,
            "filename": form_filename,
            "renderChecked": False,
            "needsReview": True,
            "note": "exam_md(인라인) 또는 exam_md_filename(경로) 중 정확히 하나를 지정하세요.",
        }

    form_path = resolve_path(form_filename)
    if exam_md_filename is not None:
        try:
            exam_md = Path(resolve_path(exam_md_filename)).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return {
                "ok": False,
                "filename": form_filename,
                "renderChecked": False,
                "needsReview": True,
                "error": type(exc).__name__,
                "note": f"exam_md_filename을 읽을 수 없습니다: {exc}",
            }
    out_path = resolve_path(output)
    oracle = None if verify else NullOracle()
    try:
        result = compose_exam_into_form(
            form_path,
            exam_md,
            out_path,
            oracle=oracle,
            max_rounds=max_rounds,
            role_style_names=role_style_names,
        )
    except (ExamParseError, FormProfileError) as exc:
        return {
            "ok": False,
            "filename": form_filename,
            "renderChecked": False,
            "needsReview": True,
            "error": type(exc).__name__,
            "note": str(exc),
        }

    payload = {
        "ok": True,
        "filename": form_filename,
        "outputPath": out_path,
        "renderChecked": result.render_checked,
        "splits": result.splits,
        "overflow": result.overflow,
        "placeholdersOk": result.placeholders_ok,
        "rounds": result.rounds,
        "needsReview": result.needs_review,
        "notes": list(result.notes),
    }
    payload["openSafety"] = build_hwpx_open_safety_report(Path(out_path))
    return payload


def verify_question_splits(
    filename: str,
    valid_question_numbers: list[str] | None = None,
    marker_regex: str | None = None,
) -> dict:
    """한컴 렌더로 시험지 .hwpx의 문항이 단/쪽 경계에서 잘렸는지(문항-split) 검증합니다 (S-056).

    오라클이 없으면 ``renderChecked=false`` 로 정직하게 degrade 합니다(임의 0 금지).
    한컴이 본문을 벡터 커브로 export 해 추출 텍스트에 조판 문항이 0개면(학교 원안지
    양식에서 관측) ``splits=null`` + ``needsReview=true`` 로 보고하고 렌더 이미지 기반
    시각 검증을 요구합니다. ``valid_question_numbers`` 로 측정 대상 문항을 한정하면 양식
    chrome(예: "2026." 연도)이 가짜 문항 블록을 열지 않습니다.
    """
    if measure_question_splits is None or resolve_oracle is None:
        return {
            "ok": False,
            "filename": filename,
            "renderChecked": False,
            "needsReview": True,
            "note": "이 python-hwpx 빌드에는 hwpx.exam 조판/측정 모듈이 없습니다.",
        }
    marker_re = None
    if marker_regex:
        try:
            marker_re = re.compile(marker_regex)
        except re.error as exc:
            return {
                "ok": False,
                "filename": filename,
                "renderChecked": False,
                "splits": None,
                "needsReview": True,
                "error": "InvalidRegex",
                "note": f"marker_regex가 올바른 정규식이 아닙니다: {exc}",
            }
    path = resolve_path(filename)
    oracle = resolve_oracle()
    if not oracle.available():
        return {
            "ok": True,
            "filename": filename,
            "renderChecked": False,
            "splits": None,
            "needsReview": True,
            "note": "한컴 오라클이 없어 문항 split을 측정할 수 없습니다 (renderChecked=false).",
        }
    pdf = oracle.render_pdf(path)
    if not pdf:
        return {
            "ok": False,
            "filename": filename,
            "renderChecked": False,
            "splits": None,
            "needsReview": True,
            "note": "한컴 렌더가 PDF를 생성하지 못했습니다 (renderChecked=false).",
        }
    kwargs: dict[str, Any] = {}
    if valid_question_numbers is not None:
        kwargs["valid_ids"] = {str(n) for n in valid_question_numbers}
    if marker_re is not None:
        kwargs["marker_re"] = marker_re
    report = measure_question_splits(pdf, **kwargs)
    if report.n_blocks == 0:
        return {
            "ok": True,
            "filename": filename,
            "renderChecked": True,
            "splits": None,
            "needsReview": True,
            "nBlocks": 0,
            "note": (
                "추출 가능한 텍스트 레이어에서 조판 문항을 찾지 못했습니다(벡터 커브 export 양식"
                " 추정). 문항-split은 텍스트 게이트로 검증할 수 없습니다 — 렌더 이미지로 시각"
                " 검증이 필요합니다."
            ),
        }
    return {
        "ok": True,
        "filename": filename,
        "renderChecked": True,
        "splits": report.n_splits,
        "kinds": dict(report.kinds),
        "splitIds": list(report.split_ids),
        "nBlocks": report.n_blocks,
        "nGlyphs": report.n_glyphs,
        "needsReview": report.n_splits > 0,
    }


def add_memo(
    filename: str,
    paragraph_index: int | None = None,
    text: str = "",
    location: dict[str, Any] | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """본문 문단 또는 표 셀 문단에 메모를 추가합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    result = add_memo_to_doc(doc, paragraph_index, text, location=location)
    if result["location"].get("kind") == "body_paragraph":
        result["paragraph_index"] = result["location"]["paragraph_index"]
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


def add_memo_by_anchor(
    filename: str,
    anchor: dict[str, Any] | str,
    text: str,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """find_text가 반환한 anchor로 메모 위치를 지정해 메모를 추가합니다."""
    return add_memo(
        filename,
        text=text,
        location=location_from_anchor(anchor),
        dry_run=dry_run,
        expected_revision=expected_revision,
    )


def remove_memo(
    filename: str,
    paragraph_index: int | None = None,
    location: dict[str, Any] | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """본문 문단 또는 표 셀 문단의 메모를 제거합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
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


def replace_in_paragraph(
    filename: str,
    old_text: str,
    new_text: str,
    paragraph_index: int | None = None,
    location: dict[str, Any] | None = None,
    count: int | None = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """본문/표 셀 문단 하나에서 run 서식을 유지하며 부분 텍스트를 치환합니다."""
    if old_text == "":
        raise ValueError("old_text는 빈 문자열일 수 없습니다.")
    if count is not None and count <= 0:
        return {"replaced_count": 0, "location": location or {"paragraph_index": paragraph_index}, "dryRun": dry_run}

    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
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


def replace_by_anchor(
    filename: str,
    anchor: dict[str, Any] | str,
    old_text: str,
    new_text: str,
    dry_run: bool = False,
    expected_revision: str = None,
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
            expected_revision=expected_revision,
        )

    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
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
    expected_revision: str = None,
) -> dict:
    """지정 범위 텍스트 서식을 변경합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
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


def create_custom_style(
    filename: str,
    style_name: str,
    bold: bool = None,
    italic: bool = None,
    font_size: float = None,
    font_name: str = None,
    color: str = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """문서에 커스텀 스타일을 생성합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
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


def list_styles(filename: str) -> dict:
    """문서에 정의된 스타일 목록을 조회합니다."""
    path = resolve_path(filename)
    doc = open_doc(path)
    styles = list_styles_in_doc(doc)
    return {"styles": styles, "count": len(styles)}


def merge_table_cells(
    filename: str,
    table_index: int,
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """표 셀 범위를 병합합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    merge_cells_in_table(doc, table_index, start_row, start_col, end_row, end_col)
    result = {"merged": True, "range": f"({start_row},{start_col})~({end_row},{end_col})"}
    if dry_run:
        return _with_dry_run_verification(result, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification(result, verification)


def split_table_cell(
    filename: str,
    table_index: int,
    row: int,
    col: int,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """병합된 셀을 분할합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    span_info = split_cell_in_table(doc, table_index, row, col)
    if dry_run:
        return _with_dry_run_verification({"split": True, "original_span": span_info}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"split": True, "original_span": span_info}, verification)


def format_table(
    filename: str,
    table_index: int,
    has_header_row: bool = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """표 서식을 적용합니다. dry_run=True이면 원본을 저장하지 않습니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    doc = open_doc(path)
    format_table_in_doc(doc, table_index, has_header_row=has_header_row)
    if dry_run:
        return _with_dry_run_verification({"formatted": True, "table_index": table_index}, doc, path)
    verification = _save_doc_verification(doc, path)
    return _with_save_verification({"formatted": True, "table_index": table_index}, verification)


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


def list_form_fields(filename: str) -> dict:
    """문서의 네이티브 누름틀/FORM 필드 목록과 현재 값을 반환합니다."""
    path = resolve_path(filename)
    return _with_document_state(_OPS.list_form_fields(path), path)


def fill_form_field(
    filename: str,
    value: str,
    field_index: int = None,
    field_id: str = None,
    name: str = None,
    dry_run: bool = False,
    expected_revision: str = None,
) -> dict:
    """네이티브 누름틀/FORM 필드 하나를 채우고 open-safety 검증 증거를 반환합니다."""
    path = resolve_path(filename)
    guard = _revision_guard(path, expected_revision)
    if guard is not None:
        return guard
    result = _OPS.fill_form_field(
        path,
        value=value,
        field_index=field_index,
        field_id=field_id,
        name=name,
        dry_run=dry_run,
    )
    return _with_document_state(result, path)


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


if _ACTIVE_ADVANCED:

    def package_parts(filename: str) -> dict:
        """[고급] HWPX 패키지 파트 목록을 조회합니다."""
        path = resolve_path(filename)
        return _OPS.package_parts(path)

    def package_get_xml(filename: str, part_name: str, max_chars: int = 5000) -> dict:
        """[고급] 특정 패키지 파트의 XML을 조회합니다."""
        path = resolve_path(filename)
        result = _OPS.package_get_xml(path, part_name)
        return truncate_response(result.get("xmlString", ""), max_chars=max_chars)

    def package_get_text(filename: str, part_name: str, max_chars: int = 5000) -> dict:
        """[고급] 특정 패키지 파트의 텍스트를 조회합니다."""
        path = resolve_path(filename)
        result = _OPS.package_get_text(path, part_name)
        return truncate_response(result.get("text", ""), max_chars=max_chars)

    def object_find_by_tag(filename: str, tag_name: str, max_results: int = 20) -> dict:
        """[고급] 문서 XML에서 태그를 검색합니다."""
        path = resolve_path(filename)
        return _OPS.object_find_by_tag(path, tag_name, max_results=max_results)

    def object_find_by_attr(filename: str, attr_name: str, attr_value: str = None, max_results: int = 20) -> dict:
        """[고급] 문서 XML에서 속성을 검색합니다."""
        path = resolve_path(filename)
        return _OPS.object_find_by_attr(path, None, attr_name, attr_value, max_results=max_results)

    def plan_edit(filename: str, instruction: str) -> dict:
        """[고급] instruction 기준 검증용 편집 계획을 생성합니다."""
        path = resolve_path(filename)
        operation = _build_verification_plan_operation(path, instruction)
        return _OPS.plan_edit(path=path, operations=[operation])

    def preview_edit(filename: str, plan_id: str) -> dict:
        """[고급] plan_edit 결과 미리보기를 조회합니다."""
        del filename
        return _OPS.preview_edit(plan_id=plan_id)

    def apply_edit(filename: str, plan_id: str) -> dict:
        """[고급] 검증 계획을 적용합니다. 원본 HWPX는 직접 수정하지 않습니다."""
        del filename
        return _OPS.apply_edit(plan_id=plan_id, confirm=True)

    def validate_structure(filename: str) -> dict:
        """[고급] HWPX 구조 유효성을 검사합니다."""
        return _OPS.validate_structure(resolve_path(filename))

    def lint_text_conventions(filename: str) -> dict:
        """[고급] 텍스트 규칙 위반 여부를 검사합니다."""
        return _OPS.lint_text_conventions(resolve_path(filename))


register_fastmcp_tools(mcp, globals(), advanced=_ACTIVE_ADVANCED)


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
    try:  # HTTP transport is an optional [http] extra
        import uvicorn
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "HTTP transport requires the 'uvicorn' package. Install it with: "
            "pip install 'hwpx-mcp-server[http]' (or pip install uvicorn). "
            "The default stdio transport needs no extra."
        ) from exc

    app = mcp.streamable_http_app()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
