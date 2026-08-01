from __future__ import annotations

import json
from pathlib import Path

from hwpx_automation.tool_contract import (
    RELEASED_CONTRACT_HASH,
    contract_hash,
)


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    return json.loads((ROOT / "docs" / name).read_text(encoding="utf-8"))


def test_6_4_1_contract_delta_is_additive_and_matches_the_live_contract() -> None:
    delta = _load("tool-contract-delta-6.4.2.json")
    contract = _load("tool-contract.generated.json")

    assert delta["target"]["contractHash"] == contract["contractHash"] == contract_hash()
    assert contract_hash() == RELEASED_CONTRACT_HASH == "dbdbdfaac26148b7"

    assert delta["baseline"]["contractHash"] == "236f8ea855c875fe"
    assert delta["baseline"]["defaultToolCount"] == 122
    assert delta["baseline"]["advancedToolCount"] == 130
    assert delta["target"]["defaultToolCount"] == 122
    assert delta["target"]["advancedToolCount"] == 130
    assert delta["target"]["skillRequiredToolCount"] == 28

    assert delta["delta"]["addedTools"] == []
    assert delta["delta"]["removedTools"] == []
    assert delta["delta"]["promotedTools"] == []
    assert delta["delta"]["profileMoves"] == []
    assert [tool["name"] for tool in delta["changedTools"]] == ["format_table"]

    tools = {tool["name"]: tool for tool in contract["tools"]}
    changed = tools["format_table"]
    assert changed["profile"] == "default"
    assert changed["classification"] == "public"
    assert changed["mutates"] is True
    parameter_names = set(changed["inputSchema"]["properties"])
    assert {
        "border_type", "border_color", "border_width", "fill_color", "row", "col",
    } <= parameter_names

    assert contract["minAutomationVersion"] == contract["minMcpVersion"] == "6.4.0"
    assert contract["minPythonHwpx"] == "5.4.0"
    assert contract["minSkillVersion"] == "1.4.0"


def test_6_3_1_delta_receipt_is_frozen_and_chains_into_the_6_4_1_baseline() -> None:
    """The historical 6.3.1 receipt stays frozen against its own hashes (not the
    live contract) and its target must be exactly the 6.4.1 baseline."""

    frozen = _load("tool-contract-delta-6.3.1.json")
    delta = _load("tool-contract-delta-6.4.2.json")
    assert frozen["target"]["contractHash"] == "236f8ea855c875fe"
    assert frozen["target"]["contractHash"] == delta["baseline"]["contractHash"]
    assert frozen["target"]["defaultToolCount"] == delta["baseline"]["defaultToolCount"]
    assert frozen["target"]["advancedToolCount"] == delta["baseline"]["advancedToolCount"]

    assert frozen["baseline"]["contractHash"] == "342cf672f29cd183"
    assert frozen["baseline"]["defaultToolCount"] == 121
    assert frozen["baseline"]["advancedToolCount"] == 129
    assert frozen["delta"]["addedTools"] == ["add_chart"]


def test_6_2_1_delta_receipt_is_frozen_and_chains_into_the_6_3_baseline() -> None:
    """The historical 6.2.1 receipt stays frozen against its own hashes (not the
    live contract) and its target must be exactly the 6.3.0 baseline."""

    frozen = _load("tool-contract-delta-6.2.1.json")
    delta = _load("tool-contract-delta-6.3.1.json")
    assert frozen["target"]["contractHash"] == "342cf672f29cd183"
    assert frozen["target"]["contractHash"] == delta["baseline"]["contractHash"]
    assert frozen["target"]["defaultToolCount"] == delta["baseline"]["defaultToolCount"]
    assert frozen["target"]["advancedToolCount"] == delta["baseline"]["advancedToolCount"]

    assert frozen["baseline"]["contractHash"] == "ac1a422376b5ac84"
    assert frozen["baseline"]["defaultToolCount"] == 120
    assert frozen["baseline"]["advancedToolCount"] == 128
    assert frozen["delta"]["addedTools"] == ["add_equation"]


def test_6_1_3_delta_receipt_is_frozen_and_chains_into_the_6_2_baseline() -> None:
    """The historical 6.1.3 receipt stays frozen; its target must be exactly
    the 6.2.1 baseline."""

    frozen = _load("tool-contract-delta-6.1.3.json")
    delta = _load("tool-contract-delta-6.2.1.json")
    assert frozen["target"]["contractHash"] == "ac1a422376b5ac84"
    assert frozen["target"]["contractHash"] == delta["baseline"]["contractHash"]
    assert frozen["target"]["defaultToolCount"] == delta["baseline"]["defaultToolCount"]
    assert frozen["target"]["advancedToolCount"] == delta["baseline"]["advancedToolCount"]

    assert frozen["baseline"]["contractHash"] == "0ce938371f0b55a6"
    assert frozen["baseline"]["defaultToolCount"] == 119
    assert frozen["baseline"]["advancedToolCount"] == 127
    assert frozen["delta"]["addedTools"] == ["add_form_field"]


def test_6_0_delta_receipt_chains_into_the_6_1_baseline() -> None:
    """The historical 6.0.0 receipt stays frozen; its target must be exactly
    the 6.1.0 baseline so the release hash chain has no gap."""

    superseded = _load("tool-contract-delta-6.0.0.json")
    delta = _load("tool-contract-delta-6.1.3.json")
    assert superseded["hash"] == delta["baseline"]["contractHash"]
    assert superseded["toolCounts"] == {
        "default": delta["baseline"]["defaultToolCount"],
        "advanced": delta["baseline"]["advancedToolCount"],
        "skillRequired": delta["baseline"]["skillRequiredToolCount"],
    }
