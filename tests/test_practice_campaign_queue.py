from __future__ import annotations

import copy
import hashlib
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hwpx.practice import (
    PRACTICE_RUN_SCHEMA,
    build_campaign_manifest,
    practice_run_id,
    redact_run_receipt,
    validate_run_receipt,
)
from hwpx_mcp_server.practice.queue import (
    PracticeCampaignQueue,
    PracticeQueueError,
    PracticeRunLease,
)


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _provenance() -> dict:
    return {
        "stack": {
            "core": {"version": "2.30.0.dev1", "sha256": _digest("core")},
            "server": {"version": "2.22.0.dev1", "sha256": _digest("server")},
            "skill": {"version": "0.1.9.dev1", "sha256": _digest("skill")},
        },
        "toolSpec": {"version": "tool-spec/v1", "sha256": "0123456789abcdef"},
        "evaluator": {
            "version": "practice-evaluator/v1",
            "sha256": _digest("evaluator"),
        },
    }


def _budgets(**overrides: int) -> dict[str, int]:
    result = {
        "toolCalls": 4,
        "attempts": 2,
        "repairRounds": 2,
        "elapsedSeconds": 120,
        "costMicrounits": 1_000,
        "artifactBytes": 10_000,
    }
    result.update(overrides)
    return result


def _evidence(completed: bool) -> dict:
    status = "passed" if completed else "not_run"
    return {
        "semanticDiff": {
            "status": status,
            "receiptSha256": _digest("semantic") if completed else None,
        },
        "openSafety": {
            "status": status,
            "receiptSha256": _digest("open") if completed else None,
        },
        "domainVerdicts": (
            [
                {
                    "verifierId": "VER-QUEUE-0001",
                    "verifierSha256": _digest("verifier"),
                    "status": "passed",
                    "receiptSha256": _digest("domain"),
                }
            ]
            if completed
            else []
        ),
        "render": {
            "status": "unverified",
            "receiptSha256": None,
            "renderChecked": False,
            "provenance": "none",
        },
        "visual": {
            "status": "unverified",
            "receiptSha256": None,
            "allPagesChecked": False,
            "visualComplete": False,
        },
        "unresolvedReasonCodes": [] if completed else ["RECOVERY_INCOMPLETE"],
    }


def _run(
    slot: int,
    *,
    state: str = "completed",
    budgets: dict[str, int] | None = None,
    usage: dict[str, int] | None = None,
) -> dict:
    fixed_budgets = budgets or _budgets()
    completed = state == "completed"
    run = {
        "schema": PRACTICE_RUN_SCHEMA,
        "scenarioRef": {
            "scenarioId": f"SCN-{slot + 1:020X}",
            "scenarioSha256": _digest(f"scenario-{slot}"),
            "runnerManifestSha256": _digest("runner-manifest"),
            "derivativeSha256": _digest(f"derivative-{slot}"),
            "startArtifactId": f"ART-{slot + 1:020X}",
            "startArtifactSha256": _digest(f"start-{slot}"),
        },
        "dispatch": {
            "slot": slot,
            "dispatchKey": f"DSP-{slot + 1:020X}",
            "seedSha256": _digest(f"seed-{slot}"),
        },
        "provenance": _provenance(),
        "budgets": fixed_budgets,
        "state": state,
        "terminalReason": "VERIFIED" if completed else "RECOVERY_INCOMPLETE",
        "workflowEvents": [],
        "artifacts": [],
        "evidence": _evidence(completed),
        "usage": usage
        or {
            "toolCalls": 0,
            "attempts": 1,
            "repairRounds": 0,
            "elapsedSeconds": 0,
            "costMicrounits": 0,
            "artifactBytes": 0,
        },
        "privacy": {
            "localOnly": True,
            "syntheticInputsOnly": True,
            "highConfidencePiiCount": 0,
            "privateCoordinatesExposed": False,
            "evaluatorDataExposed": False,
        },
    }
    run["runId"] = practice_run_id(run)
    return run


