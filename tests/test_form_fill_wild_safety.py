# SPDX-License-Identifier: Apache-2.0
"""Checked-in wild-form parity, immutability, and open-safety gates."""
from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import Any

import hwpx
import pytest
from hwpx.fill_residue import inspect_fill_residue as frozen_residue
from hwpx.formfill_quality import score_form_fill as frozen_score
from hwpx.guidance_scan import scan_form_guidance as frozen_guidance

from hwpx_mcp_server.office.form_fill.fill_residue import (
    inspect_fill_residue,
)
from hwpx_mcp_server.office.form_fill.guidance import scan_form_guidance
from hwpx_mcp_server.office.form_fill.quality import score_form_fill
from hwpx_mcp_server.storage import build_hwpx_open_safety_report


CORE_ROOT = Path(hwpx.__file__).resolve().parents[2]
WILD_FORMS = (
    (
        CORE_ROOT
        / "tests"
        / "fixtures"
        / "m105_evalplan"
        / "blank_form_3hak.hwpx",
        True,
    ),
    (
        CORE_ROOT
        / "tests"
        / "fixtures"
        / "m2_corpus"
        / "public_official_table.hwpx",
        True,
    ),
    (
        CORE_ROOT
        / "tests"
        / "fixtures"
        / "reader_robustness"
        / "irb_form_blank.hwpx",
        False,
    ),
)
pytestmark = pytest.mark.skipif(
    not all(path.is_file() for path, _expected in WILD_FORMS),
    reason="checked-in python-hwpx wild-form fixtures are unavailable",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: _plain(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@pytest.mark.parametrize(
    ("source", "expected_open_safety"),
    WILD_FORMS,
    ids=[path.stem for path, _expected in WILD_FORMS],
)
def test_wild_form_read_only_behavior_is_exact_and_non_mutating(
    source: Path,
    expected_open_safety: bool,
) -> None:
    before = (_sha256(source), source.stat().st_mtime_ns)

    canonical_guidance = _plain(scan_form_guidance(source))
    compatibility_guidance = _plain(frozen_guidance(source))
    canonical_residue = inspect_fill_residue(source, source).to_dict()
    compatibility_residue = frozen_residue(source, source).to_dict()
    canonical_score = score_form_fill(
        source,
        source,
        source,
        run_render=False,
    ).to_dict()
    compatibility_score = frozen_score(
        source,
        source,
        source,
        run_render=False,
    ).to_dict()

    assert canonical_guidance == compatibility_guidance
    assert canonical_residue == compatibility_residue
    assert canonical_score == compatibility_score
    assert (_sha256(source), source.stat().st_mtime_ns) == before
    assert build_hwpx_open_safety_report(source)["ok"] is expected_open_safety
