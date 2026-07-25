# SPDX-License-Identifier: Apache-2.0
"""Canonical form-fill inventory and production-routing gates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hwpx_mcp_server.handlers import form_fill, specialized
from hwpx_mcp_server.office import form_fill as canonical
from hwpx_mcp_server.ops_services import form_fields


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ROOT = (
    ROOT / "src" / "hwpx_mcp_server" / "office" / "form_fill"
)
OWNER = json.loads(
    (
        ROOT
        / "docs"
        / "architecture"
        / "form-fill-runtime-owner.json"
    ).read_text(encoding="utf-8")
)


def _manifest() -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for path in sorted(CANONICAL_ROOT.rglob("*.py")):
        data = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "loc": len(data.splitlines()),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return rows


def test_canonical_form_fill_inventory_is_complete() -> None:
    rows = _manifest()
    payload = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    expected = OWNER["canonical"]

    assert len(rows) == expected["pythonFiles"] == 15
    assert sum(int(row["loc"]) for row in rows) == expected["loc"]
    assert hashlib.sha256(payload).hexdigest() == expected["manifestSha256"]
    assert len(canonical.__all__) == 25


def test_all_form_fill_application_bindings_use_the_mcp_owner() -> None:
    """Workflow, seal, and residue behaviour is this layer's and must resolve here."""

    bindings = (
        form_fill.analyze_hwpx_template_formfit,
        form_fill.apply_hwpx_template_formfit,
        specialized.seal_ops.find_seal_anchor,
        specialized.render_glyph_boxes,
        form_fields.inspect_form_fill_residue,
        form_fields.scan_canonical_form_guidance,
        form_fields.score_canonical_form_fill,
    )

    for binding in bindings:
        assert binding.__module__.startswith(
            "hwpx_mcp_server.office.form_fill"
        ), binding


def test_neutral_fit_contract_resolves_to_core_not_to_a_second_copy() -> None:
    """The measurement contract is core's, and this asserts it is not re-forked.

    Before Feature 049 this layer carried its own policy, measure, engine, report
    and apply modules. Comparing normalised syntax trees showed no semantic
    difference from core's, so the copies bought nothing and could drift — which
    is what happened to the render oracle in this same stack. The owner now
    re-exports core's contract, and the check flips accordingly: these bindings
    must come *from core*, so re-introducing a local copy fails here.
    """

    from hwpx_mcp_server.office.form_fill import fit

    for binding in (
        specialized.FitPolicy,
        fit.FitEngine,
        fit.SlotMetrics,
        fit.FitResult,
        fit.measure,
        fit.resolve_slot_metrics,
    ):
        assert binding.__module__.startswith("hwpx.form_fit."), binding


def test_render_oracle_is_resolved_only_through_the_mcp_seam() -> None:
    assert specialized.resolve_oracle.__module__ == (
        "hwpx_mcp_server.office.rendering"
    )
