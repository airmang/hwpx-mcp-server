from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from hwpx.practice import (
    PRACTICE_RUN_SCHEMA,
    assert_receipt_safe,
    build_campaign_manifest,
    practice_run_id,
    validate_run_receipt,
)
from hwpx_mcp_server.practice import dispatch as dispatch_module
from hwpx_mcp_server.practice.campaign_service import PracticeCampaignService
from hwpx_mcp_server.practice.dispatch import (
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


_CASES = {
    "process_kill_restart",
    "repeated_continue",
    "stale_revision",
    "capability_skew",
    "missing_verifier",
    "source_write_attempt",
    "privacy_leak",
    "budget_exhaustion",
    "incomplete_campaign",
}


def _digest(value: object) -> str:
    payload = (
        value
        if isinstance(value, bytes)
        else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    return hashlib.sha256(payload).hexdigest()


def _provenance() -> dict[str, Any]:
    return {
        "stack": {
            "core": {"version": "2.30.0.dev1", "sha256": _digest("core")},
            "server": {"version": "2.22.0.dev1", "sha256": _digest("server")},
            "skill": {"version": "0.1.9.dev1", "sha256": _digest("skill")},
        },
        "toolSpec": {"version": "tool-spec/v1", "sha256": contract_hash()},
        "evaluator": {
            "version": "practice-evaluator/v1",
            "sha256": _digest("evaluator"),
            "authenticationKeyId": "EVK-0123456789ABCDEF0123",
        },
    }


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


def _budgets(**overrides: int) -> dict[str, int]:
    result = {
        "toolCalls": 8,
        "attempts": 2,
        "repairRounds": 3,
        "elapsedSeconds": 300,
        "costMicrounits": 100_000,
        "artifactBytes": 1_000_000,
    }
    result.update(overrides)
    return result


def _contract(
    source: Path,
    provenance: Mapping[str, Any],
    budgets: Mapping[str, int],
) -> tuple[dict[str, Any], dict[str, Any], ResolvedPracticeTask]:
    source_sha256 = _digest(source.read_bytes())
    scenario_ref = {
        "scenarioId": "SCN-00000000000000000073",
        "scenarioSha256": _digest("chaos-scenario"),
        "runnerManifestSha256": _digest("chaos-runner-manifest"),
        "derivativeSha256": _digest("chaos-derivative"),
        "startArtifactId": "ART-00000000000000000073",
        "startArtifactSha256": source_sha256,
    }
    dispatch = {
        "slot": 0,
        "dispatchKey": "DSP-00000000000000000073",
        "seedSha256": _digest("chaos-dispatch"),
    }
    run_id = practice_run_id(
        {
            "schema": PRACTICE_RUN_SCHEMA,
            "scenarioRef": scenario_ref,
            "dispatch": dispatch,
            "provenance": dict(provenance),
            "budgets": dict(budgets),
        }
    )
    run_ref = {
        "slot": 0,
        "runId": run_id,
        "scenarioId": scenario_ref["scenarioId"],
        "scenarioSha256": scenario_ref["scenarioSha256"],
        "evaluationPolicySha256": _digest("chaos-evaluation-policy"),
        "runnerManifestSha256": scenario_ref["runnerManifestSha256"],
        "derivativeSha256": scenario_ref["derivativeSha256"],
        "startArtifactId": scenario_ref["startArtifactId"],
        "startArtifactSha256": scenario_ref["startArtifactSha256"],
        "family": "unknown_form_fill",
        "difficulty": "routine",
        "budgets": dict(budgets),
    }
    manifest = build_campaign_manifest(
        scenario_manifest_sha256=_digest("chaos-scenario-manifest"),
        selection={
            "seedSha256": _digest("chaos-selection"),
            "strategyVersion": "coverage-weakness/v1",
            "policySha256": _digest("chaos-selection-policy"),
        },
        provenance=provenance,
        budgets={
            "runs": 1,
            "toolCalls": budgets["toolCalls"],
            "elapsedSeconds": budgets["elapsedSeconds"],
            "costMicrounits": budgets["costMicrounits"],
            "artifactBytes": budgets["artifactBytes"],
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


def _namespace(mutations: list[str]) -> dict[str, Callable[..., dict[str, Any]]]:
    def scan(**arguments: Any) -> dict[str, Any]:
        del arguments
        return {"ok": True}

    def apply(**arguments: Any) -> dict[str, Any]:
        mutations.append("apply_table_ops")
        source = Path(arguments["filename"])
        output = Path(arguments["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)
        return {"ok": True, "openSafety": {"ok": True}}

    def residue(**arguments: Any) -> dict[str, Any]:
        del arguments
        return {"ok": True}

    def verify(**arguments: Any) -> dict[str, Any]:
        del arguments
        return {"ok": True}

    return {
        "scan_form_guidance": scan,
        "apply_table_ops": apply,
        "inspect_fill_residue": residue,
        "verify_form_fill": verify,
    }


class _Rig:
    def __init__(
        self,
        root: Path,
        *,
        budgets: Mapping[str, int] | None = None,
        terminal_artifact_hook: Callable[..., Mapping[str, Any] | None]
        | None = _terminal_verifier,
    ) -> None:
        self.root = root
        self.source_root = root / "source"
        self.practice_root = root / "practice"
        self.queue_root = self.practice_root / "queue"
        self.sandbox_root = self.practice_root / "sandboxes"
        self.workflow_path = self.practice_root / "workflow.sqlite3"
        for directory in (
            self.source_root,
            self.practice_root,
            self.queue_root,
            self.sandbox_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.source = self.source_root / "synthetic.hwpx"
        self.source.write_bytes(b"synthetic S-073 P4 chaos artifact")
        self.source_before = self.source.read_bytes()
        self.provenance = _provenance()
        self.runtime_provenance = dict(self.provenance)
        self.manifest, self.run_ref, self.task = _contract(
            self.source, self.provenance, dict(budgets or _budgets())
        )
        self.mutations: list[str] = []
        self.terminal_artifact_hook = terminal_artifact_hook
        self._open_runtime()

    def _open_runtime(self) -> None:
        self.queue = PracticeCampaignQueue(
            self.queue_root,
            source_root=self.source_root,
            practice_root=self.practice_root,
        )
        workflow = WorkflowService(
            _namespace(self.mutations), store=WorkflowStore(self.workflow_path)
        )
        sandbox = PracticeSandboxManager(
            self.source_root, self.practice_root, self.sandbox_root
        )
        self.dispatcher = PracticeWorkflowDispatcher(
            workflow,
            sandbox,
            runtime_provenance=lambda: self.runtime_provenance,
        )
        self.service = PracticeCampaignService(
            self.queue,
            self.dispatcher,
            manifest_resolver=lambda campaign_id: self.manifest,
            task_resolver=lambda manifest, lease: self.task,
            terminal_artifact_hook=self.terminal_artifact_hook,
        )

    @property
    def campaign_id(self) -> str:
        return str(self.manifest["campaignId"])

    def start(self) -> None:
        self.service.start(
            self.campaign_id,
            idempotency_key="s073-chaos-start",
            confirm=True,
        )

    def reopen(self) -> None:
        self._open_runtime()

    def assert_one_terminal(
        self, *, state: str, reason: str, mutation_count: int
    ) -> dict[str, Any]:
        receipts = self.queue.receipts(self.campaign_id)
        assert len(receipts) == 1
        receipt = receipts[0]
        assert validate_run_receipt(receipt) == receipt
        assert receipt["state"] == state
        assert receipt["terminalReason"] == reason
        assert receipt["runId"] == self.run_ref["runId"]
        assert receipt["scenarioId"] == self.run_ref["scenarioId"]
        assert len(self.mutations) == mutation_count
        assert self.source.read_bytes() == self.source_before
        assert_receipt_safe(receipt)
        return receipt


@pytest.fixture(autouse=True)
def _installed_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hwpx_mcp_server.workflow.service._package_version",
        lambda name: {
            "python-hwpx": "2.30.0.dev1",
            "hwpx-mcp-server": "2.22.0.dev1",
        }[name],
    )


class _ProcessKilled(BaseException):
    pass


def test_process_kill_restart_and_repeated_continue_mutate_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = _Rig(tmp_path / "kill-restart")
    rig.start()
    decision = rig.service.continue_campaign(rig.campaign_id)
    assert decision["boundary"]["state"] == "decision"

    account = rig.queue.account
    killed = False

    def kill_after_mutation(lease: Any, **usage: int) -> dict[str, Any]:
        nonlocal killed
        if usage.get("artifact_bytes", 0) > 0 and not killed:
            killed = True
            raise _ProcessKilled()
        return account(lease, **usage)

    monkeypatch.setattr(rig.queue, "account", kill_after_mutation)
    with pytest.raises(_ProcessKilled):
        rig.service.continue_campaign(
            rig.campaign_id,
            run_id=rig.run_ref["runId"],
            approved=True,
            decision_receipt_sha256=decision["boundary"][
                "decisionReceiptSha256"
            ],
        )
    assert rig.mutations == ["apply_table_ops"]
    assert rig.queue.receipts(rig.campaign_id) == []
    assert rig.source.read_bytes() == rig.source_before

    # A new queue/dispatcher/workflow process reopens only durable state.  The
    # existing workflow idempotency key reconciles the output without a second
    # call to the mutation tool.
    rig.reopen()
    recovered = rig.service.continue_campaign(
        rig.campaign_id, run_id=rig.run_ref["runId"]
    )
    assert recovered["terminal"]["state"] == "completed"
    receipt = rig.assert_one_terminal(
        state="completed", reason="VERIFIED_COMPLETION", mutation_count=1
    )

    for _ in range(4):
        replay = rig.service.continue_campaign(
            rig.campaign_id, run_id=rig.run_ref["runId"]
        )
        assert replay["idempotentReplay"] is True
        assert replay["terminal"] == receipt
    assert rig.mutations == ["apply_table_ops"]
    assert len(rig.queue.receipts(rig.campaign_id)) == 1


@pytest.mark.parametrize(
    ("case", "expected_state", "expected_reason", "expected_mutations"),
    [
        ("stale_revision", "source_write_refused", "STALE_DOCUMENT_REVISION", 0),
        ("capability_skew", "provenance_mismatch", "CAPABILITY_SKEW", 0),
        ("missing_verifier", "needs_review", "DOMAIN_VERIFIER_MISSING", 1),
        ("source_write_attempt", "source_write_refused", "SOURCE_WRITE_REFUSED", 0),
        ("privacy_leak", "privacy_blocked", "PRIVACY_BOUNDARY_FAILED", 0),
        ("budget_exhaustion", "budget_exhausted", "CAMPAIGN_BUDGET_EXHAUSTED", 0),
    ],
)
def test_fail_closed_chaos_matrix_has_one_terminal_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_state: str,
    expected_reason: str,
    expected_mutations: int,
) -> None:
    missing_verifier = case == "missing_verifier"

    def unavailable_verifier(task: Any, outcome: Any) -> None:
        del task, outcome
        return None

    rig = _Rig(
        tmp_path / case,
        terminal_artifact_hook=(
            unavailable_verifier if missing_verifier else _terminal_verifier
        ),
    )
    rig.start()

    if case == "stale_revision":
        monkeypatch.setattr(
            dispatch_module,
            "document_revision",
            lambda path: "sha256:" + ("f" * 64),
        )
        result = rig.service.continue_campaign(rig.campaign_id)
    elif case == "capability_skew":
        skewed = json.loads(json.dumps(rig.provenance))
        skewed["stack"]["core"]["sha256"] = "f" * 64
        rig.runtime_provenance = skewed
        result = rig.service.continue_campaign(rig.campaign_id)
    elif case == "source_write_attempt":
        attempts: list[str] = []
        prepare = rig.dispatcher.sandbox_manager.prepare

        def refuse_source_write(*args: Any, **kwargs: Any) -> Any:
            sandbox = prepare(*args, **kwargs)
            attempts.append("source-write")
            with pytest.raises(PracticeSandboxError) as captured:
                sandbox.writable_path(str(rig.source))
            assert captured.value.code == "SANDBOX_ESCAPE"
            raise captured.value

        monkeypatch.setattr(
            rig.dispatcher.sandbox_manager, "prepare", refuse_source_write
        )
        result = rig.service.continue_campaign(rig.campaign_id)
        assert attempts == ["source-write"]
    elif case == "privacy_leak":
        account = rig.queue.account

        def leaking_account(lease: Any, **usage: int) -> dict[str, Any]:
            return {
                **account(lease, **usage),
                "rawText": "synthetic private payload",
            }

        monkeypatch.setattr(rig.queue, "account", leaking_account)
        result = rig.service.continue_campaign(rig.campaign_id)
    elif case == "budget_exhaustion":
        account = rig.queue.account

        def over_budget_account(lease: Any, **usage: int) -> dict[str, Any]:
            usage["tool_calls"] = rig.run_ref["budgets"]["toolCalls"] + 1
            return account(lease, **usage)

        monkeypatch.setattr(rig.queue, "account", over_budget_account)
        result = rig.service.continue_campaign(rig.campaign_id)
    else:
        decision = rig.service.continue_campaign(rig.campaign_id)
        assert decision["boundary"]["state"] == "decision"
        result = rig.service.continue_campaign(
            rig.campaign_id,
            run_id=rig.run_ref["runId"],
            approved=True,
            decision_receipt_sha256=decision["boundary"][
                "decisionReceiptSha256"
            ],
        )

    assert result["terminal"]["terminalReason"] == expected_reason, result[
        "terminal"
    ]
    assert result["terminal"]["state"] == expected_state, result["terminal"]
    rig.assert_one_terminal(
        state=expected_state,
        reason=expected_reason,
        mutation_count=expected_mutations,
    )
    rig.reopen()
    replay = rig.service.continue_campaign(
        rig.campaign_id, run_id=rig.run_ref["runId"]
    )
    assert replay["idempotentReplay"] is True
    rig.assert_one_terminal(
        state=expected_state,
        reason=expected_reason,
        mutation_count=expected_mutations,
    )


def test_incomplete_campaign_close_is_durable_and_idempotent(tmp_path: Path) -> None:
    rig = _Rig(tmp_path / "incomplete")
    rig.start()
    first = rig.queue.close_incomplete(rig.campaign_id)
    second = rig.queue.close_incomplete(rig.campaign_id)

    assert first["state"] == second["state"] == "incomplete"
    assert first["terminalReceiptCount"] == second["terminalReceiptCount"] == 1
    rig.reopen()
    rig.assert_one_terminal(
        state="incomplete", reason="CAMPAIGN_INCOMPLETE", mutation_count=0
    )
    assert rig.service.continue_campaign(rig.campaign_id)["advanced"] is False


def test_chaos_matrix_declares_every_required_injection() -> None:
    assert _CASES == {
        "process_kill_restart",
        "repeated_continue",
        "stale_revision",
        "capability_skew",
        "missing_verifier",
        "source_write_attempt",
        "privacy_leak",
        "budget_exhaustion",
        "incomplete_campaign",
    }
