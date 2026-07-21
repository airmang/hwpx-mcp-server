from __future__ import annotations

import importlib.util
from pathlib import Path
import zipfile

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

import hwpx_mcp_server
from hwpx_mcp_server import server
from hwpx_mcp_server.tool_contract import (
    BASELINE_TOOL_SPECS,
    DOMAIN_SPECS,
    MIN_MCP_VERSION,
    MIN_PYTHON_HWPX,
    MIN_SKILL_VERSION,
    ToolClassification,
    contract_hash,
    expected_tool_names,
    skill_required_tool_names,
)


ROOT = Path(__file__).resolve().parents[1]
REMOVED_PRACTICE_TOOLS = {
    "start_practice_scenario",
    "apply_practice_scenario",
    "start_practice_campaign",
    "get_practice_campaign",
    "continue_practice_campaign",
    "cancel_practice_campaign",
    "export_practice_campaign",
}
INTERNAL_FIXTURE_QA_TOOLS = {
    "run_fixture_benchmark",
    "export_fixture_benchmark",
    "visual_review_fixture",
    "visual_repair_fixture",
}
RETIRED_LEGACY_MODULES = {
    "hwpx_mcp_server.tools": "tools.py",
    "hwpx_mcp_server.legacy_server": "legacy_server.py",
    "hwpx_mcp_server.prompts": "prompts.py",
    "hwpx_mcp_server.logging_conf": "logging_conf.py",
    "hwpx_mcp_server.schema.builder": "schema/builder.py",
    "hwpx_mcp_server.schema.sanitizer": "schema/sanitizer.py",
}


def test_fastmcp_dependency_stays_on_the_audited_minor_line() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    optional_dependencies = project["project"]["optional-dependencies"]

    assert project["project"]["version"] == "5.0.0"
    assert "python-hwpx>=4.0.0" in dependencies
    assert optional_dependencies["oracle"] == ["python-hwpx[visual]>=4.0.0"]
    assert optional_dependencies["vision"] == ["python-hwpx[visual]>=4.0.0"]
    assert "mcp==1.28.1" in dependencies
    assert "pydantic>=2.11,<3" in dependencies


def _load_hygiene_module():
    path = ROOT / "scripts" / "check_public_hygiene.py"
    spec = importlib.util.spec_from_file_location("check_public_hygiene", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_practice_package_is_absent_from_the_import_surface() -> None:
    package_root = Path(hwpx_mcp_server.__file__).resolve().parent

    assert not (package_root / "practice").exists()
    assert importlib.util.find_spec("hwpx_mcp_server.practice") is None


def test_pre_fastmcp_shadow_modules_are_absent_from_the_import_surface() -> None:
    package_root = Path(hwpx_mcp_server.__file__).resolve().parent

    for module_name, relative_path in RETIRED_LEGACY_MODULES.items():
        assert not (package_root / relative_path).exists()
        try:
            spec = importlib.util.find_spec(module_name)
        except ModuleNotFoundError:
            # Nested retired modules are absent when their retired parent
            # package is absent; Python 3.13 reports that state by raising.
            spec = None
        assert spec is None


def test_contract_and_live_registry_exclude_internal_product_boundaries() -> None:
    default = expected_tool_names(advanced=False)
    advanced = expected_tool_names(advanced=True)
    live = set(server._fastmcp_tool_names())

    assert len(default) == 119
    assert len(advanced) == 127
    assert len(skill_required_tool_names()) == 28
    assert (MIN_PYTHON_HWPX, MIN_MCP_VERSION, MIN_SKILL_VERSION) == (
        "4.0.0",
        "5.0.0",
        "0.7.0",
    )
    assert REMOVED_PRACTICE_TOOLS.isdisjoint(default)
    assert REMOVED_PRACTICE_TOOLS.isdisjoint(advanced)
    assert REMOVED_PRACTICE_TOOLS.isdisjoint(live)
    assert INTERNAL_FIXTURE_QA_TOOLS.isdisjoint(default)
    assert INTERNAL_FIXTURE_QA_TOOLS.isdisjoint(advanced)
    assert INTERNAL_FIXTURE_QA_TOOLS.isdisjoint(live)
    assert INTERNAL_FIXTURE_QA_TOOLS == {
        spec.name
        for spec in BASELINE_TOOL_SPECS
        if spec.classification is ToolClassification.INTERNAL
    }
    assert all(domain.key != "private_practice" for domain in DOMAIN_SPECS)

    health = server.mcp_server_health()
    assert health["toolSurface"]["status"] == "ok"
    assert health["toolSurface"]["expectedFastMcpToolCount"] == 119
    assert health["toolSurface"]["actualFastMcpToolCount"] == 119
    assert health["toolSurface"]["contractHash"] == contract_hash()
    assert health["toolSurface"]["missingExpectedTools"] == []
    assert health["toolSurface"]["unexpectedRegisteredTools"] == []


def test_public_hygiene_rejects_practice_source_and_wheel_members(
    tmp_path: Path, monkeypatch
) -> None:
    hygiene = _load_hygiene_module()

    assert hygiene._forbidden_path(
        "src/hwpx_mcp_server/practice/runtime.py", "mcp"
    )
    assert hygiene._forbidden_path("tests/test_practice_runtime.py", "mcp")

    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "boundary-test.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("hwpx_mcp_server/practice/runtime.py", b"pass\n")
        archive.writestr(
            "hwpx_mcp_server/server.py",
            b"PRACTICE_ROOT = 'HWPX_PRACTICE_ROOT'\n",
        )

    monkeypatch.setattr(hygiene, "ROOT", tmp_path)
    failures = hygiene._wheel_failures()

    assert any("hwpx_mcp_server/practice/runtime.py" in item for item in failures)
    assert any("HWPX_PRACTICE_ROOT" in item for item in failures)


def test_source_tree_has_no_internal_practice_runtime_markers() -> None:
    hygiene = _load_hygiene_module()
    tracked_source = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "src").rglob("*.py")
        if path.is_file()
    ]

    assert hygiene._mcp_runtime_failures(tracked_source) == []
