from __future__ import annotations

import hashlib
import io
import ipaddress
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from hwpx_mcp_server.workflow.render_queue import DurableRenderQueue
from hwpx_mcp_server.workflow.render_security import RenderSecurityPolicy
from hwpx_mcp_server.workflow.render_transport import (
    RemoteRenderClientV2,
    serve_private_queue,
)
from hwpx_mcp_server.workflow.rendering import (
    RenderArtifactKind,
    RenderArtifactV2,
    RenderJobV2,
    RenderReceiptV2,
    RenderStatus,
)


NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)
SECRET = b"remote-transport-secret"


def tls_material(root: Path) -> dict[str, Path]:
    root.mkdir()
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HWPX test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    def issue(name: str, usage: ExtendedKeyUsageOID, *, server: bool = False):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        )
        if server:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
        return key, builder.sign(ca_key, hashes.SHA256())

    server_key, server_cert = issue(
        "127.0.0.1", ExtendedKeyUsageOID.SERVER_AUTH, server=True
    )
    client_key, client_cert = issue("render-client", ExtendedKeyUsageOID.CLIENT_AUTH)
    paths = {
        name: root / f"{name}.pem"
        for name in ("ca", "server_cert", "server_key", "client_cert", "client_key")
    }
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for path, value in (
        (paths["server_cert"], server_cert),
        (paths["client_cert"], client_cert),
    ):
        path.write_bytes(value.public_bytes(serialization.Encoding.PEM))
    for path, value in (
        (paths["server_key"], server_key),
        (paths["client_key"], client_key),
    ):
        path.write_bytes(
            value.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return paths


def hwpx() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", "<section/>")
    return stream.getvalue()


def _serve_or_skip(*args, **kwargs):
    """Run socket integration where loopback binding is permitted by the host."""

    try:
        return serve_private_queue(*args, **kwargs)
    except PermissionError as exc:
        pytest.skip(f"local socket binding is unavailable in this test sandbox: {exc}")


@pytest.fixture
def remote(tmp_path):
    root = tmp_path / "queue"
    queue = DurableRenderQueue(
        root,
        secret=SECRET,
        policy=RenderSecurityPolicy(sandbox_root=root / "sandboxes"),
    )
    server = _serve_or_skip(
        queue, secret=SECRET, host="127.0.0.1", port=0, allow_insecure_loopback=True
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = RemoteRenderClientV2(
        f"http://127.0.0.1:{server.server_port}",
        secret=SECRET,
        allow_insecure_loopback=True,
    )
    try:
        yield queue, client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_transport_requires_https_except_explicit_loopback():
    with pytest.raises(ValueError, match="HTTPS"):
        RemoteRenderClientV2("http://example.com", secret=SECRET)
    with pytest.raises(ValueError, match="transport_auth"):
        RemoteRenderClientV2(
            "https://example.com", secret=SECRET, transport_auth="none"
        )  # type: ignore[arg-type]


def test_mtls_requires_ca_and_client_identity_on_both_ends(tmp_path):
    root = tmp_path / "queue"
    queue = DurableRenderQueue(
        root,
        secret=SECRET,
        policy=RenderSecurityPolicy(sandbox_root=root / "sandboxes"),
    )
    tls = tls_material(tmp_path / "tls")
    with pytest.raises(ValueError, match="CA trust root"):
        serve_private_queue(
            queue,
            secret=SECRET,
            host="127.0.0.1",
            port=0,
            certfile=tls["server_cert"],
            keyfile=tls["server_key"],
        )
    with pytest.raises(ValueError, match="client certificate"):
        RemoteRenderClientV2("https://127.0.0.1:1", secret=SECRET, ca_file=tls["ca"])

    server = _serve_or_skip(
        queue,
        secret=SECRET,
        host="127.0.0.1",
        port=0,
        certfile=tls["server_cert"],
        keyfile=tls["server_key"],
        client_ca_file=tls["ca"],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        unsigned_client_identity = RemoteRenderClientV2(
            f"https://127.0.0.1:{server.server_port}",
            secret=SECRET,
            transport_auth="signed_https",
            ca_file=tls["ca"],
            allow_private_network=True,
        )
        with pytest.raises(Exception):
            unsigned_client_identity.capabilities()
        client = RemoteRenderClientV2(
            f"https://127.0.0.1:{server.server_port}",
            secret=SECRET,
            transport_auth="mtls",
            ca_file=tls["ca"],
            client_certfile=tls["client_cert"],
            client_keyfile=tls["client_key"],
            allow_private_network=True,
        )
        assert client.capabilities()["degradedReason"] == "NO_WORKER_HEARTBEAT"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_signed_https_is_explicit_and_preserves_hmac_auth(tmp_path):
    root = tmp_path / "queue"
    policy = RenderSecurityPolicy(
        sandbox_root=root / "sandboxes", transport_auth="signed_https"
    )
    queue = DurableRenderQueue(root, secret=SECRET, policy=policy)
    tls = tls_material(tmp_path / "tls")
    with pytest.raises(ValueError, match="must not be configured"):
        serve_private_queue(
            queue,
            secret=SECRET,
            host="127.0.0.1",
            port=0,
            certfile=tls["server_cert"],
            keyfile=tls["server_key"],
            client_ca_file=tls["ca"],
        )
    server = _serve_or_skip(
        queue,
        secret=SECRET,
        host="127.0.0.1",
        port=0,
        certfile=tls["server_cert"],
        keyfile=tls["server_key"],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = RemoteRenderClientV2(
            f"https://127.0.0.1:{server.server_port}",
            secret=SECRET,
            transport_auth="signed_https",
            ca_file=tls["ca"],
            allow_private_network=True,
        )
        assert client.capabilities()["degradedReason"] == "NO_WORKER_HEARTBEAT"
        wrong_hmac = RemoteRenderClientV2(
            client.base_url,
            secret=b"wrong",
            transport_auth="signed_https",
            ca_file=tls["ca"],
            allow_private_network=True,
        )
        with pytest.raises(Exception):
            wrong_hmac.capabilities()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_private_network_policy_rejects_wildcard_bind_unless_test_override(tmp_path):
    root = tmp_path / "queue"
    policy = RenderSecurityPolicy(
        sandbox_root=root / "sandboxes", transport_auth="signed_https"
    )
    queue = DurableRenderQueue(root, secret=SECRET, policy=policy)
    tls = tls_material(tmp_path / "tls")
    for host in ("0.0.0.0", "8.8.8.8", "::"):
        with pytest.raises(ValueError, match="private or loopback"):
            serve_private_queue(
                queue,
                secret=SECRET,
                host=host,
                port=0,
                certfile=tls["server_cert"],
                keyfile=tls["server_key"],
            )
    server = _serve_or_skip(
        queue,
        secret=SECRET,
        host="0.0.0.0",
        port=0,
        certfile=tls["server_cert"],
        keyfile=tls["server_key"],
        allow_non_private_test_bind=True,
    )
    server.server_close()


def test_server_render_client_wires_mtls_identity_from_environment(
    monkeypatch, tmp_path
):
    from hwpx_mcp_server import server as mcp_server

    captured = {}

    class CapturingClient:
        def __init__(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)

    monkeypatch.setattr(mcp_server, "RemoteRenderClientV2", CapturingClient)
    monkeypatch.setenv("HWPX_RENDER_QUEUE_URL", "https://10.0.0.8:9443")
    monkeypatch.setenv("HWPX_RENDER_QUEUE_SECRET", "signed-secret")
    monkeypatch.setenv("HWPX_RENDER_CA_FILE", str(tmp_path / "ca.pem"))
    monkeypatch.setenv("HWPX_RENDER_CLIENT_CERT_FILE", str(tmp_path / "client.pem"))
    monkeypatch.setenv("HWPX_RENDER_CLIENT_KEY_FILE", str(tmp_path / "client-key.pem"))

    mcp_server._render_client()

    assert captured == {
        "url": "https://10.0.0.8:9443",
        "secret": b"signed-secret",
        "transport_auth": "mtls",
        "ca_file": (tmp_path / "ca.pem").resolve(),
        "client_certfile": (tmp_path / "client.pem").resolve(),
        "client_keyfile": (tmp_path / "client-key.pem").resolve(),
    }


def test_authenticated_remote_submit_poll_cancel_health_and_artifact_fetch(
    remote, tmp_path
):
    queue, client = remote
    data = hwpx()
    source = tmp_path / "input.hwpx"
    source.write_bytes(data)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    job = RenderJobV2(
        job_id="remote-render-0001",
        workflow_id="remote-workflow-0001",
        idempotency_key="remote-key-0001",
        source_content_hash=digest,
        source_size_bytes=len(data),
        submitted_at=NOW,
    )
    queued = client.submit(job, source)
    assert queued.status == RenderStatus.QUEUED
    assert client.get(job.job_id) == queued
    assert client.capabilities()["degradedReason"] == "NO_WORKER_HEARTBEAT"

    lease = queue.claim("worker-1", now=NOW)
    assert lease
    pdf = b"real-pdf-fixture"
    png = b"real-page-fixture"
    pdf_hash = "sha256:" + hashlib.sha256(pdf).hexdigest()
    png_hash = "sha256:" + hashlib.sha256(png).hexdigest()
    queue.content.put(pdf, pdf_hash)
    queue.content.put(png, png_hash)
    receipt = RenderReceiptV2(
        job_id=job.job_id,
        workflow_id=job.workflow_id,
        input_content_hash=digest,
        status=RenderStatus.SUCCEEDED,
        backend="windows-com-worker",
        hancom_build="Hancom 2024 fixture",
        worker_version="worker/test",
        queued_at=NOW,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        artifacts=(
            RenderArtifactV2(
                kind=RenderArtifactKind.PDF, content_hash=pdf_hash, size_bytes=len(pdf)
            ),
            RenderArtifactV2(
                kind=RenderArtifactKind.PAGE_PNG,
                content_hash=png_hash,
                size_bytes=len(png),
                page_number=1,
            ),
        ),
        page_count=1,
        terminal_reason="SUCCEEDED",
        render_checked=True,
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
    bad = RemoteRenderClientV2(
        client.base_url, secret=b"wrong", allow_insecure_loopback=True
    )
    with pytest.raises(Exception):
        bad.capabilities()
