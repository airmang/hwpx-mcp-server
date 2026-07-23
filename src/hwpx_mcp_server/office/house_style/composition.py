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
        metadata: dict[str, Any] = {
            "generator": "section_chip",
            "style": self.style,
            "number": self.number,
        }
        if self.accent_color:
            metadata["accentColor"] = self.accent_color
        if self.style == "inline":
            return {
                "type": "heading",
                "level": 1,
                "text": f"{self.number} {self.title}",
                "metadata": metadata,
            }
        return {
            "type": "table",
            "rows": [[self.number, "", self.title]],
            "columnWidths": [1, 0.4, 11],
            "metadata": metadata,
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
