from __future__ import annotations

import hashlib
import json
from pathlib import Path

import anyio
import pytest

from hwpx_mcp_server import server
from hwpx_mcp_server.blind_eval import (
    FIXTURE_MANIFEST_SCHEMA,
    export_fixture_benchmark,
    run_fixture_benchmark,
)


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical_digest(value: object) -> str:
    return _digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def _manifest(tmp_path: Path) -> Path:
    task = {"request": "합성 fixture 문서를 검토한다", "synthetic": True}
    clients = [
        {"clientId": "fixture-alpha", "adapterFamily": "fixture-host-a"},
        {"clientId": "fixture-beta", "adapterFamily": "fixture-host-b"},
        {"clientId": "fixture-gamma", "adapterFamily": "fixture-host-c"},
    ]
    runs = []
    judgments = []
    for index, client in enumerate(clients, start=1):
        artifact = tmp_path / f"private-origin-{index}.hwpx"
        artifact.write_bytes(f"fixture-artifact-{index}".encode())
        opaque = f"artifact-{index:04d}"
        runs.append(
            {
                "runId": f"run-{index}",
                "workOrderId": "wo-001",
                "clientId": client["clientId"],
                "workflowReceipt": {
                    "schemaVersion": "hwpx.workflow.v1",
                    "workflowId": f"wf-private-{index}",
                    "family": "read_extract",
                    "state": "completed",
                    "terminal": True,
                    "toolSpecHash": "contract-123",
                    "versions": {"mcp": "2.21.0", "pythonHwpx": "2.27.0"},
                    "benchmarkProvenance": {"clientId": client["clientId"]},
                },
                "artifact": {"path": artifact.name, "contentHash": _digest(artifact.read_bytes())},
                "anonymizationEvidence": {
                    "opaqueArtifactId": opaque,
                    "metadataScanComplete": True,
                    "revealingMetadataKeys": [],
                    "scannerId": "fixture-metadata-scanner",
                    "scannerVersion": "1",
                    "evidenceHash": _digest(f"scan-{index}".encode()),
                },
            }
        )
        for reviewer in ("judge-a", "judge-b"):
            judgments.append(
                {
                    "opaqueArtifactId": opaque,
                    "reviewerId": reviewer,
                    "reviewerType": "fixture_agent_judge",
                    "humanLabels": False,
                    "provenanceVisible": False,
                    "acceptedWithoutManualHwpxEdit": True,
                }
            )
    data = {
        "schema": FIXTURE_MANIFEST_SCHEMA,
        "benchmarkId": "s070-fixture-test",
        "assurance": "fixture",
        "frozen": True,
        "workOrders": [
            {
                "workOrderId": "wo-001",
                "family": "read_extract",
                "taskBrief": task,
                "taskBriefHash": _canonical_digest(task),
            }
        ],
        "clients": clients,
        "runs": runs,
        "judgments": judgments,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_fixture_benchmark_receipt_is_complete_but_non_promotable(tmp_path: Path) -> None:
    result = run_fixture_benchmark(_manifest(tmp_path), output_dir=tmp_path / "result")

    assert result["ok"] is True, result["failReasons"]
    assert result["receipt"]["coverageComplete"] is True
    assert result["receipt"]["provenanceComplete"] is True
    assert result["receipt"]["anonymizationComplete"] is True
    assert result["receipt"]["humanControls"] is False
    assert result["receipt"]["humanJudges"] is False
    assert result["receipt"]["realAgentClients"] is False
    assert result["receipt"]["realHancomVerified"] is False
    assert result["receipt"]["renderChecked"] is False
    assert result["receipt"]["replacementClaimAllowed"] is False

    exported = export_fixture_benchmark(
        result["resultManifestPath"], output_dir=tmp_path / "public",
    )
    assert exported["ok"] is True
    public = json.loads(Path(exported["outputPath"]).read_text(encoding="utf-8"))
    encoded = json.dumps(public)
    assert "fixture-alpha" not in encoded
    assert "wf-private" not in encoded
    assert "private-origin" not in encoded
    assert public["privateProvenanceIncluded"] is False
    assert {item["opaqueArtifactId"] for item in public["artifacts"]} == {
        "artifact-0001", "artifact-0002", "artifact-0003",
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda data: data["runs"].pop(), "incomplete_work_order_client_coverage"),
        (lambda data: data["runs"][0]["anonymizationEvidence"].update(metadataScanComplete=False), "metadata_scan_incomplete"),
        (lambda data: data["judgments"][0].update(humanLabels=True), "judgment_provenance_misrepresented"),
    ],
)
def test_fixture_benchmark_fails_closed_on_coverage_anonymization_or_label_provenance(
    tmp_path: Path, mutation, reason: str,
) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    mutation(data)
    manifest.write_text(json.dumps(data), encoding="utf-8")

    result = run_fixture_benchmark(manifest)
    assert result["ok"] is False
    assert any(reason in item for item in result["failReasons"])


def test_tool_spec_registers_frozen_benchmark_surface() -> None:
    names = set(server._fastmcp_tool_names())
    assert {"run_fixture_benchmark", "export_fixture_benchmark"} <= names

    async def schemas() -> dict[str, set[str]]:
        return {
            tool.name: set(tool.inputSchema.get("properties", {}))
            for tool in await server.mcp.list_tools()
            if tool.name in {"run_fixture_benchmark", "export_fixture_benchmark"}
        }

    inputs = anyio.run(schemas)
    assert inputs["run_fixture_benchmark"] == {"manifest_path", "output_dir", "strict"}
    assert inputs["export_fixture_benchmark"] == {"result_manifest_path", "output_dir", "strict"}
