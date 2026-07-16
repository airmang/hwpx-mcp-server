from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hwpx_mcp_server import server
from hwpx_mcp_server.document_state import document_revision
from hwpx_mcp_server.visual_qa import (
    FixtureVisualReviewReceipt,
    _canonical_hash,
    repair_fixture,
    review_fixture,
)
from hwpx_mcp_server.tool_contract import BASELINE_TOOL_SPECS, ToolClassification


def _png(path: Path) -> str:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (400, 600), "white")
    draw = ImageDraw.Draw(image)
    for offset in range(150):
        draw.point((80 + (offset * 17) % 240, 80 + (offset * 29) % 440), fill="black")
    image.save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(tmp_path: Path) -> tuple[Path, dict]:
    page = tmp_path / "page.png"
    digest = _png(page)
    data = {
        "schema": "hwpx.visual-fixture-manifest/v1",
        "taxonomyVersion": "hwpx-visual-defects/1.0",
        "assurance": "fixture",
        "cases": [
            {
                "id": "case-1",
                "classification": "clean",
                "pages": [{"page": 0, "path": page.name, "sha256": digest}],
                "annotations": [],
                "provenance": {"kind": "test"},
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


def _finding(*, finding_id: str = "provider-warning", severity: str = "warning") -> dict:
    return {
        "findingId": finding_id,
        "bbox": [0.2, 0.3, 0.7, 0.5],
        "category": "leftover_guidance_placeholder_sample",
        "severity": severity,
        "confidence": 0.94,
        "evidenceHash": "sha256:" + "1" * 64,
    }


def _runtime_round(page_sha: str, findings: list[dict], *, include_target: bool = True) -> dict:
    page = {
        "page": 0,
        "path": "page.png",
        "sha256": page_sha,
        "targetMap": (
            [{"bbox": [0.2, 0.3, 0.7, 0.5], "kind": "paragraph", "targetId": "p-1"}]
            if include_target
            else []
        ),
    }
    return {
        "expectedPageCount": 1,
        "pages": [page],
        "adapters": ["provider-a"],
        "findingsByAdapter": {"provider-a": {"0": findings}},
    }


def test_fixture_receipt_can_never_be_promoted() -> None:
    with pytest.raises(ValueError, match="never claim"):
        FixtureVisualReviewReceipt(
            manifestHash="sha256:x",
            fixtureSetId="case",
            taxonomyVersion="v1",
            reviewedAt="now",
            expectedPages=1,
            reviewedPages=(0,),
            coverageComplete=True,
            adapterIds=("adapter",),
            renderChecked=True,
        )


def test_full_page_clean_fixture_is_honestly_unverified(tmp_path: Path) -> None:
    manifest, _ = _manifest(tmp_path)
    result = review_fixture(manifest, case_id="case-1")

    assert not result["coverage"]["adapterErrors"], result["coverage"]["adapterErrors"]
    assert result["ok"] is True, result
    assert result["coverage"]["reviewedPages"] == [0]
    assert result["visualReviewReceipt"]["receiptKind"] == "deterministic_fixture"
    assert result["renderChecked"] is False
    assert result["realHancomVerified"] is False
    assert result["verificationStatus"] == "structurally_verified_render_unverified"


def test_fusion_preserves_provider_disagreement_and_unmapped_critical_fails_closed(tmp_path: Path) -> None:
    manifest, data = _manifest(tmp_path)
    digest = data["cases"][0]["pages"][0]["sha256"]
    result = review_fixture(
        manifest,
        case_id="case-1",
        strict=True,
        runtime_round=_runtime_round(digest, [_finding(severity="critical")], include_target=False),
    )

    assert result["verdict"] == "needs_review"
    assert result["criticalFindingCount"] == 1
    assert result["unmappedCriticalFindingIds"]
    assert result["disagreements"][0]["supportingAdapters"] == ["provider-a"]
    assert result["findings"][0]["provenance"] == [
        {"adapterId": "provider-a", "sourceFindingId": "provider-warning"}
    ]


def test_missing_page_and_missing_adapter_result_never_pass(tmp_path: Path) -> None:
    manifest, _ = _manifest(tmp_path)
    runtime = {"expectedPageCount": 2, "pages": [], "adapters": ["provider-a"], "findingsByAdapter": {}}
    result = review_fixture(manifest, case_id="case-1", runtime_round=runtime)

    assert result["ok"] is False
    assert result["coverage"]["missingPages"] == [0, 1]
    assert "incomplete_page_or_adapter_coverage" in result["failReasons"]


def test_repair_loop_is_revision_guarded_capped_and_rolls_back_regression(tmp_path: Path) -> None:
    manifest, data = _manifest(tmp_path)
    digest = data["cases"][0]["pages"][0]["sha256"]
    source = tmp_path / "source.hwpx"
    source.write_bytes(b"ORIGINAL")
    output = tmp_path / "output.hwpx"
    plan = {
        "schema": "hwpx.visual-repair-plan/v1",
        "manifestHash": _canonical_hash(data),
        "caseId": "case-1",
        "initialReview": _runtime_round(digest, [_finding()]),
        "rounds": [
            {
                "repairs": [
                    {
                        "findingId": "provider-warning",
                        "type": "replace_text",
                        "findText": "ORIGINAL",
                        "replaceText": "FIXED",
                    }
                ],
                "rerender": _runtime_round(digest, [_finding(finding_id="new-regression")]),
            }
        ],
    }
    plan_path = tmp_path / "repair-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def apply(target: Path, _repair: dict, _revision: str, _key: str) -> dict:
        target.write_bytes(b"FIXED")
        return {"replaced_count": 1}

    stale = repair_fixture(
        filename=source,
        manifest_path=manifest,
        repair_plan_path=plan_path,
        case_id="case-1",
        output_path=output,
        expected_revision="sha256:stale",
        idempotency_key="repair-key",
        max_rounds=1,
        apply_repair=apply,
    )
    assert stale["verdict"] == "blocked"

    with pytest.raises(ValueError, match="hard cap"):
        repair_fixture(
            filename=source,
            manifest_path=manifest,
            repair_plan_path=plan_path,
            case_id="case-1",
            output_path=output,
            expected_revision=document_revision(source),
            idempotency_key="repair-key",
            max_rounds=4,
            apply_repair=apply,
        )

    result = repair_fixture(
        filename=source,
        manifest_path=manifest,
        repair_plan_path=plan_path,
        case_id="case-1",
        output_path=output,
        expected_revision=document_revision(source),
        idempotency_key="repair-key",
        max_rounds=1,
        apply_repair=apply,
    )
    assert result["rolledBack"] is True
    assert output.read_bytes() == b"ORIGINAL"
    assert result["repairLedger"]["schema"] == "hwpx.visual-repair-ledger/v1"
    assert result["ledger"][0]["status"] == "rolled_back"


def test_visual_fixture_tools_remain_internal_ci_library() -> None:
    names = set(server._fastmcp_tool_names())
    internal = {
        spec.name
        for spec in BASELINE_TOOL_SPECS
        if spec.classification is ToolClassification.INTERNAL
    }
    assert {"visual_review_fixture", "visual_repair_fixture"} <= internal
    assert {"visual_review_fixture", "visual_repair_fixture"}.isdisjoint(names)
    assert not hasattr(server, "visual_review_fixture")
    assert not hasattr(server, "visual_repair_fixture")
