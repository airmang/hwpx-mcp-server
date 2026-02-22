from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

_HP_NS = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def _distribute_lengths(total: int, weights: list[int]) -> list[int]:
    if not weights:
        return []
    if total <= 0:
        return [0 for _ in weights]

    weight_sum = sum(weights)
    if weight_sum <= 0:
        base = total // len(weights)
        remainder = total - (base * len(weights))
        shares = [base] * len(weights)
        for index in range(remainder):
            shares[index] += 1
        return shares

    shares: list[int] = []
    remainder = total
    residuals: list[tuple[int, int]] = []
    for index, weight in enumerate(weights):
        raw = total * weight
        share = raw // weight_sum
        shares.append(share)
        remainder -= share
        residuals.append((raw % weight_sum, index))

    residuals.sort(key=lambda item: (-item[0], item[1]))
    cursor = 0
    while remainder > 0 and residuals:
        _, target = residuals[cursor]
        shares[target] += 1
        remainder -= 1
        cursor = (cursor + 1) % len(residuals)

    if remainder > 0:
        shares[-1] += remainder
    return shares


def _replace_within_runs(runs: list[Any], find_text: str, replace_text: str) -> int:
    replaced_total = 0
    for run in runs:
        run_text = run.text or ""
        if not run_text or find_text not in run_text:
            continue
        expected = run_text.count(find_text)
        if expected <= 0:
            continue

        replaced = 0
        if hasattr(run, "replace_text"):
            try:
                replaced = int(run.replace_text(find_text, replace_text))
            except Exception:  # noqa: BLE001
                replaced = 0

        if replaced <= 0:
            run.text = run_text.replace(find_text, replace_text)
            replaced = expected

        replaced_total += replaced
    return replaced_total


def _append_xml_text_node(run_element: Any) -> Any:
    maker = getattr(run_element, "makeelement", None)
    if callable(maker):
        node = maker(f"{_HP_NS}t", {})
        run_element.append(node)
        return node
    return ET.SubElement(run_element, f"{_HP_NS}t")


def _xml_run_text(run_element: Any) -> str:
    parts: list[str] = []
    for text_node in run_element.findall(f"{_HP_NS}t"):
        parts.append("".join(text_node.itertext()))
    return "".join(parts)


def _set_xml_run_text(run_element: Any, value: str) -> None:
    text_nodes = list(run_element.findall(f"{_HP_NS}t"))
    primary = text_nodes[0] if text_nodes else _append_xml_text_node(run_element)
    primary.text = value
    for node in text_nodes[1:]:
        node.text = ""


def _replace_across_runs(runs: list[Any], find_text: str, replace_text: str) -> int:
    if not runs or not find_text:
        return 0

    texts = [run.text or "" for run in runs]
    merged = "".join(texts)
    if find_text not in merged:
        return 0

    replaced_count = merged.count(find_text)
    new_merged = merged.replace(find_text, replace_text)
    weights = [len(text) for text in texts]
    redistributed = _distribute_lengths(len(new_merged), weights)

    cursor = 0
    for run, size in zip(runs, redistributed):
        run.text = new_merged[cursor : cursor + size]
        cursor += size

    return replaced_count


def _replace_in_runs(runs: list[Any], find_text: str, replace_text: str) -> int:
    if not runs:
        return 0

    replaced = _replace_within_runs(runs, find_text, replace_text)
    merged_after_simple = "".join(run.text or "" for run in runs)
    if find_text in merged_after_simple:
        replaced += _replace_across_runs(runs, find_text, replace_text)
    return replaced


def _replace_within_xml_runs(run_elements: list[Any], find_text: str, replace_text: str) -> int:
    replaced_total = 0
    for run_element in run_elements:
        run_text = _xml_run_text(run_element)
        if not run_text or find_text not in run_text:
            continue
        replaced = run_text.count(find_text)
        _set_xml_run_text(run_element, run_text.replace(find_text, replace_text))
        replaced_total += replaced
    return replaced_total


def _replace_across_xml_runs(run_elements: list[Any], find_text: str, replace_text: str) -> int:
    if not run_elements or not find_text:
        return 0

    texts = [_xml_run_text(run_element) for run_element in run_elements]
    merged = "".join(texts)
    if find_text not in merged:
        return 0

    replaced_count = merged.count(find_text)
    new_merged = merged.replace(find_text, replace_text)
    weights = [len(text) for text in texts]
    redistributed = _distribute_lengths(len(new_merged), weights)

    cursor = 0
    for run_element, size in zip(run_elements, redistributed):
        _set_xml_run_text(run_element, new_merged[cursor : cursor + size])
        cursor += size

    return replaced_count


