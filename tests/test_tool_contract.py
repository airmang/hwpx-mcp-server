from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest

from hwpx_mcp_server import __version__
from hwpx_mcp_server import server
from hwpx_mcp_server.tool_contract import (
    BASELINE_TOOL_SPECS,
    DOMAIN_SPECS,
    MIN_MCP_VERSION,
    MIN_PYTHON_HWPX,
    MIN_SKILL_VERSION,
    ToolClassification,
    ToolAvailability,
    bind_tool_specs,
    bound_tool_registry,
    classification_counts,
    contract_hash,
    expected_tool_names,
    skill_required_tool_names,
    validate_registered_tools,
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
INTERNAL_FIXTURE_QA_TOOLS = {
    "run_fixture_benchmark",
    "export_fixture_benchmark",
    "visual_review_fixture",
    "visual_repair_fixture",
}
COMPATIBILITY_TOOLS = {
    "analyze_template_formfit",
    "apply_body_ops",
    "apply_edits",
    "apply_evalplan_fill",
    "apply_table_ops",
    "apply_template_formfit",
    "create_comparison_table_document",
    "create_government_report_document",
    "create_proposal_document",
    "fill_by_path",
    "fill_form_field",
}
ADVANCED_TOOLS = {
    "lint_text_conventions",
    "object_find_by_attr",
    "object_find_by_tag",
    "package_get_text",
    "package_get_xml",
    "package_parts",
    "score_form_fill",
    "validate_structure",
}
DEPRECATED_TOOLS = {
    "analyze_quality_generation",
    "apply_edit",
    "apply_quality_generation",
    "plan_edit",
    "preview_edit",
}
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
    active = expected_tool_names(advanced=server._ACTIVE_ADVANCED)
    assert skill_required_tool_names() & active <= set(server._fastmcp_tool_names())
    assert WORKFLOW_TOOLS <= set(server._fastmcp_tool_names())
    assert RENDER_TOOLS <= set(server._fastmcp_tool_names())
    assert INTERNAL_FIXTURE_QA_TOOLS.isdisjoint(server._fastmcp_tool_names())
    assert AGENT_DOCUMENT_TOOLS <= set(server._fastmcp_tool_names())
    assert len(expected_tool_names(advanced=False)) == 121
    workflow_domains = [domain for domain in DOMAIN_SPECS if domain.key == "workflow"]
    assert len(workflow_domains) == 1
    assert set(workflow_domains[0].tools) == WORKFLOW_TOOLS
    render_domains = [domain for domain in DOMAIN_SPECS if domain.key == "real_hancom_render"]
    assert len(render_domains) == 1
    assert set(render_domains[0].tools) == RENDER_TOOLS
    assert "unverified" in render_domains[0].when_to_use
    assert all(domain.key not in {"visual_qa", "blind_eval"} for domain in DOMAIN_SPECS)
    assert all(domain.key != "private_practice" for domain in DOMAIN_SPECS)
    agent_domains = [domain for domain in DOMAIN_SPECS if domain.key == "agent_document"]
    assert len(agent_domains) == 1
    assert set(agent_domains[0].tools) == AGENT_DOCUMENT_TOOLS
    assert "전문 도구" in agent_domains[0].when_to_use


def test_release_contract_versions_counts_and_hash_are_exact() -> None:
    assert (MIN_PYTHON_HWPX, MIN_MCP_VERSION, MIN_SKILL_VERSION) == (
        "3.1.0",
        "4.0.0",
        "0.3.0",
    )
    assert len(expected_tool_names(advanced=False)) == 121
    assert len(expected_tool_names(advanced=True)) == 132
    assert len(skill_required_tool_names()) == 28
    assert REMOVED_PRACTICE_TOOLS.isdisjoint(expected_tool_names(advanced=True))


def test_baseline_classification_is_disjoint_exact_and_exhaustive() -> None:
    assert len(BASELINE_TOOL_SPECS) == 136
    assert len({spec.name for spec in BASELINE_TOOL_SPECS}) == 136
    assert classification_counts() == {
        "public": 108,
        "compatibility": 11,
        "advanced": 8,
        "deprecated": 5,
        "internal": 4,
    }
    internal = {
        spec.name
        for spec in BASELINE_TOOL_SPECS
        if spec.classification is ToolClassification.INTERNAL
    }
    assert internal == INTERNAL_FIXTURE_QA_TOOLS
    assert internal.isdisjoint(expected_tool_names(advanced=True))
    by_class = {
        classification: {
            spec.name
            for spec in BASELINE_TOOL_SPECS
            if spec.classification is classification
        }
        for classification in ToolClassification
    }
    assert by_class[ToolClassification.COMPATIBILITY] == COMPATIBILITY_TOOLS
    assert by_class[ToolClassification.ADVANCED] == ADVANCED_TOOLS
    assert by_class[ToolClassification.DEPRECATED] == DEPRECATED_TOOLS
    assert all(
        spec.replacement_tools
        for spec in BASELINE_TOOL_SPECS
        if spec.classification
        in {ToolClassification.COMPATIBILITY, ToolClassification.DEPRECATED}
    )
    assert all(
        spec.availability is ToolAvailability.INTERNAL_ONLY
        for spec in BASELINE_TOOL_SPECS
        if spec.name in INTERNAL_FIXTURE_QA_TOOLS
    )


def test_live_fastmcp_binding_matches_callable_descriptions_and_schemas() -> None:
    assert validate_registered_tools(server.mcp, server._TOOL_REGISTRY)["ok"] is True


def test_registry_binding_fails_closed_before_partial_registration() -> None:
    namespace = dict(server._SERVER_TOOL_BINDINGS)
    namespace.pop("apply_document_commands")

    with pytest.raises(RuntimeError, match="apply_document_commands: missing callable"):
        bind_tool_specs(namespace, advanced=None)


def test_canonical_form_fill_tools_use_typed_closed_top_level_schemas() -> None:
    bound = bound_tool_registry().by_name()
    analyze = bound["analyze_form_fill"].input_schema
    apply = bound["apply_form_fill"].input_schema
    verify = bound["verify_form_fill"].input_schema

    assert analyze["additionalProperties"] is False
    assert apply["additionalProperties"] is False
    assert verify["additionalProperties"] is False
    assert any("$ref" in option for option in analyze["properties"]["input_json"]["anyOf"])
    assert "$ref" in apply["properties"]["analysis"]
    assert set(verify["properties"]) == {"filename", "before_path", "require", "plan"}
    assert verify["properties"]["plan"]["$ref"].endswith(
        "MixedFormCompiledPlanInput"
    )


def test_missing_required_core_symbol_is_startup_fatal_and_never_ghost_registered() -> None:
    code = """
import hwpx
delattr(hwpx, 'create_document_from_plan')
import hwpx_mcp_server.server  # noqa: F401
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "create_document_from_plan" in result.stderr


def test_health_detects_live_schema_skew(monkeypatch) -> None:
    manager = server.mcp._tool_manager
    tool = manager._tools["apply_table_ops"]
    skewed = dict(tool.parameters)
    skewed["properties"] = dict(skewed["properties"])
    skewed["properties"].pop("ops")
    monkeypatch.setattr(tool, "parameters", skewed)

    health = server.mcp_server_health()

    assert health["toolSurface"]["status"] == "skewed"
    assert health["toolSurface"]["inputSchemaMismatches"] == ["apply_table_ops"]
    assert health["capability"]["writesBlocked"] is True


def test_advanced_registry_exactly_matches_contract_in_fresh_process() -> None:
    code = """
import json
from hwpx_mcp_server import server
from hwpx_mcp_server.tool_contract import expected_tool_names, validate_registered_tools
print(json.dumps({
    'actual': sorted(server._fastmcp_tool_names()),
    'expected': sorted(expected_tool_names(advanced=True)),
    'valid': validate_registered_tools(server.mcp, server._TOOL_REGISTRY)['ok'],
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
    assert len(payload["actual"]) == 132
    assert payload["valid"] is True


def test_recovered_tool_schemas_preserve_public_argument_names() -> None:
    async def schemas() -> dict[str, set[str]]:
        tools = await server.mcp.list_tools()
        return {
            tool.name: set(tool.inputSchema.get("properties", {}))
            for tool in tools
        }

    inputs = anyio.run(schemas)
    canonical = bound_tool_registry().by_name()
    assert {"filename", "before_path", "require", "plan"} == inputs[
        "verify_form_fill"
    ]
    assert {"filename", "gold_path", "blank_path", "run_render", "expected_pages"} == set(
        canonical["score_form_fill"].input_schema["properties"]
    )
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
    report = validate_registered_tools(server.mcp, server._TOOL_REGISTRY)
    report = {**report, "ok": False}
    report["actualOrder"] = [
        name for name in report["actualOrder"] if name != "apply_table_ops"
    ]
    report["missing"] = ["apply_table_ops"]
    monkeypatch.setattr(server, "validate_registered_tools", lambda *_: report)

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
    payload = json.loads(
        (ROOT / "docs" / "tool-contract.generated.json").read_text(encoding="utf-8")
    )
    assert payload["baselineToolCount"] == 136
    assert payload["classificationCounts"] == classification_counts()
    assert payload["contractHash"] == contract_hash()


def test_generated_versioned_contract_delta_is_current() -> None:
    subprocess.run(
        [sys.executable, "scripts/render_contract_delta.py", "--check"],
        cwd=ROOT,
        check=True,
    )
    payload = json.loads(
        (ROOT / "docs" / "tool-contract-delta-4.0.0.json").read_text(encoding="utf-8")
    )

    assert payload["baseline"] == {
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
    assert payload["target"] == {
        "versions": {
            "pythonHwpx": "3.1.0",
            "mcpServer": "4.0.0",
            "skill": "0.3.0",
        },
        "defaultToolCount": 121,
        "advancedToolCount": 132,
        "domainCount": 19,
        "skillRequiredToolCount": 28,
        "contractHash": contract_hash(),
    }
    assert {item["name"] for item in payload["removedTools"]} == INTERNAL_FIXTURE_QA_TOOLS
    assert all(item["alias"] is None for item in payload["removedTools"])
    assert len(payload["compatibility"]["facades"]) == 11
    assert len(payload["compatibility"]["deprecatedStubs"]) == 5
    assert payload["compatibility"]["aliases"] == []
    assert payload["compatibility"]["ghostRegistrations"] == []
    evidence = payload["registrationEvidence"]
    assert evidence["remainingRemovedRuntimeReferences"] == []
    assert evidence["activeRemovedNames"] == []
