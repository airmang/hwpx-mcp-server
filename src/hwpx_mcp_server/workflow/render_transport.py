# SPDX-License-Identifier: Apache-2.0
"""Authenticated private HTTP transport for the durable render queue."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import socket
import ssl
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from .render_queue import DurableRenderQueue, RenderQueueError, sign_submission
from .rendering import RenderJobV2, RenderReceiptV2


def request_signature(secret: bytes, method: str, path: str, timestamp: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    message = f"{method.upper()}\n{path}\n{timestamp}\n{digest}".encode()
    return "sha256=" + hmac.new(secret, message, hashlib.sha256).hexdigest()


class RemoteRenderClientV2:
    def __init__(
        self,
        base_url: str,
        *,
        secret: bytes,
        transport_auth: Literal["mtls", "signed_https"] = "mtls",
        ca_file: Path | None = None,
        client_certfile: Path | None = None,
        client_keyfile: Path | None = None,
        timeout: float = 30,
        allow_insecure_loopback: bool = False,
    ) -> None:
        parsed = urlparse(base_url)
        if transport_auth not in {"mtls", "signed_https"}:
            raise ValueError("transport_auth must be 'mtls' or 'signed_https'")
        insecure_loopback = bool(
            allow_insecure_loopback and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )
        if parsed.scheme != "https" and not insecure_loopback:
            raise ValueError("render queue URL must use HTTPS (HTTP is test-only loopback)")
        if transport_auth == "mtls" and not insecure_loopback:
            if not ca_file or not client_certfile or not client_keyfile:
                raise ValueError("mTLS requires a CA trust root, client certificate, and client key")
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca_file))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(client_certfile), str(client_keyfile))
        elif transport_auth == "signed_https":
            if client_certfile or client_keyfile:
                raise ValueError("signed_https does not accept mTLS client credentials")
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca_file) if ca_file else None)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        else:  # explicit test-only HTTP loopback in mTLS mode
            context = None
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.transport_auth = transport_auth
        self._ssl_context = context
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, *, raw: bool = False):
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else b""
        timestamp = str(int(time.time()))
        request = Request(
            self.base_url + path, data=body if method != "GET" else None, method=method,
            headers={
                "Content-Type": "application/json", "X-HWPX-Timestamp": timestamp,
                "X-HWPX-Signature": request_signature(self.secret, method, path, timestamp, body),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout, context=self._ssl_context) as response:
                data = response.read()
        except HTTPError as exc:
            raise RenderQueueError(f"REMOTE_HTTP_{exc.code}", "remote render queue request failed") from exc
        return data if raw else json.loads(data)

    def capabilities(self) -> dict[str, object]:
        return self._request("GET", "/v1/health")

    def submit(self, job: RenderJobV2, source_path: Path) -> RenderReceiptV2:
        response = self._request("POST", "/v1/jobs", {
            "job": job.model_dump(mode="json"), "filename": source_path.name,
            "contentBase64": base64.b64encode(source_path.read_bytes()).decode("ascii"),
        })
        return RenderReceiptV2.model_validate(response["receipt"])

    def get(self, job_id: str) -> RenderReceiptV2:
        response = self._request("GET", f"/v1/jobs/{quote(job_id, safe='')}")
        return RenderReceiptV2.model_validate(response["receipt"])

    def cancel(self, job_id: str) -> RenderReceiptV2:
        response = self._request("POST", f"/v1/jobs/{quote(job_id, safe='')}/cancel", {})
        return RenderReceiptV2.model_validate(response["receipt"])

    def fetch_artifact(self, job_id: str, content_hash: str) -> bytes:
        digest = content_hash.removeprefix("sha256:")
        data = self._request("GET", f"/v1/jobs/{quote(job_id, safe='')}/artifacts/{digest}", raw=True)
        if "sha256:" + hashlib.sha256(data).hexdigest() != content_hash:
            raise RenderQueueError("ARTIFACT_HASH_MISMATCH", "downloaded artifact hash mismatch")
        return data


def make_queue_handler(queue: DurableRenderQueue, *, secret: bytes, max_clock_skew_seconds: int = 300):
    class QueueHandler(BaseHTTPRequestHandler):
        server_version = "HWPXRenderQueue/1"

        def log_message(self, format: str, *args: object) -> None:
            return None  # never log paths, query strings, headers, or document metadata

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            if length > queue.policy.max_input_bytes * 2:
                raise RenderQueueError("REQUEST_TOO_LARGE", "remote request body exceeds limit")
            return self.rfile.read(length)

        def _authenticate(self, body: bytes) -> None:
            timestamp = self.headers.get("X-HWPX-Timestamp", "")
            signature = self.headers.get("X-HWPX-Signature", "")
            try:
                fresh = abs(time.time() - int(timestamp)) <= max_clock_skew_seconds
            except ValueError:
                fresh = False
            expected = request_signature(secret, self.command, self.path, timestamp, body)
            if not fresh or not hmac.compare_digest(expected, signature):
                raise RenderQueueError("REMOTE_AUTH_REJECTED", "remote request authentication rejected")

        def _json(self, status: int, value: dict[str, Any]) -> None:
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def _dispatch(self) -> None:
            body = self._body()
            self._authenticate(body)
            parts = [part for part in self.path.split("/") if part]
            if self.command == "GET" and self.path == "/v1/health":
                self._json(200, queue.health()); return
            if self.command == "POST" and self.path == "/v1/jobs":
                payload = json.loads(body)
                job = RenderJobV2.model_validate(payload["job"])
                content = base64.b64decode(payload["contentBase64"], validate=True)
                receipt = queue.submit(
                    job, content, signature=sign_submission(secret, job),
                    principal_id="remote-mcp-client", filename=payload["filename"],
                )
                self._json(200, {"receipt": receipt.model_dump(mode="json")}); return
            if len(parts) == 3 and parts[:2] == ["v1", "jobs"] and self.command == "GET":
                self._json(200, {"receipt": queue.get(parts[2]).model_dump(mode="json")}); return
            if len(parts) == 4 and parts[:2] == ["v1", "jobs"] and parts[3] == "cancel" and self.command == "POST":
                self._json(200, {"receipt": queue.cancel(parts[2]).model_dump(mode="json")}); return
            if len(parts) == 5 and parts[:2] == ["v1", "jobs"] and parts[3] == "artifacts" and self.command == "GET":
                receipt = queue.get(parts[2])
                content_hash = "sha256:" + parts[4]
                if content_hash not in {item.content_hash for item in receipt.artifacts}:
                    raise RenderQueueError("ARTIFACT_NOT_IN_RECEIPT", "artifact is not bound to job receipt")
                data = queue.content.path_for(content_hash).read_bytes()
                self.send_response(200); self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
            self._json(404, {"errorCode": "NOT_FOUND"})

        def do_GET(self) -> None:
            self._safe_dispatch()

        def do_POST(self) -> None:
            self._safe_dispatch()

        def _safe_dispatch(self) -> None:
            try:
                self._dispatch()
            except (RenderQueueError, KeyError, ValueError, json.JSONDecodeError) as exc:
                code = getattr(exc, "code", "BAD_REQUEST")
                self._json(401 if code == "REMOTE_AUTH_REJECTED" else 400, {"errorCode": code})

    return QueueHandler


def serve_private_queue(
    queue: DurableRenderQueue, *, secret: bytes, host: str, port: int,
    certfile: Path | None = None, keyfile: Path | None = None,
    client_ca_file: Path | None = None,
    allow_insecure_loopback: bool = False,
    allow_non_private_test_bind: bool = False,
) -> ThreadingHTTPServer:
    if queue.policy.private_network_required and not allow_non_private_test_bind and not _is_private_bind_host(host):
        raise ValueError("render queue must bind only to a private or loopback address")
    insecure_loopback = bool(
        allow_insecure_loopback and host in {"127.0.0.1", "localhost", "::1"}
    )
    if not certfile or not keyfile:
        if not insecure_loopback:
            raise ValueError("TLS certificate and key are required outside test-only loopback")
    if queue.policy.transport_auth == "mtls" and not insecure_loopback and not client_ca_file:
        raise ValueError("mTLS requires a CA trust root for client-certificate verification")
    if queue.policy.transport_auth == "signed_https" and client_ca_file:
        raise ValueError("signed_https must not be configured as implicit mTLS")
    server = ThreadingHTTPServer((host, port), make_queue_handler(queue, secret=secret))
    if certfile and keyfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(certfile), str(keyfile))
        if queue.policy.transport_auth == "mtls":
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_verify_locations(cafile=str(client_ca_file))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def _is_private_bind_host(host: str) -> bool:
    """Fail closed unless every address represented by *host* is private/loopback."""

    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0].split("%", 1)[0])
                for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError):
            return False
    return bool(addresses) and all(
        (address.is_private or address.is_loopback)
        and not address.is_unspecified
        and not address.is_multicast
        for address in addresses
    )


__all__ = ["RemoteRenderClientV2", "make_queue_handler", "request_signature", "serve_private_queue"]
