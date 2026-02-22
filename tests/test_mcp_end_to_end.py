"""v2 tool surface end-to-end tests."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


PHASE1_TOOLS = {
    "create_document",
    "get_document_info",
    "get_document_text",
    "get_document_outline",
    "get_paragraph_text",
    "get_paragraphs_text",
    "find_text",
    "search_and_replace",
    "batch_replace",
}

ADVANCED_TOOLS = {
    "package_parts",
    "package_get_xml",
    "package_get_text",
    "object_find_by_tag",
    "object_find_by_attr",
    "plan_edit",
    "preview_edit",
    "apply_edit",
    "validate_structure",
    "lint_text_conventions",
}


@pytest.fixture()
def server_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HWPX_MCP_ADVANCED", raising=False)
    import hwpx_mcp_server.server as server

    return importlib.reload(server)


def test_default_toolset_exposes_phase1_and_hides_advanced(server_module) -> None:
    names = set(server_module.mcp._tool_manager._tools.keys())

    assert PHASE1_TOOLS.issubset(names)
    assert names.isdisjoint(ADVANCED_TOOLS)


def test_advanced_toolset_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HWPX_MCP_ADVANCED", "1")
    import hwpx_mcp_server.server as server

    reloaded = importlib.reload(server)
    names = set(reloaded.mcp._tool_manager._tools.keys())

    assert ADVANCED_TOOLS.issubset(names)


def test_year_rollover_batch_replace_flow(server_module, tmp_path: Path) -> None:
    target = tmp_path / "school_plan.hwpx"
    server_module.create_document(str(target))
    server_module.add_paragraph(str(target), "2025. 3. 1. ~ 2026. 2. 28.")

    result = server_module.batch_replace(
        str(target),
        [
            {"find": "2026", "replace": "2027"},
            {"find": "2025", "replace": "2026"},
        ],
    )
    text_result = server_module.get_document_text(str(target))

    assert result["total_replaced"] == 2
    assert "2026. 3. 1. ~ 2027. 2. 28." in text_result["text"]


def test_default_max_chars_from_env(server_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "long.hwpx"
    server_module.create_document(str(target))
    server_module.add_paragraph(str(target), "A" * 500)
    monkeypatch.setenv("HWPX_MCP_MAX_CHARS", "60")

    result = server_module.get_document_text(str(target))

    assert result["truncated"] is True
    assert len(result["text"]) == 60
