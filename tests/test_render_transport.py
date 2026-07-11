from __future__ import annotations

import hashlib
import io
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hwpx_mcp_server.workflow.render_queue import DurableRenderQueue
from hwpx_mcp_server.workflow.render_security import RenderSecurityPolicy
from hwpx_mcp_server.workflow.render_transport import RemoteRenderClientV2, serve_private_queue
from hwpx_mcp_server.workflow.rendering import (
    RenderArtifactKind, RenderArtifactV2, RenderJobV2, RenderReceiptV2, RenderStatus,
)


NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)
SECRET = b"remote-transport-secret"


def hwpx() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", "<section/>")
    return stream.getvalue()


@pytest.fixture
def remote(tmp_path):
    root = tmp_path / "queue"
    queue = DurableRenderQueue(root, secret=SECRET, policy=RenderSecurityPolicy(sandbox_root=root / "sandboxes"))
    server = serve_private_queue(queue, secret=SECRET, host="127.0.0.1", port=0, allow_insecure_loopback=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    client = RemoteRenderClientV2(
        f"http://127.0.0.1:{server.server_port}", secret=SECRET, allow_insecure_loopback=True,
    )
    try:
        yield queue, client
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_transport_requires_https_except_explicit_loopback():
    with pytest.raises(ValueError, match="HTTPS"):
        RemoteRenderClientV2("http://example.com", secret=SECRET)


def test_authenticated_remote_submit_poll_cancel_health_and_artifact_fetch(remote, tmp_path):
    queue, client = remote
    data = hwpx(); source = tmp_path / "input.hwpx"; source.write_bytes(data)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    job = RenderJobV2(
        job_id="remote-render-0001", workflow_id="remote-workflow-0001",
        idempotency_key="remote-key-0001", source_content_hash=digest,
        source_size_bytes=len(data), submitted_at=NOW,
    )
    queued = client.submit(job, source)
    assert queued.status == RenderStatus.QUEUED
    assert client.get(job.job_id) == queued
    assert client.capabilities()["degradedReason"] == "NO_WORKER_HEARTBEAT"

    lease = queue.claim("worker-1", now=NOW)
    assert lease
    pdf = b"real-pdf-fixture"; png = b"real-page-fixture"
    pdf_hash = "sha256:" + hashlib.sha256(pdf).hexdigest()
    png_hash = "sha256:" + hashlib.sha256(png).hexdigest()
    queue.content.put(pdf, pdf_hash); queue.content.put(png, png_hash)
    receipt = RenderReceiptV2(
        job_id=job.job_id, workflow_id=job.workflow_id, input_content_hash=digest,
        status=RenderStatus.SUCCEEDED, backend="windows-com-worker",
        hancom_build="Hancom 2024 fixture", worker_version="worker/test",
        queued_at=NOW, started_at=NOW, completed_at=NOW + timedelta(seconds=1),
        artifacts=(
            RenderArtifactV2(kind=RenderArtifactKind.PDF, content_hash=pdf_hash, size_bytes=len(pdf)),
            RenderArtifactV2(kind=RenderArtifactKind.PAGE_PNG, content_hash=png_hash, size_bytes=len(png), page_number=1),
        ), page_count=1, terminal_reason="SUCCEEDED", render_checked=True,
    )
    queue.complete(lease, receipt, now=NOW + timedelta(seconds=1))
    assert client.get(job.job_id).binds(job)
    assert client.fetch_artifact(job.job_id, pdf_hash) == pdf
    from hwpx_mcp_server import server as mcp_server
    original = mcp_server._render_client
    mcp_server._render_client = lambda: client
    try:
        downloaded = mcp_server.render_status(job.job_id, str(tmp_path / "downloaded"))
    finally:
        mcp_server._render_client = original
    assert Path(downloaded["savedArtifacts"][0]["path"]).read_bytes() == pdf
    assert Path(downloaded["savedArtifacts"][1]["path"]).read_bytes() == png
    with pytest.raises(Exception):
        client.fetch_artifact(job.job_id, "sha256:" + "f" * 64)


def test_wrong_transport_secret_is_rejected(remote):
    _, client = remote
    bad = RemoteRenderClientV2(client.base_url, secret=b"wrong", allow_insecure_loopback=True)
    with pytest.raises(Exception):
        bad.capabilities()
