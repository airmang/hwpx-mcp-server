# SPDX-License-Identifier: Apache-2.0
"""Typed family adapters for the high-level workflow service."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from hwpx_mcp_server.document_state import document_revision

from .models import WorkFamily, WorkflowRecord
from .policy import ActionRequest, PolicyViolation


class AdapterAbstention(RuntimeError):
    """The typed request is unsupported or incomplete and needs human review."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class WorkflowAdapter:
    family: WorkFamily

    def recon_action(self, record: WorkflowRecord) -> ActionRequest:
        raise NotImplementedError

    def execution_action(self, record: WorkflowRecord) -> ActionRequest | None:
        raise NotImplementedError

    def prepare_execution(self, record: WorkflowRecord) -> None:
        return None

    def recon_ok(self, result: Any) -> bool:
        return not isinstance(result, Mapping) or result.get("ok", True) is not False


class ReadExtractAdapter(WorkflowAdapter):
    _TOOLS = {
        "text": "get_document_text",
        "info": "get_document_info",
        "outline": "get_document_outline",
        "map": "get_document_map",
        "markdown": "hwpx_to_markdown",
        "json": "hwpx_extract_json",
    }

    def recon_action(self, record: WorkflowRecord) -> ActionRequest:
        if not record.work_order.source_path:
            raise AdapterAbstention("SOURCE_REQUIRED", "read/extract requires a source document")
        operation = str(record.work_order.parameters.get("operation", "info"))
        tool = self._TOOLS.get(operation)
        if tool is None:
            raise AdapterAbstention("UNSUPPORTED_READ_OPERATION", f"unsupported read operation: {operation}")
        arguments = dict(record.work_order.parameters.get("arguments") or {})
        arguments.setdefault("filename", record.work_order.source_path)
        return ActionRequest(tool_name=tool, arguments=arguments)

    def execution_action(self, record: WorkflowRecord) -> ActionRequest | None:
        return None


class TransactionalEditAdapter(WorkflowAdapter):
    def recon_action(self, record: WorkflowRecord) -> ActionRequest:
        return ActionRequest(tool_name="get_document_info", arguments={"filename": record.work_order.source_path})

    def prepare_execution(self, record: WorkflowRecord) -> None:
        source = Path(record.work_order.source_path or "")
        output = Path(record.work_order.output_path or "")
        if output.exists():
            if document_revision(output) != record.work_order.expected_revision:
                raise PolicyViolation("OUTPUT_ALREADY_EXISTS", "workflow output exists with unrelated content")
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)

    def execution_action(self, record: WorkflowRecord) -> ActionRequest:
        operations = record.work_order.parameters.get("operations")
        if not isinstance(operations, list):
            raise AdapterAbstention("OPERATIONS_REQUIRED", "transactional edit requires an operations array")
        output = record.work_order.output_path
        return ActionRequest(
            tool_name="apply_edits",
            arguments={
                "filename": output,
                "operations": operations,
                "dry_run": False,
                "expected_revision": record.work_order.expected_revision,
                "idempotency_key": f"{record.work_order.idempotency_key}:apply",
            },
            destructive=True,
        )


class KnownTemplateFillAdapter(WorkflowAdapter):
    def _required(self, record: WorkflowRecord) -> tuple[Any, dict[str, Any]]:
        baseline = record.work_order.parameters.get("baseline")
        content = record.work_order.parameters.get("content")
        if baseline is None or not isinstance(content, dict):
            raise AdapterAbstention("TEMPLATE_INPUT_REQUIRED", "baseline and typed content are required")
        return baseline, content

    def recon_action(self, record: WorkflowRecord) -> ActionRequest:
        baseline, content = self._required(record)
        return ActionRequest(
            tool_name="analyze_template_formfit",
            arguments={
                "source_filename": record.work_order.source_path,
                "baseline": baseline,
                "content": content,
                "destination_filename": record.work_order.output_path,
            },
        )

    def execution_action(self, record: WorkflowRecord) -> ActionRequest:
        baseline, content = self._required(record)
        return ActionRequest(
            tool_name="apply_template_formfit",
            arguments={
                "source_filename": record.work_order.source_path,
                "baseline": baseline,
                "content": content,
                "destination_filename": record.work_order.output_path,
                "confirm": True,
            },
            destructive=True,
        )


class UnknownFormFillAdapter(WorkflowAdapter):
    def recon_action(self, record: WorkflowRecord) -> ActionRequest:
        return ActionRequest(tool_name="scan_form_guidance", arguments={"filename": record.work_order.source_path})

    def execution_action(self, record: WorkflowRecord) -> ActionRequest:
        operation_kind = str(record.work_order.parameters.get("operationKind", "table"))
        tools = {"table": "apply_table_ops", "body": "apply_body_ops"}
        tool = tools.get(operation_kind)
        operations = record.work_order.parameters.get("operations")
        if tool is None or not isinstance(operations, list):
            raise AdapterAbstention(
                "FORM_OPERATIONS_REQUIRED",
                "unknown-form fill requires operationKind=table|body and an operations array",
            )
        arguments = {
            "filename": record.work_order.source_path,
            "ops": operations,
            "output": record.work_order.output_path,
            "dry_run": False,
        }
        if tool == "apply_table_ops":
            arguments["render_check"] = "off"
        return ActionRequest(tool_name=tool, arguments=arguments, destructive=True)


class TypedAuthoringAdapter(WorkflowAdapter):
    def _plan(self, record: WorkflowRecord) -> dict[str, Any]:
        plan = record.work_order.parameters.get("documentPlan")
        if not isinstance(plan, dict):
            raise AdapterAbstention("DOCUMENT_PLAN_REQUIRED", "typed authoring requires documentPlan")
        return plan

    def recon_action(self, record: WorkflowRecord) -> ActionRequest:
        return ActionRequest(tool_name="validate_document_plan", arguments={"document_plan": self._plan(record)})

    def recon_ok(self, result: Any) -> bool:
        return isinstance(result, Mapping) and bool(result.get("ok", result.get("can_create", False)))

    def prepare_execution(self, record: WorkflowRecord) -> None:
        output = Path(record.work_order.output_path or "")
        if output.exists():
            raise PolicyViolation("OUTPUT_ALREADY_EXISTS", "typed authoring will not overwrite an existing output")
        output.parent.mkdir(parents=True, exist_ok=True)

    def execution_action(self, record: WorkflowRecord) -> ActionRequest:
        return ActionRequest(
            tool_name="create_document_from_plan",
            arguments={
                "filename": record.work_order.output_path,
                "document_plan": self._plan(record),
                "verify_render": False,
            },
            destructive=True,
        )


ADAPTERS: dict[WorkFamily, WorkflowAdapter] = {
    WorkFamily.READ_EXTRACT: ReadExtractAdapter(WorkFamily.READ_EXTRACT),
    WorkFamily.TRANSACTIONAL_EDIT: TransactionalEditAdapter(WorkFamily.TRANSACTIONAL_EDIT),
    WorkFamily.KNOWN_TEMPLATE_FILL: KnownTemplateFillAdapter(WorkFamily.KNOWN_TEMPLATE_FILL),
    WorkFamily.UNKNOWN_FORM_FILL: UnknownFormFillAdapter(WorkFamily.UNKNOWN_FORM_FILL),
    WorkFamily.TYPED_AUTHORING: TypedAuthoringAdapter(WorkFamily.TYPED_AUTHORING),
}


__all__ = ["ADAPTERS", "AdapterAbstention", "WorkflowAdapter"]
