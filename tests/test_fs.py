from pathlib import Path

import pytest

from hwpx_mcp_server.fs import WorkdirError, WorkdirGuard


def test_resolve_path_within_root(tmp_path):
    guard = WorkdirGuard(tmp_path)
    resolved = guard.resolve_path("docs/sample.hwpx", must_exist=False)
    assert resolved == tmp_path / "docs" / "sample.hwpx"


def test_resolve_output_path_creates_parent(tmp_path):
    guard = WorkdirGuard(tmp_path)
    out_path = guard.resolve_output_path("nested/output.hwpx")
    assert out_path.parent.exists()
    assert out_path == tmp_path / "nested" / "output.hwpx"


def test_resolve_path_blocks_escape(tmp_path):
    guard = WorkdirGuard(tmp_path)
    with pytest.raises(WorkdirError):
        guard.resolve_path("../outside.hwpx", must_exist=False)


def test_backup_creates_copy(tmp_path):
    guard = WorkdirGuard(tmp_path)
    target = tmp_path / "document.hwpx"
    target.write_text("payload")
    backup = guard.ensure_backup(target)
    assert backup is not None
    assert backup.read_text() == "payload"
    assert backup.name.endswith(target.name + ".bak")
