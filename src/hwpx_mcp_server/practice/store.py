# SPDX-License-Identifier: Apache-2.0
"""Authenticated local storage for private corpus registry records."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from hwpx.practice.registry import (
    PRIVATE_REGISTRY_SCHEMA,
    redact_private_record,
    validate_private_record,
    validate_storage_roots,
)


class PracticeRegistryNotFound(KeyError):
    """Raised when an opaque document identifier is absent."""


class PracticeRegistryAuthenticationError(RuntimeError):
    """Raised when encrypted registry bytes fail authentication."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class PracticeRegistryStore:
    """SQLite registry whose sensitive record body is always AES-256-GCM encrypted."""

    _PREFIX = b"hwpx-practice:v1:"

    def __init__(
        self,
        path: str | Path,
        *,
        encryption_key: bytes | str | None = None,
        source_root: str | Path | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        if source_root is not None:
            validate_storage_roots(source_root, self.path.parent)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = self._load_or_create_key(encryption_key)
        self._aead = AESGCM(self._key)
        self._initialize()

    @property
    def key_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.key")

    def _decode_key(self, value: str, *, name: str) -> bytes:
        try:
            key = base64.b64decode(value, altchars=b"-_", validate=True)
        except Exception as exc:
            raise ValueError(f"{name} must be URL-safe base64") from exc
        if len(key) != 32:
            raise ValueError(f"{name} must decode to exactly 32 bytes")
        return key

    def _load_or_create_key(self, supplied: bytes | str | None) -> bytes:
        if supplied is not None:
            key = supplied if isinstance(supplied, bytes) else self._decode_key(
                supplied, name="practice encryption key"
            )
            if len(key) != 32:
                raise ValueError("practice encryption key must be exactly 32 bytes")
            return key
        configured = os.environ.get("HWPX_PRACTICE_ENCRYPTION_KEY")
        if configured:
            return self._decode_key(configured, name="HWPX_PRACTICE_ENCRYPTION_KEY")
        try:
            key = self.key_path.read_bytes()
        except FileNotFoundError:
            key = AESGCM.generate_key(bit_length=256)
            temporary = self.key_path.with_name(f".{self.key_path.name}.{uuid.uuid4().hex}.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(key)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, self.key_path)
                except FileExistsError:
                    key = self.key_path.read_bytes()
            finally:
                temporary.unlink(missing_ok=True)
        if len(key) != 32:
            raise PracticeRegistryAuthenticationError("invalid practice registry key file")
        os.chmod(self.key_path, 0o600)
        return key

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA secure_delete=ON")
        os.chmod(self.path, 0o600)
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS practice_records (
                    document_id TEXT PRIMARY KEY,
                    record_ciphertext BLOB NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    @staticmethod
    def _aad(document_id: str) -> bytes:
        return f"{PRIVATE_REGISTRY_SCHEMA}:{document_id}".encode("ascii")

    def _encrypt(self, record: Mapping[str, Any]) -> tuple[bytes, str]:
        plaintext = _canonical_json(record)
        digest = hashlib.sha256(plaintext).hexdigest()
        nonce = os.urandom(12)
        ciphertext = self._aead.encrypt(nonce, plaintext, self._aad(str(record["documentId"])))
        return self._PREFIX + nonce + ciphertext, digest

    def _decrypt(self, document_id: str, envelope: bytes, expected_hash: str) -> dict[str, Any]:
        if not envelope.startswith(self._PREFIX):
            raise PracticeRegistryAuthenticationError("unsupported encrypted registry envelope")
        packed = envelope[len(self._PREFIX) :]
        try:
            plaintext = self._aead.decrypt(
                packed[:12], packed[12:], self._aad(document_id)
            )
        except (InvalidTag, ValueError) as exc:
            raise PracticeRegistryAuthenticationError(
                "private registry record failed authentication"
            ) from exc
        if hashlib.sha256(plaintext).hexdigest() != expected_hash:
            raise PracticeRegistryAuthenticationError("private registry content hash mismatch")
        try:
            parsed = json.loads(plaintext)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PracticeRegistryAuthenticationError("private registry plaintext is invalid") from exc
        return validate_private_record(parsed)

    def put(self, value: Mapping[str, Any]) -> bool:
        """Insert/update a private record; return False for an identical idempotent put."""
        record = validate_private_record(value)
        envelope, content_hash = self._encrypt(record)
        document_id = str(record["documentId"])
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT content_hash FROM practice_records WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if current is not None and str(current["content_hash"]) == content_hash:
                return False
            connection.execute(
                """
                INSERT INTO practice_records(document_id, record_ciphertext, content_hash)
                VALUES (?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    record_ciphertext = excluded.record_ciphertext,
                    content_hash = excluded.content_hash,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (document_id, envelope, content_hash),
            )
        return True

    def get(self, document_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_ciphertext, content_hash FROM practice_records WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            raise PracticeRegistryNotFound(document_id)
        return self._decrypt(document_id, bytes(row["record_ciphertext"]), str(row["content_hash"]))

    def list_redacted(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document_id, record_ciphertext, content_hash "
                "FROM practice_records ORDER BY document_id"
            ).fetchall()
        return [
            redact_private_record(
                self._decrypt(
                    str(row["document_id"]),
                    bytes(row["record_ciphertext"]),
                    str(row["content_hash"]),
                )
            )
            for row in rows
        ]

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM practice_records").fetchone()
        return int(row["count"])

    def checkpoint(self) -> None:
        """Flush and truncate the WAL before backup or at-rest inspection."""
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


__all__ = [
    "PracticeRegistryAuthenticationError",
    "PracticeRegistryNotFound",
    "PracticeRegistryStore",
]
