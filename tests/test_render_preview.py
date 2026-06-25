# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from hwpx.document import HwpxDocument

from hwpx_mcp_server import server
from hwpx_mcp_server.tools import build_tool_definitions


def test_render_preview_tool_is_exposed_in_legacy_registry() -> None:
    definitions = {definition.name: definition for definition in build_tool_definitions()}

    assert "render_preview" in definitions
    schema = definitions["render_preview"].input_model.model_json_schema()
    assert "outputDir" in schema["properties"]
    assert "maxPages" in schema["properties"]


def test_render_preview_html_only_creates_manifest_and_visual_evidence(tmp_path: Path) -> None:
    source = tmp_path / "preview-source.hwpx"
    output_dir = tmp_path / "preview"
    document = HwpxDocument.new()
    document.add_paragraph("Preview title")
    document.save_to_path(source)

    call_result = server.render_preview(
        str(source),
        output_dir=str(output_dir),
        screenshot="off",
    )

    # render_preview now returns a CallToolResult so it can carry inline images;
    # the manifest lives in structuredContent and the JSON text content.
    result = call_result.structuredContent
    assert call_result.isError is False
    assert call_result.content[0].type == "text"
    assert result["status"] == "html_only"
    assert result["schemaVersion"] == "hwpx.render-preview.v1"
    assert result["pageCount"] >= 1
    assert result["pages"][0]["htmlPath"].endswith("page-001.html")
    assert result["screenshotEngine"]["requested"] is False
    # html_only -> no screenshots -> no inline image blocks
    assert all(block.type != "image" for block in call_result.content)

    html_path = Path(result["htmlPath"])
    manifest_path = Path(result["manifestPath"])
    visual_review_path = Path(result["visualReviewPath"])
    assert html_path.exists()
    assert manifest_path.exists()
    assert visual_review_path.exists()
    assert "hwpx-preview-page" in html_path.read_text(encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "html_only"
    visual_review = json.loads(visual_review_path.read_text(encoding="utf-8"))
    assert visual_review["schemaVersion"] == "hwpx.visual-review.v1"
    assert visual_review["summary"]["layout_preview_status"] == "html_only"
