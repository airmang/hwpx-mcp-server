from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from hwpx_automation.tool_contract import (
    RELEASED_CONTRACT_HASH,
    contract_hash,
)


ROOT = Path(__file__).resolve().parents[1]


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_6_0_contract_delta_preserves_tool_surface_from_superseded_candidate() -> None:
    delta = json.loads(
        (ROOT / "docs" / "tool-contract-delta-6.0.0.json").read_text(
            encoding="utf-8"
        )
    )
    contract = json.loads(
        (ROOT / "docs" / "tool-contract.generated.json").read_text(
            encoding="utf-8"
        )
    )
    assert delta["hash"] == contract["contractHash"] == contract_hash()
    assert contract_hash() == RELEASED_CONTRACT_HASH == "0ce938371f0b55a6"
    assert delta["toolCounts"] == {
        "default": 119,
        "advanced": 127,
        "skillRequired": 28,
    }
    assert delta["unchangedFromPrevious"] == [
        "names",
        "order",
        "schemas",
        "classifications",
        "errorContracts",
    ]
    proof = delta["supersededCandidateProof"]
    assert proof["comparedHash"] == "9abec41b740f3e0e"
    assert proof["onlyChangedPayloadFields"] == [
        "minAutomationVersion",
        "contractHash",
    ]
    assert contract["minAutomationVersion"] == contract["minMcpVersion"] == "6.0.0"
    fields = {
        "orderedNames": "name",
        "profiles": "profile",
        "classifications": "classification",
        "skillRequired": "skillRequired",
        "inputSchemas": "inputSchema",
        "outputSchemas": "outputSchema",
        "replacementTools": "replacementTools",
        "mutates": "mutates",
        "schemaBindings": "schemaBinding",
    }
    for receipt_key, tool_key in fields.items():
        assert _digest([tool[tool_key] for tool in contract["tools"]]) == proof[
            "unchangedDigests"
        ][receipt_key]
    advanced_change = delta["descriptionChanges"][-1]
    assert len(advanced_change["tools"]) == 8
    assert set(advanced_change["tools"]) == {
        tool["name"]
        for tool in contract["baselineClassification"]["tools"]
        if tool["profile"] == "advanced"
    }
    assert delta["floors"]["python-hwpx-automation"] == "6.0.0"
