from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import hwpx_automation.server as server
from hwpx.tools.redline import verify_redline
from hwpx_automation.core.document import open_doc
from hwpx_automation.fastmcp_adapter import snapshot_runtime_tools
from hwpx_automation.office.rendering import oracle as _mcp_oracle


@pytest.fixture
def _force_structural_oracle_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the honest no-oracle degrade path so redline verification is deterministic.

    On a macOS+Hancom dev box a live oracle is reachable, so the tool attempts a
    real (~12s each) Hancom render whose success is nondeterministic. When that
    render intermittently fails to emit a PDF the receipt reports
    ``opensClean=False`` and this test flakes (~1 in 5 under load). It verifies
    redline *structure*, not rendering, and its assertions already tolerate the
    degrade, so the backend is forced unavailable — matching the suite convention
    that live-oracle checks are opt-in via HWPX_MAC_ORACLE_SMOKE.

    One module is patched, not two. Core's ``verify_redline`` no longer resolves a
    renderer: it takes an injected ``RenderBackend`` and degrades honestly without
    one, so the only backend this test can reach is the canonical owner's. The
    earlier version patched core's oracle as well, which was necessary while both
    copies were live and is now dead weight — and patching a module the runtime
    does not reach is exactly how this fixture became a silent no-op before.
    """

    monkeypatch.setattr(_mcp_oracle.WindowsComOracle, "available", lambda self: False)
    monkeypatch.setattr(_mcp_oracle.MacHancomOracle, "available", lambda self: False)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_redline_source(path: Path) -> int:
    server.create_document(str(path))
    result = server.add_paragraph(str(path), "Alpha delete target with old token.")
    return int(result["paragraph_index"])


def _sample_edits(paragraph_index: int) -> list[dict[str, object]]:
    return [
        {"type": "insert", "paragraph_index": paragraph_index, "text": " inserted"},
        {"type": "delete", "paragraph_index": paragraph_index, "match": "delete target"},
        {"type": "replace", "paragraph_index": paragraph_index, "old": "old token", "new": "new token"},
    ]


def test_add_tracked_edit_tool_is_exposed() -> None:
    assert "add_tracked_edit" in snapshot_runtime_tools(server.mcp)


def test_add_tracked_edit_writes_structural_redline_receipt(
    tmp_path: Path, _force_structural_oracle_degrade: None
) -> None:
    source = tmp_path / "source.hwpx"
    destination = tmp_path / "redlined.hwpx"
    paragraph_index = _build_redline_source(source)
    source_hash = _sha256(source)

    result = server.add_tracked_edit(
        str(source),
        str(destination),
        _sample_edits(paragraph_index),
        author="Reviewer",
        date="2026-06-30T00:00:00Z",
    )

    assert result["ok"] is True
    assert result["applied"] is True
    assert result["written"] is True
    assert destination.exists()
    assert _sha256(source) == source_hash

    receipt = result["redlineReceipt"]
    assert receipt["changeCount"] >= 4
    assert receipt["marksLinked"] is True
    assert receipt["displayEnabled"] is True
    assert receipt["opensClean"] is not False
    assert "render_checked" in receipt

    reopened = open_doc(str(destination))
    try:
        assert len(reopened.track_changes) >= 4
    finally:
        reopened.close()

    verification = verify_redline(source, destination)
    assert verification["changeCount"] >= 4
    assert verification["marksLinked"] is True


def test_add_tracked_edit_rejects_non_hwpx_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    destination = tmp_path / "redlined.txt"
    paragraph_index = _build_redline_source(source)

    result = server.add_tracked_edit(
        str(source),
        str(destination),
        _sample_edits(paragraph_index),
    )

    assert result["ok"] is False
    assert result["handoff_status"] == "unsupported_format"
    assert "only .hwpx is supported" in result["error"]
    assert not destination.exists()


def test_add_tracked_edit_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    destination = tmp_path / "dry-run.hwpx"
    paragraph_index = _build_redline_source(source)
    source_hash = _sha256(source)

    result = server.add_tracked_edit(
        str(source),
        str(destination),
        _sample_edits(paragraph_index),
        dry_run=True,
    )

    assert result["ok"] is True
    assert result["dryRun"] is True
    assert result["written"] is False
    assert result["wouldChangeCount"] == 4
    assert not destination.exists()
    assert _sha256(source) == source_hash


def test_add_tracked_edit_refuses_source_in_place(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    paragraph_index = _build_redline_source(source)
    source_hash = _sha256(source)

    result = server.add_tracked_edit(
        str(source),
        str(source),
        _sample_edits(paragraph_index),
    )

    assert result["ok"] is False
    assert result["reason"] == "source equals destination"
    assert "refuses source-in-place edits" in result["error"]
    assert _sha256(source) == source_hash


def test_add_tracked_edit_validation_failure_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    destination = tmp_path / "invalid.hwpx"
    paragraph_index = _build_redline_source(source)

    result = server.add_tracked_edit(
        str(source),
        str(destination),
        [{"type": "delete", "paragraph_index": paragraph_index, "match": "missing text"}],
    )

    assert result["ok"] is False
    assert result["written"] is False
    assert result["errors"][0]["code"] == "delete_match_not_found"
    assert not destination.exists()
