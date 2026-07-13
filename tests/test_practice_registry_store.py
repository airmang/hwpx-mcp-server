from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from hwpx.practice.registry import PRIVATE_REGISTRY_SCHEMA
from hwpx_mcp_server.practice import (
    PracticeRegistryAuthenticationError,
    PracticeRegistryNotFound,
    PracticeRegistryStore,
)


def _record(**updates: object) -> dict:
    value = {
        "schema": PRIVATE_REGISTRY_SCHEMA,
        "documentId": "HWC-0123456789ABCDEFFEDC",
        "source": {
            "path": "/Volumes/private/hwpxcorpus",
            "filename": "학생명부-홍길동.hwpx",
            "sha256": "a" * 64,
            "sizeBytes": 123,
        },
        "storage": {
            "authenticatedEncryption": True,
            "keyId": "local-test",
            "algorithm": "AES-256-GCM",
        },
        "privacy": {
            "detectorStatus": "reviewed",
            "decision": "approved_local_only",
            "reviewedBy": "owner:local-review",
            "reviewedAt": "2026-07-13T12:00:00+09:00",
            "reviewBasis": ["content_review", "local_pii_scan"],
        },
        "lineage": {"groupId": "LIN-0123456789ABCDEFFEDC"},
        "family": "student-form",
        "state": "clean",
        "complexity": "medium",
        "suitability": "candidate_template",
        "openSafetyOk": True,
    }
    value.update(updates)
    return value


def _storage_bytes(path: Path) -> bytes:
    result = b""
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            result += candidate.read_bytes()
    return result


def test_store_encrypts_private_coordinates_and_lists_only_redacted(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    store = PracticeRegistryStore(path, encryption_key=b"k" * 32)
    assert store.put(_record()) is True
    assert store.put(_record()) is False
    store.checkpoint()

    raw = _storage_bytes(path)
    assert "학생명부".encode() not in raw
    assert "홍길동".encode() not in raw
    assert b"/Volumes/private/hwpxcorpus" not in raw
    assert store.get("HWC-0123456789ABCDEFFEDC")["source"]["filename"].endswith(".hwpx")
    redacted = store.list_redacted()
    assert len(redacted) == 1
    assert "source" not in redacted[0]
    assert "filename" not in repr(redacted)


def test_store_persists_with_0600_key_and_database(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    store = PracticeRegistryStore(path)
    store.put(_record())
    reopened = PracticeRegistryStore(path)
    assert reopened.count() == 1
    assert reopened.get("HWC-0123456789ABCDEFFEDC")["family"] == "student-form"
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(store.key_path).st_mode & 0o777 == 0o600


def test_store_rejects_tampering_wrong_key_and_missing_records(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    store = PracticeRegistryStore(path, encryption_key=b"k" * 32)
    store.put(_record())
    with sqlite3.connect(path) as connection:
        envelope = connection.execute(
            "SELECT record_ciphertext FROM practice_records"
        ).fetchone()[0]
        changed = bytearray(envelope)
        changed[-1] ^= 1
        connection.execute(
            "UPDATE practice_records SET record_ciphertext = ?", (bytes(changed),)
        )
    with pytest.raises(PracticeRegistryAuthenticationError, match="authentication"):
        store.get("HWC-0123456789ABCDEFFEDC")

    other_path = tmp_path / "other.sqlite3"
    other = PracticeRegistryStore(other_path, encryption_key=b"x" * 32)
    with pytest.raises(PracticeRegistryNotFound):
        other.get("HWC-FFFFFFFFFFFFFFFFFFFF")


def test_store_rejects_source_overlap_and_invalid_keys(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="disjoint"):
        PracticeRegistryStore(source / "registry.sqlite3", source_root=source)
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        PracticeRegistryStore(tmp_path / "bad.sqlite3", encryption_key=b"short")
