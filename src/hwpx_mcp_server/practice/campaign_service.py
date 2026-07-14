# SPDX-License-Identifier: Apache-2.0
"""Opaque high-level campaign API over queue, sandbox, and WorkflowService.

The MCP boundary accepts only campaign/run IDs and closed control values.  The
content-addressed manifest and private task material are resolved through injected
authenticated callables.  Queue/sandbox implementations remain replaceable so
durability and selection can evolve without widening the public API.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from hwpx.practice import (
    assert_receipt_safe,
    validate_campaign_manifest,
    validate_run_receipt,
)

from .dispatch import (
    PracticeDispatchError,
    PracticeWorkflowDispatcher,
    ResolvedPracticeTask,
)


_CAMPAIGN_ID = re.compile(r"^PCMP-[A-F0-9]{20}$")
_RUN_ID = re.compile(r"^PRUN-[A-F0-9]{20}$")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class CampaignQueue(Protocol):
    def enqueue(
        self, manifest: Mapping[str, Any], enqueue_key: str | None = None
    ) -> dict[str, Any]: ...

    def claim(
        self,
        worker_id: str,
        *,
        campaign_id: str | None = None,
        lease_seconds: int = 60,
        now: Any = None,
    ) -> Any | None: ...

    def resume_lease(
        self,
        run_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: Any = None,
    ) -> Any: ...

    def account(self, lease: Any, **usage: int) -> dict[str, Any]: ...

    def terminalize(
        self, lease: Any, receipt: Mapping[str, Any], now: Any = None
    ) -> dict[str, Any]: ...

    def cancel(self, campaign_id: str, now: Any = None) -> dict[str, Any]: ...

    def recover(
        self, now: Any = None, campaign_id: str | None = None
    ) -> dict[str, Any]: ...

    def resume(self, campaign_id: str, now: Any = None) -> dict[str, Any]: ...

    def status(self, campaign_id: str) -> dict[str, Any]: ...

    def receipts(self, campaign_id: str) -> list[dict[str, Any]]: ...


ManifestResolver = Callable[[str], Mapping[str, Any]]
TaskResolver = Callable[[Mapping[str, Any], Any], ResolvedPracticeTask]


class PracticeCampaignError(RuntimeError):
    """Closed path-redacted campaign error safe for an MCP response."""

    _MESSAGES = {
        "CAMPAIGN_ID_INVALID": "campaign_id must be opaque",
        "RUN_ID_INVALID": "run_id must be opaque",
        "IDEMPOTENCY_KEY_INVALID": "idempotency_key must contain 8 to 200 characters",
        "CAMPAIGN_UNAVAILABLE": "private campaign manifest is unavailable",
        "CAMPAIGN_CONTRACT_INVALID": "private campaign manifest failed validation",
        "CAMPAIGN_QUEUE_UNAVAILABLE": "durable campaign queue is unavailable",
        "TASK_BINDING_INVALID": "selected task cannot be resolved safely",
        "CAMPAIGN_EXPORT_INVALID": "redacted campaign export failed validation",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "CAMPAIGN_QUEUE_UNAVAILABLE"
        self.code = code
        super().__init__(f"{code}: {self._MESSAGES[code]}")


def _redact_internal(value: object) -> object:
    """Remove lease/worker internals, then enforce the T024 public boundary."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if any(
                token in normalized
                for token in (
                    "leasetoken",
                    "workerid",
                    "sourcepath",
                    "outputpath",
                    "manifestbody",
                    "taskparameters",
                )
            ):
                continue
            result[str(key)] = _redact_internal(child)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_internal(item) for item in value]
    return value


