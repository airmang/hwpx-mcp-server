#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the public repository-QA conformance corpus.

The structural corpus is owned by ``python-hwpx-automation`` alongside its
runner. It contains only synthetic python-hwpx outputs; private Hancom-saved
oracle inputs are never generated or committed here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from hwpx import HwpxDocument


DEFAULT_CORPUS_DIR = Path(__file__).resolve().parent / "conformance" / "corpus"


def _notice() -> bytes:
    document = HwpxDocument.new()
    document.add_paragraph("2026학년도 학교 운영 계획 알림")
    document.add_paragraph("학부모님께 다음과 같이 운영 계획을 안내합니다.")
    document.add_paragraph("가정의 평안과 건강을 기원합니다.")
    return document.to_bytes()


def _report_table() -> bytes:
    document = HwpxDocument.new()
    document.add_paragraph("분기 실적 보고")
    table = document.add_table(2, 3, width=42000)
    for column, value in enumerate(("구분", "내용", "비고")):
        table.cell(0, column).text = value
    for column, value in enumerate(("매출", "1억원", "달성")):
        table.cell(1, column).text = value
    return document.to_bytes()


def _meeting_summary() -> bytes:
    document = HwpxDocument.new()
    document.add_paragraph("회의 결과 요약")
    document.add_paragraph("참석자 전원이 안건에 동의하였습니다.")
    return document.to_bytes()


CASES: tuple[tuple[Callable[[], bytes], str, dict[str, object]], ...] = (
    (
        _notice,
        "notice.hwpx",
        {
            "id": "public-notice",
            "path": "notice.hwpx",
            "mustContain": ["운영 계획", "안내"],
            "mustNotContain": ["{{"],
            "note": "official-notice style body; open + semantic tiers",
        },
    ),
    (
        _report_table,
        "report_table.hwpx",
        {
            "id": "public-report-table",
            "path": "report_table.hwpx",
            "mustContain": ["분기 실적", "매출"],
            "formSlots": [
                {
                    "table": 0,
                    "row": 1,
                    "col": 1,
                    "value": "1억원",
                    "maxLines": 1,
                    "label": "매출-값",
                },
                {
                    "table": 0,
                    "row": 1,
                    "col": 0,
                    "value": "매출",
                    "maxLines": 1,
                    "label": "구분-값",
                },
            ],
            "note": "table body; open + semantic + form-fit tiers",
        },
    ),
    (
        _meeting_summary,
        "meeting_summary.hwpx",
        {
            "id": "public-meeting-summary",
            "path": "meeting_summary.hwpx",
            "mustContain": ["회의 결과", "참석자"],
            "note": "plain body; open + semantic tiers",
        },
    ),
)


def build_corpus(output: Path = DEFAULT_CORPUS_DIR) -> Path:
    """Generate all cases and return the manifest path."""

    output.mkdir(parents=True, exist_ok=True)
    for builder, filename, _case in CASES:
        payload = builder()
        (output / filename).write_bytes(payload)
        print(f"wrote {filename} ({len(payload)} bytes)")
    manifest = output / "corpus.json"
    manifest.write_text(
        json.dumps(
            {"name": "public", "cases": [case for _, _, case in CASES]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote corpus.json ({len(CASES)} cases)")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the python-hwpx-automation structural QA corpus."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CORPUS_DIR,
        help="corpus output directory (default: scripts/conformance/corpus)",
    )
    args = parser.parse_args(argv)
    build_corpus(args.output.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