def _run_ref(run: dict, *, family: str = "known_form_fill") -> dict:
    return {
        "slot": run["dispatch"]["slot"],
        "runId": run["runId"],
        "scenarioId": run["scenarioRef"]["scenarioId"],
        "scenarioSha256": run["scenarioRef"]["scenarioSha256"],
        "runnerManifestSha256": run["scenarioRef"]["runnerManifestSha256"],
        "derivativeSha256": run["scenarioRef"]["derivativeSha256"],
        "startArtifactSha256": run["scenarioRef"]["startArtifactSha256"],
        "family": family,
        "difficulty": "routine",
        "budgets": run["budgets"],
    }


def _campaign(
    *,
    count: int = 1,
    run_budgets: dict[str, int] | None = None,
    campaign_budget_overrides: dict[str, int] | None = None,
    seed: str = "campaign-a",
) -> tuple[dict, list[dict]]:
    runs = [_run(slot, budgets=run_budgets) for slot in range(count)]
    campaign_budgets = {
        "runs": count,
        "toolCalls": sum(run["budgets"]["toolCalls"] for run in runs),
        "elapsedSeconds": sum(run["budgets"]["elapsedSeconds"] for run in runs),
        "costMicrounits": sum(run["budgets"]["costMicrounits"] for run in runs),
        "artifactBytes": sum(run["budgets"]["artifactBytes"] for run in runs),
    }
    campaign_budgets.update(campaign_budget_overrides or {})
    manifest = build_campaign_manifest(
        scenario_manifest_sha256=_digest(f"scenario-manifest-{seed}"),
        selection={
            "seedSha256": _digest(seed),
            "strategyVersion": "coverage-weakness/v1",
            "policySha256": _digest("selection-policy"),
        },
        provenance=_provenance(),
        budgets=campaign_budgets,
        runs=[_run_ref(run) for run in runs],
    )
    return manifest, runs


def _queue(tmp_path: Path) -> PracticeCampaignQueue:
    return PracticeCampaignQueue(tmp_path / "queue")


def _receipt(
    lease: PracticeRunLease,
    *,
    state: str = "completed",
    usage_overrides: dict[str, int] | None = None,
) -> dict:
    usage = {
        "toolCalls": 0,
        "attempts": lease.attempt,
        "repairRounds": 0,
        "elapsedSeconds": 0,
        "costMicrounits": 0,
        "artifactBytes": 0,
    }
    usage.update(usage_overrides or {})
    run = _run(
        lease.slot,
        state=state,
        budgets=dict(lease.run_ref["budgets"]),
        usage=usage,
    )
    assert run["runId"] == lease.run_id
    return redact_run_receipt(run)


