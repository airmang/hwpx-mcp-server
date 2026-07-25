# SPDX-License-Identifier: Apache-2.0
"""Behavior parity between the MCP owner and the frozen core 4.x copy.

``hwpx.form_fit`` is not being deleted from core — MCP imports the fit family
on purpose as a neutral contract (core's own table/field APIs call it too).
It is compared against live below, unchanged.

``hwpx.guidance_scan`` and ``hwpx.form_fill`` *are* both gone from core as of
python-hwpx 5.0. ``test_instruction_lexicon_parity`` already didn't need a
live import (captured earlier into ``instructionSamplesExpected`` in the
scenarios fixture below). ``test_split_run_scan_fill_and_refusal_parity``
did need one — it compared core's split-run helpers against the MCP owner's.
Its expected outputs are now read from
``tests/parity_fingerprints/form_fill_module.golden.json``, captured from a
scratch git worktree at the commit before removal (see
``scripts/freeze_parity_fingerprints.py --historical``) rather than a live
``hwpx.form_fill`` import.
"""
from __future__ import annotations

import base64
import dataclasses
import json
from pathlib import Path
from typing import Any

from hwpx import form_fit as frozen_fit

from hwpx_mcp_server.office.form_fill import split_run as canonical_split
from hwpx_mcp_server.office.form_fill.fit import (
    FitEngine,
    FitPolicy,
    SlotMetrics,
    estimate_lines,
    estimate_text_width,
    measure,
)
from hwpx_mcp_server.office.form_fill.guidance import (
    is_form_instruction,
)

SCENARIOS = json.loads(
    (
        Path(__file__).parent
        / "form_fill_runtime_parity"
        / "scenarios.json"
    ).read_text(encoding="utf-8")
)
_FIXTURES = Path(__file__).parent / "parity_fingerprints"
FORM_FILL_MODULE_GOLDEN = json.loads(
    (_FIXTURES / "form_fill_module.golden.json").read_text(encoding="utf-8")
)["calls"]


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


def test_split_run_scan_fill_and_refusal_parity() -> None:
    scenario = SCENARIOS["splitRun"]
    section = scenario["section"].encode()
    canonical_found = canonical_split.find_split_placeholders(section)
    assert _plain(canonical_found) == FORM_FILL_MODULE_GOLDEN["foundPlaceholders"]

    canonical_bytes, canonical_report = canonical_split.fill_section_bytes(
        section, scenario["mappings"]
    )
    assert base64.b64encode(canonical_bytes).decode("ascii") == (
        FORM_FILL_MODULE_GOLDEN["filledSectionBase64"]
    )
    assert _plain(canonical_report) == FORM_FILL_MODULE_GOLDEN["fillReport"]

    try:
        canonical_split.find_split_placeholders(scenario["invalidSection"].encode())
    except ValueError as exc:
        assert "invalid section XML" in str(exc)
        assert str(exc) == FORM_FILL_MODULE_GOLDEN["invalidSectionRefusalMessage"]
    else:  # pragma: no cover - explicit refusal gate
        raise AssertionError("invalid XML must be refused")


def test_fit_measurement_and_policy_parity() -> None:
    for case in SCENARIOS["fitCases"]:
        value = case["value"]
        canonical_slot = SlotMetrics(
            available_width=case["availableWidth"],
            font_pt=case["fontPt"],
            max_lines=case["maxLines"],
        )
        frozen_slot = frozen_fit.SlotMetrics(
            available_width=case["availableWidth"],
            font_pt=case["fontPt"],
            max_lines=case["maxLines"],
        )

        assert estimate_text_width(value, case["fontPt"]) == (
            frozen_fit.estimate_text_width(value, case["fontPt"])
        )
        assert estimate_lines(
            value, case["availableWidth"], case["fontPt"]
        ) == frozen_fit.estimate_lines(
            value, case["availableWidth"], case["fontPt"]
        )
        assert _plain(measure(value, canonical_slot)) == _plain(
            frozen_fit.measure(value, frozen_slot)
        )

        canonical_result = FitEngine().fit(
            value,
            canonical_slot,
            FitPolicy(
                mode=case["mode"],
                min_font_pt=case["minFontPt"],
            ),
        )
        frozen_result = frozen_fit.FitEngine().fit(
            value,
            frozen_slot,
            frozen_fit.FitPolicy(
                mode=case["mode"],
                min_font_pt=case["minFontPt"],
            ),
        )
        assert _plain(canonical_result) == _plain(frozen_result)


def test_instruction_lexicon_parity() -> None:
    samples = SCENARIOS["instructionSamples"]
    assert [is_form_instruction(text) for text in samples] == (
        SCENARIOS["instructionSamplesExpected"]
    )
