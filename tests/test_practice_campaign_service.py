from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest

from hwpx.practice import (
    PRACTICE_RUN_SCHEMA,
    assert_receipt_safe,
    build_campaign_manifest,
    practice_run_id,
    validate_run_receipt,
)
from hwpx_mcp_server.practice.campaign_service import PracticeCampaignService
from hwpx_mcp_server.practice.dispatch import (
    PracticeDispatchError,
    PracticeWorkflowDispatcher,
    ResolvedPracticeTask,
)
from hwpx_mcp_server.practice.queue import PracticeCampaignQueue
from hwpx_mcp_server.practice.sandbox import PracticeSandboxManager
from hwpx_mcp_server.tool_contract import contract_hash
from hwpx_mcp_server.workflow.service import WorkflowService
from hwpx_mcp_server.workflow.store import WorkflowStore


def _digest(value: object) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _provenance(*, tool_spec_hash: str | None = None) -> dict[str, Any]:
    return {
        "stack": {
            "core": {"version": "2.28.0.dev1", "sha256": _digest("core")},
            "server": {"version": "2.22.0.dev1", "sha256": _digest("server")},
            "skill": {"version": "0.1.29.dev1", "sha256": _digest("skill")},
        },
        "toolSpec": {
            "version": "tool-spec/v1",
            "sha256": tool_spec_hash or contract_hash(),
        },
        "evaluator": {
            "version": "practice-evaluator/v1",
            "sha256": _digest("evaluator"),
        },
    }


def _budgets() -> dict[str, int]:
    return {
        "toolCalls": 8,
        "attempts": 2,
        "repairRounds": 3,
        "elapsedSeconds": 300,
        "costMicrounits": 100_000,
        "artifactBytes": 1_000_000,
    }


def _fixture_contract(source: Path, provenance: Mapping[str, Any]):
    source_hash = _digest(source.read_bytes())
    scenario_ref = {
        "scenarioId": "SCN-00000000000000000001",
        "scenarioSha256": _digest("scenario"),
        "runnerManifestSha256": _digest("runner"),
        "derivativeSha256": _digest("derivative"),
        "startArtifactId": "ART-0001",
        "startArtifactSha256": source_hash,
    }
    dispatch = {
        "slot": 0,
        "dispatchKey": "DSP-00000000000000000001",
        "seedSha256": _digest("dispatch"),
    }
    identity = {
        "schema": PRACTICE_RUN_SCHEMA,
        "scenarioRef": scenario_ref,
        "dispatch": dispatch,
        "provenance": dict(provenance),
        "budgets": _budgets(),
    }
    run_id = practice_run_id(identity)
    run_ref = {
        "slot": 0,
        "runId": run_id,
        "scenarioId": scenario_ref["scenarioId"],
        "scenarioSha256": scenario_ref["scenarioSha256"],
        "runnerManifestSha256": scenario_ref["runnerManifestSha256"],
        "derivativeSha256": scenario_ref["derivativeSha256"],
        "startArtifactSha256": scenario_ref["startArtifactSha256"],
        "family": "unknown_form_fill",
        "difficulty": "routine",
        "budgets": _budgets(),
    }
    manifest = build_campaign_manifest(
        scenario_manifest_sha256=_digest("scenario-manifest"),
        selection={
            "seedSha256": _digest("selection"),
            "strategyVersion": "coverage-weakness/v1",
            "policySha256": _digest("policy"),
        },
        provenance=provenance,
        budgets={
            "runs": 1,
            "toolCalls": 8,
            "elapsedSeconds": 300,
            "costMicrounits": 100_000,
            "artifactBytes": 1_000_000,
        },
        runs=[run_ref],
    )
    task = ResolvedPracticeTask(
        scenario_ref=scenario_ref,
        dispatch=dispatch,
        source_artifact=source,
        workflow_family="unknown_form_fill",
        parameters={"operationKind": "table", "operations": []},
    )
    return manifest, run_ref, task


