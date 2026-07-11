# SPDX-License-Identifier: Apache-2.0
"""High-level durable orchestration over ToolSpec primitive functions."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping

from hwpx_mcp_server.document_state import document_revision
from hwpx_mcp_server.tool_contract import contract_hash

from .adapters import ADAPTERS, AdapterAbstention
from .dispatcher import AllowlistedDispatcher
from .models import (
    TERMINAL_STATES,
    WORKFLOW_SCHEMA_VERSION,
    WorkFamily,
    WorkOrder,
    WorkflowEvent,
    WorkflowRecord,
    WorkflowState,
)
from .policy import ActionRequest, PolicyViolation, WorkflowPolicyEngine
from .store import WorkflowStore


def default_workflow_store_path() -> Path:
    configured = os.environ.get("HWPX_WORKFLOW_STORE")
    if configured:
        return Path(configured).expanduser().resolve()
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return (state_home / "hwpx-mcp-server" / "workflows.sqlite3").resolve()


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "uninstalled"


class WorkflowService:
    def __init__(
        self,
        namespace: Mapping[str, Any],
        *,
        store: WorkflowStore | None = None,
        capability_ok: bool = True,
    ) -> None:
        self.store = store or WorkflowStore(default_workflow_store_path())
        self.dispatcher = AllowlistedDispatcher(namespace)
        self.policy = WorkflowPolicyEngine()
        self.capability_ok = capability_ok

    def start(
        self,
        *,
        family: str,
        idempotency_key: str,
        source_path: str | None = None,
        output_path: str | None = None,
        expected_revision: str | None = None,
        parameters: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            typed_family = WorkFamily(family)
        except ValueError:
            return self._abstention_receipt(family, "UNSUPPORTED_INTENT")
        if expected_revision is None and source_path:
            expected_revision = document_revision(source_path)
        values: dict[str, Any] = {
            "family": typed_family,
            "idempotency_key": idempotency_key,
            "source_path": source_path,
            "output_path": output_path,
            "expected_revision": expected_revision,
            "parameters": parameters or {},
        }
        if budget is not None:
            values["budget"] = budget
        if policy is not None:
            values["policy"] = policy
        order = WorkOrder.model_validate(values)
        record, _ = self.store.create(
            order,
            original_content_hash=document_revision(source_path) if source_path else None,
        )
        try:
            self.policy.validate_intake(record)
        except PolicyViolation as error:
            record = self.store.transition(
                record.workflow_id,
                WorkflowState.FAILED,
                expected_state=record.state,
                expected_version=record.state_version,
                event_type="workflow.rejected",
                payload={"code": error.code},
                stop_reason=error.code,
            )
        return self.receipt(record)

    def get(self, workflow_id: str) -> dict[str, Any]:
        return self.receipt(self.store.get(workflow_id))

    def continue_workflow(self, workflow_id: str) -> dict[str, Any]:
        record = self.store.get(workflow_id)
        if record.terminal or record.state == WorkflowState.DECISION:
            return self.receipt(record)
        adapter = ADAPTERS[record.work_order.family]
        try:
            if record.state == WorkflowState.INTAKE:
                self.policy.validate_intake(record)
                record = self.store.transition(
                    workflow_id,
                    WorkflowState.RECON,
                    expected_state=record.state,
                    expected_version=record.state_version,
                )
            elif record.state == WorkflowState.RECON:
                action = adapter.recon_action(record)
                outcome = self.dispatcher.dispatch_durable(
                    self.store,
                    workflow_id,
                    action,
                    expected_version=record.state_version,
                    capability_ok=self.capability_ok,
                    policy=self.policy,
                )
                evidence = outcome.result if outcome.result is not None else outcome.receipt.payload
                target = WorkflowState.PLAN if adapter.recon_ok(evidence) else WorkflowState.NEEDS_REVIEW
                record = self.store.transition(
                    workflow_id,
                    target,
                    expected_state=outcome.receipt.to_state,
                    expected_version=self.store.get(workflow_id).state_version,
                    event_type="recon.completed" if target == WorkflowState.PLAN else "workflow.abstained",
                    payload={"actionHash": action.action_hash, "ok": target == WorkflowState.PLAN},
                    stop_reason=None if target == WorkflowState.PLAN else "RECON_NOT_ACTIONABLE",
                )
            elif record.state == WorkflowState.PLAN:
                action = adapter.execution_action(record)
                if action is None:
                    record = self.store.transition(
                        workflow_id,
                        WorkflowState.EXECUTE,
                        expected_state=record.state,
                        expected_version=record.state_version,
                        event_type="plan.accepted",
                        payload={"readOnly": True},
                    )
                else:
                    planned, _, _ = self.store.append_event(
                        workflow_id,
                        "action.planned",
                        expected_state=record.state,
                        expected_version=record.state_version,
                        payload={
                            "actionHash": action.action_hash,
                            "tool": action.tool_name,
                            "destructive": action.destructive,
                        },
                        event_key=f"plan:{action.action_hash}",
                    )
                    target = WorkflowState.DECISION if action.destructive else WorkflowState.EXECUTE
                    record = self.store.transition(
                        workflow_id,
                        target,
                        expected_state=planned.state,
                        expected_version=planned.state_version,
                        event_type="decision.requested" if action.destructive else "plan.accepted",
                        payload={"actionHash": action.action_hash},
                    )
            elif record.state == WorkflowState.EXECUTE:
                action = adapter.execution_action(record)
                if action is None:
                    record = self.store.transition(
                        workflow_id,
                        WorkflowState.VERIFY,
                        expected_state=record.state,
                        expected_version=record.state_version,
                        event_type="execution.completed",
                        payload={"ok": True, "openSafetyOk": True, "readOnly": True},
                    )
                else:
                    prior = self._action_events(record.workflow_id, action)
                    if not any(event.event_type == "dispatch.started" for event in prior):
                        adapter.prepare_execution(record)
                    outcome = self.dispatcher.dispatch_durable(
                        self.store,
                        workflow_id,
                        action,
                        expected_version=record.state_version,
                        capability_ok=self.capability_ok,
                        policy=self.policy,
                    )
                    evidence = {
                        "actionHash": action.action_hash,
                        "ok": outcome.receipt.payload.get("ok") is True,
                        "openSafetyOk": outcome.receipt.payload.get("openSafetyOk"),
                    }
                    record = self.store.transition(
                        workflow_id,
                        WorkflowState.VERIFY,
                        expected_state=outcome.receipt.to_state,
                        expected_version=self.store.get(workflow_id).state_version,
                        event_type="execution.completed",
                        payload=evidence,
                    )
            elif record.state == WorkflowState.VERIFY:
                evidence = self._execution_evidence(workflow_id)
                read_only = record.work_order.family == WorkFamily.READ_EXTRACT
                verification = {
                    "verified": evidence.get("ok") is True,
                    "openSafetyOk": True if read_only else evidence.get("openSafetyOk") is True,
                    "renderChecked": False,
                }
                if verification["verified"] and verification["openSafetyOk"]:
                    record = self.policy.complete(
                        self.store,
                        record,
                        verification,
                        output_content_hash=self._output_hash(record),
                    )
                else:
                    record = self.store.transition(
                        workflow_id,
                        WorkflowState.NEEDS_REVIEW,
                        expected_state=record.state,
                        expected_version=record.state_version,
                        event_type="verification.incomplete",
                        payload=verification,
                        stop_reason="VERIFICATION_EVIDENCE_REQUIRED",
                    )
            elif record.state == WorkflowState.REPAIR:
                record = self.store.transition(
                    workflow_id,
                    WorkflowState.NEEDS_REVIEW,
                    expected_state=record.state,
                    expected_version=record.state_version,
                    event_type="workflow.abstained",
                    stop_reason="AUTOMATIC_REPAIR_NOT_AVAILABLE",
                )
        except AdapterAbstention as error:
            record = self._abstain_existing(record, error.code)
        return self.receipt(record)

    def approve_decision(self, workflow_id: str, *, approved: bool, action_hash: str | None = None) -> dict[str, Any]:
        record = self.store.get(workflow_id)
        if record.state != WorkflowState.DECISION:
            raise PolicyViolation("DECISION_STATE_REQUIRED", "workflow is not waiting for a decision")
        action = ADAPTERS[record.work_order.family].execution_action(record)
        if action is None:
            raise PolicyViolation("DECISION_ACTION_MISSING", "workflow has no decision action")
        if action_hash is not None and action_hash != action.action_hash:
            raise PolicyViolation("DECISION_ACTION_MISMATCH", "decision action hash does not match the plan")
        record = self.policy.record_decision(self.store, record, action, approved=approved)
        target = WorkflowState.EXECUTE if approved else WorkflowState.NEEDS_REVIEW
        record = self.store.transition(
            workflow_id,
            target,
            expected_state=record.state,
            expected_version=record.state_version,
            event_type="decision.applied",
            payload={"actionHash": action.action_hash, "approved": approved},
            stop_reason=None if approved else "DECISION_REJECTED",
        )
        return self.receipt(record)

    def cancel(self, workflow_id: str, *, reason: str = "CLIENT_CANCELLED") -> dict[str, Any]:
        record = self.store.get(workflow_id)
        if record.terminal:
            return self.receipt(record)
        record = self.store.transition(
            workflow_id,
            WorkflowState.CANCELLED,
            expected_state=record.state,
            expected_version=record.state_version,
            event_type="workflow.cancelled",
            payload={"reasonCode": reason},
            stop_reason=reason,
        )
        return self.receipt(record)

    def resume(self, workflow_id: str) -> dict[str, Any]:
        try:
            return self.continue_workflow(workflow_id)
        except PolicyViolation as error:
            record = self.store.get(workflow_id)
            if error.code != "DISPATCH_IN_DOUBT" or record.state not in {
                WorkflowState.EXECUTE,
                WorkflowState.RECON,
            }:
                raise
            record = self._abstain_existing(record, error.code)
            return self.receipt(record)

    def receipt(self, record: WorkflowRecord) -> dict[str, Any]:
        events = self.store.events(record.workflow_id)
        decisions = [
            {
                "status": "approved" if event.event_type == "decision.approved" else "rejected",
                "actionHash": event.payload.get("actionHash"),
            }
            for event in events
            if event.event_type in {"decision.approved", "decision.rejected"}
        ]
        evidence = self._execution_evidence(record.workflow_id)
        read_only = record.work_order.family == WorkFamily.READ_EXTRACT
        if record.state == WorkflowState.COMPLETED:
            verification_status = "verified_read_only" if read_only else "structurally_verified_render_unverified"
        elif record.state == WorkflowState.NEEDS_REVIEW:
            verification_status = "needs_review"
        else:
            verification_status = "pending"
        stop_reason = record.stop_reason
        if record.state == WorkflowState.DECISION:
            stop_reason = "DECISION_REQUIRED"
        elif record.state == WorkflowState.COMPLETED:
            stop_reason = "VERIFIED_COMPLETION"
        return {
            "schemaVersion": WORKFLOW_SCHEMA_VERSION,
            "workflowId": record.workflow_id,
            "family": record.work_order.family.value,
            "state": record.state.value,
            "stateVersion": record.state_version,
            "terminal": record.state in TERMINAL_STATES,
            "artifacts": self._artifacts(record),
            "decisions": decisions,
            "semanticDiff": {"status": "not_computed", "available": False},
            "openSafety": {
                "ok": True if read_only and record.state == WorkflowState.COMPLETED else evidence.get("openSafetyOk"),
                "renderChecked": False,
            },
            "verificationStatus": verification_status,
            "unresolvedFindings": [] if record.state == WorkflowState.COMPLETED else self._findings(stop_reason),
            "versions": {
                "workflow": WORKFLOW_SCHEMA_VERSION,
                "mcp": _package_version("hwpx-mcp-server"),
                "pythonHwpx": _package_version("python-hwpx"),
            },
            "toolSpecHash": contract_hash(),
            "stopReason": stop_reason,
        }

    def _action_events(self, workflow_id: str, action: ActionRequest) -> list[WorkflowEvent]:
        return [
            event
            for event in self.store.events(workflow_id)
            if event.payload.get("actionHash") == action.action_hash
        ]

    def _execution_evidence(self, workflow_id: str) -> dict[str, Any]:
        for event in reversed(self.store.events(workflow_id)):
            if event.event_type == "execution.completed":
                return dict(event.payload)
        return {}

    def _abstain_existing(self, record: WorkflowRecord, reason: str) -> WorkflowRecord:
        if record.state == WorkflowState.INTAKE:
            target = WorkflowState.FAILED
        else:
            target = WorkflowState.NEEDS_REVIEW
        return self.store.transition(
            record.workflow_id,
            target,
            expected_state=record.state,
            expected_version=record.state_version,
            event_type="workflow.abstained",
            payload={"reasonCode": reason},
            stop_reason=reason,
        )

    @staticmethod
    def _output_hash(record: WorkflowRecord) -> str | None:
        output = record.work_order.output_path
        return document_revision(output) if output and Path(output).is_file() else None

    def _artifacts(self, record: WorkflowRecord) -> list[dict[str, Any]]:
        artifacts = []
        for role, path in (("source", record.work_order.source_path), ("output", record.work_order.output_path)):
            if path:
                artifacts.append(
                    {
                        "role": role,
                        "path": path,
                        "contentHash": document_revision(path) if Path(path).is_file() else None,
                    }
                )
        return artifacts

    @staticmethod
    def _findings(stop_reason: str | None) -> list[dict[str, str]]:
        return [{"code": stop_reason, "severity": "review"}] if stop_reason else []

    @staticmethod
    def _abstention_receipt(family: str, reason: str) -> dict[str, Any]:
        return {
            "schemaVersion": WORKFLOW_SCHEMA_VERSION,
            "workflowId": None,
            "family": family,
            "state": WorkflowState.NEEDS_REVIEW.value,
            "stateVersion": 0,
            "terminal": True,
            "artifacts": [],
            "decisions": [],
            "semanticDiff": {"status": "not_computed", "available": False},
            "openSafety": {"ok": None, "renderChecked": False},
            "verificationStatus": "needs_review",
            "unresolvedFindings": [{"code": reason, "severity": "review"}],
            "versions": {
                "workflow": WORKFLOW_SCHEMA_VERSION,
                "mcp": _package_version("hwpx-mcp-server"),
                "pythonHwpx": _package_version("python-hwpx"),
            },
            "toolSpecHash": contract_hash(),
            "stopReason": reason,
        }


__all__ = ["WorkflowService", "default_workflow_store_path"]
