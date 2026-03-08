"""Text formatting helpers backed by python-hwpx character/style definitions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from xml.etree import ElementTree as ET

from hwpx.document import HwpxDocument, HwpxOxmlParagraph, HwpxOxmlRun
from hwpx.oxml.namespaces import HH as HH_NS, HP as HP_NS

from ..compat import patch_python_hwpx

patch_python_hwpx()

_FONT_REF_KEYS = (
    "hangul",
    "latin",
    "hanja",
    "japanese",
    "other",
    "symbol",
    "user",
)
_FONT_FACE_LANGS = {
    "HANGUL": "hangul",
    "LATIN": "latin",
    "HANJA": "hanja",
    "JAPANESE": "japanese",
    "OTHER": "other",
    "SYMBOL": "symbol",
    "USER": "user",
}
_DEFAULT_TEXT_COLOR = "#000000"
_DEFAULT_CHAR_HEIGHT = "1000"


def _append_child(parent: ET.Element, tag: str, attrs: dict[str, str] | None = None) -> ET.Element:
    payload = dict(attrs or {})
    try:
        return ET.SubElement(parent, tag, payload)
    except TypeError:
        maker = getattr(parent, "makeelement", None)
        if not callable(maker):
            raise
        child = maker(tag, payload)
        parent.append(child)
        return child


def _iter_unique_styles(doc: HwpxDocument):
    seen: set[str] = set()
    for style in doc.styles.values():
        raw_id = getattr(style, "raw_id", None)
        if not raw_id and getattr(style, "id", None) is not None:
            raw_id = str(style.id)
        if not raw_id or raw_id in seen:
            continue
        seen.add(raw_id)
        yield style


def _normalize_color(color: str | None) -> str | None:
    if color is None:
        return None
    value = color.strip()
    if not value:
        return None
    if not value.startswith("#"):
        value = "#" + value
    if len(value) != 7 or any(ch not in "0123456789abcdefABCDEF#" for ch in value):
        raise ValueError("color must be a 6-digit hexadecimal value")
    return value.upper()


def _normalize_font_size(font_size: float | None) -> str | None:
    if font_size is None:
        return None
    if font_size <= 0:
        raise ValueError("font_size must be greater than 0")
    return str(int(round(font_size * 100)))


def _run_style_flags(run_style: Any) -> tuple[bool, bool, bool]:
    if run_style is None:
        return False, False, False
    children = getattr(run_style, "child_attributes", {})
    underline_attrs = children.get("underline")
    underline = False
    if underline_attrs is not None:
        underline = underline_attrs.get("type", "").upper() != "NONE"
    return "bold" in children, "italic" in children, underline


def _run_style_height(run_style: Any) -> str | None:
    if run_style is None:
        return None
    return getattr(run_style, "attributes", {}).get("height")


def _run_style_font_refs(run_style: Any) -> dict[str, str]:
    if run_style is None:
        return {}
    refs = dict(getattr(run_style, "child_attributes", {}).get("fontRef", {}))
    return {key: str(value) for key, value in refs.items() if key in _FONT_REF_KEYS}


def _element_flags(element: ET.Element) -> tuple[bool, bool, bool]:
    underline_node = element.find(f"{HH_NS}underline")
    underline = False
    if underline_node is not None:
        underline = underline_node.get("type", "").upper() != "NONE"
    return (
        element.find(f"{HH_NS}bold") is not None,
        element.find(f"{HH_NS}italic") is not None,
        underline,
    )


def _element_font_refs(element: ET.Element) -> dict[str, str]:
    font_ref = element.find(f"{HH_NS}fontRef")
    if font_ref is None:
        return {}
    return {
        key: str(value)
        for key, value in font_ref.attrib.items()
        if key in _FONT_REF_KEYS
    }


def _char_style_matches(
    run_style: Any,
    *,
    flags: tuple[bool, bool, bool],
    color: str,
    height: str,
    font_refs: dict[str, str],
) -> bool:
    if _run_style_flags(run_style) != flags:
        return False
    if ((run_style.text_color() if run_style is not None else None) or _DEFAULT_TEXT_COLOR).upper() != color:
        return False
    if (_run_style_height(run_style) or _DEFAULT_CHAR_HEIGHT) != height:
        return False
    current_font_refs = _run_style_font_refs(run_style)
    for key in _FONT_REF_KEYS:
        if current_font_refs.get(key) != font_refs.get(key):
            return False
    return True


def _ref_list_element(header: Any) -> ET.Element:
    element = header.element.find(f"{HH_NS}refList")
    if element is None:
        raise RuntimeError("document header does not expose a refList element")
    return element


def _fontfaces_element(header: Any) -> ET.Element:
    ref_list = _ref_list_element(header)
    fontfaces = ref_list.find(f"{HH_NS}fontfaces")
    if fontfaces is None:
        raise RuntimeError("document header does not expose fontfaces")
    return fontfaces


def _find_fontface_bucket(fontfaces: ET.Element, lang: str) -> ET.Element | None:
    for bucket in fontfaces.findall(f"{HH_NS}fontface"):
        if (bucket.get("lang") or "").upper() == lang:
            return bucket
    return None


def _next_font_id(bucket: ET.Element) -> str:
    numeric_ids: list[int] = []
    for child in bucket.findall(f"{HH_NS}font"):
        raw_id = child.get("id")
        if raw_id is None:
            continue
        try:
            numeric_ids.append(int(raw_id))
        except ValueError:
            continue
    return "0" if not numeric_ids else str(max(numeric_ids) + 1)


def _ensure_font_face_refs(header: Any, font_name: str) -> dict[str, str]:
    name = (font_name or "").strip()
    if not name:
        raise ValueError("font_name cannot be empty")

    fontfaces = _fontfaces_element(header)
    refs: dict[str, str] = {}
    changed = False

    for lang, ref_key in _FONT_FACE_LANGS.items():
        bucket = _find_fontface_bucket(fontfaces, lang)
        if bucket is None:
            bucket = _append_child(fontfaces, f"{HH_NS}fontface", {"lang": lang, "fontCnt": "0"})
            changed = True

        font_element = None
        for candidate in bucket.findall(f"{HH_NS}font"):
            if (candidate.get("face") or "").strip() == name:
                font_element = candidate
                break

        if font_element is None:
            font_element = _append_child(
                bucket,
                f"{HH_NS}font",
                {
                    "id": _next_font_id(bucket),
                    "face": name,
                    "type": "TTF",
                    "isEmbedded": "0",
                },
            )
            bucket.set("fontCnt", str(len(bucket.findall(f"{HH_NS}font"))))
            changed = True

        font_id = font_element.get("id")
        if not font_id:
            raise RuntimeError("fontface entry is missing an id")
        refs[ref_key] = font_id

    if changed:
        header.mark_dirty()
    return refs


def resolve_style_id(doc: HwpxDocument, style: str | None) -> str | None:
    if style is None:
        return None
    value = style.strip()
    if not value:
        return None

    resolved = doc.style(value)
    if resolved is not None:
        raw_id = getattr(resolved, "raw_id", None)
        if raw_id:
            return raw_id
        if getattr(resolved, "id", None) is not None:
            return str(resolved.id)

    for candidate in _iter_unique_styles(doc):
        if value not in {candidate.name, candidate.eng_name}:
            continue
        raw_id = getattr(candidate, "raw_id", None)
        if raw_id:
            return raw_id
        if getattr(candidate, "id", None) is not None:
            return str(candidate.id)

    raise ValueError(f"unknown style reference: {value}")


def _ensure_char_style(
    doc: HwpxDocument,
    *,
    base_char_pr_id: str | int | None,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    font_size: float | None = None,
    font_name: str | None = None,
    color: str | None = None,
) -> str:
    if not doc.headers:
        raise RuntimeError("document does not contain any headers to host styles")

    header = doc.headers[0]
    base_style = doc.char_property(base_char_pr_id)
    base_flags = _run_style_flags(base_style)
    target_flags = (
        base_flags[0] if bold is None else bool(bold),
        base_flags[1] if italic is None else bool(italic),
        base_flags[2] if underline is None else bool(underline),
    )
    target_color = _normalize_color(color) or (
        (base_style.text_color() if base_style is not None else None) or _DEFAULT_TEXT_COLOR
    ).upper()
    target_height = _normalize_font_size(font_size) or _run_style_height(base_style) or _DEFAULT_CHAR_HEIGHT
    target_font_refs = {
        key: value
        for key, value in _run_style_font_refs(base_style).items()
        if key in _FONT_REF_KEYS
    }
    if not target_font_refs:
        target_font_refs = {key: "0" for key in _FONT_REF_KEYS}
    if font_name is not None:
        target_font_refs.update(_ensure_font_face_refs(header, font_name))

    if base_char_pr_id is not None and _char_style_matches(
        base_style,
        flags=target_flags,
        color=target_color,
        height=target_height,
        font_refs=target_font_refs,
    ):
        return str(base_char_pr_id)

    def predicate(element: ET.Element) -> bool:
        if _element_flags(element) != target_flags:
            return False
        if (element.get("textColor", _DEFAULT_TEXT_COLOR) or _DEFAULT_TEXT_COLOR).upper() != target_color:
            return False
        if element.get("height", _DEFAULT_CHAR_HEIGHT) != target_height:
            return False
        element_font_refs = _element_font_refs(element)
        for key in _FONT_REF_KEYS:
            if element_font_refs.get(key) != target_font_refs.get(key):
                return False
        return True

    def modifier(element: ET.Element) -> None:
        underline_nodes = list(element.findall(f"{HH_NS}underline"))
        existing_underline = dict(underline_nodes[0].attrib) if underline_nodes else {}

        for child in list(element.findall(f"{HH_NS}bold")):
            element.remove(child)
        for child in list(element.findall(f"{HH_NS}italic")):
            element.remove(child)
        for child in underline_nodes:
            element.remove(child)

        if target_flags[0]:
            _append_child(element, f"{HH_NS}bold")
        if target_flags[1]:
            _append_child(element, f"{HH_NS}italic")

        underline_attrs = dict(existing_underline)
        underline_attrs.setdefault("shape", existing_underline.get("shape", "SOLID"))
        underline_attrs["color"] = underline_attrs.get("color", target_color) or target_color
        if target_flags[2]:
            underline_attrs["type"] = underline_attrs.get("type", "SOLID") or "SOLID"
            if underline_attrs["type"].upper() == "NONE":
                underline_attrs["type"] = "SOLID"
            underline_attrs["color"] = target_color
        else:
            underline_attrs["type"] = "NONE"
        _append_child(element, f"{HH_NS}underline", underline_attrs)

        element.set("textColor", target_color)
        element.set("height", target_height)

        font_ref = element.find(f"{HH_NS}fontRef")
        if font_ref is None:
            font_ref = _append_child(element, f"{HH_NS}fontRef")
        for key in _FONT_REF_KEYS:
            value = target_font_refs.get(key)
            if value is not None:
                font_ref.set(key, str(value))

    char_element = header.ensure_char_property(
        predicate=predicate,
        modifier=modifier,
        base_char_pr_id=base_char_pr_id,
    )
    char_id = char_element.get("id")
    if not char_id:
        raise RuntimeError("char property does not expose an identifier")
    return char_id


class _Segment:
    __slots__ = ("element", "attr", "text")

    def __init__(self, element: ET.Element, attr: str, text: str) -> None:
        self.element = element
        self.attr = attr
        self.text = text

    def set(self, value: str) -> None:
        self.text = value
        if value:
            setattr(self.element, self.attr, value)
        else:
            setattr(self.element, self.attr, "")


def _gather_segments(run_element: ET.Element) -> list[_Segment]:
    segments: list[_Segment] = []

    def visit(node: ET.Element) -> None:
        segments.append(_Segment(node, "text", node.text or ""))
        for child in list(node):
            visit(child)
            segments.append(_Segment(child, "tail", child.tail or ""))

    for text_node in run_element.findall(f"{HP_NS}t"):
        visit(text_node)
    return segments


def _slice_run(run_obj: HwpxOxmlRun, start: int, end: int) -> None:
    segments = _gather_segments(run_obj.element)
    if not segments:
        return

    total_length = sum(len(segment.text) for segment in segments)
    start = max(0, min(start, total_length))
    end = max(0, min(end, total_length))
    if start >= end:
        for segment in segments:
            if segment.text:
                segment.set("")
        run_obj.paragraph.section.mark_dirty()
        return

    changed = False
    offset = 0
    for segment in segments:
        seg_start = offset
        seg_end = seg_start + len(segment.text)
        offset = seg_end
        if end <= seg_start or start >= seg_end:
            if segment.text:
                segment.set("")
                changed = True
            continue

        local_start = max(start, seg_start) - seg_start
        local_end = min(end, seg_end) - seg_start
        new_value = segment.text[local_start:local_end]
        if segment.text != new_value:
            segment.set(new_value)
            changed = True

    if changed:
        run_obj.paragraph.section.mark_dirty()


def _split_run(run_obj: HwpxOxmlRun, local_start: int, local_end: int, char_pr_id_ref: str) -> None:
    text_value = run_obj.text or ""
    length = len(text_value)
    if length == 0:
        return

    local_start = max(0, min(local_start, length))
    local_end = max(0, min(local_end, length))
    if local_start >= local_end:
        return

    if local_start == 0 and local_end == length:
        run_obj.char_pr_id_ref = char_pr_id_ref
        return

    segments: list[tuple[int, int, str | None]] = []
    original_char = run_obj.char_pr_id_ref
    if local_start > 0:
        segments.append((0, local_start, original_char))
    segments.append((local_start, local_end, char_pr_id_ref))
    if local_end < length:
        segments.append((local_end, length, original_char))

    parent = run_obj.paragraph.element
    run_children = list(parent)
    try:
        index = run_children.index(run_obj.element)
    except ValueError:
        return

    new_elements: list[ET.Element] = []
    for seg_start, seg_end, char_id in segments:
        if seg_start >= seg_end:
            continue
        element_copy = deepcopy(run_obj.element)
        segment_run = HwpxOxmlRun(element_copy, run_obj.paragraph)
        _slice_run(segment_run, seg_start, seg_end)
        segment_run.char_pr_id_ref = char_id
        new_elements.append(element_copy)

    if not new_elements:
        parent.remove(run_obj.element)
        run_obj.paragraph.section.mark_dirty()
        return

    for offset, element in enumerate(new_elements):
        parent.insert(index + offset, element)
    parent.remove(run_obj.element)
    run_obj.paragraph.section.mark_dirty()


def _paragraph_length(paragraph: HwpxOxmlParagraph) -> int:
    return sum(len(run.text or "") for run in paragraph.runs)


def _style_element_by_id(styles_element: ET.Element, style_id: str) -> ET.Element | None:
    for style_element in styles_element.findall(f"{HH_NS}style"):
        raw_id = style_element.get("id")
        if raw_id == style_id:
            return style_element
        try:
            if raw_id is not None and str(int(raw_id)) == style_id:
                return style_element
        except ValueError:
            continue
    return None


def _next_style_id(styles_element: ET.Element) -> str:
    numeric_ids: list[int] = []
    for style_element in styles_element.findall(f"{HH_NS}style"):
        raw_id = style_element.get("id")
        if raw_id is None:
            continue
        try:
            numeric_ids.append(int(raw_id))
        except ValueError:
            continue
    return "0" if not numeric_ids else str(max(numeric_ids) + 1)


def _update_styles_item_count(styles_element: ET.Element) -> None:
    styles_element.set("itemCnt", str(len(styles_element.findall(f"{HH_NS}style"))))


def _default_base_style_id(doc: HwpxDocument) -> str:
    for candidate in ("1", "본문", "Body", "0", "바탕글", "Normal"):
        try:
            resolved = resolve_style_id(doc, candidate)
        except ValueError:
            continue
        if resolved is not None:
            return resolved

    for style in _iter_unique_styles(doc):
        raw_id = getattr(style, "raw_id", None)
        if raw_id:
            return raw_id
        if getattr(style, "id", None) is not None:
            return str(style.id)

    raise RuntimeError("document does not contain any styles")


def format_text_range(
    doc: HwpxDocument,
    paragraph_index: int,
    start_pos: int,
    end_pos: int,
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    font_size: float = None,
    font_name: str = None,
    color: str = None,
) -> None:
    """Apply character formatting to a text span within a paragraph."""
    if all(value is None for value in (bold, italic, underline, font_size, font_name, color)):
        return
    if paragraph_index < 0 or paragraph_index >= len(doc.paragraphs):
        raise ValueError(f"invalid paragraph_index: {paragraph_index}")
    if start_pos < 0 or end_pos < 0 or end_pos < start_pos:
        raise ValueError("start_pos/end_pos are out of range")
    if start_pos == end_pos:
        return

    paragraph = doc.paragraphs[paragraph_index]
    total_length = _paragraph_length(paragraph)
    if total_length == 0 or start_pos >= total_length:
        return
    span_end = min(end_pos, total_length)

    cursor = start_pos
    while cursor < span_end:
        runs = list(paragraph.runs)
        offset = 0
        target: tuple[HwpxOxmlRun, int, int, int] | None = None
        for candidate in runs:
            text = candidate.text or ""
            length = len(text)
            run_start = offset
            run_end = run_start + length
            offset = run_end
            if length == 0 or run_end <= cursor:
                continue
            if run_start >= span_end:
                break
            target = (candidate, run_start, run_end, length)
            break

        if target is None:
            break

        run_obj, run_start, run_end, length = target
        local_start = max(0, cursor - run_start)
        local_end = min(length, span_end - run_start)
        if local_start >= local_end:
            cursor = max(cursor + 1, run_end)
            continue

        char_id = _ensure_char_style(
            doc,
            base_char_pr_id=run_obj.char_pr_id_ref,
            bold=bold,
            italic=italic,
            underline=underline,
            font_size=font_size,
            font_name=font_name,
            color=color,
        )
        _split_run(run_obj, local_start, local_end, char_id)
        cursor = min(span_end, run_end)


def create_style_in_doc(
    doc: HwpxDocument,
    style_name: str,
    bold: bool = None,
    italic: bool = None,
    font_size: float = None,
    font_name: str = None,
    color: str = None,
) -> dict[str, Any]:
    """Create a paragraph style backed by a stable upstream charPr definition."""
    name = (style_name or "").strip()
    if not name:
        raise ValueError("style_name cannot be empty")
    if not doc.headers:
        raise RuntimeError("document does not contain any headers to host styles")

    header = doc.headers[0]
    styles_element = header._styles_element()
    if styles_element is None:
        raise RuntimeError("document header does not expose styles")

    for style in _iter_unique_styles(doc):
        if name not in {style.name, style.eng_name}:
            continue
        style_id = getattr(style, "raw_id", None) or str(style.id)
        char_ref = style.char_pr_id_ref
        para_ref = style.para_pr_id_ref
        return {
            "style_id": style_id,
            "style_name": style.name or name,
            "created": False,
            "char_pr_id_ref": None if char_ref is None else str(char_ref),
            "para_pr_id_ref": None if para_ref is None else str(para_ref),
        }

    base_style_id = _default_base_style_id(doc)
    base_style = doc.style(base_style_id)
    if base_style is None:
        raise RuntimeError(f"failed to resolve base style id: {base_style_id}")

    base_style_element = _style_element_by_id(styles_element, base_style_id)
    if base_style_element is None:
        raise RuntimeError(f"failed to locate base style element: {base_style_id}")

    base_char_ref = getattr(base_style, "char_pr_id_ref", None)
    char_ref: str | None
    if any(value is not None for value in (bold, italic, font_size, font_name, color)):
        char_ref = _ensure_char_style(
            doc,
            base_char_pr_id=None if base_char_ref is None else str(base_char_ref),
            bold=bold,
            italic=italic,
            underline=None,
            font_size=font_size,
            font_name=font_name,
            color=color,
        )
    else:
        char_ref = None if base_char_ref is None else str(base_char_ref)

    style_id = _next_style_id(styles_element)
    new_style = deepcopy(base_style_element)
    new_style.set("id", style_id)
    new_style.set("name", name)
    new_style.set("engName", name)
    new_style.set("nextStyleIDRef", style_id)
    if char_ref is not None:
        new_style.set("charPrIDRef", char_ref)
    styles_element.append(new_style)
    _update_styles_item_count(styles_element)
    header.mark_dirty()

    return {
        "style_id": style_id,
        "style_name": name,
        "created": True,
        "char_pr_id_ref": char_ref,
        "para_pr_id_ref": new_style.get("paraPrIDRef"),
    }


def list_styles_in_doc(doc: HwpxDocument) -> list[dict[str, str | None]]:
    """Return styles defined in the document."""
    header = doc.headers[0]
    styles_element = header._styles_element()
    if styles_element is None:
        return []

    styles: list[dict[str, str | None]] = []
    for style_element in styles_element.findall(f"{HH_NS}style"):
        styles.append(
            {
                "id": style_element.get("id"),
                "name": style_element.get("name"),
                "eng_name": style_element.get("engName"),
                "type": style_element.get("type"),
                "para_pr_id_ref": style_element.get("paraPrIDRef"),
                "char_pr_id_ref": style_element.get("charPrIDRef"),
            }
        )
    return styles
