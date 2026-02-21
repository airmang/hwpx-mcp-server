from __future__ import annotations

from typing import Any


def find_in_doc(doc: Any, text_to_find: str, match_case: bool = True, max_results: int = 50) -> dict:
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
    count = 0
    try:
        for para in doc.paragraphs:
            for run in para.runs:
                if run.text and find_text in run.text:
                    count += run.text.count(find_text)
                    run.text = run.text.replace(find_text, replace_text)

        seen: set[int] = set()
        for para in doc.paragraphs:
            for table in getattr(para, "tables", []):
                key = id(table)
                if key in seen:
                    continue
                seen.add(key)
                for row in table.rows:
                    for cell in row.cells:
                        if cell.text and find_text in cell.text:
                            count += cell.text.count(find_text)
                            cell.text = cell.text.replace(find_text, replace_text)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"텍스트 치환 중 오류: {e}") from e
    return count


def batch_replace_in_doc(doc: Any, replacements: list[dict]) -> dict:
    results: list[dict] = []
    total = 0
    for item in replacements:
        found = str(item.get("find", ""))
        repl = str(item.get("replace", ""))
        replaced = replace_in_doc(doc, found, repl)
        total += replaced
        results.append({"find": found, "replace": repl, "replaced_count": replaced})
    return {"results": results, "total_replaced": total}
