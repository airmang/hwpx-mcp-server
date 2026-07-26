from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_clean_wheel_runs_installed_root_and_api_smoke_with_both_checkers() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8",
    )
    probe = (ROOT / "tests" / "typing" / "installed_facade_smoke.py").read_text(
        encoding="utf-8",
    )

    assert "Type-check installed root and API facades" in workflow
    assert "tests/typing/mypy-installed.ini" in workflow
    assert "tests/typing/pyright-installed.json" in workflow
    assert "tests/typing/installed_facade_smoke.py" in workflow
    assert ".clean-package/bin/python -m mypy" in workflow
    assert ".clean-package/bin/python -m pyright" in workflow
    assert "root_create" in probe
    assert "api_create" in probe
    assert probe.count("assert_type(") == 2


def test_clean_wheel_proves_root_import_stays_lazy_and_mcp_free() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8",
    )

    root_import = workflow.find("import hwpx_automation\n")
    lazy_assertion = workflow.find(
        'assert "hwpx_automation.api" not in sys.modules',
    )
    api_import = workflow.find("import hwpx_automation.api")

    assert -1 not in (root_import, lazy_assertion, api_import)
    assert root_import < lazy_assertion < api_import
    assert 'name == "mcp" or name.startswith("mcp.")' in workflow
