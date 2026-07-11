from __future__ import annotations

import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hwpx_mcp_server.workflow.render_queue import (
    DurableRenderQueue,
    RenderQueueError,
    sign_submission,
)
from hwpx_mcp_server.workflow.render_security import RenderSecurityPolicy, RenderSecurityViolation
from hwpx_mcp_server.workflow.rendering import RenderJobV2, RenderReceiptV2, RenderStatus


NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)
SECRET = b"test-only-render-secret"


def hwpx_bytes(name: str = "Contents/section0.xml") -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr(name, "<hp:section xmlns:hp='urn:test'>안전</hp:section>")
    return out.getvalue()


def job(data: bytes, suffix: str = "0001") -> RenderJobV2:
    return RenderJobV2(
        job_id=f"render-job-{suffix}", workflow_id="workflow-0001",
        idempotency_key=f"render-key-{suffix}",
        source_content_hash="sha256:" + hashlib.sha256(data).hexdigest(),
        source_size_bytes=len(data), submitted_at=NOW,
    )


def queue(tmp_path: Path, **kwargs) -> DurableRenderQueue:
    policy = RenderSecurityPolicy(
        sandbox_root=tmp_path / "sandboxes", input_retention_seconds=0,
        output_retention_seconds=0,
    )
    return DurableRenderQueue(tmp_path / "queue", secret=SECRET, policy=policy, **kwargs)


def submit(q: DurableRenderQueue, j: RenderJobV2, data: bytes):
    return q.submit(j, data, signature=sign_submission(SECRET, j), principal_id="client-01")


def test_submit_is_authenticated_content_addressed_idempotent_and_durable(tmp_path):
    with pytest.raises(ValueError, match="max_attempts"):
        queue(tmp_path, max_attempts=0)
    q = queue(tmp_path)
    data = hwpx_bytes()
    j = job(data)
    with pytest.raises(RenderSecurityViolation, match="signature"):
        q.submit(j, data, signature="bad", principal_id="client-01")
    first = submit(q, j, data)
    second = submit(q, j, data)
    assert first == second and first.status == RenderStatus.QUEUED
    assert q.content.path_for(j.source_content_hash).read_bytes() == data
    assert q.pragmas() == ("wal", 2)
    reopened = queue(tmp_path)
    lease = reopened.claim("worker-1", now=NOW)
    assert lease and lease.job == j and lease.source_path.read_bytes() == data


def test_concurrent_duplicate_submission_creates_one_job(tmp_path):
    q = queue(tmp_path)
    data = hwpx_bytes()
    j = job(data)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: submit(q, j, data), range(8)))
    assert len({item.model_dump_json() for item in receipts}) == 1
    assert q.claim("worker-1", now=NOW) is not None
    assert q.claim("worker-2", now=NOW) is None


def test_rejects_hash_size_idempotency_and_archive_attacks(tmp_path):
    q = queue(tmp_path)
    data = hwpx_bytes()
    j = job(data)
    with pytest.raises(RenderQueueError) as mismatch:
        submit(q, j.model_copy(update={"source_size_bytes": len(data) + 1}), data)
    assert mismatch.value.code == "CONTENT_SIZE_MISMATCH"
    submit(q, j, data)
    conflict = j.model_copy(update={"source_content_hash": "sha256:" + "a" * 64})
    with pytest.raises(RenderQueueError):
        submit(q, conflict, data)
    evil = hwpx_bytes("Contents/../escape.xml")
    with pytest.raises(RenderSecurityViolation) as traversal:
        submit(q, job(evil, "evil"), evil)
    assert traversal.value.code == "ZIP_TRAVERSAL_REJECTED"

    plain_zip = io.BytesIO()
    with zipfile.ZipFile(plain_zip, "w") as archive:
        archive.writestr("mimetype", "application/zip")
        archive.writestr("Contents/not-hwpx.xml", "x")
    invalid = plain_zip.getvalue()
    with pytest.raises(RenderSecurityViolation) as package:
        submit(q, job(invalid, "plainzip"), invalid)
    assert package.value.code == "INVALID_HWPX_PACKAGE"


