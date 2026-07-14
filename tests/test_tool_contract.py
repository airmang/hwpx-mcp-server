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
PRACTICE_TOOLS = {
    "start_practice_scenario",
    "apply_practice_scenario",
    "start_practice_campaign",
    "get_practice_campaign",
    "continue_practice_campaign",
    "cancel_practice_campaign",
    "export_practice_campaign",
}
AGENT_DOCUMENT_TOOLS = {
    "get_document_node",
    "query_document_nodes",
    "apply_document_commands",
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
    assert PRACTICE_TOOLS <= set(server._fastmcp_tool_names())
    assert AGENT_DOCUMENT_TOOLS <= set(server._fastmcp_tool_names())
    assert len(expected_tool_names(advanced=False)) == 131
    assert len(
        expected_tool_names(advanced=False)
        - WORKFLOW_TOOLS
        - RENDER_TOOLS
        - BLIND_EVAL_TOOLS
        - PRACTICE_TOOLS
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
    practice_domains = [domain for domain in DOMAIN_SPECS if domain.key == "private_practice"]
    assert len(practice_domains) == 1
    assert set(practice_domains[0].tools) == PRACTICE_TOOLS
    assert "경로" in practice_domains[0].intent
    agent_domains = [domain for domain in DOMAIN_SPECS if domain.key == "agent_document"]
    assert len(agent_domains) == 1
    assert set(agent_domains[0].tools) == AGENT_DOCUMENT_TOOLS
    assert "전문 도구" in agent_domains[0].when_to_use


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


def test_campaign_tools_fail_closed_during_python_hwpx_package_skew() -> None:
    code = """
import json
from hwpx_mcp_server import server
print(json.dumps(server.start_practice_campaign(
    'PCMP-0123456789ABCDEFFEDC',
    'package-skew-check',
    True,
)))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload == {
        "ok": False,
        "state": "needs_review",
        "errorCode": "CAMPAIGN_UNAVAILABLE",
        "privateStorageCoordinatesExposed": False,
    }


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
    assert {"scenario_id", "idempotency_key"} == inputs["start_practice_scenario"]
    assert {
        "run_id",
        "destination_filename",
        "operation_kind",
        "operations",
        "use_suggested_operations",
        "confirm",
    } == inputs["apply_practice_scenario"]
    assert {"campaign_id", "idempotency_key", "confirm"} == inputs[
        "start_practice_campaign"
    ]
    assert {"campaign_id"} == inputs["get_practice_campaign"]
    assert {
        "campaign_id",
        "run_id",
        "max_steps",
        "approved",
        "decision_receipt_sha256",
    } == inputs["continue_practice_campaign"]
    assert {"campaign_id"} == inputs["cancel_practice_campaign"]
    assert {"campaign_id"} == inputs["export_practice_campaign"]
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


def test_unwired_campaign_tools_fail_closed_without_leaking_detail(monkeypatch) -> None:
    monkeypatch.setattr(server, "_PRACTICE_CAMPAIGN_SERVICE_OVERRIDE", None)
    monkeypatch.setattr(server, "_PRACTICE_CAMPAIGN_RUNTIME_FACTORY", None)

    result = server.start_practice_campaign(
        "PCMP-00000000000000000000", "campaign-start-001", confirm=True
    )

    assert result == {
        "ok": False,
        "state": "needs_review",
        "errorCode": "CAMPAIGN_UNAVAILABLE",
        "privateStorageCoordinatesExposed": False,
    }


def test_campaign_runtime_package_skew_keeps_tools_importable_and_fails_closed(
    monkeypatch,
) -> None:
    def skewed_factory(_namespace):
        raise ImportError("private package location must not escape")

    monkeypatch.setattr(server, "_PRACTICE_CAMPAIGN_SERVICE_OVERRIDE", None)
    monkeypatch.setattr(server, "_PRACTICE_CAMPAIGN_RUNTIME_FACTORY", skewed_factory)

    result = server.start_practice_campaign(
        "PCMP-00000000000000000000", "campaign-start-001", confirm=True
    )

    assert "start_practice_campaign" in server._fastmcp_tool_names()
    assert result == {
        "ok": False,
        "state": "needs_review",
        "errorCode": "CAMPAIGN_UNAVAILABLE",
        "privateStorageCoordinatesExposed": False,
    }
    assert "location" not in json.dumps(result)
