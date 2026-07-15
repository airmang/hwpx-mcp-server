from __future__ import annotations

import copy
import hashlib
import itertools
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier

import pytest

from hwpx_mcp_server.practice.selection import (
    PracticeSelectionError,
    SelectionConfig,
    _validate_runner_scenario,
    experiment_workspace,
    load_l0_weights,
    select_campaign_scenarios,
    update_l0_weights,
    validate_selection_result,
)


EXPERIMENT_ID = "EXP-0123456789ABCDEFFEDC"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _root(tmp_path: Path, *, experiment_id: str = EXPERIMENT_ID) -> Path:
    root = tmp_path / "practice"
    (root / "staging" / "experiments" / experiment_id).mkdir(parents=True)
    return root


def _scenario(
    index: int,
    *,
    family: str,
    difficulty: str = "routine",
    task_kind: str = "constrained_edit",
) -> dict:
    return {
        "schema": "hwpx.practice-runner-manifest/v1",
        "runnerScenarioId": f"SCN-{index:020X}",
        "evaluationPolicySha256": _digest(f"evaluation-policy-{index}"),
        "taskKind": task_kind,
        "family": family,
        "difficulty": difficulty,
        "instruction": "합성 문서에 통제된 편집을 적용한다.",
        "syntheticInputs": {
            "synthetic": True,
            "fields": {"기관": f"합성-연습기관-{index:02d}"},
        },
        "controlledMutation": {"synthetic": True, "operation": "append_marker"},
        "startArtifact": {
            "artifactId": f"ART-{index:020X}",
            "sha256": _digest(f"artifact-{index}"),
        },
        "allowedWorkflow": "constrained_edit",
        "budgets": {"toolCalls": 12},
        "requiredOracles": ["open_safety", "semantic_diff"],
    }


def _balanced_scenarios() -> list[dict]:
    rows: list[dict] = []
    index = 1
    tasks = ("constrained_edit", "known_template_fill", "structural_edit")
    for family_index, family in enumerate(("forms", "tables", "notices", "minutes")):
        for difficulty_index, difficulty in enumerate(
            ("routine", "intermediate", "advanced")
        ):
            rows.append(
                _scenario(
                    index,
                    family=family,
                    difficulty=difficulty,
                    task_kind=tasks[(family_index + difficulty_index) % len(tasks)],
                )
            )
            index += 1
    return rows


def test_scenario_identity_excludes_independent_evaluation_policy_binding() -> None:
    frozen = _scenario(1, family="forms")
    first = _validate_runner_scenario(frozen)

    policy_changed = copy.deepcopy(frozen)
    policy_changed["evaluationPolicySha256"] = _digest("replacement-policy")
    second = _validate_runner_scenario(policy_changed)

    content_changed = copy.deepcopy(policy_changed)
    content_changed["instruction"] = "합성 문서에 다른 통제 편집을 적용한다."
    third = _validate_runner_scenario(content_changed)

    assert first["scenarioSha256"] == second["scenarioSha256"]
    assert first["evaluationPolicySha256"] != second["evaluationPolicySha256"]
    assert third["scenarioSha256"] != second["scenarioSha256"]


def _config(
    *,
    count: int = 8,
    seed: str = "selection-seed",
    basis_points: int = 2_500,
    required: tuple[str, ...] = (),
) -> SelectionConfig:
    return SelectionConfig(
        requested_count=count,
        seed=seed,
        max_family_basis_points=basis_points,
        required_families=required,
    )


def _empty_weights() -> dict:
    return {"byFamily": {}, "byDifficulty": {}, "byTaskKind": {}}


def _process_weight_cas(
    root: str,
    weights: dict,
    ready: object,
    gate: object,
    results: object,
) -> None:
    ready.put(True)
    gate.wait()
    try:
        receipt = update_l0_weights(
            root,
            EXPERIMENT_ID,
            weights=weights,
            expected_revision=0,
        )
        results.put(("ok", receipt["revision"]))
    except PracticeSelectionError as exc:
        results.put(("error", exc.code))


