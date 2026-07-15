# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hwpx_mcp_server.workspace import (
    LEGACY_SANDBOX_ROOT_ENV,
    WORKSPACE_ROOTS_ENV,
    WorkspaceConfigurationError,
    WorkspacePathError,
    WorkspaceResolver,
)


def test_relative_absolute_and_multi_root_resolution(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    (primary / "inside.hwpx").write_bytes(b"primary")
    (secondary / "inside.hwpx").write_bytes(b"secondary")
    resolver = WorkspaceResolver.from_roots([primary, secondary])

    assert resolver.resolve("inside.hwpx") == primary / "inside.hwpx"
    assert resolver.resolve(secondary / "inside.hwpx") == secondary / "inside.hwpx"
    assert resolver.display_path(primary / "inside.hwpx") == "inside.hwpx"
    assert resolver.display_path(secondary / "inside.hwpx") == str(secondary / "inside.hwpx")


def test_missing_output_parent_is_created_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    resolver = WorkspaceResolver.from_roots([root])

    output = resolver.resolve_output("new/deep/result.hwpx")

    assert output == root / "new/deep/result.hwpx"
    assert output.parent.is_dir()


def test_outside_traversal_and_symlink_escape_are_denied(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.hwpx").write_bytes(b"secret")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    resolver = WorkspaceResolver.from_roots([root])

    for value in (outside / "secret.hwpx", "../outside/secret.hwpx", "escape/secret.hwpx"):
        with pytest.raises(WorkspacePathError) as exc_info:
            resolver.resolve(value)
        assert exc_info.value.code == "WORKSPACE_OUTSIDE_ROOT"
        assert "secret.hwpx" not in str(exc_info.value)

    with pytest.raises(WorkspacePathError):
        resolver.resolve_output("escape/new/result.hwpx")
    assert not (outside / "new").exists()


def test_environment_json_roots_and_legacy_single_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv(WORKSPACE_ROOTS_ENV, json.dumps([str(first), str(second)]))
    monkeypatch.setenv(LEGACY_SANDBOX_ROOT_ENV, str(tmp_path / "ignored"))

    resolver = WorkspaceResolver.from_environment()

    assert resolver.roots == (first, second)
    assert resolver.source == WORKSPACE_ROOTS_ENV

    monkeypatch.delenv(WORKSPACE_ROOTS_ENV)
    monkeypatch.setenv(LEGACY_SANDBOX_ROOT_ENV, str(first))
    assert WorkspaceResolver.from_environment().roots == (first,)


def test_process_cwd_is_bounded_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WORKSPACE_ROOTS_ENV, raising=False)
    monkeypatch.delenv(LEGACY_SANDBOX_ROOT_ENV, raising=False)
    resolver = WorkspaceResolver.from_environment(cwd=tmp_path)
    assert resolver.roots == (tmp_path,)
    assert resolver.source == "process-cwd"


@pytest.mark.parametrize("root", [Path("/"), Path(os.path.abspath(os.sep))])
def test_filesystem_root_authorization_is_rejected(root: Path) -> None:
    with pytest.raises(WorkspaceConfigurationError):
        WorkspaceResolver.from_roots([root])
