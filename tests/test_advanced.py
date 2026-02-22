import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def enable_advanced(monkeypatch):
    """고급 도구 테스트 시 환경변수 설정"""
    monkeypatch.setenv("HWPX_MCP_ADVANCED", "1")


def test_package_parts(tmp_path):
    import hwpx_mcp_server.server as server
    server = importlib.reload(server)

    target = tmp_path / "test.hwpx"
    server.create_document(str(target))

    result = server.package_parts(str(target))

    assert "parts" in result
    assert "Contents/content.hpf" in result["parts"]


def test_package_get_xml_truncation(tmp_path):
    import hwpx_mcp_server.server as server
    server = importlib.reload(server)

    target = tmp_path / "test.hwpx"
    server.create_document(str(target))

    result = server.package_get_xml(str(target), "Contents/section0.xml", max_chars=100)

    assert result["truncated"] is True
    assert len(result["text"]) == 100


def test_advanced_tools_hidden_by_default(monkeypatch):
    """HWPX_MCP_ADVANCED 미설정 시 고급 도구가 등록되지 않는지 확인"""
    monkeypatch.delenv("HWPX_MCP_ADVANCED", raising=False)
    import hwpx_mcp_server.server as server

    reloaded = importlib.reload(server)
    tool_names = set(reloaded.mcp._tool_manager._tools.keys())

    assert "package_parts" not in tool_names
    assert "plan_edit" not in tool_names

    monkeypatch.setenv("HWPX_MCP_ADVANCED", "1")
    importlib.reload(reloaded)
