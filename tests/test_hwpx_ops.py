from pathlib import Path

import pytest

from hwpx_mcp_server.fs import WorkdirGuard
from hwpx_mcp_server.hwpx_ops import HwpxOps


@pytest.fixture()
def ops_with_sample(tmp_path) -> tuple[HwpxOps, Path]:
    sample = Path(__file__).with_name("sample.hwpx")
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    target = workdir / "sample.hwpx"
    target.write_bytes(sample.read_bytes())
    guard = WorkdirGuard(workdir)
    ops = HwpxOps(guard, auto_backup=True)
    return ops, target


def test_open_info_counts(ops_with_sample):
    ops, path = ops_with_sample
    info = ops.open_info(str(path))
    assert info["sectionCount"] >= 1
    assert info["paragraphCount"] >= 1
    assert info["meta"]["absolutePath"].endswith("sample.hwpx")


def test_read_text_pagination(ops_with_sample):
    ops, path = ops_with_sample
    result = ops.read_text(str(path), limit=2)
    assert "HWPX" in result["textChunk"]
    assert result["nextOffset"] >= 1


def test_find_returns_matches(ops_with_sample):
    ops, path = ops_with_sample
    matches = ops.find(str(path), "HWPX")
    assert matches["matches"]
    assert any("HWPX" in match["context"] for match in matches["matches"])


def test_replace_text_in_runs_dry_run_does_not_modify(ops_with_sample):
    ops, path = ops_with_sample
    ops.replace_text_in_runs(str(path), "HWPX", "DOCX", dry_run=True)
    text = ops.read_text(str(path), limit=5)["textChunk"]
    assert "DOCX" not in text


def test_replace_text_in_runs_updates_file_and_backup(ops_with_sample):
    ops, path = ops_with_sample
    ops.replace_text_in_runs(str(path), "HWPX", "DOCX", dry_run=False)
    text = ops.read_text(str(path), limit=5)["textChunk"]
    assert "DOCX" in text
    backup = path.with_suffix(path.suffix + ".bak")
    assert backup.exists()


def test_save_as_creates_new_file(ops_with_sample, tmp_path):
    ops, path = ops_with_sample
    out = path.with_name("copy.hwpx")
    result = ops.save_as(str(path), str(out))
    assert Path(result["outPath"]).exists()
