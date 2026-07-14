# SPDX-License-Identifier: Apache-2.0
"""Bind one private-practice run to the installed durable WorkflowService.

All document work goes through :class:`WorkflowService`; this module never calls
editing primitives.  Private paths and task parameters stay inside the injected
resolver/sandbox boundary.  Only opaque IDs, hashes, closed codes, and T024 run
receipts cross back to the campaign service.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from hwpx.practice import (
    PRACTICE_RUN_EVENT_SCHEMA,
    PRACTICE_RUN_SCHEMA,
    practice_run_id,
    redact_run_receipt,
    validate_campaign_manifest,
    validate_exact_provenance,
    validate_practice_run,
    workflow_event_id,
)

from hwpx_mcp_server.document_state import document_revision
from hwpx_mcp_server.workflow.policy import PolicyViolation
from hwpx_mcp_server.workflow.service import WorkflowService

from .sandbox import PracticeSandbox, PracticeSandboxError, PracticeSandboxManager


_WORKFLOW_FAMILIES = frozenset(
    {
        "transactional_edit",
        "known_template_fill",
        "unknown_form_fill",
        "typed_authoring",
        "must_abstain",
    }
)
_CLOSED_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _strip_sha256(value: object) -> str | None:
    text = str(value or "")
    if text.startswith("sha256:"):
        text = text[7:]
    return text if re.fullmatch(r"[a-f0-9]{64}", text) else None


class PracticeDispatchError(RuntimeError):
    """Closed, path-redacted error safe for the public campaign API."""

    _MESSAGES = {
        "CAMPAIGN_CONTRACT_INVALID": "campaign manifest validation failed",
        "TASK_BINDING_INVALID": "selected task does not bind to the campaign run",
        "CAPABILITY_SKEW": "installed workflow capability does not match campaign provenance",
        "TOOL_SPEC_SKEW": "installed ToolSpec does not match campaign provenance",
        "SANDBOX_UNAVAILABLE": "isolated practice sandbox is unavailable",
        "STALE_DOCUMENT_REVISION": "sandbox input revision changed before dispatch",
        "DECISION_REQUIRED": "workflow is waiting for an explicit bound decision",
        "DECISION_RECEIPT_MISMATCH": "decision does not bind to the current workflow boundary",
        "DECISION_STATE_REQUIRED": "a decision was supplied outside a decision boundary",
        "WORKFLOW_FAILED": "high-level workflow dispatch failed closed",
        "CONTINUE_LIMIT_INVALID": "continue step limit must be between 1 and 16",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "WORKFLOW_FAILED"
        self.code = code
        super().__init__(f"{code}: {self._MESSAGES[code]}")


@dataclass(frozen=True, slots=True)
class ResolvedPracticeTask:
    """Authenticated private task material resolved from one opaque run slot."""

    scenario_ref: Mapping[str, Any]
    dispatch: Mapping[str, Any]
    source_artifact: Path
    workflow_family: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.workflow_family not in _WORKFLOW_FAMILIES:
            raise PracticeDispatchError("TASK_BINDING_INVALID")
        if not isinstance(self.scenario_ref, Mapping) or not isinstance(
            self.dispatch, Mapping
        ):
            raise PracticeDispatchError("TASK_BINDING_INVALID")
        if not isinstance(self.parameters, Mapping):
            raise PracticeDispatchError("TASK_BINDING_INVALID")


@dataclass(frozen=True, slots=True)
class PracticeDispatchResult:
    """One bounded high-level workflow advance and its redacted projection."""

    boundary: dict[str, Any]
    run_receipt: dict[str, Any] | None
    usage_delta: dict[str, int]
    sandbox_receipt: dict[str, Any]
    terminal_record: dict[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        return self.run_receipt is not None


class PracticeWorkflowDispatcher:
    """Drive installed WorkflowService calls inside one content-addressed sandbox."""

    def __init__(
        self,
        workflow_service: WorkflowService,
        sandbox_manager: PracticeSandboxManager,
        *,
        runtime_provenance: Callable[[], Mapping[str, Any]],
    ) -> None:
        self.workflow_service = workflow_service
        self.sandbox_manager = sandbox_manager
        self.runtime_provenance = runtime_provenance

    def assert_runtime_provenance(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        try:
            campaign = validate_campaign_manifest(manifest)
            expected = validate_exact_provenance(campaign["provenance"])
            actual = validate_exact_provenance(self.runtime_provenance())
        except (TypeError, ValueError) as exc:
            raise PracticeDispatchError("CAPABILITY_SKEW") from exc
        if actual != expected:
            expected_tool = expected["toolSpec"]["sha256"]
            actual_tool = actual["toolSpec"]["sha256"]
            code = "TOOL_SPEC_SKEW" if actual_tool != expected_tool else "CAPABILITY_SKEW"
            raise PracticeDispatchError(code)
        return expected

    @staticmethod
    def _workflow_idempotency_key(campaign_id: str, run_id: str) -> str:
        token = hashlib.sha256(f"{campaign_id}\n{run_id}".encode("ascii")).hexdigest()
        return f"PRAC-{token[:32].upper()}"

    @staticmethod
    def _workflow_budget(run_ref: Mapping[str, Any]) -> dict[str, int]:
        budget = run_ref["budgets"]
        if (
            budget["toolCalls"] < 1
            or budget["attempts"] < 1
            or budget["elapsedSeconds"] < 1
            or budget["repairRounds"] > 3
        ):
            raise PracticeDispatchError("TASK_BINDING_INVALID")
        return {
            "max_attempts": budget["attempts"],
            "max_tool_calls": budget["toolCalls"],
            "max_elapsed_seconds": budget["elapsedSeconds"],
            "max_repair_rounds": budget["repairRounds"],
        }

    @staticmethod
    def _empty_evidence() -> dict[str, Any]:
        return {
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
            "unresolvedReasonCodes": [],
        }

    @staticmethod
    def _zero_usage() -> dict[str, int]:
        return {
            "toolCalls": 0,
            "attempts": 0,
            "repairRounds": 0,
            "elapsedSeconds": 0,
            "costMicrounits": 0,
            "artifactBytes": 0,
        }

    def _identity_run(
        self,
        manifest: Mapping[str, Any],
        run_ref: Mapping[str, Any],
        task: ResolvedPracticeTask,
    ) -> dict[str, Any]:
        run = {
            "schema": PRACTICE_RUN_SCHEMA,
            "runId": run_ref["runId"],
            "scenarioRef": dict(task.scenario_ref),
            "dispatch": dict(task.dispatch),
            "provenance": dict(manifest["provenance"]),
            "budgets": dict(run_ref["budgets"]),
            "state": "running",
            "terminalReason": None,
            "workflowEvents": [],
            "artifacts": [],
            "evidence": self._empty_evidence(),
            "usage": self._zero_usage(),
            "privacy": {
                "localOnly": True,
                "syntheticInputsOnly": True,
                "highConfidencePiiCount": 0,
                "privateCoordinatesExposed": False,
                "evaluatorDataExposed": False,
            },
        }
        try:
            validated = validate_practice_run(run)
        except (TypeError, ValueError) as exc:
            raise PracticeDispatchError("TASK_BINDING_INVALID") from exc
        if practice_run_id(validated) != run_ref["runId"]:
            raise PracticeDispatchError("TASK_BINDING_INVALID")
        scenario = validated["scenarioRef"]
        matching = {
            "scenarioId": run_ref["scenarioId"],
            "scenarioSha256": run_ref["scenarioSha256"],
            "runnerManifestSha256": run_ref["runnerManifestSha256"],
            "derivativeSha256": run_ref["derivativeSha256"],
            "startArtifactSha256": run_ref["startArtifactSha256"],
        }
        if any(scenario[key] != value for key, value in matching.items()):
            raise PracticeDispatchError("TASK_BINDING_INVALID")
        return validated

    def _prepare(
        self,
        manifest: Mapping[str, Any],
        run_ref: Mapping[str, Any],
        task: ResolvedPracticeTask,
    ) -> tuple[PracticeSandbox, dict[str, Any], dict[str, Any]]:
        self.assert_runtime_provenance(manifest)
        identity = self._identity_run(manifest, run_ref, task)
        try:
            sandbox = self.sandbox_manager.prepare(
                task.source_artifact,
                run_id=run_ref["runId"],
                expected_sha256=run_ref["startArtifactSha256"],
            )
            output = sandbox.writable_path("output/result.hwpx", create_parents=True)
        except PracticeSandboxError as exc:
            code = (
                "STALE_DOCUMENT_REVISION"
                if exc.code == "SOURCE_CHANGED"
                else "SANDBOX_UNAVAILABLE"
            )
            raise PracticeDispatchError(code) from exc
        expected_revision = document_revision(sandbox.working_path)
        if expected_revision != f"sha256:{run_ref['startArtifactSha256']}":
            raise PracticeDispatchError("STALE_DOCUMENT_REVISION")

        raw = self.workflow_service.start(
            family=task.workflow_family,
            idempotency_key=self._workflow_idempotency_key(
                manifest["campaignId"], run_ref["runId"]
            ),
            source_path=str(sandbox.working_path),
            output_path=str(output),
            expected_revision=expected_revision,
            parameters=dict(task.parameters),
            budget=self._workflow_budget(run_ref),
            policy={
                "preserve_original": True,
                "require_expected_revision": True,
                "require_decision_for_destructive": True,
                "require_open_safety": True,
                "require_verified_completion": True,
                "require_real_hancom_render": False,
            },
        )
        self._assert_workflow_receipt_provenance(raw, manifest["provenance"])
        return sandbox, identity, raw

    @staticmethod
    def _assert_workflow_receipt_provenance(
        receipt: Mapping[str, Any], expected: Mapping[str, Any]
    ) -> None:
        versions = receipt.get("versions")
        if not isinstance(versions, Mapping):
            raise PracticeDispatchError("CAPABILITY_SKEW")
        if (
            versions.get("pythonHwpx") != expected["stack"]["core"]["version"]
            or versions.get("mcp") != expected["stack"]["server"]["version"]
        ):
            raise PracticeDispatchError("CAPABILITY_SKEW")
        if receipt.get("toolSpecHash") != expected["toolSpec"]["sha256"]:
            raise PracticeDispatchError("TOOL_SPEC_SKEW")

    def _ledger_events(self, workflow_id: str | None) -> list[Any]:
        if not workflow_id:
            return []
        try:
            return list(self.workflow_service.store.events(workflow_id))
        except Exception as exc:
            raise PracticeDispatchError("WORKFLOW_FAILED") from exc

    def _usage(self, workflow_id: str | None, output: Path) -> dict[str, int]:
        events = self._ledger_events(workflow_id)
        tool_calls = sum(event.event_type == "dispatch.started" for event in events)
        attempts = max(
            (
                int(event.payload.get("attempt", 0))
                for event in events
                if event.event_type == "dispatch.started"
            ),
            default=0,
        )
        repairs = sum(
            getattr(event.to_state, "value", event.to_state) == "repair"
            and getattr(event.from_state, "value", event.from_state) != "repair"
            for event in events
        )
        elapsed = 0
        if workflow_id:
            try:
                record = self.workflow_service.store.get(workflow_id)
                elapsed = max(
                    0, math.ceil((record.updated_at - record.created_at).total_seconds())
                )
            except Exception as exc:
                raise PracticeDispatchError("WORKFLOW_FAILED") from exc
        return {
            "toolCalls": tool_calls,
            "attempts": attempts,
            "repairRounds": repairs,
            "elapsedSeconds": elapsed,
            "costMicrounits": 0,
            "artifactBytes": output.stat().st_size if output.is_file() else 0,
        }

    @staticmethod
    def _usage_delta(before: Mapping[str, int], after: Mapping[str, int]) -> dict[str, int]:
        return {key: max(0, int(after[key]) - int(before[key])) for key in after}

    def _run_events(self, workflow_id: str | None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for sequence, event in enumerate(self._ledger_events(workflow_id)):
            kind = re.sub(r"[^a-z0-9_]+", "_", event.event_type.casefold()).strip("_")
            if not kind or not kind[0].isalpha():
                kind = "workflow_event"
            status = (
                "failed"
                if event.event_type.endswith(("failed", "rejected"))
                else "abstained"
                if event.event_type.endswith(("abstained", "cancelled", "incomplete"))
                else "succeeded"
            )
            event_key = event.event_key or event.event_hash
            row = {
                "schema": PRACTICE_RUN_EVENT_SCHEMA,
                "sequence": sequence,
                "kind": kind[:64],
                "status": status,
                "idempotencyKey": f"EVK-{_digest(event_key)[:20].upper()}",
                "requestSha256": _digest(
                    {"eventType": event.event_type, "payloadHash": _digest(event.payload)}
                ),
                "responseSha256": _digest(event.model_dump(mode="json")),
                "elapsedMilliseconds": 0,
            }
            row["eventId"] = workflow_event_id(row)
            result.append(row)
        return result

    @staticmethod
    def _evidence(receipt: Mapping[str, Any], terminal_state: str) -> dict[str, Any]:
        semantic = receipt.get("semanticDiff")
        semantic_ref = (
            {"status": "passed", "receiptSha256": _digest(semantic)}
            if isinstance(semantic, Mapping) and semantic.get("available") is True
            else {"status": "not_run", "receiptSha256": None}
        )
        open_safety = receipt.get("openSafety")
        open_ok = open_safety.get("ok") if isinstance(open_safety, Mapping) else None
        open_ref = {
            "status": "passed" if open_ok is True else "failed" if open_ok is False else "not_run",
            "receiptSha256": _digest(open_safety) if isinstance(open_ok, bool) else None,
        }
        domain = receipt.get("domainVerification")
        domains: list[dict[str, Any]] = []
        if isinstance(domain, Mapping):
            for index, row_value in enumerate(domain.get("verifiers") or []):
                if not isinstance(row_value, Mapping):
                    continue
                row = dict(row_value)
                action_hash = _strip_sha256(row.get("actionHash"))
                verifier_hash = action_hash or _digest(
                    {"tool": row.get("tool"), "index": index}
                )
                content_hash = _strip_sha256(row.get("contentHash")) or _digest(row)
                domains.append(
                    {
                        "verifierId": f"VER-{_digest({'tool': row.get('tool'), 'index': index})[:20].upper()}",
                        "verifierSha256": verifier_hash,
                        "status": "passed" if row.get("ok") is True else "failed",
                        "receiptSha256": content_hash,
                    }
                )
        stop_reason = receipt.get("stopReason")
        unresolved = []
        if terminal_state != "completed" and isinstance(stop_reason, str) and _CLOSED_CODE.fullmatch(stop_reason):
            unresolved = [stop_reason]
        return {
            "semanticDiff": semantic_ref,
            "openSafety": open_ref,
            "domainVerdicts": domains,
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
            "unresolvedReasonCodes": unresolved,
        }

    @staticmethod
    def _terminal_mapping(
        receipt: Mapping[str, Any], *, forced_reason: str | None = None
    ) -> tuple[str | None, str | None]:
        state = str(receipt.get("state") or "")
        if forced_reason:
            if forced_reason in {
                "TOOL_CALL_BUDGET_EXCEEDED",
                "ACTION_ATTEMPT_BUDGET_EXCEEDED",
                "ELAPSED_TIME_BUDGET_EXCEEDED",
                "REPAIR_BUDGET_EXCEEDED",
            }:
                return "budget_exhausted", forced_reason
            return "needs_review", forced_reason
        if state == "completed":
            return "completed", "VERIFIED_COMPLETION"
        if state == "needs_review":
            reason = str(receipt.get("stopReason") or "WORKFLOW_NEEDS_REVIEW")
            return "needs_review", reason if _CLOSED_CODE.fullmatch(reason) else "WORKFLOW_NEEDS_REVIEW"
        if state == "failed":
            reason = str(receipt.get("stopReason") or "WORKFLOW_FAILED")
            return "failed", reason if _CLOSED_CODE.fullmatch(reason) else "WORKFLOW_FAILED"
        if state == "cancelled":
            reason = str(receipt.get("stopReason") or "CLIENT_CANCELLED")
            return "cancelled", reason if _CLOSED_CODE.fullmatch(reason) else "CLIENT_CANCELLED"
        return None, None

    def _terminal_run(
        self,
        identity: Mapping[str, Any],
        sandbox: PracticeSandbox,
        receipt: Mapping[str, Any],
        *,
        forced_reason: str | None = None,
        run_attempt: int = 1,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        state, reason = self._terminal_mapping(receipt, forced_reason=forced_reason)
        if state is None or reason is None:
            return None
        output = sandbox.writable_path("output/result.hwpx", create_parents=True)
        usage = self._usage(receipt.get("workflowId"), output)
        if (
            isinstance(run_attempt, bool)
            or not isinstance(run_attempt, int)
            or not 1 <= run_attempt <= identity["budgets"]["attempts"]
        ):
            raise PracticeDispatchError("TASK_BINDING_INVALID")
        usage["attempts"] = run_attempt
        run = dict(identity)
        run.update(
            {
                "state": state,
                "terminalReason": reason,
                "workflowEvents": self._run_events(receipt.get("workflowId")),
                "artifacts": [
                    {
                        "artifactId": identity["scenarioRef"]["startArtifactId"],
                        "role": "start",
                        "sha256": identity["scenarioRef"]["startArtifactSha256"],
                        "bytes": sandbox.working_path.stat().st_size,
                    }
                ],
                "evidence": self._evidence(receipt, state),
                "usage": usage,
            }
        )
        if output.is_file():
            output_hash = hashlib.sha256(output.read_bytes()).hexdigest()
            run["artifacts"].append(
                {
                    "artifactId": f"OUT-{output_hash[:20].upper()}",
                    "role": "output",
                    "sha256": output_hash,
                    "bytes": output.stat().st_size,
                }
            )
        try:
            self.sandbox_manager.assert_source_unchanged(sandbox)
            validated = validate_practice_run(run)
            return redact_run_receipt(validated), validated
        except (PracticeSandboxError, TypeError, ValueError) as exc:
            raise PracticeDispatchError("WORKFLOW_FAILED") from exc

    @staticmethod
    def bind_accounted_usage(
        outcome: PracticeDispatchResult, usage: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Re-address a terminal receipt against durable queue usage floors."""

        if outcome.terminal_record is None:
            return None
        record = dict(outcome.terminal_record)
        current = dict(record["usage"])
        for key in current:
            value = usage.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PracticeDispatchError("TASK_BINDING_INVALID")
            current[key] = max(current[key], value)
        record["usage"] = current
        try:
            return redact_run_receipt(validate_practice_run(record))
        except (TypeError, ValueError) as exc:
            raise PracticeDispatchError("WORKFLOW_FAILED") from exc

    def _boundary(
        self,
        identity: Mapping[str, Any],
        receipt: Mapping[str, Any],
        sandbox: PracticeSandbox,
    ) -> dict[str, Any]:
        state = str(receipt.get("state") or "unknown")
        action_hash = None
        if state == "decision":
            action_hash = next(
                (
                    _strip_sha256(event.payload.get("actionHash"))
                    for event in reversed(
                        self._ledger_events(str(receipt.get("workflowId") or ""))
                    )
                    if event.event_type == "decision.requested"
                ),
                None,
            )
            if action_hash is None:
                raise PracticeDispatchError("WORKFLOW_FAILED")
        base = {
            "schema": "hwpx.practice-workflow-boundary/v1",
            "runId": identity["runId"],
            "scenarioId": identity["scenarioRef"]["scenarioId"],
            "workflowSha256": (
                _digest(receipt.get("workflowId"))
                if receipt.get("workflowId")
                else None
            ),
            "state": state,
            "terminal": bool(receipt.get("terminal")),
            "decisionRequired": state == "decision",
            "decisionActionSha256": action_hash,
            "workflowReceiptSha256": _digest(receipt),
            "toolSpecHash": receipt.get("toolSpecHash"),
            "sandboxId": sandbox.sandbox_id,
            "privateStorageCoordinatesExposed": False,
        }
        base["decisionReceiptSha256"] = _digest(base) if state == "decision" else None
        return base

    def advance(
        self,
        manifest: Mapping[str, Any],
        run_ref: Mapping[str, Any],
        task: ResolvedPracticeTask,
        *,
        max_steps: int = 8,
        approved: bool | None = None,
        decision_receipt_sha256: str | None = None,
        run_attempt: int = 1,
    ) -> PracticeDispatchResult:
        """Advance to terminal/decision/boundary using at most ``max_steps`` calls."""

        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= 16:
            raise PracticeDispatchError("CONTINUE_LIMIT_INVALID")
        try:
            campaign = validate_campaign_manifest(manifest)
        except (TypeError, ValueError) as exc:
            raise PracticeDispatchError("CAMPAIGN_CONTRACT_INVALID") from exc
        if (
            isinstance(run_attempt, bool)
            or not isinstance(run_attempt, int)
            or not 1 <= run_attempt <= run_ref["budgets"]["attempts"]
        ):
            raise PracticeDispatchError("TASK_BINDING_INVALID")
        sandbox, identity, receipt = self._prepare(campaign, run_ref, task)
        output = sandbox.writable_path("output/result.hwpx", create_parents=True)
        usage_before = self._usage(receipt.get("workflowId"), output)
        decision_consumed = False
        forced_reason: str | None = None

        for _ in range(max_steps):
            if receipt.get("terminal") is True:
                break
            if receipt.get("state") == "decision":
                boundary = self._boundary(identity, receipt, sandbox)
                if approved is None:
                    break
                if decision_receipt_sha256 != boundary["decisionReceiptSha256"]:
                    raise PracticeDispatchError("DECISION_RECEIPT_MISMATCH")
                try:
                    receipt = self.workflow_service.approve_decision(
                        str(receipt["workflowId"]), approved=approved, action_hash=None
                    )
                except PolicyViolation as exc:
                    raise PracticeDispatchError("DECISION_REQUIRED") from exc
                decision_consumed = True
                continue
            try:
                receipt = self.workflow_service.continue_workflow(
                    str(receipt["workflowId"])
                )
            except PolicyViolation as exc:
                forced_reason = exc.code if _CLOSED_CODE.fullmatch(exc.code) else "WORKFLOW_FAILED"
                try:
                    receipt = self.workflow_service.cancel(
                        str(receipt["workflowId"]), reason=forced_reason
                    )
                except Exception:
                    pass
                break
            self._assert_workflow_receipt_provenance(
                receipt, campaign["provenance"]
            )

        if approved is not None and not decision_consumed:
            raise PracticeDispatchError("DECISION_STATE_REQUIRED")
        usage_after = self._usage(receipt.get("workflowId"), output)
        terminal_result = self._terminal_run(
            identity,
            sandbox,
            receipt,
            forced_reason=forced_reason,
            run_attempt=run_attempt,
        )
        terminal, terminal_record = (
            terminal_result if terminal_result is not None else (None, None)
        )
        return PracticeDispatchResult(
            boundary=self._boundary(identity, receipt, sandbox),
            run_receipt=terminal,
            usage_delta=self._usage_delta(usage_before, usage_after),
            sandbox_receipt=sandbox.redacted_receipt(),
            terminal_record=terminal_record,
        )


__all__ = [
    "PracticeDispatchError",
    "PracticeDispatchResult",
    "PracticeWorkflowDispatcher",
    "ResolvedPracticeTask",
]
