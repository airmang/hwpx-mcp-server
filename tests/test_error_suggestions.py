# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from hwpx_mcp_server.errors import build_error_payload
from hwpx_mcp_server.hwpx_ops import HwpxOperationError


@pytest.mark.parametrize(
    "code",
    [
        "DOCUMENT_NOT_FOUND",
        "PERMISSION_DENIED",
        "DOCUMENT_LOCATOR_REQUIRED",
        "READ_ONLY_HWP_DOCUMENT",
        "DOCUMENT_OPEN_FAILED",
        "DOCUMENT_SAVE_FAILED",
        "HWP_TEXT_EXTRACT_FAILED",
        "STYLE_HEADER_MISSING",
        "STYLE_CHAR_PROPERTY_ID_MISSING",
        "STYLE_BORDER_FILL_HEADER_MISSING",
        "STYLE_ID_ALLOCATOR_MISSING",
        "REPAIR_UNAVAILABLE",
        "TABLE_INDEX_OUT_OF_RANGE",
        "TABLE_CELL_OPERATION_FAILED",
        "TABLE_CELL_INDEX_OUT_OF_RANGE",
        "TABLE_EMPTY",
        "PARAGRAPH_INDEX_OUT_OF_RANGE",
        "MEMO_NOT_FOUND",
        "SOURCE_FILE_TYPE_INVALID",
        "HWP_CONVERSION_FAILED",
        "PLAN_RECORD_MISSING",
        "PIPELINE_ERROR",
        "BYTE_PATCH_UNAVAILABLE",
        "BYTE_PATCH_OPEN_SAFETY_FAILED",
    ],
)
def test_common_error_payload_includes_human_suggestion(code: str) -> None:
    payload = build_error_payload(code=code, message="failed")

    assert payload["suggestion"]


def test_common_error_payload_uses_hint_as_unknown_code_suggestion() -> None:
    payload = build_error_payload(
        code="UNKNOWN_ERROR",
        message="failed",
        hint="문서 상태를 확인한 뒤 다시 실행하세요.",
    )

    assert payload["hint"] == "문서 상태를 확인한 뒤 다시 실행하세요."
    assert payload["suggestion"] == "문서 상태를 확인한 뒤 다시 실행하세요."


def test_pipeline_error_uses_nested_pipeline_code_suggestion() -> None:
    payload = build_error_payload(
        code="PIPELINE_ERROR",
        message="failed",
        details={"pipelineCode": "PREVIEW_REQUIRED"},
        hint="fallback",
    )

    assert "preview_edit" in payload["suggestion"]
    assert payload["suggestion"] != "fallback"


def test_hwpx_operation_error_payload_includes_suggestion() -> None:
    payload = HwpxOperationError(
        "tableIndex out of range",
        code="TABLE_INDEX_OUT_OF_RANGE",
    ).to_payload()

    assert payload["suggestion"]