def _receipt_safety_projection(value: object, *, key: str = "") -> object:
    """Keep structural/privacy validation from treating digest digits as PII.

    ``detect_pii`` intentionally scans every string, but a cryptographic digest
    can coincidentally contain a phone/card-like digit run.  Hash fields carry no
    source text, so validate their shape while scanning a neutral stand-in.
    """

    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    if isinstance(value, str) and (
        normalized.endswith("sha256")
        or normalized.endswith("hash")
        or normalized.endswith("contenthash")
    ):
        digest = value.removeprefix("sha256:")
        if re.fullmatch(r"[a-f0-9]{16}|[a-f0-9]{64}", digest):
            return "f" * len(digest)
    if isinstance(value, Mapping):
        return {
            str(child_key): _receipt_safety_projection(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_receipt_safety_projection(child, key=key) for child in value]
    return value


class PracticeCampaignService:
    """High-level campaign lifecycle with injected private resolution and queue."""

    def __init__(
        self,
        queue: CampaignQueue,
        dispatcher: PracticeWorkflowDispatcher,
        *,
        manifest_resolver: ManifestResolver,
        task_resolver: TaskResolver,
        worker_id: str = "practice-local-worker",
        lease_seconds: int = 60,
    ) -> None:
        if not worker_id or not 10 <= lease_seconds <= 300:
            raise ValueError("campaign worker and lease must be bounded")
        self.queue = queue
        self.dispatcher = dispatcher
        self.manifest_resolver = manifest_resolver
        self.task_resolver = task_resolver
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    @staticmethod
    def _campaign_id(value: str) -> str:
        if not isinstance(value, str) or not _CAMPAIGN_ID.fullmatch(value):
            raise PracticeCampaignError("CAMPAIGN_ID_INVALID")
        return value

    @staticmethod
    def _run_id(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
            raise PracticeCampaignError("RUN_ID_INVALID")
        return value

    def _manifest(self, campaign_id: str) -> dict[str, Any]:
        campaign_id = self._campaign_id(campaign_id)
        try:
            manifest = validate_campaign_manifest(self.manifest_resolver(campaign_id))
        except PracticeCampaignError:
            raise
        except (KeyError, OSError) as exc:
            raise PracticeCampaignError("CAMPAIGN_UNAVAILABLE") from exc
        except (TypeError, ValueError) as exc:
            raise PracticeCampaignError("CAMPAIGN_CONTRACT_INVALID") from exc
        if manifest["campaignId"] != campaign_id:
            raise PracticeCampaignError("CAMPAIGN_CONTRACT_INVALID")
        return manifest

    @staticmethod
    def _public(value: Mapping[str, Any]) -> dict[str, Any]:
        redacted = _redact_internal(value)
        if not isinstance(redacted, Mapping):
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE")
        result = dict(redacted)
        try:
            assert_receipt_safe(_receipt_safety_projection(result))
        except ValueError as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc
        return result

    @staticmethod
    def _run_ref(manifest: Mapping[str, Any], lease: Any) -> dict[str, Any]:
        slot = getattr(lease, "slot", None)
        run_id = getattr(lease, "run_id", None)
        scenario_id = getattr(lease, "scenario_id", None)
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < len(
            manifest["runs"]
        ):
            raise PracticeCampaignError("TASK_BINDING_INVALID")
        run_ref = dict(manifest["runs"][slot])
        if run_ref["runId"] != run_id or run_ref["scenarioId"] != scenario_id:
            raise PracticeCampaignError("TASK_BINDING_INVALID")
        if getattr(lease, "campaign_id", None) != manifest["campaignId"]:
            raise PracticeCampaignError("TASK_BINDING_INVALID")
        if getattr(lease, "manifest_sha256", None) != manifest["manifestSha256"]:
            raise PracticeCampaignError("TASK_BINDING_INVALID")
        return run_ref

    def start(
        self,
        campaign_id: str,
        *,
        idempotency_key: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        manifest = self._manifest(campaign_id)
        if (
            not isinstance(idempotency_key, str)
            or not 8 <= len(idempotency_key) <= 200
        ):
            raise PracticeCampaignError("IDEMPOTENCY_KEY_INVALID")
        self.dispatcher.assert_runtime_provenance(manifest)
        if not confirm:
            return {
                "schema": "hwpx.practice-campaign-start-preview/v1",
                "campaignId": campaign_id,
                "manifestSha256": manifest["manifestSha256"],
                "expectedRunCount": manifest["expectedRunCount"],
                "requiresConfirmation": True,
                "privateStorageCoordinatesExposed": False,
            }
        enqueue_key = f"IDEM-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:20].upper()}"
        try:
            status = self.queue.enqueue(manifest, enqueue_key=enqueue_key)
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc
        return self._public(status)

    def status(self, campaign_id: str) -> dict[str, Any]:
        campaign_id = self._campaign_id(campaign_id)
        try:
            return self._public(self.queue.status(campaign_id))
        except PracticeCampaignError:
            raise
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc

    def continue_campaign(
        self,
        campaign_id: str,
        *,
        run_id: str | None = None,
        max_steps: int = 8,
        approved: bool | None = None,
        decision_receipt_sha256: str | None = None,
    ) -> dict[str, Any]:
        manifest = self._manifest(campaign_id)
        run_id = self._run_id(run_id)
        if (
            isinstance(max_steps, bool)
            or not isinstance(max_steps, int)
            or not 1 <= max_steps <= 16
        ):
            raise PracticeDispatchError("CONTINUE_LIMIT_INVALID")
        if approved is not None and not isinstance(approved, bool):
            raise PracticeCampaignError("TASK_BINDING_INVALID")
        if approved is not None and run_id is None:
            raise PracticeCampaignError("RUN_ID_INVALID")
        try:
            self.queue.recover(campaign_id=campaign_id)
            self.queue.resume(campaign_id)
            lease = (
                self.queue.resume_lease(
                    run_id,
                    self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                if run_id is not None
                else self.queue.claim(
                    self.worker_id,
                    campaign_id=campaign_id,
                    lease_seconds=self.lease_seconds,
                )
            )
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc
        if lease is None:
            return {
                "advanced": False,
                "status": self.status(campaign_id),
                "privateStorageCoordinatesExposed": False,
            }
        run_ref = self._run_ref(manifest, lease)
        try:
            task = self.task_resolver(manifest, lease)
            if not isinstance(task, ResolvedPracticeTask):
                raise TypeError("resolver returned unsupported task")
            outcome = self.dispatcher.advance(
                manifest,
                run_ref,
                task,
                max_steps=max_steps,
                approved=approved,
                decision_receipt_sha256=decision_receipt_sha256,
                run_attempt=getattr(lease, "attempt", 0),
            )
        except PracticeDispatchError:
            raise
        except Exception as exc:
            raise PracticeCampaignError("TASK_BINDING_INVALID") from exc
        try:
            account = self.queue.account(
                lease,
                tool_calls=outcome.usage_delta["toolCalls"],
                repair_rounds=outcome.usage_delta["repairRounds"],
                elapsed_seconds=outcome.usage_delta["elapsedSeconds"],
                cost_microunits=outcome.usage_delta["costMicrounits"],
                artifact_bytes=outcome.usage_delta["artifactBytes"],
            )
            account_terminal = account.get("terminalReceipt")
            queue_usage = account.get("usage")
            bound_receipt = (
                self.dispatcher.bind_accounted_usage(outcome, queue_usage)
                if isinstance(queue_usage, Mapping)
                else outcome.run_receipt
            )
            if isinstance(account_terminal, Mapping):
                terminal = account_terminal
            elif bound_receipt is not None:
                terminal = self.queue.terminalize(lease, bound_receipt)
            else:
                terminal = None
        except PracticeDispatchError:
            raise
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc
        response = {
            "advanced": True,
            "boundary": outcome.boundary,
            "sandbox": outcome.sandbox_receipt,
            "accounting": self._public(account),
            "terminal": self._public(terminal) if terminal is not None else None,
            "status": self.status(campaign_id),
            "privateStorageCoordinatesExposed": False,
        }
        return self._public(response)

    def cancel(self, campaign_id: str) -> dict[str, Any]:
        campaign_id = self._campaign_id(campaign_id)
        try:
            return self._public(self.queue.cancel(campaign_id))
        except PracticeCampaignError:
            raise
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc

    def export(self, campaign_id: str) -> dict[str, Any]:
        manifest = self._manifest(campaign_id)
        try:
            receipts_value: Sequence[Mapping[str, Any]] = self.queue.receipts(campaign_id)
            receipts = [validate_run_receipt(item) for item in receipts_value]
            if any(
                receipt["runId"] not in {item["runId"] for item in manifest["runs"]}
                for receipt in receipts
            ):
                raise ValueError("receipt is outside the campaign")
            result = {
                "schema": "hwpx.practice-campaign-export/v1",
                "campaignId": campaign_id,
                "manifestSha256": manifest["manifestSha256"],
                "expectedRunCount": manifest["expectedRunCount"],
                "terminalReceiptCount": len(receipts),
                "receipts": receipts,
                "status": self.status(campaign_id),
                "privateStorageCoordinatesExposed": False,
            }
            result["exportSha256"] = _digest(result)
            assert_receipt_safe(_receipt_safety_projection(result))
            return result
        except PracticeCampaignError:
            raise
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_EXPORT_INVALID") from exc


__all__ = [
    "CampaignQueue",
    "ManifestResolver",
    "PracticeCampaignError",
    "PracticeCampaignService",
    "TaskResolver",
]
