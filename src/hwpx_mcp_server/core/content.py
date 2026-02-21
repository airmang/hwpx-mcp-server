from __future__ import annotations

from typing import Any


def iter_all_paragraphs(doc: Any):
    for paragraph in doc.paragraphs:
        yield paragraph


def _iter_tables(doc: Any):
    seen: set[int] = set()
    for paragraph in doc.paragraphs:
        for table in getattr(paragraph, "tables", []):
            key = id(table)
            if key not in seen:
                seen.add(key)
                yield table


def iter_table_texts(doc: Any):
    for table in _iter_tables(doc):
        for row in table.rows:
            for cell in row.cells:
                yield cell.text or ""


def collect_full_text(doc: Any) -> str:
    chunks: list[str] = []
    for paragraph in iter_all_paragraphs(doc):
        chunks.append(paragraph.text or "")
    for text in iter_table_texts(doc):
        if text:
            chunks.append(text)
    return "\n".join(chunks)
