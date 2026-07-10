from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import anyio

from hwpx_mcp_server import server
from hwpx_mcp_server.tool_contract import (
    contract_hash,
    expected_tool_names,
    skill_required_tool_names,
)


ROOT = Path(__file__).resolve().parents[1]


def test_active_registry_exactly_matches_contract() -> None:
    assert set(server._fastmcp_tool_names()) == expected_tool_names(
        advanced=server._ACTIVE_ADVANCED
    )
    assert skill_required_tool_names() <= set(server._fastmcp_tool_names())


def test_advanced_registry_exactly_matches_contract_in_fresh_process() -> None:
    code = """
import json
from hwpx_mcp_server import server
from hwpx_mcp_server.tool_contract import expected_tool_names
print(json.dumps({
    'actual': sorted(server._fastmcp_tool_names()),
    'expected': sorted(expected_tool_names(advanced=True)),
}))
"""
    env = dict(os.environ, HWPX_MCP_ADVANCED="1")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["actual"] == payload["expected"]


def test_recovered_tool_schemas_preserve_public_argument_names() -> None:
    async def schemas() -> dict[str, set[str]]:
        tools = await server.mcp.list_tools()
        return {
            tool.name: set(tool.inputSchema.get("properties", {}))
            for tool in tools
        }

    inputs = anyio.run(schemas)
    assert {"filename", "before_path", "require"} == inputs["verify_form_fill"]
    assert {"filename", "gold_path", "blank_path", "run_render", "expected_pages"} == inputs[
        "score_form_fill"
    ]
    assert {"filename", "blank_path"} == inputs["inspect_fill_residue"]
    assert {
        "filename",
        "review_md",
        "output",
        "render_check",
        "score_gold_path",
        "expected_pages",
    } == inputs["apply_evalplan_fill"]


def test_health_fails_exactly_when_required_tool_missing(monkeypatch) -> None:
    live = set(server._fastmcp_tool_names())
    live.remove("apply_table_ops")
    monkeypatch.setattr(server, "_fastmcp_tool_names", lambda: sorted(live))

    health = server.mcp_server_health()

    assert health["toolSurface"]["status"] == "skewed"
    assert health["toolSurface"]["missingExpectedTools"] == ["apply_table_ops"]
    assert health["toolSurface"]["missingSkillRequiredTools"] == ["apply_table_ops"]
    assert health["capability"]["ok"] is False
    assert health["capability"]["writesBlocked"] is True


def test_generated_mcp_contract_is_current() -> None:
    subprocess.run(
        [sys.executable, "scripts/render_tool_contract.py", "--check", "--skip-skill"],
        cwd=ROOT,
        check=True,
    )
    assert server.mcp_server_health()["toolSurface"]["contractHash"] == contract_hash()
