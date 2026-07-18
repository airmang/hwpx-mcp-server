# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..hwp_support import HwpBinaryError, extract_hwp_text
from ..core.locator import RegisteredHandle
from ..core.context import default_session_lifecycle_policy
from ..storage import DocumentStorage
from ..workspace import (
    WorkspacePathError,
)
from ..upstream import (
    HwpxDocument,
    HwpxOxmlParagraph,
    HwpxOxmlTable,
)

logger = logging.getLogger("hwpx_mcp_server.hwpx_ops")


class DocumentContext:
    def __init__(
        self,
        *,
        storage: DocumentStorage,
        paging_paragraph_limit: int,
        error_type: Callable[..., RuntimeError],
        handle_error_type: type[RuntimeError],
    ) -> None:
        self.storage = storage
        self.base_directory = storage.base_directory
        self.paging_limit = max(1, paging_paragraph_limit)
        self._registered_handles: Dict[str, RegisteredHandle] = {}
        self._error_type = error_type
        self._handle_error_type = handle_error_type

    @property
    def registered_handles(self) -> Dict[str, RegisteredHandle]:
        return self._registered_handles

    def _new_error(
        self,
        code: str,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
        hint: Optional[str] = None,
    ) -> RuntimeError:
        return self._error_type(message, code=code, details=details, hint=hint)

    def _resolve_path(self, path: str, *, must_exist: bool = True) -> Path:
        try:
            resolved = self.storage.resolve_path(path, must_exist=must_exist)
        except FileNotFoundError as exc:
            raise self._new_error(
                "DOCUMENT_NOT_FOUND",
                "요청한 문서를 허용된 작업공간에서 찾을 수 없습니다.",
                details={"requestedName": Path(path).name},
            ) from exc
        except WorkspacePathError as exc:
            raise self._new_error(
                exc.code,
                "요청한 경로가 허용된 HWPX 작업공간 경계를 벗어났습니다.",
                details=exc.safe_details(),
            ) from exc
        except PermissionError as exc:
            raise self._new_error(
                "PERMISSION_DENIED",
                "요청한 문서에 접근할 권한이 없습니다.",
                details={"requestedName": Path(path).name},
            ) from exc
        self._register_handle(path, resolved)
        return resolved

    def _make_handle_id(self, path: str, backend: Optional[str] = None) -> str:
        seed = f"{backend or 'local'}::{path}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        return f"h_{digest}"

    def _register_handle(self, path: str, resolved: Path) -> RegisteredHandle:
        relative = self._relative_path(resolved)
        handle_id = self._make_handle_id(relative)
        handle = RegisteredHandle(type="handle", handleId=handle_id, path=relative)
        self._registered_handles[handle_id] = handle
        return handle

    def list_registered_handles(self) -> List[RegisteredHandle]:
        return sorted(
            self._registered_handles.values(), key=lambda item: item.handle_id
        )

    def open_document_handle(self, path: str) -> Dict[str, Any]:
        resolved = self._resolve_path(path)
        handle = self._register_handle(path, resolved)
        return {"handle": handle.model_dump(by_alias=True)}

    def list_open_documents(self) -> Dict[str, Any]:
        policy = default_session_lifecycle_policy()
        return {
            "documents": [
                handle.model_dump(by_alias=True)
                for handle in self.list_registered_handles()
            ],
            "sessionPolicy": policy.as_dict(),
        }

    def close_document_handle(self, handle_id: str) -> Dict[str, Any]:
        removed = self._registered_handles.pop(handle_id, None)
        return {"closed": removed is not None}

    def get_registered_handle(self, handle_id: str) -> RegisteredHandle:
        handle = self._registered_handles.get(handle_id)
        if handle is None:
            raise self._handle_error_type(f"등록되지 않은 handleId입니다: {handle_id}")
        return handle

    def resolve_document_path(
        self,
        *,
        path: Optional[str] = None,
        handle_id: Optional[str] = None,
    ) -> str:
        if path:
            return path
        if handle_id:
            return self.get_registered_handle(handle_id).path
        raise self._new_error(
            "DOCUMENT_LOCATOR_REQUIRED",
            "path 또는 handleId 중 하나를 제공해야 합니다.",
        )

    def _resolve_output_path(self, path: str) -> Path:
        return self.storage.resolve_output_path(path)

    def _relative_path(self, path: Path) -> str:
        return self.storage.relative_path(path)

    def _open_document(self, path: str) -> Tuple[HwpxDocument, Path]:
        resolved = self._resolve_path(path)
        if resolved.suffix.lower() == ".hwp":
            raise self._new_error(
                "READ_ONLY_HWP_DOCUMENT",
                "HWP 파일은 편집이 불가합니다. 먼저 convert_hwp_to_hwpx 도구로 HWPX 변환 후 편집하세요.",
            )
        try:
            document, resolved = self.storage.open_document(path)
        except FileNotFoundError as exc:
            raise self._new_error(
                "DOCUMENT_NOT_FOUND",
                "요청한 문서를 허용된 작업공간에서 찾을 수 없습니다.",
                details={"requestedName": Path(path).name},
            ) from exc
        except WorkspacePathError as exc:
            raise self._new_error(
                exc.code,
                "요청한 경로가 허용된 HWPX 작업공간 경계를 벗어났습니다.",
                details=exc.safe_details(),
            ) from exc
        except PermissionError as exc:
            raise self._new_error(
                "PERMISSION_DENIED",
                "요청한 문서에 접근할 권한이 없습니다.",
                details={"requestedName": Path(path).name},
            ) from exc
        except Exception as exc:  # pragma: no cover - delegated to backend
            raise self._new_error(
                "DOCUMENT_OPEN_FAILED",
                f"failed to open '{path}': {exc}",
                details={"path": path},
            ) from exc
        return document, resolved

    def _read_only_hwp_paragraphs(self, path: str) -> Tuple[List[str], Path, str]:
        resolved = self._resolve_path(path)
        try:
            snapshot = extract_hwp_text(resolved)
        except HwpBinaryError as exc:
            raise self._new_error(
                "HWP_TEXT_EXTRACT_FAILED", f"HWP 텍스트 추출 실패: {exc}"
            ) from exc
        return snapshot.paragraphs, resolved, snapshot.source

    def _iter_paragraphs(self, document: HwpxDocument) -> List[HwpxOxmlParagraph]:
        return list(document.paragraphs)

    def _iter_tables(self, document: HwpxDocument) -> List[HwpxOxmlTable]:
        tables: List[HwpxOxmlTable] = []
        for paragraph in document.paragraphs:
            tables.extend(paragraph.tables)
        return tables
