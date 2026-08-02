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


def test_6_6_1_contract_delta_is_additive_and_matches_the_live_contract() -> None:
    delta = _load("tool-contract-delta-6.6.1.json")
    contract = _load("tool-contract.generated.json")

    assert delta["target"]["contractHash"] == contract["contractHash"] == contract_hash()
    assert contract_hash() == RELEASED_CONTRACT_HASH == "19898dba41495c47"

    assert delta["baseline"]["contractHash"] == "f61d2c60c0aa0413"
    assert delta["baseline"]["defaultToolCount"] == 125
    assert delta["baseline"]["advancedToolCount"] == 133
    assert delta["target"]["defaultToolCount"] == 126
    assert delta["target"]["advancedToolCount"] == 134
    assert delta["target"]["skillRequiredToolCount"] == 29

    assert delta["delta"]["addedTools"] == ["run_edit_plan"]
    assert delta["delta"]["removedTools"] == []
    assert delta["delta"]["promotedTools"] == []
    assert delta["delta"]["profileMoves"] == []

    tools = {tool["name"]: tool for tool in contract["tools"]}
    plan_tool = tools["run_edit_plan"]
    assert plan_tool["profile"] == "default"
    assert plan_tool["classification"] == "public"
    assert plan_tool["mutates"] is True

    assert contract["minAutomationVersion"] == contract["minMcpVersion"] == "6.5.0"
    assert contract["minPythonHwpx"] == "5.6.0"
    assert contract["minSkillVersion"] == "1.6.0"


def test_6_5_1_delta_receipt_is_frozen_and_chains_into_the_6_6_1_baseline() -> None:
    """The historical 6.5.1 receipt stays frozen against its own hashes (not the
    live contract) and its target must be exactly the 6.6.1 baseline."""

    frozen = _load("tool-contract-delta-6.5.1.json")
    delta = _load("tool-contract-delta-6.6.1.json")
    assert frozen["target"]["contractHash"] == "f61d2c60c0aa0413"
    assert frozen["target"]["contractHash"] == delta["baseline"]["contractHash"]
    assert frozen["target"]["defaultToolCount"] == delta["baseline"]["defaultToolCount"]
    assert frozen["target"]["advancedToolCount"] == delta["baseline"]["advancedToolCount"]

    assert frozen["baseline"]["contractHash"] == "dbdbdfaac26148b7"
    assert frozen["baseline"]["defaultToolCount"] == 122
    assert frozen["baseline"]["advancedToolCount"] == 130
    assert frozen["delta"]["addedTools"] == [
        "add_boxed_org_chart", "compose_section_chip", "get_genre_grammar",
    ]


def test_6_4_2_delta_receipt_is_frozen_and_chains_into_the_6_5_baseline() -> None:
    """The historical 6.4.2 receipt stays frozen against its own hashes (not the
    live contract) and its target must be exactly the 6.5.0 baseline."""

    frozen = _load("tool-contract-delta-6.4.2.json")
    delta = _load("tool-contract-delta-6.5.1.json")
    assert frozen["target"]["contractHash"] == "dbdbdfaac26148b7"
    assert frozen["target"]["contractHash"] == delta["baseline"]["contractHash"]
    assert frozen["target"]["defaultToolCount"] == delta["baseline"]["defaultToolCount"]
    assert frozen["target"]["advancedToolCount"] == delta["baseline"]["advancedToolCount"]

    assert frozen["baseline"]["contractHash"] == "236f8ea855c875fe"
    assert frozen["baseline"]["defaultToolCount"] == 122
    assert frozen["baseline"]["advancedToolCount"] == 130
    assert [tool["name"] for tool in frozen["changedTools"]] == ["format_table"]


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