def _replace_in_xml_runs(run_elements: list[Any], find_text: str, replace_text: str) -> int:
    if not run_elements:
        return 0

    replaced = _replace_within_xml_runs(run_elements, find_text, replace_text)
    merged_after_simple = "".join(_xml_run_text(run_element) for run_element in run_elements)
    if find_text in merged_after_simple:
        replaced += _replace_across_xml_runs(run_elements, find_text, replace_text)
    return replaced


def find_in_doc(doc: Any, text_to_find: str, match_case: bool = True, max_results: int = 50) -> dict:
    if text_to_find == "":
        raise ValueError("text_to_find는 빈 문자열일 수 없습니다.")
    matches: list[dict] = []
    needle = text_to_find if match_case else text_to_find.lower()

    for index, para in enumerate(doc.paragraphs):
        haystack_raw = para.text or ""
        haystack = haystack_raw if match_case else haystack_raw.lower()
        cursor = 0
        while True:
            pos = haystack.find(needle, cursor)
            if pos < 0:
                break
            context_start = max(0, pos - 20)
            context_end = min(len(haystack_raw), pos + len(text_to_find) + 20)
            matches.append(
                {
                    "paragraph_index": index,
                    "position": pos,
                    "context": haystack_raw[context_start:context_end],
                }
            )
            if len(matches) >= max_results:
                return {"matches": matches, "total_matches": len(matches)}
            cursor = pos + max(1, len(text_to_find))

    return {"matches": matches, "total_matches": len(matches)}


def replace_in_doc(doc: Any, find_text: str, replace_text: str) -> int:
    if find_text == "":
        raise ValueError("find_text는 빈 문자열일 수 없습니다.")

    count = 0
    try:
        for para in doc.paragraphs:
            runs = list(getattr(para, "runs", []))
            replaced = _replace_in_runs(runs, find_text, replace_text) if runs else 0
            has_tables = bool(getattr(para, "tables", []))
            if replaced == 0 and not has_tables:
                text = para.text or ""
                if find_text in text:
                    replaced = text.count(find_text)
                    para.text = text.replace(find_text, replace_text)
            count += replaced

        for para in doc.paragraphs:
            for table in getattr(para, "tables", []):
                for row in table.rows:
                    for cell in row.cells:
                        cell_paragraphs = list(getattr(cell, "paragraphs", []))
                        if cell_paragraphs:
                            cell_replaced = 0
                            for cell_para in cell_paragraphs:
                                runs = list(getattr(cell_para, "runs", []))
                                para_replaced = _replace_in_runs(runs, find_text, replace_text) if runs else 0
                                if para_replaced == 0:
                                    text = cell_para.text or ""
                                    if find_text in text:
                                        para_replaced = text.count(find_text)
                                        cell_para.text = text.replace(find_text, replace_text)
                                cell_replaced += para_replaced
                            count += cell_replaced
                            continue

                        xml_replaced = 0
                        cell_element = getattr(cell, "element", None)
                        if cell_element is not None:
                            for cell_para_element in cell_element.findall(f".//{_HP_NS}p"):
                                run_elements = list(cell_para_element.findall(f"{_HP_NS}run"))
                                if not run_elements:
                                    continue
                                xml_replaced += _replace_in_xml_runs(run_elements, find_text, replace_text)
                        if xml_replaced:
                            count += xml_replaced
                            section = getattr(para, "section", None)
                            if section is not None and hasattr(section, "mark_dirty"):
                                section.mark_dirty()
                            continue

                        text = cell.text or ""
                        if find_text in text:
                            replaced = text.count(find_text)
                            cell.text = text.replace(find_text, replace_text)
                            count += replaced
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"텍스트 치환 중 오류: {e}") from e
    return count


def batch_replace_in_doc(doc: Any, replacements: list[dict]) -> dict:
    results: list[dict] = []
    total = 0
    for index, item in enumerate(replacements):
        if not isinstance(item, dict):
            raise ValueError(f"replacements[{index}]는 dict여야 합니다.")

        found = str(item.get("find", ""))
        repl = str(item.get("replace", ""))
        if found == "":
            raise ValueError(f"replacements[{index}].find는 빈 문자열일 수 없습니다.")

        replaced = replace_in_doc(doc, found, repl)
        total += replaced
        results.append({"find": found, "replace": repl, "replaced_count": replaced})
    return {"results": results, "total_replaced": total}
