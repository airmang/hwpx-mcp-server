from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hwpx_mcp_server.workflow.render_metrics import (
    FROZEN_BASELINE_CORPUS, RenderMetricObservation, account_terminal_receipts,
)
from hwpx_mcp_server.workflow.render_queue import DurableRenderQueue, sign_submission
from hwpx_mcp_server.workflow.render_security import RenderSecurityPolicy
from hwpx_mcp_server.workflow.rendering import RenderJobV2, RenderStatus


NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)
SECRET = b"s068-measurement-secret"


def document(bucket: str, index: int) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr(
            "Contents/section0.xml",
            f"<section bucket='{bucket}' index='{index}'><p>{bucket}-{index}</p></section>",
        )
        if bucket in {"tables_and_merges", "known_templates", "unknown_forms"}:
            archive.writestr("Contents/header.xml", f"<header kind='{bucket}'/>")
        if bucket in {"images_and_shapes", "multi_section_multipage", "large_near_limit"}:
            archive.writestr(f"BinData/item-{index}.bin", hashlib.sha256(f"{bucket}-{index}".encode()).digest())
    return stream.getvalue()


def test_frozen_100_document_queue_measurement_has_exact_terminal_accounting(tmp_path):
    root = tmp_path / "queue"
    policy = RenderSecurityPolicy(sandbox_root=root / "sandboxes")
    queue = DurableRenderQueue(root, secret=SECRET, policy=policy, max_attempts=1)
    expected: list[str] = []
    observations: list[RenderMetricObservation] = []
    bucket_results: dict[str, int] = {}
    ordinal = 0
    for bucket in FROZEN_BASELINE_CORPUS.buckets:
        bucket_results[bucket.key] = 0
        for _ in range(bucket.document_count):
            ordinal += 1
            data = document(bucket.key, ordinal)
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
            job = RenderJobV2(
                job_id=f"render-measure-{ordinal:04d}", workflow_id="workflow-measure-0001",
                idempotency_key=f"measure-key-{ordinal:04d}", source_content_hash=digest,
                source_size_bytes=len(data), submitted_at=NOW + timedelta(milliseconds=ordinal),
            )
            signature = sign_submission(SECRET, job)
            first = queue.submit(job, data, signature=signature, principal_id="measure-client")
            duplicate = queue.submit(job, data, signature=signature, principal_id="measure-client")
            assert first == duplicate
            if ordinal % 10 == 0:
                queue = DurableRenderQueue(root, secret=SECRET, policy=policy, max_attempts=1)
            lease = queue.claim("measurement-worker", now=NOW + timedelta(seconds=ordinal))
            assert lease and lease.job.job_id == job.job_id
            receipt = queue.fail(
                lease, reason="MEASUREMENT_NO_REAL_HANCOM", retryable=False,
                now=NOW + timedelta(seconds=ordinal + 1),
            )
            assert receipt.status == RenderStatus.FAILED and receipt.render_checked is False
            expected.append(job.job_id)
            observations.append(
                RenderMetricObservation(
                    receipt_id=f"receipt-{ordinal:04d}", job_id=job.job_id,
                    submitted_at=job.submitted_at,
                    terminal_at=NOW + timedelta(seconds=ordinal + 1),
                    status=receipt.status,
                    eligible_for_success_availability=bucket.eligible_for_success_availability,
                )
            )
            bucket_results[bucket.key] += 1

    report = account_terminal_receipts(expected, observations)
    assert report.expected_jobs == report.unique_terminal_jobs == report.terminal_receipts == 100
    assert report.exact_accounting is True
    assert report.lost_jobs == report.duplicate_terminal_receipts == 0
    assert report.terminal_accounting_rate == 1.0
    assert bucket_results == {item.key: item.document_count for item in FROZEN_BASELINE_CORPUS.buckets}

    evidence = {
        "schemaVersion": "hwpx.render-measurement.v1",
        "corpusId": FROZEN_BASELINE_CORPUS.corpus_id,
        "corpusContractHash": FROZEN_BASELINE_CORPUS.contract_hash,
        "documents": 100,
        "bucketCounts": bucket_results,
        "queueReopenInterval": 10,
        "duplicateSubmissionAttempts": 100,
        "lostJobs": report.lost_jobs,
        "duplicateTerminalReceipts": report.duplicate_terminal_receipts,
        "terminalAccountingRate": report.terminal_accounting_rate,
        "exactAccounting": report.exact_accounting,
        "successAvailability": report.success_availability,
        "latencySeconds": report.latency_seconds,
        "renderChecked": False,
        "realHancom": False,
        "terminalReason": "MEASUREMENT_NO_REAL_HANCOM",
        "note": "Queue accounting baseline only; real-Hancom availability is measured by the approved soak/E2E gate.",
    }
    output = os.environ.get("HWPX_S068_MEASUREMENT_OUT")
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
