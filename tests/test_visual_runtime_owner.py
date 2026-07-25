# SPDX-License-Identifier: Apache-2.0
"""Canonical rendering inventory, production routing, and contract receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hwpx_mcp_server.handlers import specialized
from hwpx_mcp_server.office import rendering
from hwpx_mcp_server.office.rendering import page_qa, worker
from hwpx_mcp_server.tool_contract import (
    contract_hash,
    expected_tool_names,
    skill_required_tool_names,
)
from hwpx_mcp_server.visual_qa import CoreDeterministicAdapter

ROOT = Path(__file__).resolve().parents[1]
OWNER_ROOT = ROOT / "src" / "hwpx_mcp_server" / "office" / "rendering"
OWNER = json.loads(
    (ROOT / "docs" / "architecture" / "visual-runtime-owner.json").read_text(
        encoding="utf-8"
    )
)


def _manifest() -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for path in sorted(OWNER_ROOT.glob("*.py")):
        payload = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "loc": len(payload.splitlines()),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return rows


def test_canonical_rendering_inventory_is_exact() -> None:
    rows = _manifest()
    canonical = OWNER["canonical"]
    payload = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert [Path(str(row["path"])).name for row in rows] == OWNER["packageFiles"]
    assert len(rows) == canonical["pythonFiles"] == 6
    assert sum(int(row["loc"]) for row in rows) == canonical["loc"] == 2001
    assert hashlib.sha256(payload).hexdigest() == canonical["manifestSha256"]
    assert canonical["status"] == "canonical"


def test_production_runtime_symbols_resolve_to_mcp_owner() -> None:
    bindings = (
        rendering.resolve_oracle,
        rendering.MacHancomOracle,
        rendering.NullOracle,
        page_qa.inspect_page_png,
        worker.SerializedHancomWorker,
    )

    for binding in bindings:
        assert binding.__module__.startswith(
            "hwpx_mcp_server.office.rendering"
        ), binding
    assert specialized.resolve_oracle is rendering.resolve_hancom_oracle
    assert CoreDeterministicAdapter.__module__ == "hwpx_mcp_server.visual_qa"


def test_tool_contract_is_exactly_unchanged() -> None:
    assert len(expected_tool_names(advanced=False)) == 119
    assert len(expected_tool_names(advanced=True)) == 127
    assert len(skill_required_tool_names()) == 28
    assert contract_hash() == "e592ede5b0eb1a35"
