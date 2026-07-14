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
from dataclasses import replace
from typing import Any, Protocol

from hwpx.practice import (
    assert_receipt_safe,
    validate_campaign_manifest,
    validate_run_receipt,
)

from .dispatch import (
    PracticeDispatchError,
    PracticeDispatchResult,
    PracticeWorkflowDispatcher,
    ResolvedPracticeTask,
)


_CAMPAIGN_ID = re.compile(r"^PCMP-[A-F0-9]{20}$")
_RUN_ID = re.compile(r"^PRUN-[A-F0-9]{20}$")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


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

    def claim_run(
        self,
        run_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: Any = None,
    ) -> Any | None: ...

    def account(self, lease: Any, **usage: int) -> dict[str, Any]: ...

    def authorize(self, lease: Any, now: Any = None) -> dict[str, Any]: ...

    def terminalize(
        self, lease: Any, receipt: Mapping[str, Any], now: Any = None
    ) -> dict[str, Any]: ...

    def fail(
        self,
        lease: Any,
        reason: str,
        *,
        state: str = "failed",
        now: Any = None,
    ) -> dict[str, Any]: ...

    def cancel(self, campaign_id: str, now: Any = None) -> dict[str, Any]: ...

    def recover(
        self, now: Any = None, campaign_id: str | None = None
    ) -> dict[str, Any]: ...

    def resume(self, campaign_id: str, now: Any = None) -> dict[str, Any]: ...

    def status(self, campaign_id: str) -> dict[str, Any]: ...

    def receipts(self, campaign_id: str) -> list[dict[str, Any]]: ...

    def terminal_cleanup_candidates(
        self, *, limit: int = 64
    ) -> tuple[dict[str, str], ...]: ...

    def ack_terminal_cleanup(
        self,
        run_id: str,
        start_artifact_sha256: str,
        *,
        now: Any = None,
    ) -> dict[str, Any]: ...


