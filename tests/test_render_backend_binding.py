# SPDX-License-Identifier: Apache-2.0
"""MCP owns Hancom discovery and injection into the neutral core save gate."""
from __future__ import annotations

from hwpx import HwpxDocument
from hwpx.quality import QualityPolicy, SavePipeline
from hwpx.quality.rendering import VisualReport

from hwpx_mcp_server import quality as quality_contract
from hwpx_mcp_server.office import rendering


class _FakeOracle:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready

    def available(self) -> bool:
        return self.ready


def test_hancom_adapter_runs_visual_check(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str | None, str]] = []

    def _check(before, after, **_options):
        calls.append((before, after))
        return VisualReport(ok=True, render_checked=True)

    monkeypatch.setattr(rendering, "visual_check", _check)
    backend = rendering.HancomRenderBackend(_FakeOracle())
    pipeline = SavePipeline(oracle=backend)
    document = HwpxDocument.new()
    document.add_paragraph("render adapter")

    report = pipeline.run(
        document.to_bytes(),
        output_path=tmp_path / "out.hwpx",
        quality=QualityPolicy(
            render_check="required",
            require_visual_complete=True,
        ),
    )

    assert report.ok is True
    assert report.visual_complete_status == "verified"
    assert len(calls) == 1


def test_quality_save_temporarily_binds_mcp_backend(monkeypatch, tmp_path) -> None:
    document = HwpxDocument.new()
    original = document._save_pipeline
    seen: list[SavePipeline] = []

    def _save_report(path, *, quality):
        seen.append(document._save_pipeline)
        return original.run(
            document.to_bytes(),
            output_path=path,
            quality=QualityPolicy.transparent(),
        )

    monkeypatch.setattr(document, "save_report", _save_report)
    report = quality_contract.save_through_pipeline(
        document,
        tmp_path / "out.hwpx",
        quality=QualityPolicy.transparent(),
    )

    assert report.ok is True
    assert len(seen) == 1
    assert seen[0] is not original
    assert document._save_pipeline is original
