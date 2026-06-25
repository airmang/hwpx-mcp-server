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

from hwpx_mcp_server import server

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
        "status": "ok",
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


def test_render_preview_image_survives_mcp_call_tool(monkeypatch) -> None:
    # End-to-end through the FastMCP transport: the image block must reach the
    # client unchanged.
    monkeypatch.setattr(
        server._OPS, "render_preview", lambda **kwargs: _fake_manifest_with_image()
    )

    result = asyncio.run(server.mcp.call_tool("render_preview", {"filename": "doc.hwpx"}))

    assert isinstance(result, mcp_types.CallToolResult)
    image_blocks = [b for b in result.content if b.type == "image"]
    assert len(image_blocks) == 1
    assert base64.b64decode(image_blocks[0].data) == _PNG_1X1
    assert result.structuredContent["status"] == "ok"