def _namespace(calls: list[str]):
    def scan(**arguments):
        calls.append("scan_form_guidance")
        return {"ok": True}

    def apply(**arguments):
        calls.append("apply_table_ops")
        source = Path(arguments["filename"])
        output = Path(arguments["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)
        return {"ok": True, "openSafety": {"ok": True}}

    def residue(**arguments):
        calls.append("inspect_fill_residue")
        return {"ok": True}

    def verify(**arguments):
        calls.append("verify_form_fill")
        return {"ok": True}

    return {
        "scan_form_guidance": scan,
        "apply_table_ops": apply,
        "inspect_fill_residue": residue,
        "verify_form_fill": verify,
    }


@dataclass(frozen=True)
class _Lease:
    campaign_id: str
    manifest_sha256: str
    slot: int
    run_id: str
    scenario_id: str
    attempt: int = 1
    lease_token: str = "private-lease-token"
    dispatch_generation: int = 1
    dispatch_idempotency_key: str = "private-dispatch-key"


class _Queue:
    def __init__(self, manifest: Mapping[str, Any]) -> None:
        run = manifest["runs"][0]
        self.manifest = dict(manifest)
        self.lease = _Lease(
            campaign_id=manifest["campaignId"],
            manifest_sha256=manifest["manifestSha256"],
            slot=run["slot"],
            run_id=run["runId"],
            scenario_id=run["scenarioId"],
        )
        self.enqueued = False
        self.claimed = False
        self.cancelled = False
        self.enqueue_key: str | None = None
        self.usage = {key: 0 for key in _budgets()}
        self.terminal_receipt: dict[str, Any] | None = None

    def enqueue(self, manifest, enqueue_key=None):
        replay = self.enqueued
        self.enqueued = True
        self.enqueue_key = enqueue_key
        return {**self.status(manifest["campaignId"]), "idempotentReplay": replay}

    def claim(self, worker_id, *, campaign_id=None, lease_seconds=60, now=None):
        if self.claimed or self.terminal_receipt or self.cancelled:
            return None
        self.claimed = True
        return self.lease

    def resume_lease(self, run_id, worker_id, *, lease_seconds=60, now=None):
        if run_id != self.lease.run_id or self.terminal_receipt is not None:
            raise KeyError(run_id)
        return self.lease

    def account(self, lease, **usage):
        mapping = {
            "tool_calls": "toolCalls",
            "repair_rounds": "repairRounds",
            "elapsed_seconds": "elapsedSeconds",
            "cost_microunits": "costMicrounits",
            "artifact_bytes": "artifactBytes",
        }
        for key, value in usage.items():
            self.usage[mapping[key]] += value
        return {
            "schema": "hwpx.practice-accounting/v1",
            "runId": lease.run_id,
            "usage": dict(self.usage),
        }

    def terminalize(self, lease, receipt, now=None):
        validated = validate_run_receipt(receipt)
        if self.terminal_receipt is not None and self.terminal_receipt != validated:
            raise RuntimeError("terminal conflict")
        replay = self.terminal_receipt is not None
        self.terminal_receipt = validated
        return {
            "schema": "hwpx.practice-terminalization/v1",
            "runId": lease.run_id,
            "receiptSha256": validated["receiptSha256"],
            "idempotentReplay": replay,
        }

    def cancel(self, campaign_id, now=None):
        self.cancelled = True
        return self.status(campaign_id)

    def recover(self, now=None, campaign_id=None):
        return self.status(campaign_id)

    def resume(self, campaign_id, now=None):
        return self.status(campaign_id)

    def status(self, campaign_id):
        terminal_count = int(self.terminal_receipt is not None)
        state = (
            "cancelled"
            if self.cancelled
            else "completed"
            if terminal_count
            else "running"
            if self.claimed
            else "queued"
        )
        return {
            "schema": "hwpx.practice-campaign-status/v1",
            "campaignId": campaign_id,
            "manifestSha256": self.manifest["manifestSha256"],
            "state": state,
            "expectedRunCount": 1,
            "terminalReceiptCount": terminal_count,
            "incompleteSlots": [] if terminal_count else [0],
            "cancelRequested": self.cancelled,
        }

    def receipts(self, campaign_id):
        return [self.terminal_receipt] if self.terminal_receipt is not None else []


@pytest.fixture
def campaign_fixture(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "source"
    practice_root = tmp_path / "practice"
    storage_root = practice_root / "runs"
    source_root.mkdir()
    practice_root.mkdir()
    storage_root.mkdir()
    source = source_root / "synthetic.hwpx"
    source.write_bytes(b"synthetic leap-b fixture")
    provenance = _provenance()
    manifest, run_ref, task = _fixture_contract(source, provenance)
    calls: list[str] = []
    monkeypatch.setattr(
        "hwpx_mcp_server.workflow.service._package_version",
        lambda name: {
            "python-hwpx": provenance["stack"]["core"]["version"],
            "hwpx-mcp-server": provenance["stack"]["server"]["version"],
        }[name],
    )
    workflow = WorkflowService(
        _namespace(calls), store=WorkflowStore(tmp_path / "workflow.sqlite3")
    )
    sandbox = PracticeSandboxManager(source_root, practice_root, storage_root)
    dispatcher = PracticeWorkflowDispatcher(
        workflow, sandbox, runtime_provenance=lambda: provenance
    )
    queue = _Queue(manifest)
    service = PracticeCampaignService(
        queue,
        dispatcher,
        manifest_resolver=lambda campaign_id: manifest,
        task_resolver=lambda selected_manifest, lease: task,
    )
    return {
        "source": source,
        "manifest": manifest,
        "runRef": run_ref,
        "task": task,
        "calls": calls,
        "dispatcher": dispatcher,
        "queue": queue,
        "service": service,
    }


def test_start_is_opaque_confirmed_and_idempotent(campaign_fixture) -> None:
    fixture = campaign_fixture
    service = fixture["service"]
    campaign_id = fixture["manifest"]["campaignId"]

    preview = service.start(campaign_id, idempotency_key="campaign-start-001")
    first = service.start(
        campaign_id, idempotency_key="campaign-start-001", confirm=True
    )
    replay = service.start(
        campaign_id, idempotency_key="campaign-start-001", confirm=True
    )

    assert preview["requiresConfirmation"] is True
    assert first["idempotentReplay"] is False
    assert replay["idempotentReplay"] is True
    assert fixture["queue"].enqueue_key.startswith("IDEM-")
    assert len(fixture["queue"].enqueue_key) == 25
    encoded = json.dumps([preview, first, replay], ensure_ascii=False)
    assert ".hwpx" not in encoded
    assert str(fixture["source"]) not in encoded
    assert_receipt_safe([preview, first, replay])


def test_invalid_continue_limit_does_not_claim_work(campaign_fixture) -> None:
    fixture = campaign_fixture
    with pytest.raises(PracticeDispatchError, match="CONTINUE_LIMIT_INVALID"):
        fixture["service"].continue_campaign(
            fixture["manifest"]["campaignId"], max_steps=0
        )
    assert fixture["queue"].claimed is False


def test_decision_binding_runs_high_level_workflow_once_and_exports_receipt(
    campaign_fixture,
) -> None:
    fixture = campaign_fixture
    service = fixture["service"]
    campaign_id = fixture["manifest"]["campaignId"]
    source_before = fixture["source"].read_bytes()
    service.start(campaign_id, idempotency_key="campaign-start-001", confirm=True)

    decision = service.continue_campaign(campaign_id, max_steps=8)
    assert decision["boundary"]["state"] == "decision"
    assert decision["boundary"]["decisionRequired"] is True
    assert len(decision["boundary"]["decisionActionSha256"]) == 64
    assert "apply_table_ops" not in fixture["calls"]

    with pytest.raises(PracticeDispatchError, match="DECISION_RECEIPT_MISMATCH"):
        service.continue_campaign(
            campaign_id,
            run_id=fixture["runRef"]["runId"],
            approved=True,
            decision_receipt_sha256="0" * 64,
        )

    terminal = service.continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )
    receipt = fixture["queue"].terminal_receipt
    assert terminal["boundary"]["state"] == "completed"
    assert receipt is not None
    assert validate_run_receipt(receipt) == receipt
    assert fixture["calls"].count("apply_table_ops") == 1
    assert fixture["source"].read_bytes() == source_before
    assert all("path" not in key.casefold() for key in receipt)

    replay = fixture["dispatcher"].advance(
        fixture["manifest"],
        fixture["runRef"],
        fixture["task"],
        max_steps=8,
    )
    assert replay.run_receipt == receipt
    assert replay.usage_delta == {
        "toolCalls": 0,
        "attempts": 0,
        "repairRounds": 0,
        "elapsedSeconds": 0,
        "costMicrounits": 0,
        "artifactBytes": 0,
    }
    assert fixture["calls"].count("apply_table_ops") == 1
    exported = service.export(campaign_id)
    assert exported["receipts"] == [receipt]
    assert len(exported["exportSha256"]) == 64
    assert_receipt_safe(exported)


def test_tool_spec_skew_fails_before_sandbox_or_dispatch(campaign_fixture) -> None:
    fixture = campaign_fixture
    skew = json.loads(json.dumps(fixture["manifest"]))
    skew["provenance"]["toolSpec"]["sha256"] = "f" * 16
    # Rebuild so the content address remains valid while expected provenance is wrong.
    skew = build_campaign_manifest(
        scenario_manifest_sha256=skew["scenarioManifestSha256"],
        selection=skew["selection"],
        provenance=skew["provenance"],
        budgets=skew["budgets"],
        runs=skew["runs"],
    )

    with pytest.raises(PracticeDispatchError, match="TOOL_SPEC_SKEW"):
        fixture["dispatcher"].assert_runtime_provenance(skew)
    assert fixture["calls"] == []


def test_stale_source_revision_fails_closed_before_decision_execution(
    campaign_fixture,
) -> None:
    fixture = campaign_fixture
    first = fixture["dispatcher"].advance(
        fixture["manifest"], fixture["runRef"], fixture["task"], max_steps=8
    )
    assert first.boundary["state"] == "decision"
    fixture["source"].write_bytes(b"externally changed synthetic fixture")

    with pytest.raises(PracticeDispatchError, match="STALE_DOCUMENT_REVISION"):
        fixture["dispatcher"].advance(
            fixture["manifest"],
            fixture["runRef"],
            fixture["task"],
            max_steps=8,
            approved=True,
            decision_receipt_sha256=first.boundary["decisionReceiptSha256"],
        )
    assert "apply_table_ops" not in fixture["calls"]


def test_campaign_budget_maps_exactly_and_caps_repairs(campaign_fixture) -> None:
    fixture = campaign_fixture
    _, _, raw = fixture["dispatcher"]._prepare(
        fixture["manifest"], fixture["runRef"], fixture["task"]
    )
    record = fixture["dispatcher"].workflow_service.store.get(raw["workflowId"])
    budget = record.work_order.budget
    assert budget.max_attempts == _budgets()["attempts"]
    assert budget.max_tool_calls == _budgets()["toolCalls"]
    assert budget.max_elapsed_seconds == _budgets()["elapsedSeconds"]
    assert budget.max_repair_rounds == 3


def test_service_integrates_with_durable_queue(
    campaign_fixture, tmp_path: Path
) -> None:
    fixture = campaign_fixture
    queue_root = tmp_path / "durable-queue"
    queue = PracticeCampaignQueue(queue_root)
    service = PracticeCampaignService(
        queue,
        fixture["dispatcher"],
        manifest_resolver=lambda campaign_id: fixture["manifest"],
        task_resolver=lambda selected_manifest, lease: fixture["task"],
    )
    campaign_id = fixture["manifest"]["campaignId"]
    service.start(campaign_id, idempotency_key="durable-queue-start", confirm=True)

    decision = service.continue_campaign(campaign_id, max_steps=8)
    terminal = service.continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )

    assert terminal["boundary"]["state"] == "completed"
    assert terminal["status"]["state"] == "completed"
    receipts = queue.receipts(campaign_id)
    assert len(receipts) == 1
    assert receipts[0]["usage"]["attempts"] == 1
    assert service.export(campaign_id)["receipts"] == receipts