def test_lease_retry_recovery_cancel_and_terminal_receipt(tmp_path):
    q = queue(tmp_path, max_attempts=2)
    data = hwpx_bytes()
    j = job(data)
    submit(q, j, data)
    lease1 = q.claim("worker-1", lease_seconds=1, now=NOW)
    assert lease1 and lease1.attempt == 1
    lease2 = q.claim("worker-2", now=NOW + timedelta(seconds=2))
    assert lease2 and lease2.attempt == 2
    with pytest.raises(RenderQueueError) as stale:
        q.fail(lease1, reason="LATE_WORKER", retryable=False)
    assert stale.value.code == "LEASE_NOT_OWNED"
    failed = q.fail(lease2, reason="COM_HANG", retryable=True, now=NOW + timedelta(seconds=3))
    assert failed.status == RenderStatus.FAILED
    assert failed.terminal_reason == "COM_HANG"
    assert q.get(j.job_id) == failed

    j2 = job(data, "0002")
    submit(q, j2, data)
    cancelled = q.cancel(j2.job_id, now=NOW + timedelta(seconds=4))
    assert cancelled.status == RenderStatus.CANCELLED
    assert q.claim("worker-1", now=NOW + timedelta(seconds=5)) is None


def test_running_cancel_requires_worker_ack_and_stale_lease_cannot_complete(tmp_path):
    q = queue(tmp_path)
    data = hwpx_bytes()
    j = job(data)
    submit(q, j, data)
    lease = q.claim("worker-1", now=NOW)
    assert lease
    assert q.cancel(j.job_id, now=NOW + timedelta(seconds=1)).status == RenderStatus.RUNNING
    with pytest.raises(RenderQueueError) as cancel:
        q.complete(lease, _success(j, NOW + timedelta(seconds=2)), now=NOW + timedelta(seconds=2))
    assert cancel.value.code == "CANCEL_REQUESTED"
    receipt = q.fail(lease, reason="STOPPED", retryable=False, now=NOW + timedelta(seconds=2))
    assert receipt.status == RenderStatus.CANCELLED
    with pytest.raises(RenderQueueError) as stale:
        q.fail(lease, reason="LATE", retryable=False)
    assert stale.value.code == "LEASE_NOT_OWNED"


def test_retention_purge_deletes_unreferenced_blob(tmp_path):
    q = queue(tmp_path)
    data = hwpx_bytes()
    j = job(data)
    submit(q, j, data)
    q.cancel(j.job_id, now=NOW)
    blob = q.content.path_for(j.source_content_hash)
    assert q.purge(now=NOW + timedelta(seconds=1)) == 1
    assert not blob.exists()
    with pytest.raises(RenderQueueError):
        q.get(j.job_id)


def _success(j: RenderJobV2, completed_at: datetime) -> RenderReceiptV2:
    from hwpx_mcp_server.workflow.rendering import RenderArtifactKind, RenderArtifactV2

    return RenderReceiptV2(
        job_id=j.job_id, workflow_id=j.workflow_id, input_content_hash=j.source_content_hash,
        status=RenderStatus.SUCCEEDED, backend="windows-com-worker", hancom_build="Hancom 2024",
        worker_version="worker/1", queued_at=j.submitted_at, started_at=j.submitted_at,
        completed_at=completed_at, artifacts=(
            RenderArtifactV2(kind=RenderArtifactKind.PDF, content_hash="sha256:" + "b" * 64, size_bytes=1),
            RenderArtifactV2(kind=RenderArtifactKind.PAGE_PNG, content_hash="sha256:" + "c" * 64, size_bytes=1, page_number=1),
        ), page_count=1, terminal_reason="SUCCEEDED", render_checked=True,
    )
