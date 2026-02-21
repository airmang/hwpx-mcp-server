import os
import shutil
from io import BytesIO

from ..compat import patch_python_hwpx

patch_python_hwpx()

from hwpx.document import HwpxDocument
from hwpx.templates import blank_document_bytes

AUTOBACKUP = os.environ.get("HWPX_MCP_AUTOBACKUP", "1") == "1"


def open_doc(path: str) -> HwpxDocument:
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")
    return HwpxDocument.open(path)


def save_doc(doc: HwpxDocument, path: str) -> None:
    if AUTOBACKUP and os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    doc.save(path)


def create_blank(path: str, title=None, author=None) -> None:
    source = BytesIO(blank_document_bytes())
    doc = HwpxDocument.open(source)
    doc.save(path)
