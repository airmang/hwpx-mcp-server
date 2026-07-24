# SPDX-License-Identifier: Apache-2.0
"""Exact parity between the MCP owner and the frozen core 4.x compatibility API."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

import hwpx.visual.fixture_corpus as core_fixture
import hwpx.visual.hancom_worker as core_worker
import hwpx.visual.oracle as core_oracle
import hwpx.visual.page_qa as core_page_qa
import hwpx.visual.qa_metrics as core_metrics
from hwpx_mcp_server.office.rendering import fixture_corpus as mcp_fixture
from hwpx_mcp_server.office.rendering import oracle as mcp_oracle
from hwpx_mcp_server.office.rendering import page_qa as mcp_page_qa
from hwpx_mcp_server.office.rendering import qa_metrics as mcp_metrics
from hwpx_mcp_server.office.rendering import worker as mcp_worker

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads(
    (
        ROOT
        / "tests"
        / "visual_runtime_parity"
        / "fixtures"
        / "scenarios.json"
    ).read_text(encoding="utf-8")
)


def _scenario(identifier: str) -> dict[str, object]:
    return next(
        item for item in SCENARIOS["scenarios"] if item["id"] == identifier
    )


def _clean_fixture(tmp_path: Path) -> Path:
    page = tmp_path / "page.png"
    image = Image.new("RGB", (400, 600), "white")
    draw = ImageDraw.Draw(image)
    for offset in range(150):
        draw.point(
            (80 + (offset * 17) % 240, 80 + (offset * 29) % 440),
            fill="black",
        )
    image.save(page)
    manifest = {
        "schema": "hwpx.visual-fixture-manifest/v1",
        "taxonomyVersion": "hwpx-visual-defects/1.0",
        "assurance": "fixture",
        "cases": [
            {
                "id": "clean-parity",
                "classification": "clean",
                "pages": [
                    {
                        "page": 0,
                        "path": page.name,
                        "sha256": hashlib.sha256(page.read_bytes()).hexdigest(),
                    }
                ],
                "annotations": [],
                "provenance": {"kind": "synthetic-parity"},
            }
        ],
    }
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(manifest), encoding="utf-8")
    return target


def test_frozen_scenario_corpus_covers_all_required_paths() -> None:
    assert SCENARIOS["schema"] == "hwpx.visual-runtime-parity/v1"
    assert {item["id"] for item in SCENARIOS["scenarios"]} == {
        "structural-only",
        "oracle-unreachable",
        "reachable-mac-selection",
        "bounded-timeout",
        "deterministic-worker",
        "fixture-qa",
    }


def test_structural_only_and_unreachable_reports_are_exact(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HWPX_ORACLE_STRUCTURAL_ONLY", "1")
    core_backend = core_oracle.resolve_oracle()
    mcp_backend = mcp_oracle.resolve_oracle()
    expected = _scenario("structural-only")

    assert type(core_backend).__name__ == type(mcp_backend).__name__ == (
        expected["expectedBackend"]
    )
    core_report = core_oracle.visual_check(
        "before.hwpx",
        "after.hwpx",
        oracle=core_backend,
    )
    mcp_report = mcp_oracle.visual_check(
        "before.hwpx",
        "after.hwpx",
        oracle=mcp_backend,
    )
    assert core_report.to_dict() == mcp_report.to_dict()
    assert core_report.render_checked is expected["renderChecked"]
    assert str(expected["terminalReason"]) in core_report.warnings[0]


def test_reachable_selection_and_exhausted_budget_are_exact_and_non_mutating(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("HWPX_ORACLE_STRUCTURAL_ONLY", raising=False)
    monkeypatch.setattr(
        core_oracle.WindowsComOracle,
        "available",
        lambda self: False,
    )
    monkeypatch.setattr(
        mcp_oracle.WindowsComOracle,
        "available",
        lambda self: False,
    )
    monkeypatch.setattr(core_oracle.MacHancomOracle, "available", lambda self: True)
    monkeypatch.setattr(mcp_oracle.MacHancomOracle, "available", lambda self: True)

    core_backend = core_oracle.resolve_oracle(budget_seconds=0.0)
    mcp_backend = mcp_oracle.resolve_oracle(budget_seconds=0.0)
    expected = _scenario("reachable-mac-selection")
    assert type(core_backend).__name__ == type(mcp_backend).__name__ == (
        expected["expectedBackend"]
    )
    assert core_backend.budget_seconds == mcp_backend.budget_seconds == 0.0

    source = tmp_path / "input.hwpx"
    source.write_bytes(b"immutable-input")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    assert core_backend.render_pdf(str(source), str(tmp_path / "core.pdf")) is None
    assert mcp_backend.render_pdf(str(source), str(tmp_path / "mcp.pdf")) is None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not (tmp_path / "core.pdf").exists()
    assert not (tmp_path / "mcp.pdf").exists()


def test_fixture_loader_page_qa_and_metrics_projections_are_exact(
    tmp_path: Path,
) -> None:
    manifest = _clean_fixture(tmp_path)
    core_corpus = core_fixture.load_fixture_manifest(manifest)
    mcp_corpus = mcp_fixture.load_fixture_manifest(manifest)

    assert core_corpus.receipt(core_corpus.cases[0]) == mcp_corpus.receipt(
        mcp_corpus.cases[0]
    )
    assert core_page_qa.inspect_fixture_case(
        core_corpus.cases[0]
    ).to_dict() == mcp_page_qa.inspect_fixture_case(
        mcp_corpus.cases[0]
    ).to_dict()
    assert core_metrics.measure_fixture_corpus(
        core_corpus
    ) == mcp_metrics.measure_fixture_corpus(mcp_corpus)


def test_serialized_worker_success_and_hash_failure_are_exact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.hwpx"
    source.write_bytes(b"worker-parity")

    def fake_rasterize(_pdf: Path, destination: Path, _dpi: int) -> list[Path]:
        page = destination / "page-0001.png"
        page.write_bytes(b"PNG-PARITY")
        return [page]

    monkeypatch.setattr(
        core_worker.SerializedHancomWorker,
        "_rasterize",
        staticmethod(fake_rasterize),
    )
    monkeypatch.setattr(
        mcp_worker.SerializedHancomWorker,
        "_rasterize",
        staticmethod(fake_rasterize),
    )
    digest = core_worker.sha256_file(source)
    core = core_worker.SerializedHancomWorker(
        tmp_path / "core-worker",
        session_factory=core_worker.DeterministicFakeSession,
        worker_version="parity",
    )
    mcp = mcp_worker.SerializedHancomWorker(
        tmp_path / "mcp-worker",
        session_factory=mcp_worker.DeterministicFakeSession,
        worker_version="parity",
    )
    try:
        core_success = core.render(
            core_worker.WorkerJob("success", source, digest)
        )
        mcp_success = mcp.render(mcp_worker.WorkerJob("success", source, digest))
        assert json.loads(core_success.to_json()) == json.loads(
            mcp_success.to_json()
        )
        assert core_success.terminal_reason == _scenario(
            "deterministic-worker"
        )["terminalReason"]

        core_failure = core.render(
            core_worker.WorkerJob("failure", source, "sha256:wrong")
        )
        mcp_failure = mcp.render(
            mcp_worker.WorkerJob("failure", source, "sha256:wrong")
        )
        assert json.loads(core_failure.to_json()) == json.loads(
            mcp_failure.to_json()
        )
        assert core_failure.terminal_reason == "INPUT_HASH_MISMATCH"
    finally:
        core.close()
        mcp.close()
