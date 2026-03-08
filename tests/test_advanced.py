import importlib
import os

import pytest

from hwpx_mcp_server.core.document import open_doc


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


def test_object_find_by_attr_matches_any_value_when_attr_value_omitted(tmp_path):
    import hwpx_mcp_server.server as server
    server = importlib.reload(server)

    target = tmp_path / "test.hwpx"
    server.create_document(str(target))
    server.add_paragraph(str(target), "attribute target")

    result = server.object_find_by_attr(str(target), "pageBreak")

    assert result["objects"]
    assert all("pageBreak" in obj["attrs"] for obj in result["objects"])


def test_plan_edit_preview_apply_verification_flow(tmp_path):
    import hwpx_mcp_server.server as server
    server = importlib.reload(server)

    target = tmp_path / "test.hwpx"
    server.create_document(str(target))
    server.add_paragraph(str(target), "verification target text")

    plan = server.plan_edit(str(target), "target text")
    assert plan["ok"] is True

    plan_id = plan["data"]["plan"]["planId"]
    preview = server.preview_edit(str(target), plan_id)
    apply_result = server.apply_edit(str(target), plan_id)

    assert preview["ok"] is True
    assert preview["data"]["preview"]["diff"][0]["before"] == "verification target text"
    assert preview["data"]["preview"]["diff"][0]["after"] == "verification target text"
    assert apply_result["ok"] is True
    assert apply_result["data"]["apply"]["applied"] is True


def test_plan_edit_uses_extracted_text_when_memo_anchor_pollutes_raw_paragraph(tmp_path):
    import hwpx_mcp_server.server as server
    server = importlib.reload(server)

    target = tmp_path / "test.hwpx"
    server.create_document(str(target))
    server.add_paragraph(str(target), "memo verification target")
    server.add_memo(str(target), 1, "check memo")

    assert open_doc(str(target)).paragraphs[1].text != "memo verification target"

    plan = server.plan_edit(str(target), "memo verification target")

    assert plan["ok"] is True
