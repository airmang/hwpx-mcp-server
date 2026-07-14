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
from hwpx_mcp_server.workflow.render_security import RenderSecurityPolicy
from hwpx_mcp_server.workflow.rendering import (
    RenderArtifactKind,
    RenderArtifactV2,
    RenderJobV2,
    RenderReceiptV2,
    RenderStatus,
)


NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)
SECRET = b"chaos-test-render-secret"


def _hwpx() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", "<section>synthetic</section>")
    return stream.getvalue()


def _job(data: bytes, suffix: str) -> RenderJobV2:
    return RenderJobV2(
        job_id=f"render-chaos-{suffix}",
        workflow_id="workflow-chaos-0001",
        idempotency_key=f"render-chaos-key-{suffix}",
        source_content_hash="sha256:" + hashlib.sha256(data).hexdigest(),
        source_size_bytes=len(data),
        submitted_at=NOW,
    )


def _queue(root: Path, *, max_attempts: int = 3) -> DurableRenderQueue:
    policy = RenderSecurityPolicy(
        sandbox_root=root / "sandboxes",
        input_retention_seconds=0,
        output_retention_seconds=0,
    )
    return DurableRenderQueue(root / "queue", secret=SECRET, policy=policy, max_attempts=max_attempts)


def _submit(queue: DurableRenderQueue, job: RenderJobV2, data: bytes) -> RenderReceiptV2:
    return queue.submit(
        job,
        data,
        signature=sign_submission(SECRET, job),
        principal_id="chaos-client",
    )


def _success(job: RenderJobV2, completed_at: datetime) -> RenderReceiptV2:
    return RenderReceiptV2(
        job_id=job.job_id,
        workflow_id=job.workflow_id,
        input_content_hash=job.source_content_hash,
        status=RenderStatus.SUCCEEDED,
        backend="windows-com-worker",
        hancom_build="Hancom 2024 fixture",
        worker_version="worker/chaos",
        queued_at=job.submitted_at,
        started_at=job.submitted_at,
        completed_at=completed_at,
        artifacts=(
            RenderArtifactV2(
                kind=RenderArtifactKind.PDF,
                content_hash="sha256:" + "b" * 64,
                size_bytes=1,
            ),
            RenderArtifactV2(
                kind=RenderArtifactKind.PAGE_PNG,
                content_hash="sha256:" + "c" * 64,
                size_bytes=1,
                page_number=1,
            ),
        ),
        page_count=1,
        terminal_reason="SUCCEEDED",
        render_checked=True,
    )


def test_worker_kill_expires_lease_and_fresh_process_reclaims_without_duplicate_attempt(tmp_path):
    data = _hwpx()
    job = _job(data, "kill-0001")
    first_process = _queue(tmp_path)
    _submit(first_process, job, data)
    dead_worker = first_process.claim("worker-dead", lease_seconds=2, now=NOW)
    assert dead_worker and dead_worker.attempt == 1

    restarted = _queue(tmp_path)
    assert restarted.claim("worker-early", now=NOW + timedelta(seconds=1)) is None
    reclaimed = restarted.claim("worker-replacement", now=NOW + timedelta(seconds=3))
    assert reclaimed and reclaimed.attempt == 2
    with pytest.raises(RenderQueueError) as stale:
        restarted.fail(dead_worker, reason="LATE_DEAD_WORKER", retryable=False)
    assert stale.value.code == "LEASE_NOT_OWNED"
    assert restarted.get(job.job_id).status == RenderStatus.RUNNING


def test_com_hang_retries_then_exhausts_as_honestly_unverified(tmp_path):
    data = _hwpx()
    job = _job(data, "hang-0001")
    queue = _queue(tmp_path, max_attempts=2)
    _submit(queue, job, data)

    first = queue.claim("worker-1", now=NOW)
    assert first
    retry = queue.fail(
        first,
        reason="COM_WATCHDOG_TIMEOUT",
        retryable=True,
        now=NOW + timedelta(seconds=1),
    )
    assert retry.status == RenderStatus.QUEUED
    assert retry.retry_count == 1

    second = _queue(tmp_path, max_attempts=2).claim("worker-2", now=NOW + timedelta(seconds=2))
    assert second and second.attempt == 2
    terminal = queue.fail(
        second,
        reason="COM_WATCHDOG_TIMEOUT",
        retryable=True,
        now=NOW + timedelta(seconds=3),
    )
    assert terminal.status == RenderStatus.FAILED
    assert terminal.terminal_reason == "COM_WATCHDOG_TIMEOUT"
    assert terminal.render_checked is False
    assert terminal.artifacts == ()


