# SPDX-License-Identifier: Apache-2.0
"""Frozen measure-first corpus and accounting rules for the render service."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from typing import Literal, Sequence

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .rendering import RenderStatus


class CorpusBucket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    document_count: int = Field(gt=0)
    eligible_for_success_availability: bool = True


class BaselineCorpusContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["hwpx.render-baseline.v1"] = "hwpx.render-baseline.v1"
    corpus_id: Literal["s068-mixed-100-v1"] = "s068-mixed-100-v1"
    buckets: tuple[CorpusBucket, ...]

    @model_validator(mode="after")
    def exactly_one_hundred_unique_documents(self) -> "BaselineCorpusContract":
        if sum(item.document_count for item in self.buckets) != 100:
            raise ValueError("render baseline corpus must contain exactly 100 documents")
        keys = [item.key for item in self.buckets]
        if len(keys) != len(set(keys)):
            raise ValueError("render baseline bucket keys must be unique")
        return self

    @property
    def contract_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


FROZEN_BASELINE_CORPUS = BaselineCorpusContract(
    buckets=(
        CorpusBucket(key="simple_text", document_count=15),
        CorpusBucket(key="tables_and_merges", document_count=20),
        CorpusBucket(key="known_templates", document_count=15),
        CorpusBucket(key="unknown_forms", document_count=15),
        CorpusBucket(key="images_and_shapes", document_count=10),
        CorpusBucket(key="multi_section_multipage", document_count=10),
        CorpusBucket(key="large_near_limit", document_count=10),
        CorpusBucket(key="invalid_or_unsupported", document_count=5, eligible_for_success_availability=False),
    )
)


class MeasureFirstGate(BaseModel):
    """Only accounting targets are gates until observed latency/availability is frozen."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_documents: Literal[100] = 100
    lost_job_target: Literal[0] = 0
    duplicate_terminal_receipt_target: Literal[0] = 0
    terminal_accounting_rate_target: Literal[1.0] = 1.0
    latency_percentiles: tuple[Literal["p50", "p95", "p99"], ...] = ("p50", "p95", "p99")
    availability_target: None = None


class RenderMetricObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str = Field(min_length=8)
    job_id: str = Field(min_length=8)
    submitted_at: AwareDatetime
    terminal_at: AwareDatetime
    status: RenderStatus
    eligible_for_success_availability: bool = True

    @model_validator(mode="after")
    def terminal_and_monotonic(self) -> "RenderMetricObservation":
        if self.status not in {
            RenderStatus.SUCCEEDED,
            RenderStatus.FAILED,
            RenderStatus.UNAVAILABLE,
            RenderStatus.CANCELLED,
        }:
            raise ValueError("metric observation must be terminal")
        if self.terminal_at < self.submitted_at:
            raise ValueError("terminal_at cannot precede submitted_at")
        return self

    @property
    def latency_seconds(self) -> float:
        return (self.terminal_at - self.submitted_at).total_seconds()


class QueueMetricSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: AwareDatetime
    queue_depth: int = Field(ge=0)
    oldest_queued_age_seconds: float = Field(ge=0)
    running_jobs: int = Field(ge=0, le=1)
    last_success_at: AwareDatetime | None = None
    worker_available: bool
    degraded_reason: str | None = None

    @model_validator(mode="after")
    def degraded_state_is_explicit(self) -> "QueueMetricSample":
        if not self.worker_available and not self.degraded_reason:
            raise ValueError("unavailable worker requires degraded_reason")
        return self


class RenderAccountingReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_jobs: int
    unique_terminal_jobs: int
    terminal_receipts: int
    missing_job_ids: tuple[str, ...]
    duplicate_job_ids: tuple[str, ...]
    orphan_job_ids: tuple[str, ...]
    lost_jobs: int
    duplicate_terminal_receipts: int
    terminal_accounting_rate: float
    success_availability: float | None
    latency_seconds: dict[str, float | None]
    exact_accounting: bool


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[rank]


def account_terminal_receipts(
    expected_job_ids: Sequence[str],
    observations: Sequence[RenderMetricObservation],
) -> RenderAccountingReport:
    """Account every expected job exactly once; duplicates never hide losses."""

    expected = set(expected_job_ids)
    if len(expected) != len(expected_job_ids):
        raise ValueError("expected_job_ids must be unique")
    counts = Counter(item.job_id for item in observations)
    observed = set(counts)
    missing = tuple(sorted(expected - observed))
    duplicates = tuple(sorted(job_id for job_id, count in counts.items() if job_id in expected and count > 1))
    orphans = tuple(sorted(observed - expected))
    canonical = [item for item in observations if item.job_id in expected and counts[item.job_id] == 1]
    eligible = [item for item in canonical if item.eligible_for_success_availability]
    successes = sum(item.status == RenderStatus.SUCCEEDED for item in eligible)
    exact = not missing and not duplicates and not orphans
    availability = successes / len(eligible) if exact and eligible else None
    latencies = [item.latency_seconds for item in canonical]
    duplicate_receipts = sum(max(0, counts[job_id] - 1) for job_id in expected)
    accounted = len(expected & observed)
    return RenderAccountingReport(
        expected_jobs=len(expected),
        unique_terminal_jobs=accounted,
        terminal_receipts=len(observations),
        missing_job_ids=missing,
        duplicate_job_ids=duplicates,
        orphan_job_ids=orphans,
        lost_jobs=len(missing),
        duplicate_terminal_receipts=duplicate_receipts,
        terminal_accounting_rate=accounted / len(expected) if expected else 1.0,
        success_availability=availability,
        latency_seconds={
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
        exact_accounting=exact,
    )


__all__ = [
    "BaselineCorpusContract",
    "CorpusBucket",
    "FROZEN_BASELINE_CORPUS",
    "MeasureFirstGate",
    "QueueMetricSample",
    "RenderAccountingReport",
    "RenderMetricObservation",
    "account_terminal_receipts",
]
