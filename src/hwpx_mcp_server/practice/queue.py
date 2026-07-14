# SPDX-License-Identifier: Apache-2.0
"""Durable, privacy-safe campaign queue for private practice runs.

Only the redacted contracts from ``hwpx.practice`` are persisted here.  Raw
paths, filenames, prompts, document content, evaluator answers, and reversible
private mappings are not queue inputs and therefore do not require a second
private-state store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from hwpx.practice.campaign import (
    CAMPAIGN_STATES,
    validate_campaign_manifest,
)
from hwpx.practice.run import (
    ACTIVE_RUN_STATES,
    PRACTICE_RUN_RECEIPT_SCHEMA,
    RUN_BUDGET_FIELDS,
    TERMINAL_RUN_STATES,
    assert_receipt_safe,
    validate_run_receipt,
)
from hwpx_mcp_server.practice.sandbox import (
    PracticeSandboxError,
    validate_practice_roots,
)


_QUEUE_STATUS_SCHEMA = "hwpx.practice-campaign-queue-status/v1"
_QUEUE_USAGE_SCHEMA = "hwpx.practice-campaign-queue-usage/v1"
_QUEUE_RECOVERY_SCHEMA = "hwpx.practice-campaign-queue-recovery/v1"
_QUEUE_AUTHORIZATION_SCHEMA = "hwpx.practice-campaign-queue-authorization/v1"
_QUEUE_CLEANUP_ACK_SCHEMA = "hwpx.practice-campaign-queue-cleanup-ack/v1"
_ENQUEUE_KEY = re.compile(r"IDEM-[A-F0-9]{20}\Z")
_WORKER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{2,63}\Z")
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{1,63}\Z")
_RUN_ID = re.compile(r"PRUN-[A-F0-9]{20}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_RUN_STATES = ACTIVE_RUN_STATES | TERMINAL_RUN_STATES
_USAGE_COLUMNS = {
    "toolCalls": "usage_tool_calls",
    "attempts": "attempt",
    "repairRounds": "usage_repair_rounds",
    "elapsedSeconds": "usage_elapsed_seconds",
    "costMicrounits": "usage_cost_microunits",
    "artifactBytes": "usage_artifact_bytes",
}
_CAMPAIGN_USAGE_FIELDS = (
    "toolCalls",
    "elapsedSeconds",
    "costMicrounits",
    "artifactBytes",
)
_FAILURE_RUN_STATES = frozenset(
    {
        "failed",
        "incomplete",
        "needs_review",
        "privacy_blocked",
        "provenance_mismatch",
        "refused",
        "source_write_refused",
        "unverified",
    }
)

_ERROR_MESSAGES = {
    "INVALID_ROOT": "practice queue storage is invalid or unavailable",
    "INVALID_ARGUMENT": "practice queue argument is invalid",
    "MANIFEST_REJECTED": "practice campaign manifest was rejected",
    "ENQUEUE_CONFLICT": "practice campaign enqueue conflicts with durable state",
    "CAMPAIGN_NOT_FOUND": "practice campaign is unavailable",
    "RUN_NOT_FOUND": "practice run is unavailable",
    "LEASE_NOT_OWNED": "practice run lease is stale or not owned",
    "CANCEL_REQUESTED": "practice campaign cancellation is pending",
    "BUDGET_EXHAUSTED": "practice run budget is exhausted",
    "RECEIPT_REJECTED": "practice terminal receipt was rejected",
    "TERMINAL_CONFLICT": "practice run already has a different terminal receipt",
    "QUEUE_STORAGE_FAILED": "practice queue durable storage operation failed",
}


class PracticeQueueError(RuntimeError):
    """Fixed-code, path-redacted queue error safe for an MCP boundary."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_MESSAGES:
            code = "QUEUE_STORAGE_FAILED"
        self.code = code
        super().__init__(f"{code}: {_ERROR_MESSAGES[code]}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime:
    result = value or _utcnow()
    if result.tzinfo is None or result.utcoffset() is None:
        raise PracticeQueueError("INVALID_ARGUMENT")
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _require_nonnegative(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PracticeQueueError("INVALID_ARGUMENT")
    return value


@dataclass(frozen=True)
class PracticeRunLease:
    """Authenticated ownership plus an invariant downstream dispatch identity."""

    campaign_id: str
    manifest_sha256: str
    slot: int
    run_id: str
    scenario_id: str
    worker_id: str
    attempt: int
    dispatch_generation: int
    dispatch_idempotency_key: str
    lease_expires_at: datetime
    recovery_only: bool
    run_ref: Mapping[str, Any] = field(repr=False)
    provenance: Mapping[str, Any] = field(repr=False)
    lease_token: str = field(repr=False)


class PracticeCampaignQueue:
    """SQLite WAL/FULL queue with atomic leases and immutable receipts."""

    def __init__(
        self,
        root: str | Path,
        *,
        source_root: str | Path,
        practice_root: str | Path,
    ) -> None:
        try:
            self.source_root, self.practice_root, self.root = validate_practice_roots(
                source_root, practice_root, root
            )
            os.chmod(self.root, 0o700, follow_symlinks=False)
        except PracticeSandboxError as exc:
            raise PracticeQueueError("INVALID_ROOT") from exc
        except OSError as exc:
            raise PracticeQueueError("INVALID_ROOT") from exc
        self.db_path = self.root / "practice-campaign-queue.sqlite3"
        self.integrity_key_path = self.root / "practice-campaign-queue.integrity.key"
        self._preflight_storage_files(code="INVALID_ROOT")
        self._prepare_database_file()
        self._integrity_key = self._load_or_create_integrity_key()
        self._initialize()

    def _storage_files(self) -> tuple[Path, ...]:
        return (
            self.db_path,
            self.db_path.with_name(f"{self.db_path.name}-wal"),
            self.db_path.with_name(f"{self.db_path.name}-shm"),
            self.integrity_key_path,
        )

    def _preflight_storage_files(self, *, code: str) -> None:
        """Reject aliases before chmod, key access, or any SQLite open."""

        for path in self._storage_files():
            if path.parent != self.root:
                raise PracticeQueueError(code)
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise PracticeQueueError(code) from exc
            try:
                resolved = path.resolve(strict=True)
            except FileNotFoundError as exc:
                if path.name.endswith(("-wal", "-shm")):
                    continue
                raise PracticeQueueError(code) from exc
            except OSError as exc:
                raise PracticeQueueError(code) from exc
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or resolved != path
            ):
                raise PracticeQueueError(code)

    def _prepare_database_file(self) -> None:
        self._database_created = False
        self._preflight_storage_files(code="INVALID_ROOT")
        try:
            if self.db_path.exists() or self.db_path.is_symlink():
                metadata = self.db_path.lstat()
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or self.db_path.resolve(strict=True) != self.db_path
                ):
                    raise PracticeQueueError("INVALID_ROOT")
            else:
                descriptor = os.open(
                    self.db_path,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                os.close(descriptor)
                self._database_created = True
            self._preflight_storage_files(code="INVALID_ROOT")
            os.chmod(self.db_path, 0o600, follow_symlinks=False)
        except PracticeQueueError:
            raise
        except OSError as exc:
            raise PracticeQueueError("INVALID_ROOT") from exc

    def _read_integrity_key(self) -> bytes:
        self._preflight_storage_files(code="INVALID_ROOT")
        descriptor: int | None = None
        try:
            before = self.integrity_key_path.lstat()
            descriptor = os.open(
                self.integrity_key_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise PracticeQueueError("INVALID_ROOT")
            payload = os.read(descriptor, 33)
            after = os.fstat(descriptor)
            if (
                len(payload) != 32
                or after.st_nlink != 1
                or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            ):
                raise PracticeQueueError("INVALID_ROOT")
            os.fchmod(descriptor, 0o600)
        except PracticeQueueError:
            raise
        except OSError as exc:
            raise PracticeQueueError("INVALID_ROOT") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        self._preflight_storage_files(code="INVALID_ROOT")
        return payload

    def _load_or_create_integrity_key(self) -> bytes:
        self._preflight_storage_files(code="INVALID_ROOT")
        if self.integrity_key_path.exists() or self.integrity_key_path.is_symlink():
            return self._read_integrity_key()
        payload = secrets.token_bytes(32)
        descriptor: int | None = None
        try:
            descriptor = os.open(
                self.integrity_key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise PracticeQueueError("INVALID_ROOT")
                view = view[written:]
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o600)
        except FileExistsError:
            if descriptor is not None:
                os.close(descriptor)
            return self._read_integrity_key()
        except PracticeQueueError:
            raise
        except OSError as exc:
            raise PracticeQueueError("INVALID_ROOT") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        self._preflight_storage_files(code="INVALID_ROOT")
        return payload

    def _secure_files(self) -> None:
        self._preflight_storage_files(code="QUEUE_STORAGE_FAILED")
        for path in self._storage_files():
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise PracticeQueueError("QUEUE_STORAGE_FAILED") from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise PracticeQueueError("QUEUE_STORAGE_FAILED")
            try:
                os.chmod(path, 0o600, follow_symlinks=False)
            except OSError as exc:
                raise PracticeQueueError("QUEUE_STORAGE_FAILED") from exc

    def _connect(self) -> sqlite3.Connection:
        self._preflight_storage_files(code="QUEUE_STORAGE_FAILED")
        try:
            connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA synchronous=FULL")
            self._secure_files()
            return connection
        except PracticeQueueError:
            raise
        except sqlite3.Error as exc:
            raise PracticeQueueError("QUEUE_STORAGE_FAILED") from exc

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except PracticeQueueError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise PracticeQueueError("QUEUE_STORAGE_FAILED") from exc
        finally:
            connection.close()
            self._secure_files()

    @staticmethod
    def _campaign_integrity_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "campaignId": str(row["campaign_id"]),
            "manifestSha256": str(row["manifest_sha256"]),
            "enqueueKey": str(row["enqueue_key"]),
            "manifestJson": str(row["manifest_json"]),
            "createdAt": str(row["created_at"]),
        }

    @staticmethod
    def _run_integrity_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "campaignId": str(row["campaign_id"]),
            "slot": int(row["slot"]),
            "runId": str(row["run_id"]),
            "scenarioId": str(row["scenario_id"]),
            "runRefJson": str(row["run_ref_json"]),
            "dispatchGeneration": int(row["dispatch_generation"]),
            "dispatchIdempotencyKey": str(row["dispatch_idempotency_key"]),
            "createdAt": str(row["created_at"]),
        }

    def _integrity_mac(self, payload: Mapping[str, Any]) -> str:
        return hmac.new(
            self._integrity_key,
            _canonical(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _campaign_mac(self, row: Mapping[str, Any]) -> str:
        return self._integrity_mac(self._campaign_integrity_payload(row))

    def _run_mac(self, row: Mapping[str, Any]) -> str:
        return self._integrity_mac(self._run_integrity_payload(row))

    def _initialize(self) -> None:
        with self._connect() as connection:
            existing_tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required_tables = {
                "practice_campaigns",
                "practice_run_slots",
                "practice_terminal_receipts",
            }
            if not self._database_created and not required_tables <= existing_tables:
                raise PracticeQueueError("QUEUE_STORAGE_FAILED")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS practice_campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    manifest_sha256 TEXT NOT NULL UNIQUE,
                    enqueue_key TEXT NOT NULL UNIQUE,
                    manifest_json TEXT NOT NULL,
                    integrity_mac TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS practice_run_slots (
                    campaign_id TEXT NOT NULL REFERENCES practice_campaigns(campaign_id),
                    slot INTEGER NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    scenario_id TEXT NOT NULL,
                    run_ref_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    dispatch_generation INTEGER NOT NULL DEFAULT 1,
                    dispatch_idempotency_key TEXT NOT NULL UNIQUE,
                    integrity_mac TEXT NOT NULL,
                    lease_worker TEXT,
                    lease_token_sha256 TEXT,
                    leased_at TEXT,
                    lease_expires_at TEXT,
                    recovery_only INTEGER NOT NULL DEFAULT 0 CHECK(recovery_only IN (0,1)),
                    usage_tool_calls INTEGER NOT NULL DEFAULT 0,
                    usage_repair_rounds INTEGER NOT NULL DEFAULT 0,
                    usage_elapsed_seconds INTEGER NOT NULL DEFAULT 0,
                    usage_cost_microunits INTEGER NOT NULL DEFAULT 0,
                    usage_artifact_bytes INTEGER NOT NULL DEFAULT 0,
                    terminal_receipt_sha256 TEXT,
                    sandbox_cleanup_ack INTEGER NOT NULL DEFAULT 0 CHECK(sandbox_cleanup_ack IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, slot)
                );

                CREATE TABLE IF NOT EXISTS practice_terminal_receipts (
                    campaign_id TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    receipt_sha256 TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, slot),
                    FOREIGN KEY(campaign_id, slot)
                        REFERENCES practice_run_slots(campaign_id, slot)
                );

                CREATE INDEX IF NOT EXISTS practice_run_slots_state
                    ON practice_run_slots(state, campaign_id, slot);

                CREATE UNIQUE INDEX IF NOT EXISTS practice_run_slots_one_active_campaign
                    ON practice_run_slots(campaign_id)
                    WHERE state IN ('running','cancelling');

                CREATE TRIGGER IF NOT EXISTS practice_terminal_receipts_immutable_update
                BEFORE UPDATE ON practice_terminal_receipts
                BEGIN
                    SELECT RAISE(ABORT, 'terminal receipts are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS practice_terminal_receipts_immutable_delete
                BEFORE DELETE ON practice_terminal_receipts
                BEGIN
                    SELECT RAISE(ABORT, 'terminal receipts are immutable');
                END;
                """
            )
            campaign_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(practice_campaigns)")
            }
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(practice_run_slots)")
            }
            required_campaign_columns = {"integrity_mac"}
            required_run_columns = {
                "recovery_only",
                "integrity_mac",
                "sandbox_cleanup_ack",
            }
            if not required_campaign_columns <= campaign_columns or not (
                required_run_columns <= columns
            ):
                raise PracticeQueueError("QUEUE_STORAGE_FAILED")
            if (
                connection.execute(
                    "SELECT 1 FROM practice_campaigns WHERE integrity_mac IS NULL LIMIT 1"
                ).fetchone()
                is not None
                or connection.execute(
                    "SELECT 1 FROM practice_run_slots WHERE integrity_mac IS NULL LIMIT 1"
                ).fetchone()
                is not None
            ):
                raise PracticeQueueError("QUEUE_STORAGE_FAILED")
            campaigns = {
                str(row["campaign_id"]): row
                for row in connection.execute("SELECT * FROM practice_campaigns")
            }
            for campaign in campaigns.values():
                self._manifest(campaign)
            for slot in connection.execute("SELECT * FROM practice_run_slots"):
                campaign = campaigns.get(str(slot["campaign_id"]))
                if campaign is None:
                    raise PracticeQueueError("QUEUE_STORAGE_FAILED")
                self._run_ref(campaign, slot)
        self._secure_files()

    def pragmas(self) -> tuple[str, int]:
        with self._connect() as connection:
            journal = str(
                connection.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower()
            synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        return journal, synchronous

    @staticmethod
    def _enqueue_key(manifest_hash: str) -> str:
        digest = hashlib.sha256(("enqueue:" + manifest_hash).encode()).hexdigest()
        return f"IDEM-{digest[:20].upper()}"

    @staticmethod
    def _dispatch_key(manifest_hash: str, slot: int, run_id: str) -> str:
        payload = f"dispatch:{manifest_hash}:{slot}:{run_id}".encode("ascii")
        return f"IDEM-{hashlib.sha256(payload).hexdigest()[:20].upper()}"

    def enqueue(
        self,
        manifest: Mapping[str, Any],
        *,
        enqueue_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        try:
            validated = validate_campaign_manifest(manifest)
            assert_receipt_safe(validated)
        except ValueError as exc:
            raise PracticeQueueError("MANIFEST_REJECTED") from exc
        manifest_json = _canonical(validated)
        manifest_hash = str(validated["manifestSha256"])
        campaign_id = str(validated["campaignId"])
        key = enqueue_key or self._enqueue_key(manifest_hash)
        if not _ENQUEUE_KEY.fullmatch(key):
            raise PracticeQueueError("INVALID_ARGUMENT")
        observed = _aware(now)
        replay = False
        with self._transaction() as connection:
            by_key = connection.execute(
                "SELECT * FROM practice_campaigns WHERE enqueue_key=?",
                (key,),
            ).fetchone()
            if by_key is not None:
                self._manifest(by_key)
            if by_key is not None and (
                by_key["campaign_id"] != campaign_id
                or by_key["manifest_sha256"] != manifest_hash
                or by_key["manifest_json"] != manifest_json
            ):
                raise PracticeQueueError("ENQUEUE_CONFLICT")
            existing = connection.execute(
                "SELECT * FROM practice_campaigns "
                "WHERE campaign_id=? OR manifest_sha256=?",
                (campaign_id, manifest_hash),
            ).fetchone()
            if existing is not None:
                self._manifest(existing)
            if existing is not None or by_key is not None:
                existing = existing or by_key
                if (
                    existing["campaign_id"] != campaign_id
                    or existing["manifest_sha256"] != manifest_hash
                    or existing["manifest_json"] != manifest_json
                ):
                    raise PracticeQueueError("ENQUEUE_CONFLICT")
                replay = True
            else:
                timestamp = _iso(observed)
                run_ids = [str(run_ref["runId"]) for run_ref in validated["runs"]]
                placeholders = ",".join("?" for _ in run_ids)
                conflicting_run = connection.execute(
                    f"SELECT 1 FROM practice_run_slots WHERE run_id IN ({placeholders}) LIMIT 1",
                    run_ids,
                ).fetchone()
                if conflicting_run is not None:
                    raise PracticeQueueError("ENQUEUE_CONFLICT")
                campaign_record = {
                    "campaign_id": campaign_id,
                    "manifest_sha256": manifest_hash,
                    "enqueue_key": key,
                    "manifest_json": manifest_json,
                    "created_at": timestamp,
                }
                connection.execute(
                    "INSERT INTO practice_campaigns "
                    "(campaign_id,manifest_sha256,enqueue_key,manifest_json,integrity_mac,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        campaign_id,
                        manifest_hash,
                        key,
                        manifest_json,
                        self._campaign_mac(campaign_record),
                        timestamp,
                        timestamp,
                    ),
                )
                for run_ref in validated["runs"]:
                    slot = int(run_ref["slot"])
                    run_ref_json = _canonical(run_ref)
                    dispatch_key = self._dispatch_key(
                        manifest_hash, slot, run_ref["runId"]
                    )
                    run_record = {
                        "campaign_id": campaign_id,
                        "slot": slot,
                        "run_id": run_ref["runId"],
                        "scenario_id": run_ref["scenarioId"],
                        "run_ref_json": run_ref_json,
                        "dispatch_generation": 1,
                        "dispatch_idempotency_key": dispatch_key,
                        "created_at": timestamp,
                    }
                    connection.execute(
                        "INSERT INTO practice_run_slots "
                        "(campaign_id,slot,run_id,scenario_id,run_ref_json,state,"
                        "dispatch_idempotency_key,integrity_mac,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,'queued',?,?,?,?)",
                        (
                            campaign_id,
                            slot,
                            run_ref["runId"],
                            run_ref["scenarioId"],
                            run_ref_json,
                            dispatch_key,
                            self._run_mac(run_record),
                            timestamp,
                            timestamp,
                        ),
                    )
        return self._status(campaign_id, idempotent_replay=replay)

    def _campaign_row(
        self, connection: sqlite3.Connection, campaign_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM practice_campaigns WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        if row is None:
            raise PracticeQueueError("CAMPAIGN_NOT_FOUND")
        self._manifest(row)
        return row

    def _manifest(self, row: sqlite3.Row) -> dict[str, Any]:
        stored_mac = row["integrity_mac"]
        if not isinstance(stored_mac, str) or not hmac.compare_digest(
            stored_mac, self._campaign_mac(row)
        ):
            raise PracticeQueueError("QUEUE_STORAGE_FAILED")
        try:
            manifest = validate_campaign_manifest(json.loads(str(row["manifest_json"])))
        except (ValueError, json.JSONDecodeError) as exc:
            raise PracticeQueueError("QUEUE_STORAGE_FAILED") from exc
        if (
            manifest["campaignId"] != row["campaign_id"]
            or manifest["manifestSha256"] != row["manifest_sha256"]
        ):
            raise PracticeQueueError("QUEUE_STORAGE_FAILED")
        return manifest

    def _run_ref(self, campaign: sqlite3.Row, row: sqlite3.Row) -> dict[str, Any]:
        stored_mac = row["integrity_mac"]
        if not isinstance(stored_mac, str) or not hmac.compare_digest(
            stored_mac, self._run_mac(row)
        ):
            raise PracticeQueueError("QUEUE_STORAGE_FAILED")
        try:
            value = json.loads(str(row["run_ref_json"]))
        except json.JSONDecodeError as exc:
            raise PracticeQueueError("QUEUE_STORAGE_FAILED") from exc
        if not isinstance(value, dict):
            raise PracticeQueueError("QUEUE_STORAGE_FAILED")
        manifest = self._manifest(campaign)
        if str(row["campaign_id"]) != str(campaign["campaign_id"]):
            raise PracticeQueueError("QUEUE_STORAGE_FAILED")
        expected = [
            run_ref
            for run_ref in manifest["runs"]
            if int(run_ref["slot"]) == int(row["slot"])
        ]
        if (
            len(expected) != 1
            or value != expected[0]
            or value.get("runId") != row["run_id"]
            or value.get("scenarioId") != row["scenario_id"]
        ):
            raise PracticeQueueError("QUEUE_STORAGE_FAILED")
        return dict(expected[0])

    @staticmethod
    def _usage(row: sqlite3.Row) -> dict[str, int]:
        return {field: int(row[column]) for field, column in _USAGE_COLUMNS.items()}

    @staticmethod
    def _lease_token() -> str:
        return f"PLS-{secrets.token_hex(20).upper()}"

    @staticmethod
    def _lease_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def _make_lease(
        self,
        campaign: sqlite3.Row,
        slot: sqlite3.Row,
        *,
        worker_id: str,
        token: str,
    ) -> PracticeRunLease:
        manifest = self._manifest(campaign)
        return PracticeRunLease(
            campaign_id=str(slot["campaign_id"]),
            manifest_sha256=str(campaign["manifest_sha256"]),
            slot=int(slot["slot"]),
            run_id=str(slot["run_id"]),
            scenario_id=str(slot["scenario_id"]),
            worker_id=worker_id,
            attempt=int(slot["attempt"]),
            dispatch_generation=int(slot["dispatch_generation"]),
            dispatch_idempotency_key=str(slot["dispatch_idempotency_key"]),
            lease_expires_at=_datetime(str(slot["lease_expires_at"])),
            recovery_only=bool(slot["recovery_only"]),
            run_ref=self._run_ref(campaign, slot),
            provenance=dict(manifest["provenance"]),
            lease_token=token,
        )

    def _auto_receipt(
        self,
        campaign: sqlite3.Row,
        slot: sqlite3.Row,
        *,
        state: str,
        reason: str,
        usage: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        if state not in _FAILURE_RUN_STATES | {"cancelled", "budget_exhausted"}:
            raise PracticeQueueError("RECEIPT_REJECTED")
        if not _REASON_CODE.fullmatch(reason):
            raise PracticeQueueError("INVALID_ARGUMENT")
        manifest = self._manifest(campaign)
        run_ref = self._run_ref(campaign, slot)
        receipt: dict[str, Any] = {
            "schema": PRACTICE_RUN_RECEIPT_SCHEMA,
            "runId": slot["run_id"],
            "scenarioId": slot["scenario_id"],
            "state": state,
            "terminalReason": reason,
            "provenance": manifest["provenance"],
            "budgets": run_ref["budgets"],
            "usage": dict(usage or self._usage(slot)),
            "workflowEvents": [],
            "artifacts": [],
            "evidence": {
                "semanticDiff": {"status": "not_run", "receiptSha256": None},
                "openSafety": {"status": "not_run", "receiptSha256": None},
                "domainVerdicts": [],
                "render": {
                    "status": "unverified",
                    "receiptSha256": None,
                    "renderChecked": False,
                    "provenance": "none",
                },
                "visual": {
                    "status": "unverified",
                    "receiptSha256": None,
                    "allPagesChecked": False,
                    "visualComplete": False,
                },
                "unresolvedReasonCodes": [reason],
            },
            "privacy": {
                "localOnly": True,
                "syntheticInputsOnly": True,
                "highConfidencePiiCount": 0,
                "privateCoordinatesExposed": False,
                "evaluatorDataExposed": False,
            },
        }
        receipt["receiptSha256"] = _sha256(receipt)
        try:
            return validate_run_receipt(receipt)
        except ValueError as exc:
            raise PracticeQueueError("QUEUE_STORAGE_FAILED") from exc

    def _insert_terminal(
        self,
        connection: sqlite3.Connection,
        campaign: sqlite3.Row,
        slot: sqlite3.Row,
        receipt: Mapping[str, Any],
        observed: datetime,
    ) -> dict[str, Any]:
        validated = dict(receipt)
        receipt_hash = str(validated["receiptSha256"])
        existing = connection.execute(
            "SELECT receipt_sha256,receipt_json FROM practice_terminal_receipts "
            "WHERE campaign_id=? AND slot=?",
            (slot["campaign_id"], slot["slot"]),
        ).fetchone()
        if existing is not None:
            if existing["receipt_sha256"] == receipt_hash and existing[
                "receipt_json"
            ] == _canonical(validated):
                return validated
            raise PracticeQueueError("TERMINAL_CONFLICT")
        timestamp = _iso(observed)
        connection.execute(
            "INSERT INTO practice_terminal_receipts "
            "(campaign_id,slot,run_id,receipt_sha256,receipt_json,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                slot["campaign_id"],
                slot["slot"],
                slot["run_id"],
                receipt_hash,
                _canonical(validated),
                timestamp,
            ),
        )
        usage = validated["usage"]
        connection.execute(
            "UPDATE practice_run_slots SET state=?,terminal_receipt_sha256=?,"
            "lease_worker=NULL,lease_token_sha256=NULL,leased_at=NULL,lease_expires_at=NULL,"
            "usage_tool_calls=?,usage_repair_rounds=?,usage_elapsed_seconds=?,"
            "usage_cost_microunits=?,usage_artifact_bytes=?,updated_at=? "
            "WHERE campaign_id=? AND slot=? AND terminal_receipt_sha256 IS NULL",
            (
                validated["state"],
                receipt_hash,
                usage["toolCalls"],
                usage["repairRounds"],
                usage["elapsedSeconds"],
                usage["costMicrounits"],
                usage["artifactBytes"],
                timestamp,
                slot["campaign_id"],
                slot["slot"],
            ),
        )
        connection.execute(
            "UPDATE practice_campaigns SET updated_at=? WHERE campaign_id=?",
            (timestamp, campaign["campaign_id"]),
        )
        return validated

    def _terminalize_auto(
        self,
        connection: sqlite3.Connection,
        campaign: sqlite3.Row,
        slot: sqlite3.Row,
        *,
        state: str,
        reason: str,
        observed: datetime,
        usage: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        receipt = self._auto_receipt(
            campaign, slot, state=state, reason=reason, usage=usage
        )
        return self._insert_terminal(connection, campaign, slot, receipt, observed)

    def _budget_snapshot(
        self,
        connection: sqlite3.Connection,
        campaign: sqlite3.Row,
        slot: sqlite3.Row,
    ) -> dict[str, Any]:
        manifest = self._manifest(campaign)
        run_ref = self._run_ref(campaign, slot)
        usage = self._usage(slot)
        campaign_usage = self._campaign_usage(connection, str(campaign["campaign_id"]))
        run_remaining = {
            field: int(run_ref["budgets"][field]) - int(usage[field])
            for field in RUN_BUDGET_FIELDS
        }
        campaign_remaining = {
            field: int(manifest["budgets"][field]) - campaign_usage[field]
            for field in _CAMPAIGN_USAGE_FIELDS
        }
        if any(value < 0 for value in run_remaining.values()) or any(
            value < 0 for value in campaign_remaining.values()
        ):
            raise PracticeQueueError("QUEUE_STORAGE_FAILED")
        effective_remaining = dict(run_remaining)
        for budget_field in _CAMPAIGN_USAGE_FIELDS:
            effective_remaining[budget_field] = min(
                run_remaining[budget_field], campaign_remaining[budget_field]
            )
        return {
            "manifest": manifest,
            "runRef": run_ref,
            "usage": usage,
            "campaignUsage": campaign_usage,
            "runRemaining": run_remaining,
            "campaignRemaining": campaign_remaining,
            "effectiveRemaining": effective_remaining,
        }

    def _recover_expired(
        self,
        connection: sqlite3.Connection,
        observed: datetime,
        campaign_id: str | None = None,
    ) -> int:
        parameters: list[Any] = [_iso(observed)]
        clause = ""
        if campaign_id is not None:
            clause = " AND campaign_id=?"
            parameters.append(campaign_id)
        rows = connection.execute(
            "SELECT * FROM practice_run_slots "
            "WHERE state IN ('running','cancelling') AND lease_expires_at<=?" + clause,
            parameters,
        ).fetchall()
        recovered = 0
        for slot in rows:
            campaign = self._campaign_row(connection, str(slot["campaign_id"]))
            run_ref = self._run_ref(campaign, slot)
            usage = self._usage(slot)
            try:
                leased_at = _datetime(str(slot["leased_at"]))
                expires_at = _datetime(str(slot["lease_expires_at"]))
                elapsed = max(0, math.ceil((expires_at - leased_at).total_seconds()))
            except (TypeError, ValueError):
                self._terminalize_auto(
                    connection,
                    campaign,
                    slot,
                    state="incomplete",
                    reason="RECOVERY_INCOMPLETE",
                    observed=observed,
                )
                recovered += 1
                continue
            usage["elapsedSeconds"] = min(
                usage["elapsedSeconds"] + elapsed,
                int(run_ref["budgets"]["elapsedSeconds"]),
            )
            if bool(campaign["cancel_requested"]) or slot["state"] == "cancelling":
                self._terminalize_auto(
                    connection,
                    campaign,
                    slot,
                    state="cancelled",
                    reason="CAMPAIGN_CANCELLED",
                    observed=observed,
                    usage=usage,
                )
            else:
                # Keep the expired identity until another worker leases this slot.
                # This gives the original worker one durable, named reconciliation
                # chance even when the final execution attempt used the budget.
                connection.execute(
                    "UPDATE practice_run_slots SET state='queued',"
                    "usage_elapsed_seconds=?,updated_at=? WHERE campaign_id=? AND slot=?",
                    (
                        usage["elapsedSeconds"],
                        _iso(observed),
                        slot["campaign_id"],
                        slot["slot"],
                    ),
                )
            recovered += 1
        return recovered

    def claim(
        self,
        worker_id: str,
        *,
        campaign_id: str | None = None,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> PracticeRunLease | None:
        if not _WORKER_ID.fullmatch(worker_id) or not isinstance(lease_seconds, int):
            raise PracticeQueueError("INVALID_ARGUMENT")
        if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 3_600:
            raise PracticeQueueError("INVALID_ARGUMENT")
        observed = _aware(now)
        with self._transaction() as connection:
            if campaign_id is not None:
                self._campaign_row(connection, campaign_id)
            self._recover_expired(connection, observed, campaign_id)
            parameters: list[Any] = []
            campaign_clause = ""
            if campaign_id is not None:
                campaign_clause = " AND s.campaign_id=?"
                parameters.append(campaign_id)
            while True:
                slot = connection.execute(
                    "SELECT s.* FROM practice_run_slots s "
                    "JOIN practice_campaigns c ON c.campaign_id=s.campaign_id "
                    "WHERE s.state='queued' AND c.cancel_requested=0 "
                    "AND NOT EXISTS (SELECT 1 FROM practice_run_slots active "
                    "WHERE active.campaign_id=s.campaign_id "
                    "AND active.state IN ('running','cancelling'))"
                    + campaign_clause
                    + " ORDER BY c.created_at,s.slot LIMIT 1",
                    parameters,
                ).fetchone()
                if slot is None:
                    return None
                campaign = self._campaign_row(connection, str(slot["campaign_id"]))
                lease = self._claim_queued_slot(
                    connection,
                    campaign,
                    slot,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                    observed=observed,
                )
                if lease is None:
                    continue
                return lease

    def _claim_queued_slot(
        self,
        connection: sqlite3.Connection,
        campaign: sqlite3.Row,
        slot: sqlite3.Row,
        *,
        worker_id: str,
        lease_seconds: int,
        observed: datetime,
    ) -> PracticeRunLease | None:
        self._run_ref(campaign, slot)
        snapshot = self._budget_snapshot(connection, campaign, slot)
        effective = snapshot["effectiveRemaining"]
        exhausted_dispatch = any(
            effective[field] <= 0 for field in _CAMPAIGN_USAGE_FIELDS
        )
        recovery_only = int(slot["attempt"]) > 0 and (
            effective["attempts"] <= 0 or exhausted_dispatch
        )
        if exhausted_dispatch and not recovery_only:
            campaign_exhausted = any(
                snapshot["campaignRemaining"][field] <= 0
                for field in _CAMPAIGN_USAGE_FIELDS
            )
            self._terminalize_auto(
                connection,
                campaign,
                slot,
                state="budget_exhausted",
                reason=(
                    "CAMPAIGN_BUDGET_EXHAUSTED"
                    if campaign_exhausted
                    else "RUN_BUDGET_EXHAUSTED"
                ),
                observed=observed,
            )
            return None
        duration = (
            lease_seconds
            if recovery_only
            else min(lease_seconds, effective["elapsedSeconds"])
        )
        token = self._lease_token()
        attempt = int(slot["attempt"]) + (0 if recovery_only else 1)
        expires = observed + timedelta(seconds=duration)
        updated = connection.execute(
            "UPDATE practice_run_slots SET state='running',attempt=?,lease_worker=?,"
            "lease_token_sha256=?,leased_at=?,lease_expires_at=?,recovery_only=?,"
            "updated_at=? WHERE campaign_id=? AND slot=? AND state='queued'",
            (
                attempt,
                worker_id,
                self._lease_hash(token),
                _iso(observed),
                _iso(expires),
                int(recovery_only),
                _iso(observed),
                slot["campaign_id"],
                slot["slot"],
            ),
        )
        if updated.rowcount != 1:
            return None
        leased = connection.execute(
            "SELECT * FROM practice_run_slots WHERE campaign_id=? AND slot=?",
            (slot["campaign_id"], slot["slot"]),
        ).fetchone()
        return self._make_lease(campaign, leased, worker_id=worker_id, token=token)

    def claim_run(
        self,
        run_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> PracticeRunLease | None:
        """Claim exactly one queued run, including an expired recovery claim."""

        if not _WORKER_ID.fullmatch(worker_id) or not isinstance(lease_seconds, int):
            raise PracticeQueueError("INVALID_ARGUMENT")
        if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 3_600:
            raise PracticeQueueError("INVALID_ARGUMENT")
        observed = _aware(now)
        with self._transaction() as connection:
            slot = self._slot_by_run(connection, run_id)
            campaign_id = str(slot["campaign_id"])
            self._recover_expired(connection, observed, campaign_id)
            slot = self._slot_by_run(connection, run_id)
            campaign = self._campaign_row(connection, campaign_id)
            if slot["state"] != "queued" or bool(campaign["cancel_requested"]):
                return None
            active = connection.execute(
                "SELECT 1 FROM practice_run_slots WHERE campaign_id=? "
                "AND state IN ('running','cancelling') LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if active is not None:
                return None
            return self._claim_queued_slot(
                connection,
                campaign,
                slot,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                observed=observed,
            )

    def _slot_by_run(self, connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM practice_run_slots WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise PracticeQueueError("RUN_NOT_FOUND")
        campaign = self._campaign_row(connection, str(row["campaign_id"]))
        self._run_ref(campaign, row)
        return row

    def _owned_slot(
        self,
        connection: sqlite3.Connection,
        lease: PracticeRunLease,
        observed: datetime,
    ) -> sqlite3.Row:
        slot = self._slot_by_run(connection, lease.run_id)
        if (
            slot["campaign_id"] != lease.campaign_id
            or int(slot["slot"]) != lease.slot
            or slot["state"] not in {"running", "cancelling"}
            or slot["lease_worker"] != lease.worker_id
            or int(slot["attempt"]) != lease.attempt
            or bool(slot["recovery_only"]) != lease.recovery_only
            or slot["lease_token_sha256"] != self._lease_hash(lease.lease_token)
            or _datetime(str(slot["lease_expires_at"])) <= observed
        ):
            raise PracticeQueueError("LEASE_NOT_OWNED")
        return slot

    def _reconciliation_slot(
        self,
        connection: sqlite3.Connection,
        lease: PracticeRunLease,
    ) -> sqlite3.Row:
        """Authenticate a lease identity without imposing an expiry cutoff."""

        slot = self._slot_by_run(connection, lease.run_id)
        campaign = self._campaign_row(connection, str(slot["campaign_id"]))
        run_ref = self._run_ref(campaign, slot)
        manifest = self._manifest(campaign)
        if (
            slot["campaign_id"] != lease.campaign_id
            or campaign["manifest_sha256"] != lease.manifest_sha256
            or int(slot["slot"]) != lease.slot
            or slot["scenario_id"] != lease.scenario_id
            or slot["state"] not in {"queued", "running", "cancelling"}
            or slot["lease_worker"] != lease.worker_id
            or int(slot["attempt"]) != lease.attempt
            or int(slot["dispatch_generation"]) != lease.dispatch_generation
            or slot["dispatch_idempotency_key"] != lease.dispatch_idempotency_key
            or bool(slot["recovery_only"]) != lease.recovery_only
            or slot["lease_token_sha256"] != self._lease_hash(lease.lease_token)
            or dict(lease.run_ref) != run_ref
            or dict(lease.provenance) != manifest["provenance"]
        ):
            raise PracticeQueueError("LEASE_NOT_OWNED")
        return slot

    def authorize(
        self,
        lease: PracticeRunLease,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Authorize dispatch from immutable budgets and durable aggregate usage."""

        observed = _aware(now)
        terminal_code: str | None = None
        result: dict[str, Any] | None = None
        with self._transaction() as connection:
            slot = self._owned_slot(connection, lease, observed)
            campaign = self._campaign_row(connection, lease.campaign_id)
            snapshot = self._budget_snapshot(connection, campaign, slot)
            if bool(campaign["cancel_requested"]) or slot["state"] == "cancelling":
                self._terminalize_auto(
                    connection,
                    campaign,
                    slot,
                    state="cancelled",
                    reason="CAMPAIGN_CANCELLED",
                    observed=observed,
                )
                terminal_code = "CANCEL_REQUESTED"
            elif not lease.recovery_only and any(
                snapshot["effectiveRemaining"][field] <= 0
                for field in _CAMPAIGN_USAGE_FIELDS
            ):
                campaign_exhausted = any(
                    snapshot["campaignRemaining"][field] <= 0
                    for field in _CAMPAIGN_USAGE_FIELDS
                )
                self._terminalize_auto(
                    connection,
                    campaign,
                    slot,
                    state="budget_exhausted",
                    reason=(
                        "CAMPAIGN_BUDGET_EXHAUSTED"
                        if campaign_exhausted
                        else "RUN_BUDGET_EXHAUSTED"
                    ),
                    observed=observed,
                    usage=snapshot["usage"],
                )
                terminal_code = "BUDGET_EXHAUSTED"
            else:
                result = {
                    "schema": _QUEUE_AUTHORIZATION_SCHEMA,
                    "campaignId": lease.campaign_id,
                    "runId": lease.run_id,
                    "authorized": True,
                    "recoveryOnly": lease.recovery_only,
                    "mutationAllowed": not lease.recovery_only,
                    "runRemaining": snapshot["runRemaining"],
                    "campaignRemaining": snapshot["campaignRemaining"],
                    "effectiveRemaining": snapshot["effectiveRemaining"],
                    "privateStorageCoordinatesExposed": False,
                }
                assert_receipt_safe(result)
        if terminal_code is not None:
            raise PracticeQueueError(terminal_code)
        assert result is not None
        return result

    def resume_lease(
        self,
        run_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> PracticeRunLease:
        if not _WORKER_ID.fullmatch(worker_id) or not isinstance(lease_seconds, int):
            raise PracticeQueueError("INVALID_ARGUMENT")
        if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 3_600:
            raise PracticeQueueError("INVALID_ARGUMENT")
        observed = _aware(now)
        exhausted = False
        stale = False
        result: PracticeRunLease | None = None
        with self._transaction() as connection:
            slot = self._slot_by_run(connection, run_id)
            campaign = self._campaign_row(connection, str(slot["campaign_id"]))
            if bool(campaign["cancel_requested"]) or slot["state"] == "cancelling":
                raise PracticeQueueError("CANCEL_REQUESTED")
            if slot["state"] != "running" or slot["lease_worker"] != worker_id:
                raise PracticeQueueError("LEASE_NOT_OWNED")
            expires = _datetime(str(slot["lease_expires_at"]))
            if expires <= observed:
                self._recover_expired(connection, observed, str(slot["campaign_id"]))
                stale = True
            else:
                leased_at = _datetime(str(slot["leased_at"]))
                usage = self._usage(slot)
                usage["elapsedSeconds"] += max(
                    0, math.ceil((observed - leased_at).total_seconds())
                )
                run_ref = self._run_ref(campaign, slot)
                usage["elapsedSeconds"] = min(
                    usage["elapsedSeconds"], int(run_ref["budgets"]["elapsedSeconds"])
                )
                remaining = (
                    int(run_ref["budgets"]["elapsedSeconds"]) - usage["elapsedSeconds"]
                )
                recovery_only = bool(slot["recovery_only"])
                if remaining <= 0 and not recovery_only:
                    self._terminalize_auto(
                        connection,
                        campaign,
                        slot,
                        state="budget_exhausted",
                        reason="TIME_BUDGET_EXHAUSTED",
                        observed=observed,
                        usage={
                            **usage,
                            "elapsedSeconds": int(run_ref["budgets"]["elapsedSeconds"]),
                        },
                    )
                    exhausted = True
                else:
                    token = self._lease_token()
                    new_expiry = observed + timedelta(
                        seconds=(
                            lease_seconds
                            if recovery_only
                            else min(lease_seconds, remaining)
                        )
                    )
                    connection.execute(
                        "UPDATE practice_run_slots SET lease_token_sha256=?,leased_at=?,"
                        "lease_expires_at=?,usage_elapsed_seconds=?,updated_at=? "
                        "WHERE campaign_id=? AND slot=?",
                        (
                            self._lease_hash(token),
                            _iso(observed),
                            _iso(new_expiry),
                            usage["elapsedSeconds"],
                            _iso(observed),
                            slot["campaign_id"],
                            slot["slot"],
                        ),
                    )
                    resumed = connection.execute(
                        "SELECT * FROM practice_run_slots WHERE campaign_id=? AND slot=?",
                        (slot["campaign_id"], slot["slot"]),
                    ).fetchone()
                    result = self._make_lease(
                        campaign, resumed, worker_id=worker_id, token=token
                    )
        if stale:
            raise PracticeQueueError("LEASE_NOT_OWNED")
        if exhausted:
            raise PracticeQueueError("BUDGET_EXHAUSTED")
        assert result is not None
        return result

    def _campaign_usage(
        self, connection: sqlite3.Connection, campaign_id: str
    ) -> dict[str, int]:
        campaign = self._campaign_row(connection, campaign_id)
        rows = connection.execute(
            "SELECT * FROM practice_run_slots WHERE campaign_id=?",
            (campaign_id,),
        ).fetchall()
        usage = {field: 0 for field in _CAMPAIGN_USAGE_FIELDS}
        for row in rows:
            self._run_ref(campaign, row)
            row_usage = self._usage(row)
            for budget_field in _CAMPAIGN_USAGE_FIELDS:
                usage[budget_field] += row_usage[budget_field]
        return usage

    def _campaign_budget_exceeded(
        self,
        connection: sqlite3.Connection,
        campaign: sqlite3.Row,
        slot: sqlite3.Row,
        *,
        current_usage: Mapping[str, int],
        projected_usage: Mapping[str, int],
    ) -> bool:
        """Rebind the slot to its immutable manifest and check aggregate use."""

        manifest = self._manifest(campaign)
        self._run_ref(campaign, slot)
        campaign_usage = self._campaign_usage(connection, str(campaign["campaign_id"]))
        return any(
            campaign_usage[field]
            - int(current_usage[field])
            + int(projected_usage[field])
            > int(manifest["budgets"][field])
            for field in _CAMPAIGN_USAGE_FIELDS
        )

    def account(
        self,
        lease: PracticeRunLease,
        *,
        tool_calls: int = 0,
        repair_rounds: int = 0,
        elapsed_seconds: int = 0,
        cost_microunits: int = 0,
        artifact_bytes: int = 0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        deltas = {
            "toolCalls": _require_nonnegative(tool_calls),
            "repairRounds": _require_nonnegative(repair_rounds),
            "elapsedSeconds": _require_nonnegative(elapsed_seconds),
            "costMicrounits": _require_nonnegative(cost_microunits),
            "artifactBytes": _require_nonnegative(artifact_bytes),
        }
        observed = _aware(now)
        terminal_receipt: dict[str, Any] | None = None
        with self._transaction() as connection:
            slot = self._owned_slot(connection, lease, observed)
            campaign = self._campaign_row(connection, lease.campaign_id)
            snapshot = self._budget_snapshot(connection, campaign, slot)
            usage = snapshot["usage"]
            projected = dict(usage)
            for field, amount in deltas.items():
                projected[field] += amount
            ceilings = {
                field: usage[field] + snapshot["runRemaining"][field]
                for field in RUN_BUDGET_FIELDS
            }
            for field in _CAMPAIGN_USAGE_FIELDS:
                ceilings[field] = usage[field] + snapshot["effectiveRemaining"][field]
            if lease.recovery_only:
                for field in deltas:
                    ceilings[field] = usage[field]
            clamped = dict(projected)
            for field in deltas:
                clamped[field] = min(projected[field], ceilings[field])
            run_exceeded = (
                lease.recovery_only
                and any(deltas.values())
                or any(
                    projected[field] > usage[field] + snapshot["runRemaining"][field]
                    for field in deltas
                )
            )
            campaign_exceeded = any(
                projected[field] > usage[field] + snapshot["campaignRemaining"][field]
                for field in _CAMPAIGN_USAGE_FIELDS
            )
            if run_exceeded or campaign_exceeded:
                terminal_receipt = self._terminalize_auto(
                    connection,
                    campaign,
                    slot,
                    state="budget_exhausted",
                    reason=(
                        "CAMPAIGN_BUDGET_EXHAUSTED"
                        if campaign_exceeded
                        else "RUN_BUDGET_EXHAUSTED"
                    ),
                    observed=observed,
                    usage=clamped,
                )
            elif bool(campaign["cancel_requested"]) or slot["state"] == "cancelling":
                terminal_receipt = self._terminalize_auto(
                    connection,
                    campaign,
                    slot,
                    state="cancelled",
                    reason="CAMPAIGN_CANCELLED",
                    observed=observed,
                    usage=projected,
                )
            else:
                connection.execute(
                    "UPDATE practice_run_slots SET usage_tool_calls=?,"
                    "usage_repair_rounds=?,usage_elapsed_seconds=?,usage_cost_microunits=?,"
                    "usage_artifact_bytes=?,"
                    "leased_at=CASE WHEN ?>0 THEN ? ELSE leased_at END,updated_at=? "
                    "WHERE campaign_id=? AND slot=?",
                    (
                        clamped["toolCalls"],
                        clamped["repairRounds"],
                        clamped["elapsedSeconds"],
                        clamped["costMicrounits"],
                        clamped["artifactBytes"],
                        deltas["elapsedSeconds"],
                        _iso(observed),
                        _iso(observed),
                        lease.campaign_id,
                        lease.slot,
                    ),
                )
                usage = clamped
        return {
            "schema": _QUEUE_USAGE_SCHEMA,
            "campaignId": lease.campaign_id,
            "runId": lease.run_id,
            "state": terminal_receipt["state"] if terminal_receipt else "running",
            "usage": terminal_receipt["usage"] if terminal_receipt else usage,
            "terminalReceipt": terminal_receipt,
            "privateStorageCoordinatesExposed": False,
        }

    def terminalize(
        self,
        lease: PracticeRunLease,
        receipt: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        try:
            validated = validate_run_receipt(receipt)
            assert_receipt_safe(validated)
        except ValueError as exc:
            raise PracticeQueueError("RECEIPT_REJECTED") from exc
        observed = _aware(now)
        with self._transaction() as connection:
            slot = self._slot_by_run(connection, lease.run_id)
            campaign = self._campaign_row(connection, str(slot["campaign_id"]))
            self._run_ref(campaign, slot)
            existing = connection.execute(
                "SELECT receipt_sha256,receipt_json FROM practice_terminal_receipts "
                "WHERE run_id=?",
                (lease.run_id,),
            ).fetchone()
            if existing is not None:
                if existing["receipt_sha256"] == validated[
                    "receiptSha256"
                ] and existing["receipt_json"] == _canonical(validated):
                    return validated
                raise PracticeQueueError("TERMINAL_CONFLICT")
            slot = self._reconciliation_slot(connection, lease)
            run_ref = self._run_ref(campaign, slot)
            manifest = self._manifest(campaign)
            if (
                validated["runId"] != slot["run_id"]
                or validated["scenarioId"] != slot["scenario_id"]
                or validated["budgets"] != run_ref["budgets"]
                or validated["provenance"] != manifest["provenance"]
                or validated["usage"]["attempts"] != int(slot["attempt"])
            ):
                raise PracticeQueueError("RECEIPT_REJECTED")
            current_usage = self._usage(slot)
            if any(
                validated["usage"][field] < current_usage[field]
                for field in RUN_BUDGET_FIELDS
            ):
                raise PracticeQueueError("RECEIPT_REJECTED")
            if bool(campaign["cancel_requested"]) and validated["state"] != "cancelled":
                raise PracticeQueueError("CANCEL_REQUESTED")
            campaign_exceeded = self._campaign_budget_exceeded(
                connection,
                campaign,
                slot,
                current_usage=current_usage,
                projected_usage=validated["usage"],
            )
            if campaign_exceeded:
                return self._terminalize_auto(
                    connection,
                    campaign,
                    slot,
                    state="budget_exhausted",
                    reason="CAMPAIGN_BUDGET_EXHAUSTED",
                    observed=observed,
                    usage=current_usage,
                )
            return self._insert_terminal(
                connection, campaign, slot, validated, observed
            )

    def fail(
        self,
        lease: PracticeRunLease,
        reason: str,
        *,
        state: str = "failed",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Immediately close an owned lease with one fail-closed receipt."""

        if state not in _FAILURE_RUN_STATES or not _REASON_CODE.fullmatch(reason):
            raise PracticeQueueError("INVALID_ARGUMENT")
        observed = _aware(now)
        with self._transaction() as connection:
            slot = self._slot_by_run(connection, lease.run_id)
            campaign = self._campaign_row(connection, str(slot["campaign_id"]))
            run_ref = self._run_ref(campaign, slot)
            manifest = self._manifest(campaign)
            if (
                lease.campaign_id != slot["campaign_id"]
                or lease.manifest_sha256 != campaign["manifest_sha256"]
                or lease.slot != int(slot["slot"])
                or lease.scenario_id != slot["scenario_id"]
                or lease.attempt != int(slot["attempt"])
                or lease.dispatch_generation != int(slot["dispatch_generation"])
                or lease.dispatch_idempotency_key != slot["dispatch_idempotency_key"]
                or dict(lease.run_ref) != run_ref
                or dict(lease.provenance) != manifest["provenance"]
            ):
                raise PracticeQueueError("LEASE_NOT_OWNED")
            requested = self._auto_receipt(
                campaign,
                slot,
                state=state,
                reason=reason,
            )
            existing = connection.execute(
                "SELECT receipt_sha256,receipt_json FROM practice_terminal_receipts "
                "WHERE run_id=?",
                (lease.run_id,),
            ).fetchone()
            if existing is not None:
                if existing["receipt_sha256"] == requested[
                    "receiptSha256"
                ] and existing["receipt_json"] == _canonical(requested):
                    return requested
                raise PracticeQueueError("TERMINAL_CONFLICT")
            # A worker may report a named terminal outcome after its lease expiry.
            # The expired identity is retained while queued and is overwritten by
            # any subsequent lease, so this cannot close a run that was re-leased.
            if (
                slot["state"] not in {"queued", "running", "cancelling"}
                or slot["lease_worker"] != lease.worker_id
                or slot["lease_token_sha256"] != self._lease_hash(lease.lease_token)
                or bool(slot["recovery_only"]) != lease.recovery_only
            ):
                raise PracticeQueueError("LEASE_NOT_OWNED")
            if bool(campaign["cancel_requested"]) or slot["state"] == "cancelling":
                return self._terminalize_auto(
                    connection,
                    campaign,
                    slot,
                    state="cancelled",
                    reason="CAMPAIGN_CANCELLED",
                    observed=observed,
                )
            return self._insert_terminal(
                connection, campaign, slot, requested, observed
            )

    def cancel(
        self, campaign_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        observed = _aware(now)
        with self._transaction() as connection:
            campaign = self._campaign_row(connection, campaign_id)
            connection.execute(
                "UPDATE practice_campaigns SET cancel_requested=1,updated_at=? "
                "WHERE campaign_id=?",
                (_iso(observed), campaign_id),
            )
            queued = connection.execute(
                "SELECT * FROM practice_run_slots WHERE campaign_id=? AND state='queued'",
                (campaign_id,),
            ).fetchall()
            for slot in queued:
                self._terminalize_auto(
                    connection,
                    campaign,
                    slot,
                    state="cancelled",
                    reason="CAMPAIGN_CANCELLED",
                    observed=observed,
                )
            connection.execute(
                "UPDATE practice_run_slots SET state='cancelling',updated_at=? "
                "WHERE campaign_id=? AND state='running'",
                (_iso(observed), campaign_id),
            )
            self._recover_expired(connection, observed, campaign_id)
        return self._status(campaign_id)

    def close_incomplete(
        self,
        campaign_id: str,
        *,
        reason: str = "CAMPAIGN_INCOMPLETE",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not _REASON_CODE.fullmatch(reason):
            raise PracticeQueueError("INVALID_ARGUMENT")
        observed = _aware(now)
        with self._transaction() as connection:
            campaign = self._campaign_row(connection, campaign_id)
            active = connection.execute(
                "SELECT * FROM practice_run_slots WHERE campaign_id=? "
                "AND state IN ('queued','running','cancelling') ORDER BY slot",
                (campaign_id,),
            ).fetchall()
            for slot in active:
                self._terminalize_auto(
                    connection,
                    campaign,
                    slot,
                    state="incomplete",
                    reason=reason,
                    observed=observed,
                )
        return self._status(campaign_id)

    def recover(
        self,
        *,
        campaign_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed = _aware(now)
        with self._transaction() as connection:
            if campaign_id is not None:
                self._campaign_row(connection, campaign_id)
            recovered = self._recover_expired(connection, observed, campaign_id)
            if campaign_id is None:
                campaign_ids = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT campaign_id FROM practice_campaigns ORDER BY campaign_id"
                    )
                ]
            else:
                campaign_ids = [campaign_id]
        return {
            "schema": _QUEUE_RECOVERY_SCHEMA,
            "recoveredSlots": recovered,
            "campaignIds": campaign_ids,
            "privateStorageCoordinatesExposed": False,
        }

    def resume(
        self, campaign_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        observed = _aware(now)
        with self._transaction() as connection:
            campaign = self._campaign_row(connection, campaign_id)
            self._recover_expired(connection, observed, campaign_id)
            if bool(campaign["cancel_requested"]):
                # Cancellation is terminal intent and is never silently undone.
                pass
        return self._status(campaign_id)

    def _status(
        self, campaign_id: str, *, idempotent_replay: bool = False
    ) -> dict[str, Any]:
        with self._connect() as connection:
            campaign = self._campaign_row(connection, campaign_id)
            manifest = self._manifest(campaign)
            rows = connection.execute(
                "SELECT * FROM practice_run_slots WHERE campaign_id=? ORDER BY slot",
                (campaign_id,),
            ).fetchall()
            for row in rows:
                self._run_ref(campaign, row)
            terminal_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM practice_terminal_receipts WHERE campaign_id=?",
                    (campaign_id,),
                ).fetchone()[0]
            )
        counts = {state: 0 for state in sorted(_RUN_STATES)}
        for row in rows:
            counts[str(row["state"])] += 1
        incomplete_slots = [
            int(row["slot"])
            for row in rows
            if str(row["state"]) not in TERMINAL_RUN_STATES
        ]
        if terminal_count == len(rows):
            states = {str(row["state"]) for row in rows}
            successful_outcomes = {"completed", "needs_review", "refused", "unverified"}
            if "budget_exhausted" in states:
                state = "budget_exhausted"
            elif "incomplete" in states:
                state = "incomplete"
            elif bool(campaign["cancel_requested"]) or "cancelled" in states:
                state = "cancelled"
            elif states <= successful_outcomes:
                state = "completed"
            else:
                state = "failed"
        elif bool(campaign["cancel_requested"]):
            state = "cancelling"
        elif counts["running"] or counts["cancelling"]:
            state = "running"
        else:
            state = "queued"
        if state not in CAMPAIGN_STATES:
            raise PracticeQueueError("QUEUE_STORAGE_FAILED")
        result = {
            "schema": _QUEUE_STATUS_SCHEMA,
            "campaignId": campaign_id,
            "manifestSha256": campaign["manifest_sha256"],
            "state": state,
            "expectedRunCount": manifest["expectedRunCount"],
            "counts": counts,
            "terminalReceiptCount": terminal_count,
            "incompleteSlots": incomplete_slots,
            "cancelRequested": bool(campaign["cancel_requested"]),
            "idempotentReplay": idempotent_replay,
            "privateStorageCoordinatesExposed": False,
        }
        assert_receipt_safe(result)
        return result

    def status(self, campaign_id: str) -> dict[str, Any]:
        return self._status(campaign_id)

    def receipts(self, campaign_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            campaign = self._campaign_row(connection, campaign_id)
            slots = connection.execute(
                "SELECT * FROM practice_run_slots WHERE campaign_id=? ORDER BY slot",
                (campaign_id,),
            ).fetchall()
            for slot in slots:
                self._run_ref(campaign, slot)
            rows = connection.execute(
                "SELECT receipt_json FROM practice_terminal_receipts "
                "WHERE campaign_id=? ORDER BY slot",
                (campaign_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                result.append(validate_run_receipt(json.loads(row["receipt_json"])))
            except (ValueError, json.JSONDecodeError) as exc:
                raise PracticeQueueError("QUEUE_STORAGE_FAILED") from exc
        return result

    def terminal_cleanup_candidates(
        self, limit: int = 64
    ) -> tuple[dict[str, str], ...]:
        """Return the next redacted batch of unacknowledged terminal sandboxes."""

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1024
        ):
            raise PracticeQueueError("INVALID_ARGUMENT")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT s.* FROM practice_run_slots s "
                "JOIN practice_terminal_receipts r "
                "ON r.campaign_id=s.campaign_id AND r.slot=s.slot "
                "WHERE s.state NOT IN ('queued','running','cancelling') "
                "AND s.sandbox_cleanup_ack=0 "
                "ORDER BY r.created_at,s.campaign_id,s.slot LIMIT ?",
                (limit,),
            ).fetchall()
            result: list[dict[str, str]] = []
            for slot in rows:
                campaign = self._campaign_row(connection, str(slot["campaign_id"]))
                run_ref = self._run_ref(campaign, slot)
                result.append(
                    {
                        "runId": str(slot["run_id"]),
                        "startArtifactSha256": str(run_ref["startArtifactSha256"]),
                    }
                )
        return tuple(result)

    def ack_terminal_cleanup(
        self,
        run_id: str,
        start_artifact_sha256: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Durably acknowledge cleanup of one content-bound terminal sandbox."""

        if (
            not isinstance(run_id, str)
            or not _RUN_ID.fullmatch(run_id)
            or not isinstance(start_artifact_sha256, str)
            or not _SHA256.fullmatch(start_artifact_sha256)
        ):
            raise PracticeQueueError("INVALID_ARGUMENT")
        observed = _aware(now)
        replay = False
        with self._transaction() as connection:
            slot = self._slot_by_run(connection, run_id)
            campaign = self._campaign_row(connection, str(slot["campaign_id"]))
            run_ref = self._run_ref(campaign, slot)
            if (
                run_ref["startArtifactSha256"] != start_artifact_sha256
                or slot["state"] not in TERMINAL_RUN_STATES
                or slot["terminal_receipt_sha256"] is None
            ):
                raise PracticeQueueError("INVALID_ARGUMENT")
            replay = bool(slot["sandbox_cleanup_ack"])
            if not replay:
                connection.execute(
                    "UPDATE practice_run_slots SET sandbox_cleanup_ack=1,updated_at=? "
                    "WHERE campaign_id=? AND slot=? AND sandbox_cleanup_ack=0",
                    (_iso(observed), slot["campaign_id"], slot["slot"]),
                )
        result = {
            "schema": _QUEUE_CLEANUP_ACK_SCHEMA,
            "runId": run_id,
            "startArtifactSha256": start_artifact_sha256,
            "acknowledged": True,
            "idempotentReplay": replay,
            "privateStorageCoordinatesExposed": False,
        }
        assert_receipt_safe(result)
        return result


__all__ = [
    "PracticeCampaignQueue",
    "PracticeQueueError",
    "PracticeRunLease",
]
