# SPDX-License-Identifier: Apache-2.0
import os
from io import BytesIO
from pathlib import Path
from typing import Any

from ..storage import LocalDocumentStorage, require_hwpx_editor_open_safe
from ..upstream import HwpxDocument, blank_document_template_bytes, open_document


def _local_storage() -> LocalDocumentStorage:
    return LocalDocumentStorage(
        base_directory=Path.cwd(),
        auto_backup=os.environ.get("HWPX_MCP_AUTOBACKUP", "1") == "1",
    )


def open_doc(path: str) -> HwpxDocument:
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")
    require_hwpx_editor_open_safe(Path(path), role="local HWPX open")
    return open_document(path)


def save_doc(doc: HwpxDocument, path: str) -> dict[str, Any]:
    storage = _local_storage()
    target = storage.resolve_output_path(path)
    return storage.save_document(doc, target)


def create_blank(path: str, title=None, author=None) -> dict[str, Any]:
    source = BytesIO(blank_document_template_bytes())
    doc = open_document(source)
    try:
        return save_doc(doc, path)
    finally:
        doc.close()
