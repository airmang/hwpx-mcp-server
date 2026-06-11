"""FastMCP Streamable HTTP surface smoke tests."""

from __future__ import annotations

from starlette.applications import Starlette
import pytest

from hwpx_mcp_server.server import mcp, mcp_server_health


def test_streamable_http_app_is_constructible() -> None:
    app = mcp.streamable_http_app()
    assert isinstance(app, Starlette)


@pytest.mark.anyio("asyncio")
async def test_list_tools_contains_v2_core_tools() -> None:
    names = {tool.name for tool in await mcp.list_tools()}
    assert "create_document" in names
    assert "get_document_text" in names
    assert "search_and_replace" in names
    assert "replace_in_paragraph" in names
    assert "replace_by_anchor" in names
    assert "add_memo_by_anchor" in names
    assert "mcp_server_health" in names


def test_mcp_server_health_reports_disconnect_and_path_diagnostics(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HWPX_MCP_SANDBOX_ROOT", str(tmp_path))
    health = mcp_server_health()

    assert health["streamable_http_available"] is True
    assert health["pythonHwpxVersion"] != "unknown"
    assert health["toolSurface"]["status"] == "ok"
    assert health["toolSurface"]["actualFastMcpToolCount"] >= health["toolSurface"]["expectedFastMcpToolCount"]
    assert health["toolSurface"]["actualLegacyToolCount"] >= health["toolSurface"]["expectedLegacyToolCount"]
    assert health["toolSurface"]["missingKeyTools"] == []
    assert "byte_preserving_patch" in health["toolSurface"]["keyTools"]
    assert health["unitPolicy"]["fontSize"] == "points"
    assert health["unitPolicy"]["fileSizeLimits"] == "bytes"
    assert health["sandbox"]["root"] == str(tmp_path)
    assert health["sandbox"]["absolute_paths_inside_root_allowed"] is True
    assert "absolute paths inside" in health["sandbox"]["path_guidance"]
    assert "large document extraction exceeding client/tool timeout" in health["disconnect_diagnostics"]["likely_conditions"]
    assert "stdio keepalive is client-controlled" in health["disconnect_diagnostics"]["keepalive_check"]
