from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_floor_gate_bootstraps_its_dependencies_before_running() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = workflow["jobs"]["test"]["steps"]
    names = [step.get("name") for step in steps]
    bootstrap_index = names.index("Install pinned-floor gate dependencies")
    gate_index = names.index("Pinned core satisfies the declared floor")

    assert bootstrap_index < gate_index
    command = steps[bootstrap_index]["run"]
    assert "python -m pip install" in command
    assert "packaging>=23.0" in command
    assert "tomli>=2.0; python_version < '3.11'" in command
