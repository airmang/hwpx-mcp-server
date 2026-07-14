from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest
import hwpx_mcp_server.practice.queue as queue_module

from hwpx.practice import (
    PRACTICE_RUN_SCHEMA,
    assert_receipt_safe,
    build_campaign_manifest,
    practice_run_id,
    validate_run_receipt,
)
from hwpx_mcp_server.practice.campaign_service import (
    PracticeCampaignError,
    PracticeCampaignService,
)
from hwpx_mcp_server.practice.dispatch import (
    PracticeDispatchError,
    PracticeWorkflowDispatcher,
    ResolvedPracticeTask,
)
from hwpx_mcp_server.practice.queue import PracticeCampaignQueue
from hwpx_mcp_server.practice.sandbox import (
    PracticeSandboxError,
    PracticeSandboxManager,
)
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


def _terminal_verifier(
    task: ResolvedPracticeTask, outcome: Any
) -> dict[str, Any]:
    receipt = validate_run_receipt(outcome.run_receipt)
    output = next(item for item in receipt["artifacts"] if item["role"] == "output")
    return {
        "schema": "hwpx.practice-terminal-verifier/v1",
        "runId": receipt["runId"],
        "scenarioId": receipt["scenarioId"],
        "terminalReceiptSha256": receipt["receiptSha256"],
        "outputArtifact": {"sha256": output["sha256"], "bytes": output["bytes"]},
        "evaluationPolicySha256": task.evaluation_policy_sha256,
        "evaluator": dict(outcome.evaluator_provenance),
        "idempotencyKey": outcome.artifact_hook_idempotency_key,
        "eligibleForSuccess": True,
        "privateStorageCoordinatesExposed": False,
    }


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
            "authenticationKeyId": "EVK-0123456789ABCDEF0123",
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


def _new_storage_root(practice_root: Path, name: str) -> Path:
    root = practice_root / name
    root.mkdir()
    return root


