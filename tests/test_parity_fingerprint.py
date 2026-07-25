# SPDX-License-Identifier: Apache-2.0
"""fingerprint() must be deterministic: it is the whole basis for comparing
a live module against a JSON file frozen in a previous process."""
from __future__ import annotations

import json

from parity_fingerprint import fingerprint

import hwpx_automation.office.agent as mcp_agent
import hwpx_automation.office.exam.compose as mcp_exam_compose
import hwpx_automation.office.form_fill.guidance as mcp_guidance


def test_fingerprint_is_deterministic_across_repeated_calls() -> None:
    for module in (mcp_agent, mcp_exam_compose, mcp_guidance):
        assert fingerprint(module) == fingerprint(module)


def test_fingerprint_output_is_json_serialisable_and_round_trips() -> None:
    for module in (mcp_agent, mcp_exam_compose, mcp_guidance):
        result = fingerprint(module)
        text = json.dumps(result, sort_keys=True, ensure_ascii=False)
        assert json.loads(text) == result