def test_selection_is_deterministic_under_input_permutations_and_replay(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    scenarios = _balanced_scenarios()[:6]
    expected = select_campaign_scenarios(
        scenarios,
        config=_config(count=4, basis_points=5_000),
        practice_root=root,
        experiment_id=EXPERIMENT_ID,
    )

    for permutation in itertools.permutations(scenarios[:3]):
        reordered = list(permutation) + scenarios[3:]
        assert select_campaign_scenarios(
            reordered,
            config=_config(count=4, basis_points=5_000),
            practice_root=root,
            experiment_id=EXPERIMENT_ID,
        ) == expected
    assert validate_selection_result(expected) == expected
    assert [row["slot"] for row in expected["selected"]] == list(range(4))


def test_seed_is_hashed_and_drives_deterministic_tie_breaking(tmp_path: Path) -> None:
    root = _root(tmp_path)
    scenarios = [
        _scenario(index, family=f"family-{index:02d}") for index in range(1, 9)
    ]
    first = select_campaign_scenarios(
        scenarios,
        config=_config(count=3, seed="seed-alpha"),
        practice_root=root,
        experiment_id=EXPERIMENT_ID,
    )
    second = select_campaign_scenarios(
        scenarios,
        config=_config(count=3, seed="seed-beta"),
        practice_root=root,
        experiment_id=EXPERIMENT_ID,
    )
    assert first["seedSha256"] == _digest("seed-alpha")
    assert "seed-alpha" not in json.dumps(first)
    assert first["selectionId"] != second["selectionId"]
    assert [row["runnerScenarioId"] for row in first["selected"]] != [
        row["runnerScenarioId"] for row in second["selected"]
    ]


def test_family_caps_missing_coverage_and_unfilled_slots_are_explicit(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    scenarios = [
        _scenario(index, family=family)
        for index, family in enumerate(
            ("forms", "forms", "forms", "tables", "tables", "minutes"), start=1
        )
    ]
    result = select_campaign_scenarios(
        scenarios,
        config=_config(
            count=8,
            basis_points=2_500,
            required=("forms", "missing-family", "tables"),
        ),
        practice_root=root,
        experiment_id=EXPERIMENT_ID,
    )
    coverage = result["coverage"]
    assert coverage["familyCaps"] == {"forms": 2, "minutes": 2, "tables": 2}
    assert max(coverage["byFamily"].values()) <= 2
    assert coverage["missingFamilies"] == ["missing-family"]
    assert coverage["unfilledSlots"] == 3
    assert result["selectedCount"] == 5


def test_family_and_difficulty_coverage_precede_weakness_tie_break(tmp_path: Path) -> None:
    root = _root(tmp_path)
    scenarios = _balanced_scenarios()
    result = select_campaign_scenarios(
        scenarios,
        config=_config(count=8),
        practice_root=root,
        experiment_id=EXPERIMENT_ID,
    )
    assert set(result["coverage"]["byFamily"].values()) == {2}
    difficulty_counts = result["coverage"]["byDifficulty"].values()
    assert max(difficulty_counts) - min(difficulty_counts) <= 1


def test_recurring_integer_weakness_prioritizes_equal_coverage_candidates(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    scenarios = [
        _scenario(1, family="forms", task_kind="known_template_fill"),
        _scenario(2, family="tables", task_kind="structural_edit"),
        _scenario(3, family="minutes", task_kind="constrained_edit"),
    ]
    result = select_campaign_scenarios(
        scenarios,
        config=_config(count=1),
        practice_root=root,
        experiment_id=EXPERIMENT_ID,
        recurring_weaknesses={
            "byFamily": {"tables": 7},
            "byDifficulty": {},
            "byTaskKind": {"structural_edit": 5},
        },
    )
    assert result["selected"][0]["family"] == "tables"
    assert result["selected"][0]["weaknessScore"] == 12


def test_l0_weights_persist_only_in_opaque_experiment_and_replay_idempotently(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    live_markers = {
        root / "registry" / "live.bin": b"registry",
        root / "scenarios" / "frozen" / "live.bin": b"frozen",
        root / "evidence" / "live.bin": b"evidence",
    }
    for path, payload in live_markers.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    weights = {
        "byFamily": {"forms": 3},
        "byDifficulty": {"advanced": 2},
        "byTaskKind": {"known_template_fill": 4},
    }

    first = update_l0_weights(
        root,
        EXPERIMENT_ID,
        weights=weights,
        expected_revision=0,
    )
    assert first["revision"] == 1 and first["changed"] is True
    state = load_l0_weights(root, EXPERIMENT_ID)
    assert state["weights"] == weights
    weight_path = experiment_workspace(root, EXPERIMENT_ID) / "l0-selection-weights.json"
    assert weight_path.is_file()
    assert stat.S_IMODE(weight_path.stat().st_mode) == 0o600

    replay = update_l0_weights(
        root,
        EXPERIMENT_ID,
        weights=copy.deepcopy(weights),
        expected_revision=1,
    )
    assert replay["revision"] == 1 and replay["changed"] is False
    assert replay["weightsSha256"] == first["weightsSha256"]
    assert all(path.read_bytes() == payload for path, payload in live_markers.items())
    assert list(root.rglob("l0-selection-weights.json")) == [weight_path]


def test_persisted_and_ephemeral_weaknesses_compose_without_live_mutation(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    persisted = {
        "byFamily": {"forms": 2},
        "byDifficulty": {},
        "byTaskKind": {"known_template_fill": 3},
    }
    update_l0_weights(root, EXPERIMENT_ID, weights=persisted, expected_revision=0)
    before = load_l0_weights(root, EXPERIMENT_ID)
    result = select_campaign_scenarios(
        [
            _scenario(1, family="forms", task_kind="known_template_fill"),
            _scenario(2, family="tables", task_kind="structural_edit"),
        ],
        config=_config(count=1),
        practice_root=root,
        experiment_id=EXPERIMENT_ID,
        recurring_weaknesses={
            "byFamily": {"forms": 1},
            "byDifficulty": {},
            "byTaskKind": {},
        },
    )
    assert result["selected"][0]["weaknessScore"] == 6
    assert load_l0_weights(root, EXPERIMENT_ID) == before


@pytest.mark.parametrize("bad_weight", [True, -1, 1.5, float("nan"), "3"])
def test_weights_and_weaknesses_reject_bool_negative_float_nan_and_string(
    tmp_path: Path, bad_weight: object
) -> None:
    root = _root(tmp_path)
    weights = _empty_weights()
    weights["byFamily"] = {"forms": bad_weight}
    with pytest.raises(PracticeSelectionError):
        update_l0_weights(
            root,
            EXPERIMENT_ID,
            weights=weights,
            expected_revision=0,
        )
    with pytest.raises(PracticeSelectionError):
        select_campaign_scenarios(
            [_scenario(1, family="forms")],
            config=_config(count=1),
            practice_root=root,
            experiment_id=EXPERIMENT_ID,
            recurring_weaknesses=weights,
        )


def test_weight_revision_conflict_and_tampering_fail_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    update_l0_weights(root, EXPERIMENT_ID, weights=_empty_weights(), expected_revision=0)
    updated = {**_empty_weights(), "byFamily": {"forms": 1}}
    update_l0_weights(root, EXPERIMENT_ID, weights=updated, expected_revision=0)
    with pytest.raises(PracticeSelectionError) as captured:
        update_l0_weights(root, EXPERIMENT_ID, weights=updated, expected_revision=0)
    assert captured.value.code == "WEIGHT_CONFLICT"

    weight_path = experiment_workspace(root, EXPERIMENT_ID) / "l0-selection-weights.json"
    payload = json.loads(weight_path.read_text(encoding="utf-8"))
    payload["weights"]["byFamily"]["forms"] = 999
    weight_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PracticeSelectionError) as captured:
        load_l0_weights(root, EXPERIMENT_ID)
    assert captured.value.code == "INVALID_WEIGHTS"


def test_weight_cas_serializes_thread_and_process_races(tmp_path: Path) -> None:
    thread_root = _root(tmp_path / "threads")
    contenders = [
        {**_empty_weights(), "byFamily": {"forms": 1}},
        {**_empty_weights(), "byFamily": {"tables": 1}},
    ]
    barrier = Barrier(2)

    def thread_update(weights: dict) -> tuple[str, object]:
        barrier.wait()
        try:
            receipt = update_l0_weights(
                thread_root,
                EXPERIMENT_ID,
                weights=weights,
                expected_revision=0,
            )
            return "ok", receipt["revision"]
        except PracticeSelectionError as exc:
            return "error", exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        thread_results = list(pool.map(thread_update, contenders))
    assert sorted(thread_results) == [("error", "WEIGHT_CONFLICT"), ("ok", 1)]
    assert load_l0_weights(thread_root, EXPERIMENT_ID)["revision"] == 1

    process_root = _root(tmp_path / "processes")
    context = get_context("spawn")
    ready = context.Queue()
    gate = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_process_weight_cas,
            args=(str(process_root), weights, ready, gate, results),
        )
        for weights in contenders
    ]
    for process in processes:
        process.start()
    for _process in processes:
        assert ready.get(timeout=10) is True
    gate.set()
    for process in processes:
        process.join(timeout=20)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        assert process.exitcode == 0
    process_results = [results.get(timeout=5) for _process in processes]
    assert sorted(process_results) == [("error", "WEIGHT_CONFLICT"), ("ok", 1)]
    assert load_l0_weights(process_root, EXPERIMENT_ID)["revision"] == 1
    lock_path = (
        experiment_workspace(process_root, EXPERIMENT_ID)
        / ".l0-selection-weights.lock"
    )
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_experiment_identity_and_symlink_workspace_are_rejected_without_path_leak(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    for bad_id in ("experiment-1", "../escape", "EXP-1234"):
        with pytest.raises(PracticeSelectionError) as captured:
            experiment_workspace(root, bad_id)
        assert captured.value.code == "INVALID_EXPERIMENT"
        assert str(root) not in str(captured.value)

    other_id = "EXP-FFFFFFFFFFFFFFFFFFFF"
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = root / "staging" / "experiments" / other_id
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PracticeSelectionError) as captured:
        experiment_workspace(root, other_id)
    assert captured.value.code == "INVALID_EXPERIMENT"
    assert str(outside) not in str(captured.value)


@pytest.mark.parametrize(
    "leak",
    [
        {"gold": {"verifierId": "VER-PRIVATE"}},
        {"holdoutAnswer": "private"},
        {"evaluatorPolicy": _digest("private-policy")},
        {"split": "practice"},
        {"sourcePath": "/Volumes/private/corpus"},
        {"sourceFilename": "private-student.hwpx"},
        {"operatorNote": "010-1234-5678"},
        {"operatorNote": "read /workspace/private/student.hwpx"},
    ],
)
def test_runner_input_refuses_evaluator_private_coordinate_and_pii_leaks(
    tmp_path: Path, leak: dict
) -> None:
    root = _root(tmp_path)
    scenario = _scenario(1, family="forms")
    scenario.update(leak)
    with pytest.raises(PracticeSelectionError) as captured:
        select_campaign_scenarios(
            [scenario],
            config=_config(count=1),
            practice_root=root,
            experiment_id=EXPERIMENT_ID,
        )
    assert captured.value.code == "PRIVATE_INPUT"
    assert "/Volumes/private" not in str(captured.value)
    assert "010-1234" not in str(captured.value)


def test_public_result_is_content_addressed_and_omits_runner_content_and_paths(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    scenario = _scenario(1, family="forms")
    result = select_campaign_scenarios(
        [scenario],
        config=_config(count=1),
        practice_root=root,
        experiment_id=EXPERIMENT_ID,
    )
    encoded = json.dumps(result, ensure_ascii=False)
    assert scenario["instruction"] not in encoded
    assert "syntheticInputs" not in encoded
    assert str(root) not in encoded
    assert ".hwpx" not in encoded
    assert "gold" not in encoded.casefold()
    assert "holdout" not in encoded.casefold()
    assert "evaluator" not in encoded.casefold()

    tampered = copy.deepcopy(result)
    tampered["selected"][0]["weaknessScore"] += 1
    with pytest.raises(PracticeSelectionError):
        validate_selection_result(tampered)


def test_korean_discovery_families_map_to_closed_nonleaking_codes(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    scenarios = [
        _scenario(1, family="시험지-문항-답안"),
        _scenario(2, family="공문-기안-시행"),
        _scenario(3, family="합성미분류군"),
    ]
    result = select_campaign_scenarios(
        scenarios,
        config=_config(count=3, basis_points=10_000),
        practice_root=root,
        experiment_id=EXPERIMENT_ID,
    )
    families = {row["family"] for row in result["selected"]}
    assert "exam_question_answer" in families
    assert "official_document_draft_dispatch" in families
    assert any(family.startswith("family_") for family in families)
    encoded = json.dumps(result, ensure_ascii=False)
    assert "시험지" not in encoded
    assert "공문" not in encoded
    assert "합성미분류군" not in encoded
    assert validate_selection_result(result) == result


def test_duplicate_scenario_ids_and_invalid_config_are_rejected(tmp_path: Path) -> None:
    root = _root(tmp_path)
    scenario = _scenario(1, family="forms")
    with pytest.raises(PracticeSelectionError):
        select_campaign_scenarios(
            [scenario, copy.deepcopy(scenario)],
            config=_config(count=1),
            practice_root=root,
            experiment_id=EXPERIMENT_ID,
        )
    for bad in (
        {"requested_count": True, "seed": "x"},
        {"requested_count": 1, "seed": "x", "max_family_basis_points": 1.5},
        {"requested_count": 1, "seed": "x", "max_family_basis_points": 10_001},
        {"requested_count": 1, "seed": ""},
    ):
        with pytest.raises(PracticeSelectionError):
            SelectionConfig(**bad)  # type: ignore[arg-type]
