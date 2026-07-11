# SPDX-License-Identifier: Apache-2.0
"""Transport-neutral render boundary consumed by the S-067 workflow engine.

S-067 never claims that this interface rendered a document.  S-068 supplies a
real-Hancom backend and provenance receipts behind this frozen contract.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


RENDER_SCHEMA_VERSION = "hwpx.render.v1"


class RenderStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"


class RenderJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = RENDER_SCHEMA_VERSION
    job_id: str = Field(min_length=8, max_length=200)
    workflow_id: str = Field(min_length=8, max_length=200)
    source_path: str
    source_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    requested_backend: str = "real_hancom"
    dpi: int = Field(default=144, ge=72, le=600)


class RenderReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = RENDER_SCHEMA_VERSION
    job_id: str
    status: RenderStatus
    backend: str | None = None
    hancom_build: str | None = None
    page_artifacts: tuple[str, ...] = ()
    output_content_hash: str | None = None
    render_checked: bool = False
    error_code: str | None = None

    @model_validator(mode="after")
    def truthful_render_claim(self) -> "RenderReceipt":
        if self.render_checked and (
            self.status != RenderStatus.SUCCEEDED
            or not self.backend
            or not self.hancom_build
            or not self.page_artifacts
        ):
            raise ValueError(
                "render_checked requires succeeded status, backend, Hancom build, and page artifacts"
            )
        if self.status == RenderStatus.SUCCEEDED and not self.output_content_hash:
            raise ValueError("successful render receipt requires output_content_hash")
        return self


@runtime_checkable
class RenderBackend(Protocol):
    """Stable S-068 provider boundary; implementations may be local or remote."""

    def capabilities(self) -> dict[str, object]: ...

    def submit(self, job: RenderJob) -> RenderReceipt: ...

    def get(self, job_id: str) -> RenderReceipt: ...

    def cancel(self, job_id: str) -> RenderReceipt: ...


class NullRenderBackend:
    """Honest S-067 default: render evidence is unavailable, never silently true."""

    def capabilities(self) -> dict[str, object]:
        return {
            "schemaVersion": RENDER_SCHEMA_VERSION,
            "available": False,
            "backend": "none",
            "renderChecked": False,
        }

    def submit(self, job: RenderJob) -> RenderReceipt:
        return self._unavailable(job.job_id)

    def get(self, job_id: str) -> RenderReceipt:
        return self._unavailable(job_id)

    def cancel(self, job_id: str) -> RenderReceipt:
        return RenderReceipt(job_id=job_id, status=RenderStatus.CANCELLED, error_code="NO_RENDER_BACKEND")

    @staticmethod
    def _unavailable(job_id: str) -> RenderReceipt:
        return RenderReceipt(
            job_id=job_id,
            status=RenderStatus.UNAVAILABLE,
            render_checked=False,
            error_code="REAL_HANCOM_BACKEND_UNAVAILABLE",
        )


__all__ = [
    "RENDER_SCHEMA_VERSION",
    "NullRenderBackend",
    "RenderBackend",
    "RenderJob",
    "RenderReceipt",
    "RenderStatus",
]
