# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import socket
from urllib.request import Request

import pytest

from hwpx_mcp_server.network_policy import (
    NetworkPolicy,
    NetworkPolicyError,
    PolicyRedirectHandler,
)


def _dns(address: str):
    def resolve(_host: str, port: int, *, type: int):
        assert type == socket.SOCK_STREAM
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    return resolve


def test_public_https_destination_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns("93.184.216.34"))
    assert NetworkPolicy().validate_url("https://example.test/document.hwpx")


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/document.hwpx",
        "https://10.0.0.4/document.hwpx",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/document.hwpx",
        "file:///workspace/document.hwpx",
    ],
)
def test_private_metadata_and_non_http_destinations_are_denied(url: str) -> None:
    with pytest.raises(NetworkPolicyError):
        NetworkPolicy().validate_url(url)


def test_explicit_private_opt_in_still_blocks_link_local_metadata() -> None:
    policy = NetworkPolicy(allow_private_network=True)
    assert policy.validate_url("https://10.0.0.4/document.hwpx")
    assert policy.validate_url("https://127.0.0.1/document.hwpx")
    assert policy.validate_url("https://[::1]/document.hwpx")
    with pytest.raises(NetworkPolicyError):
        policy.validate_url("https://169.254.169.254/latest/meta-data")


def test_connected_peer_is_revalidated_against_dns_rebinding() -> None:
    policy = NetworkPolicy()
    policy.validate_connected_address("93.184.216.34")
    with pytest.raises(NetworkPolicyError, match="connected network peer"):
        policy.validate_connected_address("127.0.0.1")


def test_invalid_port_is_reported_as_network_policy_error() -> None:
    with pytest.raises(NetworkPolicyError) as exc_info:
        NetworkPolicy().validate_url("https://example.test:99999/document.hwpx")
    assert exc_info.value.reason == "authority_invalid"


def test_redirect_target_is_revalidated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns("93.184.216.34"))
    handler = PolicyRedirectHandler(NetworkPolicy())
    request = Request("https://example.test/source")

    with pytest.raises(NetworkPolicyError):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://127.0.0.1/private",
        )
