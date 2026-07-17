# SPDX-License-Identifier: Apache-2.0
"""Stateless HWPX MCP 서버."""

from __future__ import annotations


from ..workflow.service import WorkflowService
from ..runtime_services import RUNTIME_SERVICES
from ..tool_contract import contract_hash
from ._shared import (
    _render_client,
)


def _workflow_service() -> WorkflowService:
    """Build a request-scoped service over the durable configured store."""

    capability = RUNTIME_SERVICES.require_tool_bindings()["mcp_server_health"]()[
        "capability"
    ]
    return WorkflowService(
        RUNTIME_SERVICES.require_tool_bindings(),
        capability_ok=bool(capability["ok"]),
        render_client=_render_client(),
        tool_spec_hash=contract_hash(),
    )


def start_workflow(
    family: str,
    idempotency_key: str,
    source_path: str = None,
    output_path: str = None,
    expected_revision: str = None,
    parameters: dict = None,
    budget: dict = None,
    policy: dict = None,
) -> dict:
    """타입화된 작업을 서버 강제 durable workflow로 시작합니다."""

    return _workflow_service().start(
        family=family,
        idempotency_key=idempotency_key,
        source_path=source_path,
        output_path=output_path,
        expected_revision=expected_revision,
        parameters=parameters,
        budget=budget,
        policy=policy,
    )


def get_workflow(workflow_id: str) -> dict:
    """현재 상태와 증거를 구조화된 workflow receipt로 조회합니다."""

    return _workflow_service().get(workflow_id)


def get_workflow_result(workflow_id: str, action_hash: str = None) -> dict:
    """암호화 저장된 workflow primitive 결과를 content hash와 함께 조회합니다."""

    return _workflow_service().workflow_result(workflow_id, action_hash=action_hash)


def continue_workflow(workflow_id: str) -> dict:
    """서버 정책에 따라 workflow를 다음 durable 경계까지 진행합니다."""

    return _workflow_service().continue_workflow(workflow_id)


def approve_workflow_decision(
    workflow_id: str,
    approved: bool,
    action_hash: str = None,
) -> dict:
    """decision 상태의 정확한 계획 action을 승인하거나 거절합니다."""

    return _workflow_service().approve_decision(
        workflow_id,
        approved=approved,
        action_hash=action_hash,
    )


def cancel_workflow(workflow_id: str, reason: str = "CLIENT_CANCELLED") -> dict:
    """아직 terminal이 아닌 workflow를 취소합니다."""

    return _workflow_service().cancel(workflow_id, reason=reason)


def resume_workflow(workflow_id: str) -> dict:
    """재시작 후 durable receipt를 기준으로 안전하게 workflow를 재개합니다."""

    return _workflow_service().resume(workflow_id)


__all__ = [
    "start_workflow",
    "get_workflow",
    "get_workflow_result",
    "continue_workflow",
    "approve_workflow_decision",
    "cancel_workflow",
    "resume_workflow",
]
