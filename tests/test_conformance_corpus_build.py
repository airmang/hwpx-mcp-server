from __future__ import annotations

import json
from pathlib import Path

from conformance import ConformanceCorpus, diff_golden, run_conformance
from conformance_corpus_build import build_corpus


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "conformance" / "golden" / "structural.json"


def test_generator_owns_the_runnable_structural_corpus(tmp_path: Path) -> None:
    manifest = build_corpus(tmp_path / "corpus")
    data = json.loads(manifest.read_text(encoding="utf-8"))

    assert data["name"] == "public"
    assert {case["path"] for case in data["cases"]} == {
        "notice.hwpx",
        "report_table.hwpx",
        "meeting_summary.hwpx",
    }
    assert all((manifest.parent / case["path"]).is_file() for case in data["cases"])

    report = run_conformance(ConformanceCorpus.load(manifest), tier="structural")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert report.ok is True
    assert diff_golden(golden, report) == []
