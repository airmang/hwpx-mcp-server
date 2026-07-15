from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import anyio

from hwpx_mcp_server import __version__
from hwpx_mcp_server import server
from hwpx_mcp_server.tool_contract import (
    DOMAIN_SPECS,
    MIN_MCP_VERSION,
    MIN_PYTHON_HWPX,
    MIN_SKILL_VERSION,
    contract_hash,
    expected_tool_names,
    skill_required_tool_names,
)


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_TOOLS = {
    "start_workflow",
    "get_workflow",
    "get_workflow_result",
    "continue_workflow",
    "approve_workflow_decision",
    "cancel_workflow",
    "resume_workflow",
}
RENDER_TOOLS = {
    "render_submit",
    "render_status",
    "render_cancel",
    "render_health",
}
BLIND_EVAL_TOOLS = {"run_fixture_benchmark", "export_fixture_benchmark"}
AGENT_DOCUMENT_TOOLS = {
    "get_document_node",
    "query_document_nodes",
    "apply_document_commands",
    "dump_document_blueprint",
    "replay_document_blueprint",
}
REMOVED_PRACTICE_TOOLS = {
    "start_practice_scenario",
    "apply_practice_scenario",
    "start_practice_campaign",
    "get_practice_campaign",
    "continue_practice_campaign",
    "cancel_practice_campaign",
    "export_practice_campaign",
}


def test_active_registry_exactly_matches_contract() -> None:
    assert server.mcp._mcp_server.version == __version__
    assert set(server._fastmcp_tool_names()) == expected_tool_names(
        advanced=server._ACTIVE_ADVANCED
    )
    assert skill_required_tool_names() <= set(server._fastmcp_tool_names())
    assert WORKFLOW_TOOLS <= set(server._fastmcp_tool_names())
    assert RENDER_TOOLS <= set(server._fastmcp_tool_names())
    assert BLIND_EVAL_TOOLS <= set(server._fastmcp_tool_names())
    assert AGENT_DOCUMENT_TOOLS <= set(server._fastmcp_tool_names())
    assert len(expected_tool_names(advanced=False)) == 126
    assert len(
        expected_tool_names(advanced=False)
        - WORKFLOW_TOOLS
        - RENDER_TOOLS
        - BLIND_EVAL_TOOLS
        - AGENT_DOCUMENT_TOOLS
    ) == 108
    workflow_domains = [domain for domain in DOMAIN_SPECS if domain.key == "workflow"]
    assert len(workflow_domains) == 1
    assert set(workflow_domains[0].tools) == WORKFLOW_TOOLS
    render_domains = [domain for domain in DOMAIN_SPECS if domain.key == "real_hancom_render"]
    assert len(render_domains) == 1
    assert set(render_domains[0].tools) == RENDER_TOOLS
    assert "unverified" in render_domains[0].when_to_use
    blind_domains = [domain for domain in DOMAIN_SPECS if domain.key == "blind_eval"]
    assert len(blind_domains) == 1
    assert set(blind_domains[0].tools) == BLIND_EVAL_TOOLS
    assert "승격" in blind_domains[0].when_to_use
    assert all(domain.key != "private_practice" for domain in DOMAIN_SPECS)
    agent_domains = [domain for domain in DOMAIN_SPECS if domain.key == "agent_document"]
    assert len(agent_domains) == 1
    assert set(agent_domains[0].tools) == AGENT_DOCUMENT_TOOLS
    assert "전문 도구" in agent_domains[0].when_to_use


def test_release_contract_versions_counts_and_hash_are_exact() -> None:
    assert (MIN_PYTHON_HWPX, MIN_MCP_VERSION, MIN_SKILL_VERSION) == (
        "3.0.0",
        "3.0.0",
        "0.2.0",
    )
    assert len(expected_tool_names(advanced=False)) == 126
    assert len(expected_tool_names(advanced=True)) == 136
    assert len(skill_required_tool_names()) == 30
    assert contract_hash() == "76d143ccc0787828"
    assert REMOVED_PRACTICE_TOOLS.isdisjoint(expected_tool_names(advanced=True))


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
    assert {
        "family",
        "idempotency_key",
        "source_path",
        "output_path",
        "expected_revision",
        "parameters",
        "budget",
        "policy",
    } == inputs["start_workflow"]
    assert {"workflow_id", "approved", "action_hash"} == inputs["approve_workflow_decision"]
    assert {"workflow_id", "action_hash"} == inputs["get_workflow_result"]
    assert {
        "filename",
        "path",
        "depth",
        "child_limit",
        "expected_revision",
    } == inputs["get_document_node"]
    assert {
        "filename",
        "selector",
        "limit",
        "node_depth",
        "child_limit",
        "expected_revision",
    } == inputs["query_document_nodes"]
    assert {
        "filename",
        "output",
        "commands",
        "expected_revision",
        "idempotency_key",
        "dry_run",
        "quality",
        "verification_requirements",
        "overwrite",
    } == inputs["apply_document_commands"]
    assert {
        "filename",
        "path",
        "mode",
        "expected_revision",
        "output",
        "overwrite",
        "include_assets",
        "require_replayable",
        "include_manifest",
    } == inputs["dump_document_blueprint"]
    assert {"request"} == inputs["replay_document_blueprint"]


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


def test_generated_versioned_contract_delta_is_current() -> None:
    subprocess.run(
        [sys.executable, "scripts/render_contract_delta.py", "--check"],
        cwd=ROOT,
        check=True,
    )
    payload = json.loads(
        (ROOT / "docs" / "tool-contract-delta-3.0.0.json").read_text(encoding="utf-8")
    )

    assert payload["baseline"] == {
        "versions": {
            "pythonHwpx": "2.29.2",
            "mcpServer": "2.23.1",
            "skill": "0.1.31",
        },
        "defaultToolCount": 133,
        "advancedToolCount": 143,
        "domainCount": 22,
        "skillRequiredToolCount": 32,
        "contractHash": "0f9e1dcb7c646202",
    }
    assert payload["target"] == {
        "versions": {
            "pythonHwpx": "3.0.0",
            "mcpServer": "3.0.0",
            "skill": "0.2.0",
        },
        "defaultToolCount": 126,
        "advancedToolCount": 136,
        "domainCount": 21,
        "skillRequiredToolCount": 30,
        "contractHash": "76d143ccc0787828",
    }
    assert {item["name"] for item in payload["removedTools"]} == REMOVED_PRACTICE_TOOLS
    assert all(item["alias"] is None for item in payload["removedTools"])
    assert all(
        item["replacementKind"] == "none" and item["replacementTools"] == []
        for item in payload["removedTools"]
    )
    assert payload["compatibility"] == {
        "aliases": [],
        "deprecatedStubs": [],
        "ghostRegistrations": [],
    }
    assert payload["replacementGuidance"] == {
        "oneToOnePublicReplacement": False,
        "internalScenarioCampaignDestination": "workspace-private QA harness",
        "publicDocumentWorkAlternatives": [
            "apply_document_commands",
            "apply_evalplan_fill",
            "scan_form_guidance",
            "apply_table_ops",
            "apply_body_ops",
            "verify_form_fill",
        ],
    }
    evidence = payload["callSiteRuntimeRemovalEvidence"]
    assert evidence["runtimePackagePresent"] is False
    assert evidence["remainingRuntimeReferences"] == []
    assert evidence["activeRemovedNames"] == []
