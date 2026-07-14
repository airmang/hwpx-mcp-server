from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from hwpx_mcp_server.workflow.render_metrics import (
    FROZEN_BASELINE_CORPUS,
    MeasureFirstGate,
    QueueMetricSample,
    RenderMetricObservation,
    account_terminal_receipts,
)
from hwpx_mcp_server.workflow.render_security import (
    HWPX_MEDIA_TYPE,
    RenderInputInspection,
    RenderSecurityPolicy,
    RenderSecurityViolation,
    redact_render_log,
    validate_render_input,
)
from hwpx_mcp_server.workflow.rendering import (
    RENDER_SCHEMA_VERSION,
    RENDER_SCHEMA_VERSION_V2,
    RenderArtifactKind,
    RenderArtifactV2,
    RenderJob,
    RenderJobV2,
    RenderReceipt,
    RenderReceiptV2,
    RenderStatus,
)


NOW = datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc)
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64


def v2_job() -> RenderJobV2:
    return RenderJobV2(
        job_id="render-job-v2-0001",
        workflow_id="workflow-v2-0001",
        idempotency_key="render-idempotency-0001",
        source_content_hash=HASH_A,
        source_size_bytes=4096,
        submitted_at=NOW,
    )


def v2_success() -> RenderReceiptV2:
    return RenderReceiptV2(
        job_id="render-job-v2-0001",
        workflow_id="workflow-v2-0001",
        input_content_hash=HASH_A,
        status=RenderStatus.SUCCEEDED,
        backend="windows-com-worker",
        hancom_build="Hancom Office 2024 build fixture",
        worker_version="render-worker/1.0.0",
        queued_at=NOW,
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=4),
        artifacts=(
            RenderArtifactV2(kind=RenderArtifactKind.PDF, content_hash=HASH_B, size_bytes=2048),
            RenderArtifactV2(
                kind=RenderArtifactKind.PAGE_PNG,
                content_hash=HASH_C,
                size_bytes=1024,
                page_number=1,
            ),
            RenderArtifactV2(
                kind=RenderArtifactKind.PAGE_PNG,
                content_hash=HASH_D,
                size_bytes=1024,
                page_number=2,
            ),
        ),
        page_count=2,
        retry_count=1,
        terminal_reason="SUCCEEDED",
        render_checked=True,
    )


def test_v1_wire_contract_remains_unchanged_while_v2_is_additive():
    legacy_job = RenderJob(
        job_id="render-job-0001",
        workflow_id="workflow-0001",
        source_path="/tmp/document.hwpx",
        source_content_hash=HASH_A,
    )
    legacy_receipt = RenderReceipt(job_id="render-job-0001", status=RenderStatus.QUEUED)

    assert RENDER_SCHEMA_VERSION == "hwpx.render.v1"
    assert legacy_job.model_dump(mode="json") == {
        "schema_version": "hwpx.render.v1",
        "job_id": "render-job-0001",
        "workflow_id": "workflow-0001",
        "source_path": "/tmp/document.hwpx",
        "source_content_hash": HASH_A,
        "requested_backend": "real_hancom",
        "dpi": 144,
    }
    assert legacy_receipt.model_dump(mode="json") == {
        "schema_version": "hwpx.render.v1",
        "job_id": "render-job-0001",
        "status": "queued",
        "backend": None,
        "hancom_build": None,
        "page_artifacts": [],
        "output_content_hash": None,
        "render_checked": False,
        "error_code": None,
    }
    assert RENDER_SCHEMA_VERSION_V2 == "hwpx.render.v2"


