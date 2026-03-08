import os
from io import BytesIO
from pathlib import Path

from ..compat import patch_python_hwpx

patch_python_hwpx()

from hwpx.document import HwpxDocument
from hwpx.templates import blank_document_bytes
from ..storage import LocalDocumentStorage


def _local_storage() -> LocalDocumentStorage:
    return LocalDocumentStorage(
        base_directory=Path.cwd(),
        auto_backup=os.environ.get("HWPX_MCP_AUTOBACKUP", "1") == "1",
    )


def open_doc(path: str) -> HwpxDocument:
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")
    return HwpxDocument.open(path)


def save_doc(doc: HwpxDocument, path: str) -> None:
    storage = _local_storage()
    target = storage.resolve_output_path(path)
    storage.save_document(doc, target)


def create_blank(path: str, title=None, author=None) -> None:
    source = BytesIO(blank_document_bytes())
    doc = HwpxDocument.open(source)
    save_doc(doc, path)
