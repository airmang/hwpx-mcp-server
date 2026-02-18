"""Streamable HTTP transport 최소 통합 테스트."""

from __future__ import annotations

from pathlib import Path

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server import NotificationOptions
from mcp.server.streamable_http import StreamableHTTPServerTransport

from hwpx_mcp_server.hwpx_ops import HwpxOps
from hwpx_mcp_server.server import _build_server
from hwpx_mcp_server.tools import build_tool_definitions


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_streamable_http_initialize_and_list_tools(tmp_path: Path) -> None:
    server = _build_server(HwpxOps(base_directory=tmp_path, auto_backup=False), build_tool_definitions())
    init_options = server.create_initialization_options(NotificationOptions())
    transport = StreamableHTTPServerTransport(mcp_session_id=None)

    async def app(scope, receive, send):
        if scope.get("path") != "/mcp":
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"not found"})
            return
        await transport.handle_request(scope, receive, send)

    async with transport.connect() as (read_stream, write_stream):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(server.run, read_stream, write_stream, init_options)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as http_client:
                async with streamable_http_client(
                    "http://testserver/mcp",
                    http_client=http_client,
                ) as (client_read, client_write, _):
                    async with ClientSession(client_read, client_write) as session:
                        await session.initialize()
                        tools_result = await session.list_tools()
                        assert len(tools_result.tools) >= 1
                        assert any(tool.name == "open_info" for tool in tools_result.tools)

            task_group.cancel_scope.cancel()
