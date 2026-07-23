# SPDX-License-Identifier: Apache-2.0
"""PII policy injection remains explicit at MCP-owned workflow seams."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from hwpx_mcp_server import form_fill
from hwpx_mcp_server.handlers import specialized
from hwpx_mcp_server.office.compliance import DEFAULT_POLICY, mask_pii


def test_mail_merge_injects_the_canonical_mcp_policy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_mail_merge(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"createdCount": 0, "rowCount": 0, "rows": []}

    template = tmp_path / "template.hwpx"
    template.touch()
    monkeypatch.setattr(specialized, "build_hwpx_mail_merge", fake_mail_merge)

    report = specialized.mail_merge(str(template), data_rows=[])

    assert report["openSafety"]["ok"] is True
    assert captured["kwargs"]["masking_policy"] is DEFAULT_POLICY


def test_form_fill_binds_the_canonical_mcp_masker_and_policy() -> None:
    assert form_fill.mask_pii is mask_pii
    assert form_fill.DEFAULT_POLICY is DEFAULT_POLICY