def _fixture_contract(
    source: Path,
    provenance: Mapping[str, Any],
    *,
    budgets: Mapping[str, int] | None = None,
):
    budget_values = dict(budgets or _budgets())
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
        "budgets": budget_values,
    }
    run_id = practice_run_id(identity)
    run_ref = {
        "slot": 0,
        "runId": run_id,
        "scenarioId": scenario_ref["scenarioId"],
        "scenarioSha256": scenario_ref["scenarioSha256"],
        "evaluationPolicySha256": _digest("evaluation-policy"),
        "runnerManifestSha256": scenario_ref["runnerManifestSha256"],
        "derivativeSha256": scenario_ref["derivativeSha256"],
        "startArtifactId": scenario_ref["startArtifactId"],
        "startArtifactSha256": scenario_ref["startArtifactSha256"],
        "family": "unknown_form_fill",
        "difficulty": "routine",
        "budgets": budget_values,
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
            "toolCalls": budget_values["toolCalls"],
            "elapsedSeconds": budget_values["elapsedSeconds"],
            "costMicrounits": budget_values["costMicrounits"],
            "artifactBytes": budget_values["artifactBytes"],
        },
        runs=[run_ref],
    )
    task = ResolvedPracticeTask(
        scenario_ref=scenario_ref,
        dispatch=dispatch,
        source_artifact=source,
        workflow_family="unknown_form_fill",
        parameters={"operationKind": "table", "operations": []},
        evaluation_policy_sha256=run_ref["evaluationPolicySha256"],
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
    recovery_only: bool = False


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
        self.failed_receipt: dict[str, Any] | None = None
        self.last_account: dict[str, int] | None = None
        self.authorize_calls = 0
        self.cleanup_acks: set[tuple[str, str]] = set()

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

    def claim_run(self, run_id, worker_id, *, lease_seconds=60, now=None):
        if run_id != self.lease.run_id or self.terminal_receipt is not None:
            return None
        self.claimed = True
        return self.lease

    def authorize(self, lease, now=None):
        self.authorize_calls += 1
        run_budget = self.manifest["runs"][0]["budgets"]
        campaign_budget = self.manifest["budgets"]
        run_remaining = {
            key: max(0, run_budget[key] - self.usage[key])
            for key in _budgets()
        }
        run_remaining["attempts"] = max(0, run_budget["attempts"] - lease.attempt)
        campaign_remaining = {
            key: max(0, campaign_budget[key] - self.usage[key])
            for key in (
                "toolCalls",
                "elapsedSeconds",
                "costMicrounits",
                "artifactBytes",
            )
        }
        effective = dict(run_remaining)
        for key, amount in campaign_remaining.items():
            effective[key] = min(effective[key], amount)
        recovery_only = bool(lease.recovery_only)
        return {
            "schema": "hwpx.practice-campaign-queue-authorization/v1",
            "campaignId": lease.campaign_id,
            "runId": lease.run_id,
            "authorized": True,
            "recoveryOnly": recovery_only,
            "mutationAllowed": not recovery_only,
            "runRemaining": run_remaining,
            "campaignRemaining": campaign_remaining,
            "effectiveRemaining": effective,
            "privateStorageCoordinatesExposed": False,
        }

    def account(self, lease, **usage):
        self.last_account = dict(usage)
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
            "terminalReceipt": None,
        }

    def terminalize(self, lease, receipt, now=None):
        validated = validate_run_receipt(receipt)
        if self.terminal_receipt is not None and self.terminal_receipt != validated:
            raise RuntimeError("terminal conflict")
        self.terminal_receipt = validated
        return validated

    def fail(self, lease, reason, *, state="failed", now=None):
        if self.terminal_receipt is not None:
            return self.terminal_receipt
        receipt = {
            "schema": "hwpx.practice-failure/v1",
            "runId": lease.run_id,
            "scenarioId": lease.scenario_id,
            "state": state,
            "terminalReason": reason,
            "receiptSha256": _digest(
                {"runId": lease.run_id, "state": state, "reason": reason}
            ),
            "privateStorageCoordinatesExposed": False,
        }
        self.failed_receipt = receipt
        return receipt

    def cancel(self, campaign_id, now=None):
        self.cancelled = True
        return self.status(campaign_id)

    def recover(self, now=None, campaign_id=None):
        return self.status(campaign_id)

    def resume(self, campaign_id, now=None):
        return self.status(campaign_id)

    def status(self, campaign_id):
        terminal_count = int(
            self.terminal_receipt is not None or self.failed_receipt is not None
        )
        state = (
            "cancelled"
            if self.cancelled
            else "failed"
            if self.failed_receipt is not None
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
        receipt = self.terminal_receipt or self.failed_receipt
        return [receipt] if receipt is not None else []

    def terminal_cleanup_candidates(self, *, limit=64):
        if self.terminal_receipt is None and self.failed_receipt is None:
            return ()
        run = self.manifest["runs"][0]
        if (run["runId"], run["startArtifactSha256"]) in self.cleanup_acks:
            return ()
        return (
            {
                "runId": run["runId"],
                "startArtifactSha256": run["startArtifactSha256"],
            },
        )

    def ack_terminal_cleanup(
        self, run_id, start_artifact_sha256, *, now=None
    ):
        key = (run_id, start_artifact_sha256)
        replay = key in self.cleanup_acks
        self.cleanup_acks.add(key)
        return {
            "schema": "hwpx.practice-campaign-queue-cleanup-ack/v1",
            "runId": run_id,
            "startArtifactSha256": start_artifact_sha256,
            "acknowledged": True,
            "idempotentReplay": replay,
            "privateStorageCoordinatesExposed": False,
        }


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
        terminal_artifact_hook=_terminal_verifier,
    )
    return {
        "source": source,
        "sourceRoot": source_root,
        "practiceRoot": practice_root,
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
    numeric_truthy = service.start(
        campaign_id, idempotency_key="campaign-start-002", confirm=1
    )

    assert preview["requiresConfirmation"] is True
    assert first["idempotentReplay"] is False
    assert replay["idempotentReplay"] is True
    assert numeric_truthy["requiresConfirmation"] is True
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
    assert decision["boundary"]["resourceAccounting"] == {
        "artifactBytes": "retained-output-bytes/v1",
        "costMicrounits": "local-zero/v1",
        "costMeasured": False,
    }
    assert len(decision["boundary"]["decisionActionSha256"]) == 64
    assert fixture["queue"].last_account["elapsed_seconds"] == 0
    assert "apply_table_ops" not in fixture["calls"]

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
    assert terminal["sandboxCleanup"]["removed"] is True
    assert fixture["calls"].count("apply_table_ops") == 1
    replay = service.continue_campaign(
        campaign_id, run_id=fixture["runRef"]["runId"]
    )
    assert replay["idempotentReplay"] is True
    assert replay["terminal"] == receipt
    assert (
        fixture["runRef"]["runId"],
        fixture["runRef"]["startArtifactSha256"],
    ) in fixture["queue"].cleanup_acks
    exported = service.export(campaign_id)
    assert exported["receipts"] == [receipt]
    assert len(exported["exportSha256"]) == 64
    assert_receipt_safe(exported)


def test_wrong_decision_terminalizes_and_cleans_sandbox(campaign_fixture) -> None:
    fixture = campaign_fixture
    service = fixture["service"]
    campaign_id = fixture["manifest"]["campaignId"]
    decision = service.continue_campaign(campaign_id, max_steps=8)

    failed = service.continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256="0" * 64,
    )

    assert failed["terminal"]["terminalReason"] == "DECISION_RECEIPT_MISMATCH"
    assert failed["sandboxCleanup"]["removed"] is True
    assert fixture["queue"].failed_receipt is not None
    assert "apply_table_ops" not in fixture["calls"]


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


def test_service_terminalizes_tool_spec_skew_without_leaving_lease(
    campaign_fixture,
) -> None:
    fixture = campaign_fixture
    skew = build_campaign_manifest(
        scenario_manifest_sha256=fixture["manifest"]["scenarioManifestSha256"],
        selection=fixture["manifest"]["selection"],
        provenance=_provenance(tool_spec_hash="f" * 16),
        budgets=fixture["manifest"]["budgets"],
        runs=fixture["manifest"]["runs"],
    )
    queue = _Queue(skew)
    service = PracticeCampaignService(
        queue,
        fixture["dispatcher"],
        manifest_resolver=lambda campaign_id: skew,
        task_resolver=lambda selected_manifest, lease: fixture["task"],
    )

    failed = service.continue_campaign(skew["campaignId"])

    assert failed["terminal"]["terminalReason"] == "TOOL_SPEC_SKEW"
    assert failed["terminal"]["state"] == "provenance_mismatch"
    assert queue.failed_receipt is not None


