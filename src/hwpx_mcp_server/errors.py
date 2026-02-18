"""공통 에러 모델과 MCP 에러 변환 유틸리티."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class CommonErrorModel(BaseModel):
    """클라이언트가 일관되게 처리할 수 있는 공통 에러 페이로드."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    hint: Optional[str] = None


ERROR_CODE_TO_MCP_CODE: dict[str, int] = {
    "DOCUMENT_NOT_FOUND": -32044,
    "PERMISSION_DENIED": -32043,
    "TABLE_INDEX_OUT_OF_RANGE": -32042,
    "PARAGRAPH_INDEX_OUT_OF_RANGE": -32042,
    "PIPELINE_ERROR": -32041,
}


DEFAULT_MCP_ERROR_CODE = -32000


def mcp_code_for_error(error_code: str) -> int:
    return ERROR_CODE_TO_MCP_CODE.get(error_code, DEFAULT_MCP_ERROR_CODE)


def build_error_payload(
    *,
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    hint: Optional[str] = None,
) -> Dict[str, Any]:
    return CommonErrorModel(
        code=code,
        message=message,
        details=details,
        hint=hint,
    ).model_dump(exclude_none=True)