def test_v2_job_and_receipt_round_trip_and_bind_all_provenance():
    job = RenderJobV2.model_validate_json(v2_job().model_dump_json())
    receipt = RenderReceiptV2.model_validate_json(v2_success().model_dump_json())

    assert receipt.binds(job) is True
    assert receipt.page_count == 2
    assert receipt.retry_count == 1
    assert receipt.render_checked is True
    assert {item.content_hash for item in receipt.artifacts} == {HASH_B, HASH_C, HASH_D}
    assert receipt.model_dump(mode="json")["schema_version"] == "hwpx.render.v2"
    assert receipt.model_copy(update={"input_content_hash": HASH_B}).binds(job) is False


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"terminal_reason": None}, "terminal receipt requires"),
        ({"completed_at": NOW}, "monotonic timestamps"),
        ({"page_count": 1}, "exact page_count"),
        ({"worker_version": None}, "worker provenance"),
        ({"artifacts": ()}, "requires artifacts"),
    ],
)
def test_v2_success_cannot_overclaim_missing_evidence(changes, message):
    payload = v2_success().model_dump(mode="python")
    payload.update(changes)
    with pytest.raises(ValidationError, match=message):
        RenderReceiptV2.model_validate(payload)


def test_v2_success_requires_complete_page_sequence_and_one_pdf():
    payload = v2_success().model_dump(mode="python")
    payload["artifacts"] = (
        payload["artifacts"][0],
        payload["artifacts"][2],
    )
    payload["page_count"] = 1
    with pytest.raises(ValidationError, match="contiguous page numbers"):
        RenderReceiptV2.model_validate(payload)

    payload = v2_success().model_dump(mode="python")
    payload["artifacts"] = (*payload["artifacts"], payload["artifacts"][0])
    with pytest.raises(ValidationError, match="exactly one PDF"):
        RenderReceiptV2.model_validate(payload)


def test_v2_job_id_is_windows_sandbox_safe():
    payload = v2_job().model_dump(mode="python")
    payload["job_id"] = "render:job:0001"
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        RenderJobV2.model_validate(payload)


def test_cancelled_before_worker_start_is_terminal_but_never_render_checked():
    receipt = RenderReceiptV2(
        job_id="render-job-v2-0001",
        workflow_id="workflow-v2-0001",
        input_content_hash=HASH_A,
        status=RenderStatus.CANCELLED,
        queued_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        terminal_reason="CLIENT_CANCELLED",
    )
    assert receipt.started_at is None
    assert receipt.render_checked is False


def security_policy(tmp_path: Path) -> RenderSecurityPolicy:
    return RenderSecurityPolicy(sandbox_root=tmp_path / "render-sandboxes")


def valid_inspection(**changes) -> RenderInputInspection:
    values = {
        "authenticated": True,
        "principal_id": "worker-client-01",
        "filename": "document.hwpx",
        "media_type": HWPX_MEDIA_TYPE,
        "compressed_bytes": 1_000,
        "uncompressed_bytes": 5_000,
        "zip_entries": 20,
    }
    values.update(changes)
    return RenderInputInspection.model_validate(values)


def test_security_policy_is_non_optional_and_allocates_contained_sandboxes(tmp_path):
    policy = security_policy(tmp_path)
    sandbox = policy.sandbox_for("render-job-0001")

    assert sandbox.parent == policy.sandbox_root.resolve()
    assert policy.authenticated_transport_required is True
    assert policy.private_network_required is True
    assert policy.per_job_sandbox_required is True
    assert policy.delete_on_terminal is True
    with pytest.raises(ValidationError):
        RenderSecurityPolicy(sandbox_root=tmp_path, delete_on_terminal=False)
    with pytest.raises(RenderSecurityViolation) as caught:
        policy.sandbox_for("../escape")
    assert caught.value.code == "INVALID_JOB_ID"


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"authenticated": False, "principal_id": None}, "AUTH_REQUIRED"),
        ({"media_type": "application/zip"}, "MEDIA_TYPE_REJECTED"),
        ({"filename": "../document.hwpx"}, "FILENAME_REJECTED"),
        ({"compressed_bytes": 200 * 1024 * 1024}, "INPUT_TOO_LARGE"),
        ({"zip_entries": 20_000}, "ZIP_ENTRY_LIMIT"),
        ({"uncompressed_bytes": 600 * 1024 * 1024}, "ZIP_EXPANDED_SIZE_LIMIT"),
        ({"compressed_bytes": 1, "uncompressed_bytes": 101}, "ZIP_COMPRESSION_RATIO"),
        ({"has_encrypted_entry": True}, "ENCRYPTED_ZIP_REJECTED"),
        ({"has_symlink_entry": True}, "ZIP_SYMLINK_REJECTED"),
        ({"has_path_traversal": True}, "ZIP_TRAVERSAL_REJECTED"),
    ],
)
def test_input_security_matrix_fails_closed(tmp_path, changes, code):
    with pytest.raises(RenderSecurityViolation) as caught:
        validate_render_input(valid_inspection(**changes), security_policy(tmp_path))
    assert caught.value.code == code


