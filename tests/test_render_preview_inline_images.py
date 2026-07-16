# SPDX-License-Identifier: Apache-2.0
"""render_preview inline-image support (rhwp T1: the P0 '눈' quick win).

Proves a captured page PNG is (a) embedded as base64 by the ops layer under a
byte cap, and (b) lifted into an MCP ImageContent block by the server tool while
the structured manifest stays lean — so the model can see the page without
Hancom/ComputerUse.
"""

from __future__ import annotations

import asyncio
import base64

import mcp.types as mcp_types
import pytest
from jsonschema import Draft202012Validator
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

from hwpx_mcp_server import server
from hwpx_mcp_server.tool_contract import bound_tool_registry

# 1x1 transparent PNG.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/axwAqkAAAAASUVORK5CYII="
)


# --------------------------------------------------------------------------- #
# ops layer: _embed_screenshot_image                                          #
# --------------------------------------------------------------------------- #
def test_embed_screenshot_image_attaches_base64(tmp_path) -> None:
    png = tmp_path / "page-001.png"
    png.write_bytes(_PNG_1X1)
    item: dict = {"pageIndex": 0, "path": "preview/page-001.png"}

    server._OPS._embed_screenshot_image(
        item, png, embed_images=True, max_image_bytes=10_000
    )

    assert item["imageMime"] == "image/png"
    assert item["bytes"] == len(_PNG_1X1)
    assert base64.b64decode(item["imageBase64"]) == _PNG_1X1


def test_embed_screenshot_image_respects_byte_cap(tmp_path) -> None:
    png = tmp_path / "page-001.png"
    png.write_bytes(_PNG_1X1)
    item: dict = {"pageIndex": 0}

    server._OPS._embed_screenshot_image(item, png, embed_images=True, max_image_bytes=1)

    assert "imageBase64" not in item
    assert item["imageOmitted"] == "exceeds_max_image_bytes"
    assert item["bytes"] == len(_PNG_1X1)


def test_embed_screenshot_image_noop_when_disabled(tmp_path) -> None:
    png = tmp_path / "page-001.png"
    png.write_bytes(_PNG_1X1)
    item: dict = {"pageIndex": 0}
    server._OPS._embed_screenshot_image(item, png, embed_images=False, max_image_bytes=None)
    assert item == {"pageIndex": 0}


# --------------------------------------------------------------------------- #
# server tool: lift base64 into ImageContent, strip from structuredContent     #
# --------------------------------------------------------------------------- #
def _fake_manifest_with_image() -> dict:
    return {
        "schemaVersion": "hwpx.render-preview.v1",
        "status": "ok",
        "generatedAt": "2026-07-16T00:00:00Z",
        "sourcePath": "doc.hwpx",
        "outputDir": "doc-preview",
        "htmlPath": "doc-preview/preview.html",
        "manifestPath": "doc-preview/manifest.json",
        "visualReviewPath": "doc-preview/visual-review.json",
        "mode": "pages",
        "pageCount": 1,
        "pages": [{"index": 0}],
        "screenshots": [
            {
                "pageIndex": 0,
                "path": "preview/page-001.png",
                "imageBase64": base64.b64encode(_PNG_1X1).decode("ascii"),
                "imageMime": "image/png",
                "bytes": len(_PNG_1X1),
            }
        ],
        "screenshotEngine": {
            "requested": True,
            "available": True,
            "backend": "test",
            "message": "captured",
        },
        "warnings": [],
        "suggestion": None,
    }


def test_render_preview_lifts_base64_into_image_content(monkeypatch) -> None:
    monkeypatch.setattr(
        server._OPS, "render_preview", lambda **kwargs: _fake_manifest_with_image()
    )

    result = server.render_preview("doc.hwpx")

    assert isinstance(result, mcp_types.CallToolResult)
    assert result.isError is False
    kinds = [block.type for block in result.content]
    assert kinds[0] == "text"
    assert "image" in kinds

    image = next(b for b in result.content if b.type == "image")
    assert image.mimeType == "image/png"
    assert base64.b64decode(image.data) == _PNG_1X1

    # structuredContent keeps the manifest but WITHOUT the base64 payload.
    shot = result.structuredContent["screenshots"][0]
    assert "imageBase64" not in shot
    assert shot["imageEmbedded"] is True


def test_render_preview_image_survives_client_schema_validation(monkeypatch) -> None:
    # End-to-end through a real ClientSession: tools/list must cache the owned
    # manifest schema and tools/call must validate structuredContent against it
    # without dropping the image block.
    monkeypatch.setattr(
        server._OPS, "render_preview", lambda **kwargs: _fake_manifest_with_image()
    )

    async def run_client() -> mcp_types.CallToolResult:
        async with create_connected_server_and_client_session(server.mcp) as client:
            listed = await client.list_tools()
            tool = next(item for item in listed.tools if item.name == "render_preview")
            assert tool.outputSchema is not None
            assert tool.outputSchema["title"] == "RenderPreviewOutput"
            assert {
                "content",
                "structuredContent",
                "isError",
            }.isdisjoint(tool.outputSchema["properties"])
            return await client.call_tool("render_preview", {"filename": "doc.hwpx"})

    result = asyncio.run(run_client())

    assert isinstance(result, mcp_types.CallToolResult)
    image_blocks = [b for b in result.content if b.type == "image"]
    assert len(image_blocks) == 1
    assert base64.b64decode(image_blocks[0].data) == _PNG_1X1
    assert result.structuredContent["status"] == "ok"
    schema = bound_tool_registry().by_name()["render_preview"].output_schema
    assert not list(Draft202012Validator(schema).iter_errors(result.structuredContent))


def test_render_preview_rejects_invalid_structured_content(monkeypatch) -> None:
    monkeypatch.setattr(
        server._OPS,
        "render_preview",
        lambda **kwargs: {"status": "ok"},
    )

    with pytest.raises(ToolError, match="validation error"):
        asyncio.run(server.mcp.call_tool("render_preview", {"filename": "doc.hwpx"}))