ManifestResolver = Callable[[str], Mapping[str, Any]]
TaskResolver = Callable[[Mapping[str, Any], Any], ResolvedPracticeTask]
TerminalArtifactHook = Callable[
    [ResolvedPracticeTask, PracticeDispatchResult], Mapping[str, Any] | None
]
TerminalEvaluatorHook = Callable[
    [
        ResolvedPracticeTask,
        PracticeDispatchResult,
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any] | None,
    ],
    Mapping[str, Any],
]


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
        terminal_artifact_hook: TerminalArtifactHook | None = None,
        terminal_evaluator_hook: TerminalEvaluatorHook | None = None,
        worker_id: str = "practice-local-worker",
        lease_seconds: int = 60,
    ) -> None:
        if not worker_id or not 10 <= lease_seconds <= 300:
            raise ValueError("campaign worker and lease must be bounded")
        self.queue = queue
        self.dispatcher = dispatcher
        self.manifest_resolver = manifest_resolver
        self.task_resolver = task_resolver
        self.terminal_artifact_hook = terminal_artifact_hook
        self.terminal_evaluator_hook = terminal_evaluator_hook
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

    def _existing_terminal(
        self, campaign_id: str, run_id: str
    ) -> dict[str, Any] | None:
        try:
            matches = [
                validate_run_receipt(receipt)
                for receipt in self.queue.receipts(campaign_id)
                if receipt.get("runId") == run_id
            ]
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc
        if len(matches) > 1:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE")
        return matches[0] if matches else None

    @staticmethod
    def _failure_state(reason: str) -> str:
        if reason in {"CAPABILITY_SKEW", "TOOL_SPEC_SKEW"}:
            return "provenance_mismatch"
        if reason == "PRIVACY_BOUNDARY_FAILED":
            return "privacy_blocked"
        if reason in {"DOMAIN_VERIFIER_MISSING", "DOMAIN_VERIFIER_FAILED"}:
            return "needs_review"
        if reason in {
            "SOURCE_WRITE_REFUSED",
            "STALE_DOCUMENT_REVISION",
            "SOURCE_CHANGED",
        }:
            return "source_write_refused"
        if reason in {
            "TASK_BINDING_INVALID",
            "CAMPAIGN_CONTRACT_INVALID",
            "UNSUPPORTED_INTENT",
        }:
            return "refused"
        return "failed"

    @staticmethod
    def _authorization(
        value: Mapping[str, Any], lease: Any
    ) -> tuple[dict[str, int], dict[str, int], bool]:
        expected_keys = {
            "schema",
            "campaignId",
            "runId",
            "authorized",
            "recoveryOnly",
            "mutationAllowed",
            "runRemaining",
            "campaignRemaining",
            "effectiveRemaining",
            "privateStorageCoordinatesExposed",
        }
        run_keys = {
            "toolCalls",
            "attempts",
            "repairRounds",
            "elapsedSeconds",
            "costMicrounits",
            "artifactBytes",
        }
        campaign_keys = {
            "toolCalls",
            "elapsedSeconds",
            "costMicrounits",
            "artifactBytes",
        }
        if set(value) != expected_keys or (
            value.get("schema")
            != "hwpx.practice-campaign-queue-authorization/v1"
            or value.get("campaignId") != getattr(lease, "campaign_id", None)
            or value.get("runId") != getattr(lease, "run_id", None)
            or value.get("authorized") is not True
            or value.get("privateStorageCoordinatesExposed") is not False
        ):
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE")
        recovery_only = value.get("recoveryOnly")
        mutation_allowed = value.get("mutationAllowed")
        if (
            not isinstance(recovery_only, bool)
            or recovery_only is not bool(getattr(lease, "recovery_only", False))
            or mutation_allowed is not (not recovery_only)
        ):
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE")
        budgets: dict[str, dict[str, int]] = {}
        for name, keys in (
            ("runRemaining", run_keys),
            ("campaignRemaining", campaign_keys),
            ("effectiveRemaining", run_keys),
        ):
            raw = value.get(name)
            if not isinstance(raw, Mapping) or set(raw) != keys:
                raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE")
            normalized: dict[str, int] = {}
            for key, amount in raw.items():
                if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                    raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE")
                normalized[str(key)] = amount
            budgets[name] = normalized
        effective = budgets["effectiveRemaining"]
        if any(effective[key] > budgets["runRemaining"][key] for key in run_keys):
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE")
        if any(
            effective[key] > budgets["campaignRemaining"][key]
            for key in campaign_keys
        ):
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE")
        return effective, budgets["runRemaining"], recovery_only

    def _cleanup_terminal_candidates(self) -> dict[str, Any] | None:
        try:
            candidates = self.queue.terminal_cleanup_candidates(limit=64)
            if not candidates:
                return None
            if not isinstance(candidates, tuple) or len(candidates) > 64:
                raise TypeError("terminal cleanup candidates must be a tuple")
            cleanup = self.dispatcher.cleanup_terminal_candidates(list(candidates))
            raw_rows = cleanup.get("candidates")
            if not isinstance(raw_rows, list) or len(raw_rows) != len(candidates):
                raise TypeError("terminal cleanup rows do not match candidates")
            ack_hashes: list[str] = []
            failure_codes = {
                str(code)
                for code in cleanup.get("failureCodes", [])
                if isinstance(code, str) and _REASON_CODE.fullmatch(code)
            }
            rows: list[dict[str, Any]] = []
            for candidate, raw_row in zip(candidates, raw_rows, strict=True):
                if not isinstance(raw_row, Mapping):
                    raise TypeError("terminal cleanup row must be an object")
                row = dict(raw_row)
                row["acknowledged"] = False
                row["ackFailureCode"] = None
                safe = row.get("failed") is False and (
                    row.get("removed") is True
                    or row.get("alreadyAbsent") is True
                )
                if not safe:
                    code = row.get("failureCode")
                    failure_codes.add(
                        code
                        if isinstance(code, str) and _REASON_CODE.fullmatch(code)
                        else "SANDBOX_CONFLICT"
                    )
                    rows.append(row)
                    continue
                try:
                    if set(candidate) != {"runId", "startArtifactSha256"}:
                        raise TypeError("terminal cleanup candidate is malformed")
                    ack = self.queue.ack_terminal_cleanup(
                        candidate["runId"], candidate["startArtifactSha256"]
                    )
                    if (
                        not isinstance(ack, Mapping)
                        or ack.get("schema")
                        != "hwpx.practice-campaign-queue-cleanup-ack/v1"
                        or ack.get("runId") != candidate["runId"]
                        or ack.get("startArtifactSha256")
                        != candidate["startArtifactSha256"]
                        or ack.get("acknowledged") is not True
                        or ack.get("privateStorageCoordinatesExposed") is not False
                    ):
                        raise TypeError(
                            "terminal cleanup acknowledgement is invalid"
                        )
                    ack_hashes.append(_digest(ack))
                    row["acknowledged"] = True
                except Exception:
                    row["ackFailureCode"] = "CLEANUP_ACK_FAILED"
                    failure_codes.add("CLEANUP_ACK_FAILED")
                rows.append(row)
            result = dict(cleanup)
            result["candidates"] = rows
            result["acknowledgedCount"] = len(ack_hashes)
            result["ackReceiptSha256"] = ack_hashes
            result["failureCount"] = len(rows) - len(ack_hashes)
            result["failureCodes"] = sorted(failure_codes)
            return result
        except PracticeCampaignError:
            raise
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc

    def _fail_lease(
        self,
        lease: Any,
        reason: str,
        *,
        sandbox: Any = None,
    ) -> dict[str, Any]:
        """Close an owned lease with a named receipt and bounded cleanup."""

        if not _REASON_CODE.fullmatch(reason):
            reason = "WORKFLOW_FAILED"
        try:
            terminal = self.queue.fail(
                lease, reason, state=self._failure_state(reason)
            )
        except PracticeCampaignError:
            raise
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc
        cleanup = None
        post_terminal_error = None
        try:
            cleanup = self.dispatcher.cleanup_sandbox(sandbox)
        except PracticeDispatchError:
            post_terminal_error = "SANDBOX_CLEANUP_FAILED"
        try:
            response = {
                "advanced": True,
                "terminal": self._public(terminal),
                "status": self.status(getattr(lease, "campaign_id", "")),
                "sandboxCleanup": self._public(cleanup) if cleanup else None,
                "postTerminalErrorCode": post_terminal_error,
                "privateStorageCoordinatesExposed": False,
            }
            return self._public(response)
        except PracticeCampaignError:
            raise
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc

    def _retain_terminal_artifact(
        self,
        task: ResolvedPracticeTask,
        outcome: PracticeDispatchResult,
        manifest: Mapping[str, Any],
        run_ref: Mapping[str, Any],
    ) -> tuple[str | None, str | None]:
        """Retain artifacts, then grade the final accounted terminal receipt.

        The legacy single-hook contract remains for injected P4 test services.
        Production supplies a distinct evaluator hook: an artifact-retention
        receipt by itself is never accepted as evaluator success.
        """

        if outcome.run_receipt is None:
            return None, None
        if (
            self.terminal_evaluator_hook is None
            and outcome.run_receipt.get("state") != "completed"
        ):
            return None, None
        if self.terminal_evaluator_hook is None and self.terminal_artifact_hook is None:
            return None, "DOMAIN_VERIFIER_MISSING"
        try:
            receipt = validate_run_receipt(outcome.run_receipt)
            outputs = [
                item for item in receipt["artifacts"] if item["role"] == "output"
            ]
            if receipt["state"] == "completed" and len(outputs) != 1:
                raise TypeError("completed verifier hook requires one output")
            if receipt["state"] != "completed" and len(outputs) > 1:
                raise TypeError("terminal verifier permits at most one output")
            idempotency_key = outcome.artifact_hook_idempotency_key
            if not isinstance(idempotency_key, str) or not re.fullmatch(
                r"IDEM-[A-F0-9]{20}", idempotency_key
            ):
                raise TypeError("terminal verifier requires a stable idempotency key")
            evaluator = dict(manifest["provenance"]["evaluator"])
            if self.terminal_evaluator_hook is not None:
                artifact_receipt = (
                    self.terminal_artifact_hook(task, outcome)
                    if self.terminal_artifact_hook is not None
                    else None
                )
                if receipt["state"] == "completed":
                    if not isinstance(artifact_receipt, Mapping):
                        return None, "DOMAIN_VERIFIER_MISSING"
                    if (
                        artifact_receipt.get("schema")
                        != "hwpx.practice-terminal-artifact/v1"
                        or artifact_receipt.get("runId") != receipt["runId"]
                        or artifact_receipt.get("artifactSha256")
                        != outputs[0]["sha256"]
                        or artifact_receipt.get("bytes") != outputs[0]["bytes"]
                        or artifact_receipt.get("retained") is not True
                        or artifact_receipt.get("privateStorageCoordinatesExposed")
                        is not False
                    ):
                        raise TypeError("terminal artifact retention binding mismatch")
                    assert_receipt_safe(_receipt_safety_projection(artifact_receipt))
                elif artifact_receipt is not None:
                    raise TypeError("non-completed run cannot retain an output")

                evaluation = self.terminal_evaluator_hook(
                    task, outcome, manifest, run_ref, artifact_receipt
                )
                if not isinstance(evaluation, Mapping):
                    raise TypeError("terminal evaluator result is missing")
                evaluator = dict(manifest["provenance"]["evaluator"])
                if (
                    evaluation.get("schema")
                    != "hwpx.practice-evaluator-result/v1"
                    or evaluation.get("runId") != receipt["runId"]
                    or evaluation.get("terminalState") != receipt["state"]
                    or evaluation.get("terminalReceiptSha256")
                    != receipt["receiptSha256"]
                    or evaluation.get("evaluationPolicySha256")
                    != run_ref["evaluationPolicySha256"]
                    or evaluation.get("evaluatorCodeSha256")
                    != evaluator["sha256"]
                    or evaluation.get("authenticationKeyId")
                    != evaluator["authenticationKeyId"]
                    or evaluation.get("overallStatus") != "passed"
                    or evaluation.get("eligibleForSuccess") is not True
                ):
                    raise TypeError("terminal evaluator result binding mismatch")
                assert_receipt_safe(_receipt_safety_projection(evaluation))
                return _digest(evaluation), None

            # Compatibility path for bounded injected services used by the P4
            # chaos matrix. Production never enters this branch.
            expected = {
                "schema": "hwpx.practice-terminal-verifier/v1",
                "runId": receipt["runId"],
                "scenarioId": receipt["scenarioId"],
                "terminalReceiptSha256": receipt["receiptSha256"],
                "outputArtifact": {
                    "sha256": outputs[0]["sha256"],
                    "bytes": outputs[0]["bytes"],
                },
                "evaluationPolicySha256": run_ref["evaluationPolicySha256"],
                "evaluator": evaluator,
                "idempotencyKey": idempotency_key,
                "eligibleForSuccess": True,
                "privateStorageCoordinatesExposed": False,
            }
            hook_receipt = self.terminal_artifact_hook(task, outcome)
            if hook_receipt is None:
                return None, "DOMAIN_VERIFIER_MISSING"
            if not isinstance(hook_receipt, Mapping) or dict(hook_receipt) != expected:
                raise TypeError("terminal verifier verdict binding mismatch")
            assert_receipt_safe(_receipt_safety_projection(hook_receipt))
            return _digest(hook_receipt), None
        except Exception:
            return None, "DOMAIN_VERIFIER_FAILED"

    @staticmethod
    def _assert_resource_accounting(outcome: PracticeDispatchResult) -> None:
        expected = {
            "artifactBytes": "retained-output-bytes/v1",
            "costMicrounits": "local-zero/v1",
            "costMeasured": False,
        }
        if (
            outcome.boundary.get("resourceAccounting") != expected
            or outcome.usage_delta.get("costMicrounits") != 0
        ):
            raise PracticeDispatchError(
                "WORKFLOW_FAILED", sandbox=outcome.sandbox
            )

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
        if confirm is not True:
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
        recovery_cleanup = None
        try:
            self.queue.recover(campaign_id=campaign_id)
            self.queue.resume(campaign_id)
            recovery_cleanup = self._cleanup_terminal_candidates()
            if run_id is not None:
                existing = self._existing_terminal(campaign_id, run_id)
                if existing is not None:
                    return self._public(
                        {
                            "advanced": False,
                            "terminal": existing,
                            "status": self.status(campaign_id),
                            "sandboxCleanup": (
                                self._public(recovery_cleanup)
                                if recovery_cleanup
                                else None
                            ),
                            "idempotentReplay": True,
                            "privateStorageCoordinatesExposed": False,
                        }
                    )
            if run_id is not None:
                try:
                    lease = self.queue.resume_lease(
                        run_id,
                        self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                except Exception as exc:
                    if getattr(exc, "code", None) != "LEASE_NOT_OWNED":
                        raise
                    lease = self.queue.claim_run(
                        run_id,
                        self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
            else:
                lease = self.queue.claim(
                    self.worker_id,
                    campaign_id=campaign_id,
                    lease_seconds=self.lease_seconds,
                )
        except Exception as exc:
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc
        if lease is None:
            return {
                "advanced": False,
                "status": self.status(campaign_id),
                "privateStorageCoordinatesExposed": False,
            }
        try:
            run_ref = self._run_ref(manifest, lease)
        except PracticeCampaignError as exc:
            return self._fail_lease(
                lease,
                exc.code,
                sandbox=self.dispatcher.known_sandbox(
                    str(getattr(lease, "run_id", ""))
                ),
            )
        try:
            authorization_raw = self.queue.authorize(lease)
            if not isinstance(authorization_raw, Mapping):
                raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE")
            execution_limits, run_remaining, recovery_only = self._authorization(
                authorization_raw, lease
            )
        except PracticeCampaignError:
            raise
        except Exception as exc:
            if getattr(exc, "code", None) in {
                "BUDGET_EXHAUSTED",
                "CANCEL_REQUESTED",
            }:
                existing = self._existing_terminal(campaign_id, run_ref["runId"])
                if existing is not None:
                    cleanup = self._cleanup_terminal_candidates()
                    return self._public(
                        {
                            "advanced": True,
                            "terminal": existing,
                            "status": self.status(campaign_id),
                            "sandboxCleanup": (
                                self._public(cleanup) if cleanup else None
                            ),
                            "privateStorageCoordinatesExposed": False,
                        }
                    )
            raise PracticeCampaignError("CAMPAIGN_QUEUE_UNAVAILABLE") from exc
        task: ResolvedPracticeTask
        durable_usage = {
            key: int(run_ref["budgets"][key]) - run_remaining[key]
            for key in run_remaining
        }
        if durable_usage["costMicrounits"] != 0:
            return self._fail_lease(
                lease,
                "COST_MODEL_MISMATCH",
                sandbox=self.dispatcher.known_sandbox(run_ref["runId"]),
            )
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
                execution_limits=execution_limits,
                durable_usage=durable_usage,
                recovery_only=recovery_only,
            )
        except PracticeDispatchError as exc:
            return self._fail_lease(lease, exc.code, sandbox=exc.sandbox)
        except Exception:
            return self._fail_lease(
                lease,
                "TASK_BINDING_INVALID",
                sandbox=self.dispatcher.known_sandbox(run_ref["runId"]),
            )
        try:
            self._assert_resource_accounting(outcome)
        except PracticeDispatchError as exc:
            return self._fail_lease(lease, exc.code, sandbox=exc.sandbox)
        hook_receipt_sha256 = None
        hook_checked = False
        verifier_failure_reason = None
        try:
            account = self.queue.account(
                lease,
                tool_calls=outcome.usage_delta["toolCalls"],
                repair_rounds=outcome.usage_delta["repairRounds"],
                # The durable queue owns lease wall-time accounting.  Workflow
                # elapsed time remains evidence in the final receipt only.
                elapsed_seconds=0,
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
        except PracticeDispatchError as exc:
            return self._fail_lease(lease, exc.code, sandbox=outcome.sandbox)
        except Exception as exc:
            if (
                getattr(exc, "code", None) == "LEASE_NOT_OWNED"
                and outcome.terminal
                and outcome.run_receipt is not None
            ):
                try:
                    durable_receipt = self.dispatcher.bind_accounted_usage(
                        outcome, durable_usage
                    )
                    reconciled = validate_run_receipt(
                        durable_receipt or outcome.run_receipt
                    )
                    reconciled_outcome = replace(
                        outcome, run_receipt=reconciled
                    )
                    hook_receipt_sha256, retention_error = (
                        self._retain_terminal_artifact(
                            task, reconciled_outcome, manifest, run_ref
                        )
                    )
                    hook_checked = True
                    if retention_error is not None:
                        verifier_failure_reason = retention_error
                        reconciled = self.dispatcher.verifier_failure_receipt(
                            outcome,
                            reconciled["usage"],
                            reason=retention_error,
                        )
                    terminal = self.queue.terminalize(lease, reconciled)
                    account = {
                        "schema": "hwpx.practice-terminal-reconciliation/v1",
                        "runId": reconciled["runId"],
                        "usage": reconciled["usage"],
                        "terminalReceipt": terminal,
                        "expiredLeaseReconciled": True,
                        "privateStorageCoordinatesExposed": False,
                    }
                    account_terminal = terminal
                    queue_usage = reconciled["usage"]
                    bound_receipt = reconciled
                except Exception as reconciliation_error:
                    # A re-leased identity must never be overwritten with a
                    # generic accounting failure after workflow success.
                    raise PracticeCampaignError(
                        "CAMPAIGN_QUEUE_UNAVAILABLE"
                    ) from reconciliation_error
            else:
                return self._fail_lease(
                    lease, "CAMPAIGN_ACCOUNTING_FAILED", sandbox=outcome.sandbox
                )

        if (
            outcome.terminal
            and not hook_checked
            and not isinstance(account_terminal, Mapping)
        ):
            if bound_receipt is None:
                return self._fail_lease(
                    lease, "TERMINAL_RECEIPT_MISSING", sandbox=outcome.sandbox
                )
            accounted_outcome = replace(outcome, run_receipt=bound_receipt)
            hook_receipt_sha256, retention_error = self._retain_terminal_artifact(
                task, accounted_outcome, manifest, run_ref
            )
            if retention_error is not None:
                verifier_failure_reason = retention_error
                bound_receipt = self.dispatcher.verifier_failure_receipt(
                    outcome,
                    bound_receipt["usage"],
                    reason=retention_error,
                )

        cleanup = None
        post_terminal_error = None
        terminal_path = outcome.terminal or isinstance(account_terminal, Mapping)
        if terminal_path:
            if isinstance(account_terminal, Mapping):
                terminal = account_terminal
            elif bound_receipt is not None:
                try:
                    terminal = self.queue.terminalize(lease, bound_receipt)
                except Exception:
                    return self._fail_lease(
                        lease,
                        "TERMINAL_RECONCILIATION_FAILED",
                        sandbox=outcome.sandbox,
                    )
            else:
                return self._fail_lease(
                    lease, "TERMINAL_RECEIPT_MISSING", sandbox=outcome.sandbox
                )
            try:
                cleanup = self.dispatcher.cleanup_sandbox(outcome.sandbox)
            except PracticeDispatchError:
                post_terminal_error = "SANDBOX_CLEANUP_FAILED"
        else:
            terminal = None
        try:
            response_boundary = dict(outcome.boundary)
            if verifier_failure_reason is not None:
                response_boundary.update(
                    {
                        "state": "needs_review",
                        "terminal": True,
                        "decisionRequired": False,
                        "decisionReceiptSha256": None,
                    }
                )
            response = {
                "advanced": True,
                "boundary": response_boundary,
                "sandbox": outcome.sandbox_receipt,
                "accounting": self._public(account),
                "terminal": self._public(terminal) if terminal is not None else None,
                "status": self.status(campaign_id),
                "sandboxCleanup": self._public(cleanup) if cleanup else None,
                "artifactHookReceiptSha256": hook_receipt_sha256,
                "postTerminalErrorCode": post_terminal_error,
                "privateStorageCoordinatesExposed": False,
            }
            return self._public(response)
        except PracticeCampaignError:
            if terminal is None:
                return self._fail_lease(
                    lease, "PRIVACY_BOUNDARY_FAILED", sandbox=outcome.sandbox
                )
            raise

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
            expected_pairs = [
                (item["runId"], item["scenarioId"]) for item in manifest["runs"]
            ]
            actual_pairs = [
                (receipt["runId"], receipt["scenarioId"]) for receipt in receipts
            ]
            if (
                len(receipts) != manifest["expectedRunCount"]
                or len(set(actual_pairs)) != len(actual_pairs)
                or len({receipt["runId"] for receipt in receipts}) != len(receipts)
                or len({receipt["scenarioId"] for receipt in receipts})
                != len(receipts)
                or set(actual_pairs) != set(expected_pairs)
            ):
                raise ValueError("terminal receipts do not bijectively match the campaign")
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
    "TerminalArtifactHook",
    "TerminalEvaluatorHook",
]
