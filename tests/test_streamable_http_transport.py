"""FastMCP Streamable HTTP surface smoke tests."""

from __future__ import annotations

from starlette.applications import Starlette
import pytest

from hwpx_mcp_server.server import mcp


def test_streamable_http_app_is_constructible() -> None:
    app = mcp.streamable_http_app()
    assert isinstance(app, Starlette)


@pytest.mark.anyio("asyncio")
async def test_list_tools_contains_v2_core_tools() -> None:
    names = {tool.name for tool in await mcp.list_tools()}
    assert "create_document" in names
    assert "get_document_text" in names
    assert "search_and_replace" in names
