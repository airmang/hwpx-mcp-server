# SPDX-License-Identifier: Apache-2.0
"""Hancom-bound redline verification at the MCP application boundary."""

from __future__ import annotations

from os import PathLike, fspath
from typing import Any

from hwpx.tools.redline import inspect_redline_structure

from ..rendering import RenderBackend, resolve_oracle, visual_check

REDLINE_VERIFY_REPORT_VERSION = "redline-verify-v1"


def verify_redline(
    before_hwpx: str | PathLike[str],
    after_hwpx: str | PathLike[str],
    *,
    oracle: RenderBackend | None = None,
) -> dict[str, Any]:
    """Combine neutral structure evidence with canonical Hancom rendering."""

    structural = inspect_redline_structure(after_hwpx)
    warnings = list(structural["warnings"])
    backend = oracle if oracle is not None else resolve_oracle()
    visual_report = visual_check(
        fspath(before_hwpx),
        fspath(after_hwpx),
        oracle=backend,
    )
    warnings.extend(visual_report.warnings)
    warnings.extend(f"visual error: {error}" for error in visual_report.errors)
    warnings.extend(_visual_signal_warnings(visual_report))

    render_checked = bool(visual_report.render_checked)
    opens_clean: bool | None
    if render_checked:
        opens_clean = True
    elif visual_report.errors:
        opens_clean = False
    else:
        opens_clean = None

    return {
        "report_version": REDLINE_VERIFY_REPORT_VERSION,
        "changeCount": structural["changeCount"],
        "changesByType": structural["changesByType"],
        "marksLinked": structural["marksLinked"],
        "displayEnabled": structural["displayEnabled"],
        "opensClean": opens_clean if structural["opensClean"] else False,
        "render_checked": render_checked,
        "visual_ok": visual_report.ok if render_checked else None,
        "warnings": warnings,
    }


def _visual_signal_warnings(report: Any) -> list[str]:
    warnings: list[str] = []
    if report.unexpected_diff_outside_mask:
        warnings.append("visual signal: unexpected_diff_outside_mask")
    if report.overlap_detected:
        warnings.append("visual signal: overlap_detected")
    if report.overflow_detected:
        warnings.append("visual signal: overflow_detected")
    if report.table_break_detected:
        warnings.append("visual signal: table_break_detected")
    if report.page_count_changed:
        warnings.append("visual signal: page_count_changed")
    return warnings


__all__ = ["REDLINE_VERIFY_REPORT_VERSION", "verify_redline"]