def test_network_interruption_during_get_does_not_mutate_durable_queue(tmp_path):
    data = _hwpx()
    job = _job(data, "network-0001")
    queue = _queue(tmp_path)
    submitted = _submit(queue, job, data)

    class InterruptedClient:
        disconnected = True

        def get(self, job_id: str) -> RenderReceiptV2:
            if self.disconnected:
                self.disconnected = False
                raise ConnectionError("simulated response loss")
            return _queue(tmp_path).get(job_id)

    client = InterruptedClient()
    with pytest.raises(ConnectionError, match="response loss"):
        client.get(job.job_id)
    assert client.get(job.job_id) == submitted
    lease = _queue(tmp_path).claim("worker-after-network", now=NOW)
    assert lease and lease.attempt == 1


def test_concurrent_duplicate_submit_across_queue_instances_is_one_durable_job(tmp_path):
    data = _hwpx()
    job = _job(data, "duplicate-0001")
    queues = [_queue(tmp_path) for _ in range(6)]

    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(lambda queue: _submit(queue, job, data), queues))

    assert len({receipt.model_dump_json() for receipt in receipts}) == 1
    reopened = _queue(tmp_path)
    first = reopened.claim("worker-only", now=NOW)
    assert first and first.attempt == 1
    assert reopened.claim("worker-duplicate", now=NOW) is None


def test_queued_and_running_cancel_are_terminal_and_race_safe(tmp_path):
    data = _hwpx()
    queue = _queue(tmp_path)

    queued_job = _job(data, "cancel-queued")
    _submit(queue, queued_job, data)
    queued_cancel = queue.cancel(queued_job.job_id, now=NOW)
    assert queued_cancel.status == RenderStatus.CANCELLED
    assert queue.cancel(queued_job.job_id, now=NOW) == queued_cancel

    running_job = _job(data, "cancel-running")
    _submit(queue, running_job, data)
    lease = queue.claim("worker-running", now=NOW)
    assert lease
    requested = queue.cancel(running_job.job_id, now=NOW + timedelta(seconds=1))
    assert requested.status == RenderStatus.RUNNING
    with pytest.raises(RenderQueueError) as race:
        queue.complete(lease, _success(running_job, NOW + timedelta(seconds=2)))
    assert race.value.code == "CANCEL_REQUESTED"
    acknowledged = queue.fail(
        lease,
        reason="WORKER_STOPPED",
        retryable=False,
        now=NOW + timedelta(seconds=2),
    )
    assert acknowledged.status == RenderStatus.CANCELLED
    assert acknowledged.render_checked is False

    completed_job = _job(data, "complete-first")
    _submit(queue, completed_job, data)
    completed_lease = queue.claim("worker-complete", now=NOW + timedelta(seconds=3))
    assert completed_lease
    completed = queue.complete(
        completed_lease,
        _success(completed_job, NOW + timedelta(seconds=4)),
        now=NOW + timedelta(seconds=4),
    )
    assert queue.cancel(completed_job.job_id, now=NOW + timedelta(seconds=5)) == completed


def test_restart_preserves_retry_and_terminal_receipt_without_orphan_requeue(tmp_path):
    data = _hwpx()
    job = _job(data, "restart-0001")
    queue = _queue(tmp_path, max_attempts=3)
    _submit(queue, job, data)
    first = queue.claim("worker-before-restart", now=NOW)
    assert first
    queue.fail(first, reason="TRANSIENT_COM", retryable=True, now=NOW + timedelta(seconds=1))

    restarted = _queue(tmp_path, max_attempts=3)
    second = restarted.claim("worker-after-restart", now=NOW + timedelta(seconds=2))
    assert second and second.attempt == 2
    terminal = restarted.fail(
        second,
        reason="PERMANENT_RENDER_FAILURE",
        retryable=False,
        now=NOW + timedelta(seconds=3),
    )
    assert terminal.status == RenderStatus.FAILED

    reopened = _queue(tmp_path, max_attempts=3)
    assert reopened.get(job.job_id) == terminal
    assert reopened.claim("worker-orphan-check", now=NOW + timedelta(days=1)) is None