def test_service_maps_capability_skew_to_provenance_mismatch(
    campaign_fixture,
) -> None:
    fixture = campaign_fixture
    provenance = json.loads(json.dumps(fixture["manifest"]["provenance"]))
    provenance["stack"]["core"]["sha256"] = "f" * 64
    skew = build_campaign_manifest(
        scenario_manifest_sha256=fixture["manifest"]["scenarioManifestSha256"],
        selection=fixture["manifest"]["selection"],
        provenance=provenance,
        budgets=fixture["manifest"]["budgets"],
        runs=fixture["manifest"]["runs"],
    )
    queue = _Queue(skew)
    service = PracticeCampaignService(
        queue,
        fixture["dispatcher"],
        manifest_resolver=lambda campaign_id: skew,
        task_resolver=lambda selected_manifest, lease: fixture["task"],
    )

    failed = service.continue_campaign(skew["campaignId"])

    assert failed["terminal"]["terminalReason"] == "CAPABILITY_SKEW"
    assert failed["terminal"]["state"] == "provenance_mismatch"


def test_task_resolution_and_privacy_failures_terminalize_owned_lease(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    broken_queue = _Queue(fixture["manifest"])
    broken = PracticeCampaignService(
        broken_queue,
        fixture["dispatcher"],
        manifest_resolver=lambda selected_id: fixture["manifest"],
        task_resolver=lambda manifest, lease: (_ for _ in ()).throw(
            RuntimeError("private resolver detail")
        ),
    )
    task_failed = broken.continue_campaign(campaign_id)
    assert task_failed["terminal"]["terminalReason"] == "TASK_BINDING_INVALID"

    original_account = fixture["queue"].account

    def leaking_account(lease, **usage):
        return {**original_account(lease, **usage), "rawText": "private body"}

    monkeypatch.setattr(fixture["queue"], "account", leaking_account)
    privacy_failed = fixture["service"].continue_campaign(campaign_id)
    assert privacy_failed["terminal"]["terminalReason"] == "PRIVACY_BOUNDARY_FAILED"
    assert privacy_failed["terminal"]["state"] == "privacy_blocked"
    assert privacy_failed["sandboxCleanup"]["removed"] is True


def test_stale_source_revision_fails_closed_before_decision_execution(
    campaign_fixture,
) -> None:
    fixture = campaign_fixture
    first = fixture["service"].continue_campaign(
        fixture["manifest"]["campaignId"], max_steps=8
    )
    assert first["boundary"]["state"] == "decision"
    fixture["source"].write_bytes(b"externally changed synthetic fixture")

    failed = fixture["service"].continue_campaign(
        fixture["manifest"]["campaignId"],
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=first["boundary"]["decisionReceiptSha256"],
    )
    assert failed["terminal"]["terminalReason"] == "SOURCE_WRITE_REFUSED"
    assert failed["terminal"]["state"] == "source_write_refused"
    assert failed["sandboxCleanup"]["removed"] is True
    assert "apply_table_ops" not in fixture["calls"]


def test_terminal_source_change_signal_maps_to_source_write_refused(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    decision = fixture["service"].continue_campaign(campaign_id)

    def changed(_sandbox: Any) -> None:
        raise PracticeSandboxError("SOURCE_CHANGED")

    monkeypatch.setattr(
        fixture["dispatcher"].sandbox_manager,
        "assert_source_unchanged",
        changed,
    )
    failed = fixture["service"].continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )

    assert failed["terminal"]["state"] == "source_write_refused"
    assert failed["terminal"]["terminalReason"] == "SOURCE_WRITE_REFUSED"
    assert fixture["calls"].count("apply_table_ops") == 1
    assert fixture["source"].read_bytes() == b"synthetic leap-b fixture"


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


def test_must_abstain_closes_without_workflow_record(campaign_fixture) -> None:
    fixture = campaign_fixture
    task = ResolvedPracticeTask(
        scenario_ref=fixture["task"].scenario_ref,
        dispatch=fixture["task"].dispatch,
        source_artifact=fixture["source"],
        workflow_family="must_abstain",
        parameters={},
        evaluation_policy_sha256=fixture["runRef"]["evaluationPolicySha256"],
    )
    service = PracticeCampaignService(
        fixture["queue"],
        fixture["dispatcher"],
        manifest_resolver=lambda selected_id: fixture["manifest"],
        task_resolver=lambda manifest, lease: task,
    )

    terminal = service.continue_campaign(fixture["manifest"]["campaignId"])

    assert terminal["boundary"]["state"] == "needs_review"
    assert fixture["queue"].terminal_receipt["terminalReason"] == "UNSUPPORTED_INTENT"
    assert terminal["sandboxCleanup"]["removed"] is True


def test_export_requires_exact_complete_bijection(campaign_fixture, monkeypatch) -> None:
    fixture = campaign_fixture
    service = fixture["service"]
    campaign_id = fixture["manifest"]["campaignId"]
    with pytest.raises(PracticeCampaignError, match="CAMPAIGN_EXPORT_INVALID"):
        service.export(campaign_id)

    decision = service.continue_campaign(campaign_id)
    service.continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )
    receipt = fixture["queue"].terminal_receipt
    monkeypatch.setattr(
        fixture["queue"], "receipts", lambda selected_id: [receipt, receipt]
    )
    with pytest.raises(PracticeCampaignError, match="CAMPAIGN_EXPORT_INVALID"):
        service.export(campaign_id)


