# SPDX-License-Identifier: Apache-2.0
"""외부 라이브러리 호환성 패치."""

from __future__ import annotations

import builtins
from typing import Any
from xml.etree import ElementTree as _ET


_SUBELEMENT_PATCHED = False


def _patch_sub_element_for_lxml_parent() -> None:
    global _SUBELEMENT_PATCHED
    if _SUBELEMENT_PATCHED:
        return

    original_sub_element = _ET.SubElement

    def _safe_sub_element(parent: Any, tag: str, attrib: Any = None, **extra: Any):
        attrs = {} if attrib is None else dict(attrib)
        attrs.update(extra)
        try:
            return original_sub_element(parent, tag, attrs)
        except TypeError as exc:
            message = str(exc)
            if "lxml.etree._Element" not in message or not hasattr(parent, "makeelement"):
                raise
            child = parent.makeelement(tag, attrs)
            parent.append(child)
            return child

    _ET.SubElement = _safe_sub_element  # type: ignore[assignment]
    _SUBELEMENT_PATCHED = True


_ID_GENERATORS_PATCHED = False


def _patch_upstream_id_generators_to_signed_int32() -> None:
    """Clamp ``hwpx.oxml.document._paragraph_id`` / ``_object_id`` / ``_memo_id``
    to the signed int32 range.

    Older releases of ``python-hwpx`` mask ``uuid4().int`` with ``0xFFFFFFFF``,
    which yields values ``>= 2**31`` about half the time. Several downstream
    HWPX consumers parse the ``id`` attribute as a signed 32-bit integer and
    misinterpret those values as negative. Once the upstream library masks
    with ``0x7FFFFFFF`` this patch becomes a no-op; until then it gives
    hwpx-mcp-server users an in-range guarantee regardless of the installed
    upstream version.
    """

    global _ID_GENERATORS_PATCHED
    if _ID_GENERATORS_PATCHED:
        return

    try:
        from hwpx.oxml import document as _hwpx_document
    except ImportError:
        return

    from uuid import uuid4

    def _safe_id() -> str:
        return str(uuid4().int & 0x7FFFFFFF)

    if not hasattr(_hwpx_document, "_paragraph_id"):
        return

    _hwpx_document._paragraph_id = _safe_id  # type: ignore[attr-defined]
    if hasattr(_hwpx_document, "_object_id"):
        _hwpx_document._object_id = _safe_id  # type: ignore[attr-defined]
    if hasattr(_hwpx_document, "_memo_id"):
        _hwpx_document._memo_id = _safe_id  # type: ignore[attr-defined]
    _ID_GENERATORS_PATCHED = True


def patch_python_hwpx() -> None:
    """python-hwpx의 ET 누락/혼합 파서 호환 이슈와 id 생성 범위를 보정합니다."""
    if not hasattr(builtins, "ET"):
        builtins.ET = _ET
    _patch_sub_element_for_lxml_parent()
    _patch_upstream_id_generators_to_signed_int32()
