"""HWP(바이너리) 파일의 읽기 전용 텍스트 추출 유틸리티."""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List

import olefile


@dataclass(slots=True)
class HwpTextSnapshot:
    """HWP 파일에서 추출한 텍스트 스냅샷."""

    paragraphs: List[str]
    source: str


class HwpBinaryError(RuntimeError):
    """HWP 바이너리 처리 과정에서 발생한 오류."""


def _decode_preview_text(payload: bytes) -> str:
    try:
        return payload.decode("utf-16-le", errors="ignore").replace("\x00", "")
    except Exception as exc:  # pragma: no cover
        raise HwpBinaryError(f"PrvText 디코딩 실패: {exc}") from exc


def _iter_bodytext_stream_names(ole: olefile.OleFileIO) -> List[List[str]]:
    names: List[List[str]] = []
    for stream_path in ole.listdir(streams=True, storages=False):
        if len(stream_path) == 2 and stream_path[0] == "BodyText" and stream_path[1].startswith("Section"):
            names.append(stream_path)
    names.sort(key=lambda parts: int(re.sub(r"[^0-9]", "", parts[1]) or "0"))
    return names


def _decode_section_payload(payload: bytes) -> str:
    try:
        inflated = zlib.decompress(payload, -15)
    except zlib.error:
        return ""
    # HWP record binary에서 사람이 읽을 수 있는 UTF-16 조각만 보수적으로 추출
    text = inflated.decode("utf-16-le", errors="ignore")
    text = text.replace("\x00", "")
    return text


def extract_hwp_text(path: Path) -> HwpTextSnapshot:
    if path.suffix.lower() != ".hwp":
        raise HwpBinaryError(".hwp 파일만 처리할 수 있습니다")

    if not olefile.isOleFile(str(path)):
        raise HwpBinaryError("유효한 OLE2(HWP) 파일이 아닙니다")

    try:
        with olefile.OleFileIO(str(path)) as ole:
            if ole.exists("PrvText"):
                raw = ole.openstream("PrvText").read()
                preview_text = _decode_preview_text(raw)
                paragraphs = [line.strip() for line in preview_text.splitlines() if line.strip()]
                return HwpTextSnapshot(paragraphs=paragraphs, source="PrvText")

            body_lines: List[str] = []
            for stream_name in _iter_bodytext_stream_names(ole):
                raw = ole.openstream(stream_name).read()
                decoded = _decode_section_payload(raw)
                for line in decoded.splitlines():
                    cleaned = line.strip()
                    if cleaned:
                        body_lines.append(cleaned)

            if body_lines:
                return HwpTextSnapshot(paragraphs=body_lines, source="BodyText")
    except OSError as exc:
        raise HwpBinaryError(f"HWP 스트림 읽기 실패: {exc}") from exc

    return HwpTextSnapshot(paragraphs=[], source="empty")
