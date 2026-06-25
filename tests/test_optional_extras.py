# SPDX-License-Identifier: Apache-2.0
"""Optional dependency extras (rhwp T2 item 7): olefile [hwp] / uvicorn [http]."""

from __future__ import annotations

import pytest

from hwpx_mcp_server import hwp_support


def test_hwp_support_module_imports_without_requiring_olefile() -> None:
    # The module must import even if olefile is absent (guarded import).
    assert hasattr(hwp_support, "extract_hwp_text")


def test_extract_hwp_text_without_olefile_raises_friendly(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(hwp_support, "olefile", None)
    path = tmp_path / "x.hwp"
    path.write_bytes(b"not really ole")
    with pytest.raises(hwp_support.HwpBinaryError) as excinfo:
        hwp_support.extract_hwp_text(path)
    message = str(excinfo.value)
    assert "olefile" in message
    assert "[hwp]" in message
