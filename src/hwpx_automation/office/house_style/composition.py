# SPDX-License-Identifier: Apache-2.0
"""Typed application composition for an operating-plan section divider."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator


class SectionChipPlan(BaseModel):
    """Application-level section divider lowered to generic plan blocks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    number: str
    title: str
    style: Literal["box", "inline"] = "box"
    accent_color: str | None = None

    @field_validator("number", "title")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("section chip number and title must be non-empty")
        return normalized

    def to_document_plan_block(self) -> dict[str, Any]:
        if self.style == "inline":
            return {
                "type": "heading",
                "level": 1,
                "text": f"{self.number} {self.title}",
            }
        return {
            "type": "table",
            "showHeader": False,
            "columns": [
                {"key": "number", "label": "번호", "widthWeight": 2},
                {"key": "gap", "label": "여백", "widthWeight": 1},
                {"key": "title", "label": "제목", "widthWeight": 15},
            ],
            "rows": [
                {"number": self.number, "gap": "", "title": self.title}
            ],
        }


def compose_section_chip(
    number: str,
    title: str,
    *,
    style: Literal["box", "inline"] = "box",
    accent_color: str | None = None,
) -> dict[str, Any]:
    """Compose a genre-specific divider using generic document-plan blocks."""

    return SectionChipPlan(
        number=number,
        title=title,
        style=style,
        accent_color=accent_color,
    ).to_document_plan_block()


__all__ = ["SectionChipPlan", "compose_section_chip"]
