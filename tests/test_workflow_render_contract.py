from __future__ import annotations

import pytest
from pydantic import ValidationError

from hwpx_mcp_server.workflow import (
    NullRenderBackend,
    RenderBackend,
    RenderJob,
    RenderReceipt,
    RenderStatus,
)


def job() -> RenderJob:
    return RenderJob(
        job_id="render-job-0001",
        workflow_id="workflow-0001",
        source_path="/tmp/document.hwpx",
        source_content_hash="sha256:" + "a" * 64,
    )


def test_null_backend_is_protocol_compatible_and_honestly_unverified():
    backend = NullRenderBackend()

    assert isinstance(backend, RenderBackend)
    assert backend.capabilities()["renderChecked"] is False
    receipt = backend.submit(job())
    assert receipt.status == RenderStatus.UNAVAILABLE
    assert receipt.render_checked is False
    assert receipt.error_code == "REAL_HANCOM_BACKEND_UNAVAILABLE"


def test_render_checked_cannot_be_claimed_without_real_hancom_provenance():
    with pytest.raises(ValidationError, match="render_checked requires"):
        RenderReceipt(
            job_id="render-job-0001",
            status=RenderStatus.SUCCEEDED,
            render_checked=True,
            output_content_hash="sha256:" + "b" * 64,
        )


def test_successful_real_hancom_receipt_contract_is_transport_neutral():
    receipt = RenderReceipt(
        job_id="render-job-0001",
        status=RenderStatus.SUCCEEDED,
        backend="windows-com-worker",
        hancom_build="Hancom Office 2024 build fixture",
        page_artifacts=("sha256:page-1",),
        output_content_hash="sha256:" + "b" * 64,
        render_checked=True,
    )

    assert receipt.render_checked is True
    assert receipt.status == RenderStatus.SUCCEEDED