def test_terminal_hook_runs_before_cleanup_and_hook_failure_closes_run(
    campaign_fixture, monkeypatch,
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    observed: list[bool] = []
    ordering: list[str] = []

    def retain(task, outcome):
        ordering.append("hook")
        observed.append(bool(outcome.output_path and outcome.output_path.is_file()))
        assert outcome.artifact_hook_idempotency_key.startswith("IDEM-")
        return _terminal_verifier(task, outcome)

    original_terminalize = fixture["queue"].terminalize
    original_cleanup = fixture["dispatcher"].cleanup_sandbox

    def terminalize(*args, **kwargs):
        ordering.append("terminalize")
        return original_terminalize(*args, **kwargs)

    def cleanup(sandbox):
        ordering.append("cleanup")
        return original_cleanup(sandbox)

    monkeypatch.setattr(fixture["queue"], "terminalize", terminalize)
    monkeypatch.setattr(fixture["dispatcher"], "cleanup_sandbox", cleanup)

    service = PracticeCampaignService(
        fixture["queue"],
        fixture["dispatcher"],
        manifest_resolver=lambda selected_id: fixture["manifest"],
        task_resolver=lambda manifest, lease: fixture["task"],
        terminal_artifact_hook=retain,
    )
    decision = service.continue_campaign(campaign_id)
    terminal = service.continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )
    assert observed == [True]
    assert terminal["artifactHookReceiptSha256"] is not None
    assert terminal["sandboxCleanup"]["removed"] is True
    assert ordering[-3:] == ["hook", "terminalize", "cleanup"]

    failing_queue = _Queue(fixture["manifest"])
    failing_dispatcher = PracticeWorkflowDispatcher(
        WorkflowService(
            _namespace([]),
            store=WorkflowStore(
                fixture["practiceRoot"] / "hook-failure-workflow.sqlite3"
            ),
        ),
        PracticeSandboxManager(
            fixture["sourceRoot"],
            fixture["practiceRoot"],
            _new_storage_root(fixture["practiceRoot"], "hook-failure-runs"),
        ),
        runtime_provenance=lambda: fixture["manifest"]["provenance"],
    )
    failing = PracticeCampaignService(
        failing_queue,
        failing_dispatcher,
        manifest_resolver=lambda selected_id: fixture["manifest"],
        task_resolver=lambda manifest, lease: fixture["task"],
        terminal_artifact_hook=lambda task, outcome: (_ for _ in ()).throw(
            RuntimeError("evaluator unavailable")
        ),
    )
    failed_decision = failing.continue_campaign(campaign_id)
    failed = failing.continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=failed_decision["boundary"]["decisionReceiptSha256"],
    )
    assert failed["terminal"]["terminalReason"] == "DOMAIN_VERIFIER_FAILED"
    assert failed["terminal"]["state"] == "needs_review"


@pytest.mark.parametrize(
    "attack",
    [
        "schema",
        "extra",
        "run",
        "scenario",
        "terminal_hash",
        "output_hash",
        "output_bytes",
        "policy",
        "evaluator_hash",
        "evaluator_key",
        "idempotency",
        "ineligible",
        "private_coordinate",
    ],
)
def test_terminal_verifier_rejects_every_binding_attack(
    campaign_fixture, attack: str
) -> None:
    fixture = campaign_fixture

    def poisoned(task: ResolvedPracticeTask, outcome: Any) -> Mapping[str, Any]:
        verdict = _terminal_verifier(task, outcome)
        if attack == "schema":
            verdict["schema"] = "hwpx.practice-terminal-verifier/v2"
        elif attack == "extra":
            verdict["unexpected"] = False
        elif attack == "run":
            verdict["runId"] = "PRUN-FFFFFFFFFFFFFFFFFFFF"
        elif attack == "scenario":
            verdict["scenarioId"] = "SCN-FFFFFFFFFFFFFFFFFFFF"
        elif attack == "terminal_hash":
            verdict["terminalReceiptSha256"] = "f" * 64
        elif attack == "output_hash":
            verdict["outputArtifact"]["sha256"] = "f" * 64
        elif attack == "output_bytes":
            verdict["outputArtifact"]["bytes"] += 1
        elif attack == "policy":
            verdict["evaluationPolicySha256"] = "f" * 64
        elif attack == "evaluator_hash":
            verdict["evaluator"]["sha256"] = "f" * 64
        elif attack == "evaluator_key":
            verdict["evaluator"]["authenticationKeyId"] = (
                "EVK-FFFFFFFFFFFFFFFFFFFF"
            )
        elif attack == "idempotency":
            verdict["idempotencyKey"] = "IDEM-FFFFFFFFFFFFFFFFFFFF"
        elif attack == "ineligible":
            verdict["eligibleForSuccess"] = False
        else:
            verdict["privateStorageCoordinatesExposed"] = True
        return verdict

    service = PracticeCampaignService(
        fixture["queue"],
        fixture["dispatcher"],
        manifest_resolver=lambda selected_id: fixture["manifest"],
        task_resolver=lambda manifest, lease: fixture["task"],
        terminal_artifact_hook=poisoned,
    )
    decision = service.continue_campaign(fixture["manifest"]["campaignId"])
    failed = service.continue_campaign(
        fixture["manifest"]["campaignId"],
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )

    assert failed["terminal"]["state"] == "needs_review"
    assert failed["terminal"]["terminalReason"] == "DOMAIN_VERIFIER_FAILED"
    assert fixture["calls"].count("apply_table_ops") == 1
    assert fixture["source"].read_bytes() == b"synthetic leap-b fixture"