def test_valid_input_retention_and_log_redaction_contract(tmp_path):
    policy = security_policy(tmp_path)
    assert validate_render_input(valid_inspection(), policy).compression_ratio == 5
    deadlines = policy.retention_deadlines(NOW)
    assert deadlines["inputDeleteBy"] == NOW + timedelta(hours=1)
    assert deadlines["outputDeleteBy"] == NOW + timedelta(days=1)

    secret = "학생 주민번호 000000-0000000"
    redacted = redact_render_log(
        {
            "event": "render.completed",
            "jobId": "render-job-0001",
            "status": "succeeded",
            "documentText": secret,
            "authorization": "Bearer top-secret",
            "sourcePath": "/private/student.hwpx",
            "details": {"prompt": secret},
        }
    )
    serialized = str(redacted)
    assert redacted["event"] == "render.completed"
    assert redacted["jobId"] == "render-job-0001"
    assert redacted["documentText"]["redacted"] is True
    assert secret not in serialized
    assert "Bearer top-secret" not in serialized
    assert "/private/student.hwpx" not in serialized

    free_text_reason = redact_render_log({"terminalReason": "학생 문서 처리 실패"})
    assert free_text_reason["terminalReason"]["redacted"] is True
    assert "학생 문서" not in str(free_text_reason)


def test_measure_first_corpus_and_gate_are_frozen_before_slo_claims():
    assert sum(bucket.document_count for bucket in FROZEN_BASELINE_CORPUS.buckets) == 100
    assert FROZEN_BASELINE_CORPUS.contract_hash == "5bb221d90c5cd138fa7561ae09eea41a25154fdd2f9f54cf2011c49d6dfab442"
    gate = MeasureFirstGate()
    assert gate.lost_job_target == 0
    assert gate.duplicate_terminal_receipt_target == 0
    assert gate.terminal_accounting_rate_target == 1.0
    assert gate.availability_target is None


def test_terminal_accounting_reports_no_lost_or_duplicate_jobs_and_raw_latency():
    expected = [f"render-job-{index:04d}" for index in range(100)]
    observations = [
        RenderMetricObservation(
            receipt_id=f"receipt-{index:04d}",
            job_id=job_id,
            submitted_at=NOW,
            terminal_at=NOW + timedelta(seconds=index + 1),
            status=RenderStatus.SUCCEEDED if index < 95 else RenderStatus.FAILED,
            eligible_for_success_availability=index < 95,
        )
        for index, job_id in enumerate(expected)
    ]
    report = account_terminal_receipts(expected, observations)

    assert report.exact_accounting is True
    assert report.lost_jobs == 0
    assert report.duplicate_terminal_receipts == 0
    assert report.terminal_accounting_rate == 1.0
    assert report.success_availability == 1.0
    assert report.latency_seconds == {"p50": 50.0, "p95": 95.0, "p99": 99.0}

    broken = account_terminal_receipts(
        expected,
        [*observations[:-1], observations[0].model_copy(update={"receipt_id": "receipt-duplicate"})],
    )
    assert broken.exact_accounting is False
    assert broken.missing_job_ids == ("render-job-0099",)
    assert broken.duplicate_job_ids == ("render-job-0000",)
    assert broken.lost_jobs == 1
    assert broken.duplicate_terminal_receipts == 1


def test_queue_metric_requires_explicit_degraded_reason():
    with pytest.raises(ValidationError, match="degraded_reason"):
        QueueMetricSample(
            observed_at=NOW,
            queue_depth=3,
            oldest_queued_age_seconds=12.5,
            running_jobs=0,
            worker_available=False,
        )
