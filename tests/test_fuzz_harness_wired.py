# SPDX-License-Identifier: Apache-2.0
"""The core fuzz harness must actually run somewhere.

The harness builds documents through this package's builder, so after the 5.0
split core's own CI could only ``importorskip`` it — and an importorskip with
no environment that satisfies the import is a test that runs nowhere. That is
exactly what happened: the harness ran in neither repository's CI from the 5.0
split until the wiring this file guards was added. A regression asset that
silently stops running is worse than none, because its existence keeps being
cited as coverage.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_ci_runs_the_core_fuzz_harness_here() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["test"]["steps"]
    names = [step.get("name") for step in steps]
    assert "Run the core fuzz regression harness" in names, (
        "the fuzz harness step left tests.yml; without it the harness runs in "
        "no CI at all (core can only skip it)"
    )
    run = steps[names.index("Run the core fuzz regression harness")]["run"]
    assert "test_fuzz_loop.py" in run
    assert "test_fuzz_regressions.py" in run


def test_the_harness_itself_still_imports_and_generates() -> None:
    """A wiring guard on a broken harness would be another false receipt."""

    import sys

    # Pick a co-located core checkout that actually CONTAINS the harness.
    # Merely existing is not enough: this workspace keeps stale historical
    # checkouts around, and the plain "python-hwpx" directory can predate the
    # harness entirely — resolving by name alone imported nothing and failed.
    candidates = [ROOT.parent / "python-hwpx" / "scripts"]
    candidates += sorted(ROOT.parent.glob("python-hwpx*/scripts"))
    core_scripts = next(
        (path for path in candidates if (path / "fuzz" / "__init__.py").is_file()),
        None,
    )
    if core_scripts is None:
        import pytest

        pytest.skip("no co-located python-hwpx checkout carries scripts/fuzz")

    import tempfile

    sys.path.insert(0, str(core_scripts))
    try:
        from fuzz import generate_scenario, run_scenario

        scenario = generate_scenario(0)
        with tempfile.TemporaryDirectory() as tmp:
            result = run_scenario(scenario, Path(tmp) / "seed0.hwpx")
        assert result.ok, f"seed-0 fuzz scenario regressed: {result.error} ({result.classification})"
    finally:
        sys.path.remove(str(core_scripts))