@pytest.mark.parametrize("missing_mode", ["absent", "none"])
def test_completed_run_requires_a_terminal_verifier(
    campaign_fixture, missing_mode: str
) -> None:
    fixture = campaign_fixture
    hook = None if missing_mode == "absent" else lambda task, outcome: None
    service = PracticeCampaignService(
        fixture["queue"],
        fixture["dispatcher"],
        manifest_resolver=lambda selected_id: fixture["manifest"],
        task_resolver=lambda manifest, lease: fixture["task"],
        terminal_artifact_hook=hook,
    )
    decision = service.continue_campaign(fixture["manifest"]["campaignId"])
    failed = service.continue_campaign(
        fixture["manifest"]["campaignId"],
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )

    assert failed["terminal"]["state"] == "needs_review"
    assert failed["terminal"]["terminalReason"] == "DOMAIN_VERIFIER_MISSING"
    assert fixture["calls"].count("apply_table_ops") == 1


def test_authorization_precedes_dispatch_and_min_caps_workflow_budget(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    queue = fixture["queue"]
    original_authorize = queue.authorize
    observed: list[str] = []

    def authorize(lease, now=None):
        observed.append("authorize")
        value = original_authorize(lease, now=now)
        value["runRemaining"]["toolCalls"] = 2
        value["effectiveRemaining"]["toolCalls"] = 2
        value["runRemaining"]["elapsedSeconds"] = 45
        value["effectiveRemaining"]["elapsedSeconds"] = 45
        return value

    original_advance = fixture["dispatcher"].advance

    def advance(*args, **kwargs):
        observed.append("dispatch")
        assert kwargs["execution_limits"]["toolCalls"] == 2
        return original_advance(*args, **kwargs)

    monkeypatch.setattr(queue, "authorize", authorize)
    monkeypatch.setattr(fixture["dispatcher"], "advance", advance)
    fixture["service"].continue_campaign(
        fixture["manifest"]["campaignId"], max_steps=1
    )
    receipt = fixture["dispatcher"]._recovery_receipt(
        fixture["manifest"]["campaignId"], fixture["runRef"]["runId"]
    )
    record = fixture["dispatcher"].workflow_service.store.get(receipt["workflowId"])

    assert observed[:2] == ["authorize", "dispatch"]
    assert record.work_order.budget.max_tool_calls == 2
    assert record.work_order.budget.max_elapsed_seconds == 45


def test_local_zero_cost_model_rejects_nonzero_durable_cost(
    campaign_fixture,
) -> None:
    fixture = campaign_fixture
    fixture["queue"].usage["costMicrounits"] = 1

    failed = fixture["service"].continue_campaign(
        fixture["manifest"]["campaignId"]
    )

    assert failed["terminal"]["terminalReason"] == "COST_MODEL_MISMATCH"
    assert failed["terminal"]["state"] == "failed"
    assert fixture["calls"] == []


def test_recovery_only_nonterminal_does_not_mutate_workflow(
    campaign_fixture,
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    decision = fixture["service"].continue_campaign(campaign_id)
    receipt = fixture["dispatcher"]._recovery_receipt(
        campaign_id, fixture["runRef"]["runId"]
    )
    events_before = fixture["dispatcher"].workflow_service.store.events(
        receipt["workflowId"]
    )
    calls_before = list(fixture["calls"])
    fixture["queue"].lease = replace(fixture["queue"].lease, recovery_only=True)

    terminal = fixture["service"].continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )

    assert terminal["terminal"]["state"] == "budget_exhausted"
    assert terminal["terminal"]["terminalReason"] == "RECOVERY_ONLY_NONTERMINAL"
    assert fixture["calls"] == calls_before
    assert fixture["dispatcher"].workflow_service.store.events(
        receipt["workflowId"]
    ) == events_before


def test_oversized_output_cannot_terminalize_as_success(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    original_authorize = fixture["queue"].authorize

    def tiny_artifact_budget(lease, now=None):
        value = original_authorize(lease, now=now)
        value["campaignRemaining"]["artifactBytes"] = 1
        value["effectiveRemaining"]["artifactBytes"] = 1
        return value

    monkeypatch.setattr(fixture["queue"], "authorize", tiny_artifact_budget)
    decision = fixture["service"].continue_campaign(campaign_id)
    terminal = fixture["service"].continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )

    assert terminal["boundary"]["state"] == "completed"
    assert terminal["terminal"]["state"] == "budget_exhausted"
    assert (
        terminal["terminal"]["terminalReason"]
        == "OUTPUT_ARTIFACT_BUDGET_EXCEEDED"
    )
    assert terminal["sandboxCleanup"]["removed"] is True


def test_failure_terminal_is_durable_before_cleanup(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    events: list[str] = []
    original_fail = fixture["queue"].fail
    original_cleanup = fixture["dispatcher"].cleanup_sandbox

    def fail(*args, **kwargs):
        events.append("fail")
        return original_fail(*args, **kwargs)

    def cleanup(sandbox):
        events.append("cleanup")
        return original_cleanup(sandbox)

    monkeypatch.setattr(fixture["queue"], "fail", fail)
    monkeypatch.setattr(fixture["dispatcher"], "cleanup_sandbox", cleanup)
    decision = fixture["service"].continue_campaign(
        fixture["manifest"]["campaignId"]
    )
    fixture["service"].continue_campaign(
        fixture["manifest"]["campaignId"],
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256="0" * 64,
    )

    assert events[-2:] == ["fail", "cleanup"]


def test_crash_after_workflow_success_reconciles_without_duplicate_mutation(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    service = fixture["service"]
    campaign_id = fixture["manifest"]["campaignId"]
    decision = service.continue_campaign(campaign_id)
    original_terminalize = fixture["queue"].terminalize
    calls = 0

    def crash_once(lease, receipt, now=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt("simulated crash after cleanup")
        return original_terminalize(lease, receipt, now=now)

    monkeypatch.setattr(fixture["queue"], "terminalize", crash_once)
    with pytest.raises(KeyboardInterrupt):
        service.continue_campaign(
            campaign_id,
            run_id=fixture["runRef"]["runId"],
            approved=True,
            decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
        )

    reconciled = service.continue_campaign(
        campaign_id, run_id=fixture["runRef"]["runId"]
    )
    assert reconciled["boundary"]["state"] == "completed"
    assert fixture["calls"].count("apply_table_ops") == 1
    receipt = validate_run_receipt(fixture["queue"].terminal_receipt)
    assert [artifact["role"] for artifact in receipt["artifacts"]] == [
        "start",
        "output",
    ]
    assert receipt["artifacts"][1]["bytes"] == receipt["usage"]["artifactBytes"]


def test_crash_before_terminal_accounting_reconciles_full_output_delta(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    decision = fixture["service"].continue_campaign(campaign_id)
    original_account = fixture["queue"].account
    crashed = False

    def crash_before_account(lease, **usage):
        nonlocal crashed
        if usage["artifact_bytes"] > 0 and not crashed:
            crashed = True
            raise KeyboardInterrupt("crash before durable terminal accounting")
        return original_account(lease, **usage)

    monkeypatch.setattr(fixture["queue"], "account", crash_before_account)
    with pytest.raises(KeyboardInterrupt):
        fixture["service"].continue_campaign(
            campaign_id,
            run_id=fixture["runRef"]["runId"],
            approved=True,
            decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
        )
    assert fixture["queue"].usage["artifactBytes"] == 0

    terminal = fixture["service"].continue_campaign(
        campaign_id, run_id=fixture["runRef"]["runId"]
    )
    receipt = fixture["queue"].terminal_receipt
    assert terminal["terminal"]["state"] == "completed"
    assert receipt["usage"]["artifactBytes"] > 0
    assert receipt["artifacts"][1]["bytes"] == receipt["usage"]["artifactBytes"]
    assert fixture["calls"].count("apply_table_ops") == 1


def test_expired_accounting_terminalizes_success_instead_of_overwriting_failure(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    decision = fixture["service"].continue_campaign(campaign_id)

    class LeaseExpired(RuntimeError):
        code = "LEASE_NOT_OWNED"

    original_account = fixture["queue"].account

    def expire_terminal_account(lease, **usage):
        if usage["artifact_bytes"] > 0:
            raise LeaseExpired("lease expired after workflow success")
        return original_account(lease, **usage)

    monkeypatch.setattr(fixture["queue"], "account", expire_terminal_account)
    terminal = fixture["service"].continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )

    assert terminal["terminal"]["state"] == "completed"
    assert terminal["accounting"]["expiredLeaseReconciled"] is True
    assert fixture["queue"].failed_receipt is None
    assert fixture["calls"].count("apply_table_ops") == 1


def test_expired_accounting_and_missing_verifier_preserve_measured_usage(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    service = PracticeCampaignService(
        fixture["queue"],
        fixture["dispatcher"],
        manifest_resolver=lambda selected_id: fixture["manifest"],
        task_resolver=lambda manifest, lease: fixture["task"],
        terminal_artifact_hook=None,
    )
    decision = service.continue_campaign(campaign_id)

    class LeaseExpired(RuntimeError):
        code = "LEASE_NOT_OWNED"

    original_account = fixture["queue"].account

    def expire_terminal_account(lease, **usage):
        if usage["artifact_bytes"] > 0:
            raise LeaseExpired("lease expired after workflow success")
        return original_account(lease, **usage)

    monkeypatch.setattr(fixture["queue"], "account", expire_terminal_account)
    terminal = service.continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )

    receipt = validate_run_receipt(terminal["terminal"])
    outputs = [item for item in receipt["artifacts"] if item["role"] == "output"]
    assert receipt["state"] == "needs_review"
    assert receipt["terminalReason"] == "DOMAIN_VERIFIER_MISSING"
    assert "DOMAIN_VERIFIER_MISSING" in receipt["evidence"][
        "unresolvedReasonCodes"
    ]
    assert receipt["usage"]["toolCalls"] > 0
    assert len(outputs) == 1
    assert receipt["usage"]["artifactBytes"] == outputs[0]["bytes"] > 0
    assert fixture["calls"].count("apply_table_ops") == 1
    assert fixture["queue"].receipts(campaign_id) == [receipt]
    assert fixture["source"].read_bytes() == b"synthetic leap-b fixture"


def test_terminal_hook_replay_uses_one_stable_idempotency_key(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    keys: list[str] = []
    effects: set[str] = set()
    verdicts: list[dict[str, Any]] = []

    def retain(task, outcome):
        key = outcome.artifact_hook_idempotency_key
        assert key is not None
        keys.append(key)
        effects.add(key)
        verdict = _terminal_verifier(task, outcome)
        verdicts.append(verdict)
        return verdict

    service = PracticeCampaignService(
        fixture["queue"],
        fixture["dispatcher"],
        manifest_resolver=lambda selected_id: fixture["manifest"],
        task_resolver=lambda manifest, lease: fixture["task"],
        terminal_artifact_hook=retain,
    )
    decision = service.continue_campaign(fixture["manifest"]["campaignId"])
    original_terminalize = fixture["queue"].terminalize
    terminalize_calls = 0

    def crash_once(lease, receipt, now=None):
        nonlocal terminalize_calls
        terminalize_calls += 1
        if terminalize_calls == 1:
            raise KeyboardInterrupt("crash before durable terminal")
        return original_terminalize(lease, receipt, now=now)

    monkeypatch.setattr(fixture["queue"], "terminalize", crash_once)
    with pytest.raises(KeyboardInterrupt):
        service.continue_campaign(
            fixture["manifest"]["campaignId"],
            run_id=fixture["runRef"]["runId"],
            approved=True,
            decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
        )
    service.continue_campaign(
        fixture["manifest"]["campaignId"], run_id=fixture["runRef"]["runId"]
    )

    assert len(keys) == 2
    assert keys[0] == keys[1]
    assert len(effects) == 1
    assert verdicts[0] == verdicts[1]
    assert fixture["calls"].count("apply_table_ops") == 1


def test_missing_output_with_stored_hash_fails_closed(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    decision = fixture["service"].continue_campaign(campaign_id)
    workflow = fixture["dispatcher"].workflow_service
    original_continue = workflow.continue_workflow

    def remove_terminal_output(workflow_id):
        receipt = original_continue(workflow_id)
        if receipt.get("terminal") is True:
            sandbox = fixture["dispatcher"].known_sandbox(
                fixture["runRef"]["runId"]
            )
            output = sandbox.writable_path(
                "output/result.hwpx", create_parents=True
            )
            output.unlink(missing_ok=True)
        return receipt

    monkeypatch.setattr(workflow, "continue_workflow", remove_terminal_output)
    failed = fixture["service"].continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )

    assert failed["terminal"]["terminalReason"] == "OUTPUT_ARTIFACT_UNAVAILABLE"
    assert failed["terminal"]["state"] == "failed"
    assert failed["sandboxCleanup"]["removed"] is True


def test_crash_after_durable_terminal_is_cleaned_from_queue_candidate(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    decision = fixture["service"].continue_campaign(campaign_id)
    sandbox = fixture["dispatcher"].known_sandbox(fixture["runRef"]["runId"])
    assert sandbox is not None

    def crash_cleanup(_sandbox):
        raise KeyboardInterrupt("crash after durable terminal")

    monkeypatch.setattr(fixture["dispatcher"], "cleanup_sandbox", crash_cleanup)
    with pytest.raises(KeyboardInterrupt):
        fixture["service"].continue_campaign(
            campaign_id,
            run_id=fixture["runRef"]["runId"],
            approved=True,
            decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
        )
    assert fixture["queue"].terminal_receipt is not None
    assert sandbox.root.exists()

    restarted = PracticeSandboxManager(
        fixture["sourceRoot"], fixture["practiceRoot"], sandbox.root.parents[2]
    )
    cleanup = restarted.cleanup_terminal_candidates(
        fixture["queue"].terminal_cleanup_candidates()
    )
    assert cleanup["removedCount"] == 1
    assert not sandbox.root.exists()


def test_cleanup_failure_is_reported_after_durable_terminal(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    campaign_id = fixture["manifest"]["campaignId"]
    decision = fixture["service"].continue_campaign(campaign_id)

    def cleanup_failure(sandbox):
        raise PracticeDispatchError("SANDBOX_UNAVAILABLE", sandbox=sandbox)

    monkeypatch.setattr(
        fixture["dispatcher"], "cleanup_sandbox", cleanup_failure
    )
    failed = fixture["service"].continue_campaign(
        campaign_id,
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )

    assert failed["terminal"] is not None
    assert failed["postTerminalErrorCode"] == "SANDBOX_CLEANUP_FAILED"
    assert failed["status"]["state"] == "completed"
    assert fixture["queue"].cleanup_acks == set()


def test_poison_cleanup_candidate_does_not_block_valid_second_ack(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    decision = fixture["service"].continue_campaign(
        fixture["manifest"]["campaignId"]
    )
    fixture["service"].continue_campaign(
        fixture["manifest"]["campaignId"],
        run_id=fixture["runRef"]["runId"],
        approved=True,
        decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
    )
    candidates = (
        {
            "runId": fixture["runRef"]["runId"],
            "startArtifactSha256": fixture["runRef"]["startArtifactSha256"],
            "unexpected": "value",
        },
        {
            "runId": fixture["runRef"]["runId"],
            "startArtifactSha256": fixture["runRef"]["startArtifactSha256"],
        },
    )
    monkeypatch.setattr(
        fixture["queue"], "terminal_cleanup_candidates", lambda limit=64: candidates
    )

    cleanup = fixture["service"]._cleanup_terminal_candidates()

    assert cleanup["candidateCount"] == 2
    assert cleanup["failureCount"] == 1
    assert cleanup["failureCodes"] == ["SANDBOX_CONFLICT"]
    assert cleanup["acknowledgedCount"] == 1
    assert cleanup["candidates"][0]["acknowledged"] is False
    assert cleanup["candidates"][1]["acknowledged"] is True
    assert (
        fixture["runRef"]["runId"],
        fixture["runRef"]["startArtifactSha256"],
    ) in fixture["queue"].cleanup_acks
    assert str(fixture["source"]) not in json.dumps(cleanup)


def test_service_integrates_with_durable_queue(
    campaign_fixture, tmp_path: Path
) -> None:
    fixture = campaign_fixture
    queue_root = fixture["practiceRoot"] / "durable-queue"
    queue_root.mkdir()
    queue = PracticeCampaignQueue(
        queue_root,
        source_root=fixture["sourceRoot"],
        practice_root=fixture["practiceRoot"],
    )
    service = PracticeCampaignService(
        queue,
        fixture["dispatcher"],
        manifest_resolver=lambda campaign_id: fixture["manifest"],
        task_resolver=lambda selected_manifest, lease: fixture["task"],
        terminal_artifact_hook=_terminal_verifier,
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
    assert len(queue.terminal_cleanup_candidates()) == 1
    replay = service.continue_campaign(campaign_id, run_id=fixture["runRef"]["runId"])
    assert replay["idempotentReplay"] is True
    assert queue.terminal_cleanup_candidates() == ()
    assert service.export(campaign_id)["receipts"] == receipts


def test_durable_queue_expiry_exact_claim_reconciles_original_success(
    campaign_fixture, monkeypatch
) -> None:
    fixture = campaign_fixture
    budgets = _budgets()
    budgets["attempts"] = 1
    manifest, run_ref, task = _fixture_contract(
        fixture["source"], fixture["manifest"]["provenance"], budgets=budgets
    )
    queue_root = fixture["practiceRoot"] / "expiry-queue"
    queue_root.mkdir()
    storage_root = _new_storage_root(
        fixture["practiceRoot"], "expiry-sandboxes"
    )
    queue = PracticeCampaignQueue(
        queue_root,
        source_root=fixture["sourceRoot"],
        practice_root=fixture["practiceRoot"],
    )
    calls: list[str] = []
    dispatcher = PracticeWorkflowDispatcher(
        WorkflowService(
            _namespace(calls),
            store=WorkflowStore(
                fixture["practiceRoot"] / "expiry-workflow.sqlite3"
            ),
        ),
        PracticeSandboxManager(
            fixture["sourceRoot"], fixture["practiceRoot"], storage_root
        ),
        runtime_provenance=lambda: manifest["provenance"],
    )
    service = PracticeCampaignService(
        queue,
        dispatcher,
        manifest_resolver=lambda campaign_id: manifest,
        task_resolver=lambda selected_manifest, lease: task,
        terminal_artifact_hook=_terminal_verifier,
        lease_seconds=10,
    )
    clock = [datetime(2026, 7, 14, tzinfo=timezone.utc)]
    monkeypatch.setattr(queue_module, "_utcnow", lambda: clock[0])
    campaign_id = manifest["campaignId"]
    service.start(campaign_id, idempotency_key="expiry-recovery", confirm=True)
    decision = service.continue_campaign(campaign_id)
    original_terminalize = queue.terminalize
    captured_receipt: dict[str, Any] | None = None

    def crash_before_terminal(lease, receipt, now=None):
        nonlocal captured_receipt
        if captured_receipt is None:
            captured_receipt = validate_run_receipt(receipt)
            raise KeyboardInterrupt("crash after success accounting")
        return original_terminalize(lease, receipt, now=now)

    monkeypatch.setattr(queue, "terminalize", crash_before_terminal)
    with pytest.raises(KeyboardInterrupt):
        service.continue_campaign(
            campaign_id,
            run_id=run_ref["runId"],
            approved=True,
            decision_receipt_sha256=decision["boundary"]["decisionReceiptSha256"],
        )
    assert captured_receipt is not None
    assert calls.count("apply_table_ops") == 1

    clock[0] += timedelta(seconds=11)
    reconciled = service.continue_campaign(campaign_id, run_id=run_ref["runId"])

    durable_receipt = validate_run_receipt(reconciled["terminal"])
    assert durable_receipt["state"] == captured_receipt["state"] == "completed"
    assert durable_receipt["terminalReason"] == captured_receipt["terminalReason"]
    assert durable_receipt["artifacts"] == captured_receipt["artifacts"]
    assert durable_receipt["workflowEvents"] == captured_receipt["workflowEvents"]
    assert durable_receipt["usage"]["elapsedSeconds"] >= captured_receipt["usage"][
        "elapsedSeconds"
    ]
    assert queue.receipts(campaign_id) == [durable_receipt]
    assert calls.count("apply_table_ops") == 1
    assert fixture["source"].read_bytes() == b"synthetic leap-b fixture"
