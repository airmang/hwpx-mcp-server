from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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
    source_root = tmp_path / "source"
    practice_root = tmp_path / "practice"
    queue_root = practice_root / "runs" / "queue"
    source_root.mkdir(parents=True)
    queue_root.mkdir(parents=True)
    return PracticeCampaignQueue(
        queue_root,
        source_root=source_root,
        practice_root=practice_root,
    )


def _reopen(queue: PracticeCampaignQueue) -> PracticeCampaignQueue:
    return PracticeCampaignQueue(
        queue.root,
        source_root=queue.source_root,
        practice_root=queue.practice_root,
    )


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
    assert stat.S_IMODE(queue.integrity_key_path.stat().st_mode) == 0o600
    assert len(queue.integrity_key_path.read_bytes()) == 32
    for sidecar in (f"{queue.db_path.name}-wal", f"{queue.db_path.name}-shm"):
        path = queue.db_path.with_name(sidecar)
        if path.exists():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

    reopened = _reopen(queue)
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


def test_campaign_never_has_more_than_one_active_lease(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(count=2)
    queue.enqueue(manifest, now=NOW)

    with ThreadPoolExecutor(max_workers=4) as pool:
        leases = list(
            pool.map(
                lambda index: queue.claim(
                    f"campaign-worker-{index}",
                    campaign_id=manifest["campaignId"],
                    now=NOW,
                ),
                range(4),
            )
        )
    claimed = [lease for lease in leases if lease is not None]
    assert len(claimed) == 1
    assert queue.claim("same-campaign-worker", now=NOW) is None

    queue.terminalize(claimed[0], _receipt(claimed[0]), now=NOW)
    next_lease = queue.claim("next-campaign-worker", now=NOW)
    assert next_lease is not None
    assert next_lease.slot != claimed[0].slot


def test_authorize_returns_fixed_durable_budget_snapshot_and_terminals_zero(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(run_budgets=_budgets(toolCalls=1))
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("authorization-worker", now=NOW)
    assert lease is not None

    authorization = queue.authorize(lease, now=NOW)
    assert set(authorization) == {
        "schema",
        "campaignId",
        "runId",
        "authorized",
        "recoveryOnly",
        "mutationAllowed",
        "runRemaining",
        "campaignRemaining",
        "effectiveRemaining",
        "privateStorageCoordinatesExposed",
    }
    assert set(authorization["runRemaining"]) == set(_budgets())
    assert set(authorization["campaignRemaining"]) == {
        "toolCalls",
        "elapsedSeconds",
        "costMicrounits",
        "artifactBytes",
    }
    assert authorization["runRemaining"]["attempts"] == 1
    assert authorization["authorized"] is True
    assert authorization["mutationAllowed"] is True

    assert queue.account(lease, tool_calls=1, now=NOW)["state"] == "running"
    with pytest.raises(PracticeQueueError) as captured:
        queue.authorize(lease, now=NOW)
    assert captured.value.code == "BUDGET_EXHAUSTED"
    assert queue.receipts(manifest["campaignId"])[0]["usage"]["toolCalls"] == 1


def test_crash_recovery_reuses_dispatch_identity_then_issues_recovery_only_lease(
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
    queue = _reopen(queue)
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

    queue.recover(campaign_id=manifest["campaignId"], now=NOW + timedelta(seconds=6))
    third = queue.claim(
        "worker-recovery",
        campaign_id=manifest["campaignId"],
        now=NOW + timedelta(seconds=6),
    )
    assert third is not None
    assert third.attempt == 2
    assert third.recovery_only is True
    assert third.dispatch_generation == first.dispatch_generation
    assert third.dispatch_idempotency_key == first.dispatch_idempotency_key
    authorization = queue.authorize(third, now=NOW + timedelta(seconds=6))
    assert authorization["recoveryOnly"] is True
    assert authorization["mutationAllowed"] is False
    assert authorization["effectiveRemaining"]["attempts"] == 0
    queue.fail(
        third, "RECOVERY_INCOMPLETE", state="incomplete", now=NOW + timedelta(seconds=6)
    )
    receipts = queue.receipts(manifest["campaignId"])
    assert len(receipts) == 1
    assert receipts[0]["state"] == "incomplete"
    assert validate_run_receipt(receipts[0]) == receipts[0]


def test_claim_run_targets_only_the_requested_queued_or_recovery_slot(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(
        count=2, run_budgets=_budgets(attempts=1, elapsedSeconds=20)
    )
    queue.enqueue(manifest, now=NOW)
    first_id = manifest["runs"][0]["runId"]
    second_id = manifest["runs"][1]["runId"]
    first = queue.claim_run(first_id, "exact-first", lease_seconds=2, now=NOW)
    assert first is not None and first.run_id == first_id
    assert queue.claim_run(second_id, "blocked-second", now=NOW) is None

    recovered = queue.claim_run(
        first_id,
        "exact-recovery",
        lease_seconds=2,
        now=NOW + timedelta(seconds=3),
    )
    assert recovered is not None
    assert recovered.run_id == first_id
    assert recovered.attempt == first.attempt == 1
    assert recovered.recovery_only is True
    queue.fail(
        recovered,
        "RECOVERY_INCOMPLETE",
        state="incomplete",
        now=NOW + timedelta(seconds=3),
    )
    second = queue.claim_run(second_id, "exact-second", now=NOW + timedelta(seconds=3))
    assert second is not None and second.run_id == second_id


def test_resume_lease_rotates_token_without_new_attempt_or_dispatch(
    tmp_path: Path,
) -> None:
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


def test_resume_lease_commits_expiry_recovery_and_time_exhaustion(
    tmp_path: Path,
) -> None:
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

    resumed = queue.claim(
        "time-worker", lease_seconds=8, now=NOW + timedelta(seconds=2)
    )
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
    field = next(iter(budget_override))
    assert result["terminalReceipt"]["usage"][field] == budget_override[field]
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


def test_manifest_run_reference_is_rechecked_before_terminalization(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("manifest-bind-worker", now=NOW)
    assert lease is not None

    tampered = copy.deepcopy(dict(lease.run_ref))
    tampered["budgets"]["toolCalls"] += 1
    with sqlite3.connect(queue.db_path) as connection:
        connection.execute(
            "UPDATE practice_run_slots SET run_ref_json=? WHERE run_id=?",
            (json.dumps(tampered), lease.run_id),
        )

    with pytest.raises(PracticeQueueError) as captured:
        queue.terminalize(lease, _receipt(lease), now=NOW)
    assert captured.value.code == "QUEUE_STORAGE_FAILED"
    with pytest.raises(PracticeQueueError) as captured:
        queue.status(manifest["campaignId"])
    assert captured.value.code == "QUEUE_STORAGE_FAILED"


def test_paired_manifest_and_run_reference_tamper_cannot_rebind_db_identity(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    replacement, _ = _campaign(
        run_budgets=_budgets(toolCalls=3), seed="replacement-campaign"
    )
    queue.enqueue(manifest, now=NOW)
    replacement_ref = replacement["runs"][0]
    with sqlite3.connect(queue.db_path) as connection:
        connection.execute(
            "UPDATE practice_campaigns SET campaign_id=?,manifest_sha256=?,"
            "enqueue_key=?,manifest_json=? WHERE campaign_id=?",
            (
                replacement["campaignId"],
                replacement["manifestSha256"],
                queue._enqueue_key(replacement["manifestSha256"]),
                json.dumps(replacement),
                manifest["campaignId"],
            ),
        )
        connection.execute(
            "UPDATE practice_run_slots SET campaign_id=?,run_ref_json=?,run_id=?,"
            "scenario_id=?,dispatch_idempotency_key=? WHERE campaign_id=? AND slot=0",
            (
                replacement["campaignId"],
                json.dumps(replacement_ref),
                replacement_ref["runId"],
                replacement_ref["scenarioId"],
                queue._dispatch_key(
                    replacement["manifestSha256"],
                    0,
                    replacement_ref["runId"],
                ),
                manifest["campaignId"],
            ),
        )

    with pytest.raises(PracticeQueueError) as captured:
        queue.status(replacement["campaignId"])
    assert captured.value.code == "QUEUE_STORAGE_FAILED"


def test_null_integrity_mac_is_never_backfilled_or_trusted_on_reopen(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    with sqlite3.connect(queue.db_path) as connection:
        schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql=replace(sql,"
            "'integrity_mac TEXT NOT NULL','integrity_mac TEXT') "
            "WHERE type='table' AND name='practice_campaigns'"
        )
        connection.execute("PRAGMA writable_schema=OFF")
        connection.execute(f"PRAGMA schema_version={schema_version + 1}")
    with sqlite3.connect(queue.db_path) as connection:
        connection.execute(
            "UPDATE practice_campaigns SET integrity_mac=NULL WHERE campaign_id=?",
            (manifest["campaignId"],),
        )

    with pytest.raises(PracticeQueueError) as captured:
        _reopen(queue)
    assert captured.value.code == "QUEUE_STORAGE_FAILED"
    with sqlite3.connect(queue.db_path) as connection:
        assert (
            connection.execute(
                "SELECT integrity_mac FROM practice_campaigns WHERE campaign_id=?",
                (manifest["campaignId"],),
            ).fetchone()[0]
            is None
        )


def test_missing_integrity_schema_is_not_migrated_or_trusted(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    with sqlite3.connect(queue.db_path) as connection:
        connection.execute("ALTER TABLE practice_campaigns DROP COLUMN integrity_mac")

    with pytest.raises(PracticeQueueError) as captured:
        _reopen(queue)
    assert captured.value.code == "QUEUE_STORAGE_FAILED"


def test_missing_queue_table_is_not_recreated_on_reopen(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    with sqlite3.connect(queue.db_path) as connection:
        connection.execute("DROP TABLE practice_terminal_receipts")

    with pytest.raises(PracticeQueueError) as captured:
        _reopen(queue)
    assert captured.value.code == "QUEUE_STORAGE_FAILED"
    with sqlite3.connect(queue.db_path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='practice_terminal_receipts'"
            ).fetchone()
            is None
        )


def test_wrong_integrity_key_fails_during_reopen(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    original_key = queue.integrity_key_path.read_bytes()
    replacement_key = bytes(byte ^ 0xFF for byte in original_key)
    assert len(replacement_key) == 32 and replacement_key != original_key
    queue.integrity_key_path.write_bytes(replacement_key)
    os.chmod(queue.integrity_key_path, 0o600)

    with pytest.raises(PracticeQueueError) as captured:
        _reopen(queue)
    assert captured.value.code == "QUEUE_STORAGE_FAILED"


def test_fail_immediately_terminalizes_and_exact_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("failure-worker", now=NOW)
    assert lease is not None

    first = queue.fail(
        lease,
        "CAPABILITY_SKEW",
        state="provenance_mismatch",
        now=NOW,
    )
    assert first["state"] == "provenance_mismatch"
    assert first["terminalReason"] == "CAPABILITY_SKEW"
    assert (
        queue.fail(
            lease,
            "CAPABILITY_SKEW",
            state="provenance_mismatch",
            now=NOW,
        )
        == first
    )
    with pytest.raises(PracticeQueueError) as captured:
        queue.fail(lease, "DISPATCH_FAILED", now=NOW)
    assert captured.value.code == "TERMINAL_CONFLICT"
    assert len(queue.receipts(manifest["campaignId"])) == 1


def test_fail_can_reconcile_expired_identity_until_the_run_is_released(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path / "reconcile")
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    expired = queue.claim("expired-worker", lease_seconds=2, now=NOW)
    assert expired is not None
    queue.recover(now=NOW + timedelta(seconds=3))
    receipt = queue.fail(
        expired,
        "RECOVERY_INCOMPLETE",
        state="incomplete",
        now=NOW + timedelta(seconds=3),
    )
    assert receipt["state"] == "incomplete"

    queue = _queue(tmp_path / "released")
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    stale = queue.claim("stale-worker", lease_seconds=2, now=NOW)
    assert stale is not None
    queue.recover(now=NOW + timedelta(seconds=3))
    current = queue.claim("current-worker", now=NOW + timedelta(seconds=3))
    assert current is not None
    with pytest.raises(PracticeQueueError) as captured:
        queue.fail(
            stale,
            "RECOVERY_INCOMPLETE",
            state="incomplete",
            now=NOW + timedelta(seconds=3),
        )
    assert captured.value.code == "LEASE_NOT_OWNED"


def test_terminalize_can_reconcile_expired_identity_but_not_a_released_run(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path / "reconcile")
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    expired = queue.claim("expired-terminal", lease_seconds=2, now=NOW)
    assert expired is not None
    queue.recover(now=NOW + timedelta(seconds=3))
    receipt = _receipt(expired, usage_overrides={"elapsedSeconds": 2})
    assert (
        queue.terminalize(expired, receipt, now=NOW + timedelta(seconds=3)) == receipt
    )

    queue = _queue(tmp_path / "released")
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    stale = queue.claim("stale-terminal", lease_seconds=2, now=NOW)
    assert stale is not None
    queue.recover(now=NOW + timedelta(seconds=3))
    assert queue.claim("new-terminal", now=NOW + timedelta(seconds=3)) is not None
    with pytest.raises(PracticeQueueError) as captured:
        queue.terminalize(
            stale,
            _receipt(stale, usage_overrides={"elapsedSeconds": 2}),
            now=NOW + timedelta(seconds=3),
        )
    assert captured.value.code == "LEASE_NOT_OWNED"


def test_terminal_cleanup_candidates_require_content_bound_durable_ack(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("cleanup-worker", now=NOW)
    assert lease is not None
    queue.terminalize(lease, _receipt(lease), now=NOW)
    expected = (
        {
            "runId": lease.run_id,
            "startArtifactSha256": lease.run_ref["startArtifactSha256"],
        },
    )
    assert queue.terminal_cleanup_candidates() == expected
    assert queue.terminal_cleanup_candidates(limit=1) == expected
    assert set(expected[0]) == {"runId", "startArtifactSha256"}
    with pytest.raises(PracticeQueueError) as captured:
        queue.ack_terminal_cleanup(lease.run_id, "0" * 64, now=NOW)
    assert captured.value.code == "INVALID_ARGUMENT"
    ack = queue.ack_terminal_cleanup(
        lease.run_id, lease.run_ref["startArtifactSha256"], now=NOW
    )
    assert ack == {
        "schema": "hwpx.practice-campaign-queue-cleanup-ack/v1",
        "runId": lease.run_id,
        "startArtifactSha256": lease.run_ref["startArtifactSha256"],
        "acknowledged": True,
        "idempotentReplay": False,
        "privateStorageCoordinatesExposed": False,
    }
    assert queue.terminal_cleanup_candidates() == ()
    assert (
        queue.ack_terminal_cleanup(
            lease.run_id, lease.run_ref["startArtifactSha256"], now=NOW
        )["idempotentReplay"]
        is True
    )
    with pytest.raises(PracticeQueueError) as captured:
        queue.terminal_cleanup_candidates(limit=0)
    assert captured.value.code == "INVALID_ARGUMENT"


def test_cleanup_ack_advances_bounded_candidate_batches(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    manifest, _runs = _campaign(count=2)
    queue.enqueue(manifest, now=NOW)
    first = queue.claim("cleanup-first", now=NOW)
    assert first is not None
    queue.terminalize(first, _receipt(first), now=NOW)
    second = queue.claim("cleanup-second", now=NOW)
    assert second is not None
    queue.terminalize(second, _receipt(second), now=NOW)

    first_batch = queue.terminal_cleanup_candidates(limit=1)
    assert len(first_batch) == 1
    queue.ack_terminal_cleanup(
        first_batch[0]["runId"], first_batch[0]["startArtifactSha256"], now=NOW
    )
    second_batch = queue.terminal_cleanup_candidates(limit=1)
    assert len(second_batch) == 1
    assert second_batch[0]["runId"] != first_batch[0]["runId"]


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
    assert {result["receiptSha256"] for result in results} == {receipt["receiptSha256"]}
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
    assert first is not None
    other_ref = manifest["runs"][1]
    second = replace(
        first,
        slot=1,
        run_id=other_ref["runId"],
        scenario_id=other_ref["scenarioId"],
        run_ref=other_ref,
    )
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

    recovered = queue.resume(manifest["campaignId"], now=NOW + timedelta(seconds=3))
    assert recovered["state"] == "cancelled"
    receipts = queue.receipts(manifest["campaignId"])
    assert len(receipts) == 2
    assert {receipt["state"] for receipt in receipts} == {"cancelled"}
    with pytest.raises(PracticeQueueError) as captured:
        queue.terminalize(lease, _receipt(lease), now=NOW + timedelta(seconds=3))
    assert captured.value.code == "TERMINAL_CONFLICT"


def test_cancel_during_account_preserves_mutation_and_budget_has_precedence(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path / "within")
    manifest, _runs = _campaign()
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("cancel-account-worker", now=NOW)
    assert lease is not None
    queue.cancel(manifest["campaignId"], now=NOW)
    cancelled = queue.account(
        lease,
        tool_calls=1,
        artifact_bytes=17,
        now=NOW,
    )
    assert cancelled["state"] == "cancelled"
    assert cancelled["terminalReceipt"]["usage"]["toolCalls"] == 1
    assert cancelled["terminalReceipt"]["usage"]["artifactBytes"] == 17

    queue = _queue(tmp_path / "over")
    manifest, _runs = _campaign(run_budgets=_budgets(toolCalls=1))
    queue.enqueue(manifest, now=NOW)
    lease = queue.claim("cancel-budget-worker", now=NOW)
    assert lease is not None
    queue.cancel(manifest["campaignId"], now=NOW)
    exhausted = queue.account(lease, tool_calls=2, now=NOW)
    assert exhausted["state"] == "budget_exhausted"
    assert exhausted["terminalReceipt"]["terminalReason"] == "CAMPAIGN_BUDGET_EXHAUSTED"
    assert exhausted["terminalReceipt"]["usage"]["toolCalls"] == 1


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
    reopened = _reopen(queue)
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

    assert queue.claim("aggregate-two", now=NOW) is None
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

    alias = queue.practice_root / "queue-alias"
    alias.symlink_to(queue.root, target_is_directory=True)
    with pytest.raises(PracticeQueueError) as captured:
        PracticeCampaignQueue(
            alias,
            source_root=queue.source_root,
            practice_root=queue.practice_root,
        )
    assert captured.value.code == "INVALID_ROOT"
    assert str(alias) not in str(captured.value)

    malicious_root = queue.practice_root / "malicious-queue"
    malicious_root.mkdir()
    outside = tmp_path / "must-not-open.db"
    outside.write_bytes(b"outside")
    (malicious_root / "practice-campaign-queue.sqlite3").symlink_to(outside)
    with pytest.raises(PracticeQueueError) as captured:
        PracticeCampaignQueue(
            malicious_root,
            source_root=queue.source_root,
            practice_root=queue.practice_root,
        )
    assert captured.value.code == "INVALID_ROOT"
    assert outside.read_bytes() == b"outside"


@pytest.mark.parametrize(
    "terminal",
    [
        "practice-campaign-queue.sqlite3",
        "practice-campaign-queue.sqlite3-wal",
        "practice-campaign-queue.sqlite3-shm",
        "practice-campaign-queue.integrity.key",
    ],
)
def test_queue_storage_hardlinks_are_rejected_before_source_or_sibling_mutation(
    tmp_path: Path, terminal: str
) -> None:
    source_root = tmp_path / "source"
    practice_root = tmp_path / "practice"
    queue_root = practice_root / "runtime" / "queue"
    source_root.mkdir()
    queue_root.mkdir(parents=True)
    source = source_root / "immutable-source.hwpx"
    source.write_bytes(b"immutable-source-snapshot")
    before = (
        source.read_bytes(),
        source.stat().st_mode,
        source.stat().st_size,
        source.stat().st_mtime_ns,
    )
    os.link(source, queue_root / terminal)

    with pytest.raises(PracticeQueueError) as captured:
        PracticeCampaignQueue(
            queue_root,
            source_root=source_root,
            practice_root=practice_root,
        )
    assert captured.value.code == "INVALID_ROOT"
    assert (
        source.read_bytes(),
        source.stat().st_mode,
        source.stat().st_size,
        source.stat().st_mtime_ns,
    ) == before
    other_terminals = {
        "practice-campaign-queue.sqlite3",
        "practice-campaign-queue.integrity.key",
    } - {terminal}
    assert all(not (queue_root / name).exists() for name in other_terminals)


def test_queue_requires_existing_disjoint_roots_before_database_creation(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    practice_root = tmp_path / "practice"
    source_root.mkdir()
    practice_root.mkdir()

    inside_source = source_root / "queue"
    inside_source.mkdir()
    with pytest.raises(PracticeQueueError) as captured:
        PracticeCampaignQueue(
            inside_source,
            source_root=source_root,
            practice_root=practice_root,
        )
    assert captured.value.code == "INVALID_ROOT"
    assert not (inside_source / "practice-campaign-queue.sqlite3").exists()

    missing = practice_root / "runs" / "missing"
    with pytest.raises(PracticeQueueError) as captured:
        PracticeCampaignQueue(
            missing,
            source_root=source_root,
            practice_root=practice_root,
        )
    assert captured.value.code == "INVALID_ROOT"
    assert not missing.exists()


def test_boolean_usage_and_naive_time_are_rejected_without_mutation(
    tmp_path: Path,
) -> None:
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
