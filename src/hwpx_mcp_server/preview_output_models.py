# SPDX-License-Identifier: Apache-2.0
"""Typed structured output for the layout preview tool."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RenderPreviewScreenshotEngine(_StrictOutput):
    requested: bool
    available: bool
    backend: str | None
    executable: str | None = None
    message: str


class RenderPreviewOutput(_StrictOutput):
    """Manifest carried in ``CallToolResult.structuredContent``."""

    schema_version: Literal["hwpx.render-preview.v1"] = Field(alias="schemaVersion")
    status: Literal["ok", "partial", "blocked", "html_only"]
    generated_at: str = Field(alias="generatedAt")
    source_path: str = Field(alias="sourcePath")
    output_dir: str = Field(alias="outputDir")
    html_path: str = Field(alias="htmlPath")
    manifest_path: str = Field(alias="manifestPath")
    visual_review_path: str = Field(alias="visualReviewPath")
    mode: Literal["pages", "long"]
    page_count: int = Field(alias="pageCount", ge=0)
    pages: list[dict[str, Any]]
    screenshots: list[dict[str, Any]]
    screenshot_engine: RenderPreviewScreenshotEngine = Field(alias="screenshotEngine")
    warnings: list[str]
    suggestion: str | None


__all__ = ["RenderPreviewOutput", "RenderPreviewScreenshotEngine"]