def test_enqueue_is_content_addressed_idempotent_durable_and_mode_hardened(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    enqueue_key = "IDEM-0123456789ABCDEFFEDC"
    first = queue.enqueue(manifest, enqueue_key=enqueue_key, now=NOW)
    replay = queue.enqueue(manifest, enqueue_key=enqueue_key, now=NOW)
    assert first["state"] == "queued"
    assert first["manifestSha256"] == manifest["manifestSha256"]
    assert first["idempotentReplay"] is False
    assert replay["idempotentReplay"] is True
    assert queue.pragmas() == ("wal", 2)
    assert stat.S_IMODE(queue.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(queue.db_path.stat().st_mode) == 0o600
    for sidecar in (f"{queue.db_path.name}-wal", f"{queue.db_path.name}-shm"):
        path = queue.db_path.with_name(sidecar)
        if path.exists():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

    reopened = PracticeCampaignQueue(queue.root)
    assert reopened.status(manifest["campaignId"])["expectedRunCount"] == 1

    other, _ = _campaign(seed="campaign-b")
    with pytest.raises(PracticeQueueError) as captured:
        queue.enqueue(
            other,
            enqueue_key=enqueue_key,
            now=NOW,
        )
    assert captured.value.code == "ENQUEUE_CONFLICT"


def test_concurrent_claim_has_one_owner_and_stable_dispatch_generation(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)

    with ThreadPoolExecutor(max_workers=4) as pool:
        leases = list(
            pool.map(
                lambda index: queue.claim(
                    f"worker-{index}", campaign_id=manifest["campaignId"], now=NOW
                ),
                range(4),
            )
        )
    claimed = [lease for lease in leases if lease is not None]
    assert len(claimed) == 1
    assert claimed[0].dispatch_generation == 1
    assert claimed[0].dispatch_idempotency_key.startswith("IDEM-")


def test_crash_recovery_reuses_dispatch_identity_and_exhausts_attempt_budget(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(run_budgets=_budgets(attempts=2, elapsedSeconds=20))
    queue.enqueue(manifest, now=NOW)
    first = queue.claim(
        "worker-first",
        campaign_id=manifest["campaignId"],
        lease_seconds=2,
        now=NOW,
    )
    assert first is not None and first.attempt == 1

    # Simulate a process crash by discarding the original queue object and
    # reopening only the durable root.
    queue = PracticeCampaignQueue(queue.root)
    recovery = queue.recover(
        campaign_id=manifest["campaignId"], now=NOW + timedelta(seconds=3)
    )
    assert recovery["recoveredSlots"] == 1
    second = queue.claim(
        "worker-second",
        campaign_id=manifest["campaignId"],
        lease_seconds=2,
        now=NOW + timedelta(seconds=3),
    )
    assert second is not None and second.attempt == 2
    assert second.dispatch_generation == first.dispatch_generation == 1
    assert second.dispatch_idempotency_key == first.dispatch_idempotency_key
    with pytest.raises(PracticeQueueError) as captured:
        queue.account(first, tool_calls=1, now=NOW + timedelta(seconds=3))
    assert captured.value.code == "LEASE_NOT_OWNED"

    queue.recover(
        campaign_id=manifest["campaignId"], now=NOW + timedelta(seconds=6)
    )
    receipts = queue.receipts(manifest["campaignId"])
    assert len(receipts) == 1
    assert receipts[0]["state"] == "budget_exhausted"
    assert validate_run_receipt(receipts[0]) == receipts[0]


def test_resume_lease_rotates_token_without_new_attempt_or_dispatch(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    first = queue.claim("decision-worker", lease_seconds=30, now=NOW)
    assert first is not None
    resumed = queue.resume_lease(
        first.run_id,
        "decision-worker",
        lease_seconds=30,
        now=NOW + timedelta(seconds=5),
    )
    assert resumed.attempt == first.attempt
    assert resumed.dispatch_generation == first.dispatch_generation
    assert resumed.dispatch_idempotency_key == first.dispatch_idempotency_key
    assert resumed.lease_token != first.lease_token
    with pytest.raises(PracticeQueueError) as captured:
        queue.account(first, tool_calls=1, now=NOW + timedelta(seconds=6))
    assert captured.value.code == "LEASE_NOT_OWNED"
    accounted = queue.account(
        resumed, tool_calls=1, cost_microunits=2, now=NOW + timedelta(seconds=6)
    )
    assert accounted["state"] == "running"
    assert accounted["usage"]["elapsedSeconds"] == 5


def test_resume_lease_commits_expiry_recovery_and_time_exhaustion(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(run_budgets=_budgets(attempts=2, elapsedSeconds=10))
    queue.enqueue(manifest, now=NOW)
    expired = queue.claim("expiry-worker", lease_seconds=2, now=NOW)
    assert expired is not None
    with pytest.raises(PracticeQueueError) as captured:
        queue.resume_lease(
            expired.run_id,
            expired.worker_id,
            now=NOW + timedelta(seconds=2),
        )
    assert captured.value.code == "LEASE_NOT_OWNED"
    assert queue.status(manifest["campaignId"])["state"] == "queued"

    resumed = queue.claim("time-worker", lease_seconds=8, now=NOW + timedelta(seconds=2))
    assert resumed is not None
    queue.account(resumed, elapsed_seconds=7, now=NOW + timedelta(seconds=2))
    with pytest.raises(PracticeQueueError) as captured:
        queue.resume_lease(
            resumed.run_id,
            resumed.worker_id,
            now=NOW + timedelta(seconds=3),
        )
    assert captured.value.code == "BUDGET_EXHAUSTED"
    receipts = queue.receipts(manifest["campaignId"])
    assert len(receipts) == 1
    assert receipts[0]["state"] == "budget_exhausted"


@pytest.mark.parametrize(
    ("budget_override", "account_kwargs"),
    [
        ({"toolCalls": 1}, {"tool_calls": 2}),
        ({"repairRounds": 1}, {"repair_rounds": 2}),
        ({"elapsedSeconds": 1}, {"elapsed_seconds": 2}),
        ({"costMicrounits": 1}, {"cost_microunits": 2}),
        ({"artifactBytes": 1}, {"artifact_bytes": 2}),
    ],
)
def test_account_reserves_fixed_ceiling_and_creates_one_budget_receipt(
    tmp_path: Path,
    budget_override: dict[str, int],
    account_kwargs: dict[str, int],
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(run_budgets=_budgets(**budget_override))
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("budget-worker", now=NOW)
    assert lease is not None
    result = queue.account(lease, now=NOW, **account_kwargs)
    assert result["state"] == "budget_exhausted"
    assert result["terminalReceipt"]["state"] == "budget_exhausted"
    assert queue.status(manifest["campaignId"])["terminalReceiptCount"] == 1
    assert len(queue.receipts(manifest["campaignId"])) == 1
    with pytest.raises(PracticeQueueError) as captured:
        queue.account(lease, now=NOW, **account_kwargs)
    assert captured.value.code == "LEASE_NOT_OWNED"


def test_terminal_receipt_is_bound_immutable_and_exact_replay_is_allowed(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("terminal-worker", now=NOW)
    assert lease is not None
    receipt = _receipt(lease)
    assert queue.terminalize(lease, receipt, now=NOW) == receipt
    assert queue.terminalize(lease, receipt, now=NOW) == receipt

    different = _receipt(lease, state="incomplete")
    with pytest.raises(PracticeQueueError) as captured:
        queue.terminalize(lease, different, now=NOW)
    assert captured.value.code == "TERMINAL_CONFLICT"
    assert queue.status(manifest["campaignId"])["state"] == "completed"

    with sqlite3.connect(queue.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE practice_terminal_receipts SET receipt_json='{}'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM practice_terminal_receipts")


def test_concurrent_identical_terminal_replay_keeps_exactly_one_receipt(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("terminal-race", now=NOW)
    assert lease is not None
    receipt = _receipt(lease)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _index: queue.terminalize(lease, receipt, now=NOW),
                range(8),
            )
        )
    assert {result["receiptSha256"] for result in results} == {
        receipt["receiptSha256"]
    }
    assert len(queue.receipts(manifest["campaignId"])) == 1


def test_refusal_is_a_completed_campaign_outcome_not_an_infrastructure_failure(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("refusal-worker", now=NOW)
    assert lease is not None
    queue.terminalize(lease, _receipt(lease, state="refused"), now=NOW)
    status = queue.status(manifest["campaignId"])
    assert status["state"] == "completed"
    assert status["counts"]["refused"] == 1


def test_receipt_binding_rejects_other_run_and_usage_rollback(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(count=2)
    queue.enqueue(manifest, now=NOW)
    first = queue.claim("worker-one", now=NOW)
    second = queue.claim("worker-two", now=NOW)
    assert first is not None and second is not None
    queue.account(first, tool_calls=1, now=NOW)

    with pytest.raises(PracticeQueueError) as captured:
        queue.terminalize(first, _receipt(second), now=NOW)
    assert captured.value.code == "RECEIPT_REJECTED"
    rollback = _receipt(first, usage_overrides={"toolCalls": 0})
    with pytest.raises(PracticeQueueError) as captured:
        queue.terminalize(first, rollback, now=NOW)
    assert captured.value.code == "RECEIPT_REJECTED"


def test_cancel_terminalizes_queued_then_expired_running_without_resume(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(count=2)
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("cancel-worker", lease_seconds=2, now=NOW)
    assert lease is not None
    cancelling = queue.cancel(manifest["campaignId"], now=NOW)
    assert cancelling["state"] == "cancelling"
    assert cancelling["terminalReceiptCount"] == 1
    assert queue.claim("late-worker", now=NOW) is None
    assert queue.resume(manifest["campaignId"], now=NOW)["state"] == "cancelling"

    recovered = queue.resume(
        manifest["campaignId"], now=NOW + timedelta(seconds=3)
    )
    assert recovered["state"] == "cancelled"
    receipts = queue.receipts(manifest["campaignId"])
    assert len(receipts) == 2
    assert {receipt["state"] for receipt in receipts} == {"cancelled"}
    with pytest.raises(PracticeQueueError) as captured:
        queue.terminalize(lease, _receipt(lease), now=NOW + timedelta(seconds=3))
    assert captured.value.code == "TERMINAL_CONFLICT"


def test_close_incomplete_is_fail_closed_durable_and_one_receipt_per_slot(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(count=2)
    queue.enqueue(manifest, now=NOW)
    assert queue.claim("incomplete-worker", now=NOW) is not None
    closed = queue.close_incomplete(manifest["campaignId"], now=NOW)
    assert closed["state"] == "incomplete"
    assert closed["incompleteSlots"] == []
    assert closed["terminalReceiptCount"] == 2
    assert queue.claim("other-worker", now=NOW) is None
    reopened = PracticeCampaignQueue(queue.root)
    receipts = reopened.receipts(manifest["campaignId"])
    assert len(receipts) == 2
    assert len({receipt["receiptSha256"] for receipt in receipts}) == 2
    assert {receipt["state"] for receipt in receipts} == {"incomplete"}


def test_campaign_budget_is_aggregate_and_cannot_be_hidden_by_second_slot(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(
        count=2,
        run_budgets=_budgets(toolCalls=2),
        campaign_budget_overrides={"toolCalls": 1},
    )
    queue.enqueue(manifest, now=NOW)
    first = queue.claim("aggregate-one", now=NOW)
    assert first is not None
    queue.account(first, tool_calls=1, now=NOW)
    queue.terminalize(
        first,
        _receipt(first, usage_overrides={"toolCalls": 1}),
        now=NOW,
    )

    second = queue.claim("aggregate-two", now=NOW)
    assert second is not None
    exhausted = queue.account(second, tool_calls=1, now=NOW)
    assert exhausted["state"] == "budget_exhausted"
    assert queue.status(manifest["campaignId"])["state"] == "budget_exhausted"
    assert len(queue.receipts(manifest["campaignId"])) == 2


def test_manifest_private_field_and_root_symlink_fail_without_path_disclosure(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    leaked = copy.deepcopy(manifest)
    leaked["sourcePath"] = str(tmp_path / "private-source.hwpx")
    with pytest.raises(PracticeQueueError) as captured:
        queue.enqueue(leaked, now=NOW)
    assert captured.value.code == "MANIFEST_REJECTED"
    assert str(tmp_path) not in str(captured.value)

    alias = tmp_path / "queue-alias"
    alias.symlink_to(queue.root, target_is_directory=True)
    with pytest.raises(PracticeQueueError) as captured:
        PracticeCampaignQueue(alias)
    assert captured.value.code == "INVALID_ROOT"
    assert str(alias) not in str(captured.value)

    malicious_root = tmp_path / "malicious-queue"
    malicious_root.mkdir()
    outside = tmp_path / "must-not-open.db"
    outside.write_bytes(b"outside")
    (malicious_root / "practice-campaign-queue.sqlite3").symlink_to(outside)
    with pytest.raises(PracticeQueueError) as captured:
        PracticeCampaignQueue(malicious_root)
    assert captured.value.code == "INVALID_ROOT"
    assert outside.read_bytes() == b"outside"


def test_boolean_usage_and_naive_time_are_rejected_without_mutation(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("argument-worker", now=NOW)
    assert lease is not None
    with pytest.raises(PracticeQueueError) as captured:
        queue.account(lease, tool_calls=True, now=NOW)
    assert captured.value.code == "INVALID_ARGUMENT"
    with pytest.raises(PracticeQueueError) as captured:
        queue.resume_lease(lease.run_id, lease.worker_id, now=datetime(2026, 7, 14))
    assert captured.value.code == "INVALID_ARGUMENT"
    assert queue.status(manifest["campaignId"])["terminalReceiptCount"] == 0
