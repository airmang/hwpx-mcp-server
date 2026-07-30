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


def test_6_1_contract_delta_is_additive_and_matches_the_live_contract() -> None:
    delta = _load("tool-contract-delta-6.1.0.json")
    contract = _load("tool-contract.generated.json")

    assert delta["target"]["contractHash"] == contract["contractHash"] == contract_hash()
    assert contract_hash() == RELEASED_CONTRACT_HASH == "ac1a422376b5ac84"

    assert delta["baseline"]["contractHash"] == "0ce938371f0b55a6"
    assert delta["baseline"]["defaultToolCount"] == 119
    assert delta["baseline"]["advancedToolCount"] == 127
    assert delta["target"]["defaultToolCount"] == 120
    assert delta["target"]["advancedToolCount"] == 128
    assert delta["target"]["skillRequiredToolCount"] == 28

    assert delta["delta"]["addedTools"] == ["add_form_field"]
    assert delta["delta"]["removedTools"] == []
    assert delta["delta"]["promotedTools"] == []
    assert delta["delta"]["profileMoves"] == []

    tools = {tool["name"]: tool for tool in contract["tools"]}
    added = tools["add_form_field"]
    assert added["profile"] == "default"
    assert added["classification"] == "public"
    assert added["mutates"] is True
    assert added["skillRequired"] is False

    assert contract["minAutomationVersion"] == contract["minMcpVersion"] == "6.1.0"
    assert contract["minPythonHwpx"] == "5.1.0"
    assert contract["minSkillVersion"] == "1.1.0"


def test_6_0_delta_receipt_chains_into_the_6_1_baseline() -> None:
    """The historical 6.0.0 receipt stays frozen; its target must be exactly
    the 6.1.0 baseline so the release hash chain has no gap."""

    superseded = _load("tool-contract-delta-6.0.0.json")
    delta = _load("tool-contract-delta-6.1.0.json")
    assert superseded["hash"] == delta["baseline"]["contractHash"]
    assert superseded["toolCounts"] == {
        "default": delta["baseline"]["defaultToolCount"],
        "advanced": delta["baseline"]["advancedToolCount"],
        "skillRequired": delta["baseline"]["skillRequiredToolCount"],
    }
