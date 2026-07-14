# SPDX-License-Identifier: Apache-2.0
"""Deterministic, privacy-safe campaign selection and experiment-local L0 weights.

Selection sees only the redacted runner view.  It emits opaque scenario IDs,
content hashes, and aggregate coverage metadata; instructions and synthetic
payload content are never copied to the public selection receipt.  Mutable L0
weights live at one fixed file below an opaque experiment directory inside the
practice root, never beside live stack or evaluator assets.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

if os.name == "nt":  # pragma: no cover - exercised on Windows hosts
    import msvcrt
else:
    import fcntl

from hwpx.practice.registry import SHA256_PATTERN, assert_redacted_payload
from hwpx.practice.run import OPAQUE_ID_PATTERN
from hwpx.practice.scenario import SCENARIO_ID_PATTERN, TASK_KINDS
from hwpx.tools.pii import detect_pii


SELECTION_SCHEMA = "hwpx.practice-campaign-selection/v1"
L0_WEIGHTS_SCHEMA = "hwpx.practice-l0-selection-weights/v1"
L0_WEIGHTS_RECEIPT_SCHEMA = "hwpx.practice-l0-weights-receipt/v1"

_SELECTION_ID = re.compile(r"PSEL-[A-F0-9]{20}\Z")
_EXPERIMENT_ID = re.compile(r"EXP-[A-F0-9]{20}\Z")
_WEIGHT_FILE = "l0-selection-weights.json"
_WEIGHT_LOCK_FILE = ".l0-selection-weights.lock"
_DIFFICULTIES = ("routine", "intermediate", "advanced")
_WEIGHT_AXES = ("byFamily", "byDifficulty", "byTaskKind")
_FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "answer",
        "answerkey",
        "expectedanswer",
        "expectedterminalstate",
        "lineagegroup",
        "sourceeligibility",
        "sourcedocumentid",
        "split",
        "visibility",
    }
)
_FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "instruction",
        "syntheticinputs",
        "controlledmutation",
        "startartifact",
        "suggestedoperations",
        "requiredoracles",
        "rawtext",
        "content",
        "body",
        "message",
        "detail",
        "note",
    }
)
_PRIVATE_COORDINATE = re.compile(
    r"(?:^|[\s'\"])(?:/[^\s'\"]+|[A-Za-z]:[\\/][^\s'\"]+|(?:smb|file)://[^\s'\"]+)",
    re.IGNORECASE,
)
_PRIVATE_FILENAME = re.compile(r"(?<![A-Za-z0-9_.-])[^/\\\s]+\.hwpx(?![A-Za-z0-9_.-])", re.IGNORECASE)


class PracticeSelectionError(RuntimeError):
    """Fail-closed selection error with no caller path or payload content."""

    _MESSAGES = {
        "INVALID_ROOT": "practice selection storage is invalid",
        "INVALID_EXPERIMENT": "practice experiment identity or workspace is invalid",
        "INVALID_SCENARIO": "runner scenario selection input is invalid",
        "PRIVATE_INPUT": "runner scenario selection input violates privacy isolation",
        "INVALID_WEIGHTS": "practice selection weights are invalid",
        "WEIGHT_CONFLICT": "practice selection weight revision conflicts",
        "WEIGHT_IO": "practice selection weights could not be stored safely",
        "INVALID_CONFIG": "practice campaign selection configuration is invalid",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "INVALID_SCENARIO"
        self.code = code
        super().__init__(f"{code}: {self._MESSAGES[code]}")


@dataclass(frozen=True, slots=True)
class SelectionConfig:
    """Frozen integer-only campaign selection settings."""

    requested_count: int
    seed: str
    max_family_basis_points: int = 2_500
    required_families: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.requested_count, bool)
            or not isinstance(self.requested_count, int)
            or self.requested_count < 1
            or isinstance(self.max_family_basis_points, bool)
            or not isinstance(self.max_family_basis_points, int)
            or not 1 <= self.max_family_basis_points <= 10_000
            or not isinstance(self.seed, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", self.seed)
        ):
            raise PracticeSelectionError("INVALID_CONFIG")
        normalized = tuple(_validate_label(item, "config") for item in self.required_families)
        if normalized != tuple(sorted(set(normalized))):
            raise PracticeSelectionError("INVALID_CONFIG")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _content_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _require_int(value: object, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PracticeSelectionError("INVALID_WEIGHTS")
    return value


def _validate_label(value: object, kind: str) -> str:
    label = value if isinstance(value, str) else ""
    if (
        not 1 <= len(label) <= 64
        or any(not (character.isalnum() or character in "._-") for character in label)
        or detect_pii(label)
        or any(token in label.casefold() for token in ("gold", "holdout", "evaluator"))
    ):
        error_code = {
            "config": "INVALID_CONFIG",
            "scenario": "INVALID_SCENARIO",
            "weight": "INVALID_WEIGHTS",
            "result": "INVALID_SCENARIO",
        }.get(kind, "INVALID_WEIGHTS")
        raise PracticeSelectionError(error_code)
    return label


def _strict_directory(value: str | Path, code: str) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    if ".." in raw.parts:
        raise PracticeSelectionError(code)
    try:
        metadata = raw.lstat()
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise PracticeSelectionError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or raw != resolved
        or resolved == Path(resolved.anchor)
    ):
        raise PracticeSelectionError(code)
    return resolved


def experiment_workspace(practice_root: str | Path, experiment_id: str) -> Path:
    """Resolve the one allowed opaque experiment workspace inside practice root."""

    root = _strict_directory(practice_root, "INVALID_ROOT")
    if not isinstance(experiment_id, str) or not _EXPERIMENT_ID.fullmatch(experiment_id):
        raise PracticeSelectionError("INVALID_EXPERIMENT")
    raw_workspace = root / "staging" / "experiments" / experiment_id
    workspace = _strict_directory(raw_workspace, "INVALID_EXPERIMENT")
    if root not in workspace.parents or workspace != raw_workspace:
        raise PracticeSelectionError("INVALID_EXPERIMENT")
    return workspace


def _contains_private_or_evaluator_data(value: object) -> bool:
    def visit(item: object) -> bool:
        if isinstance(item, Mapping):
            for key, child in item.items():
                normalized = _normalized_key(key)
                if (
                    normalized == "evaluationpolicysha256"
                    and isinstance(child, str)
                    and SHA256_PATTERN.fullmatch(child)
                ):
                    continue
                if (
                    normalized in _FORBIDDEN_INPUT_KEYS
                    or "gold" in normalized
                    or "holdout" in normalized
                    or "evaluator" in normalized
                    or normalized.endswith("path")
                    or normalized.endswith("filename")
                ):
                    return True
                if visit(child):
                    return True
            return False
        if isinstance(item, (list, tuple)):
            return any(visit(child) for child in item)
        if isinstance(item, str):
            folded = item.casefold()
            return bool(
                (not SHA256_PATTERN.fullmatch(item) and detect_pii(item))
                or _PRIVATE_COORDINATE.search(item)
                or _PRIVATE_FILENAME.search(item)
                or folded in {"gold", "holdout"}
            )
        return False

    return visit(value)


def _validate_runner_scenario(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PracticeSelectionError("INVALID_SCENARIO")
    raw = dict(value)
    try:
        assert_redacted_payload(raw)
    except ValueError as exc:
        raise PracticeSelectionError("PRIVATE_INPUT") from exc
    if _contains_private_or_evaluator_data(raw):
        raise PracticeSelectionError("PRIVATE_INPUT")
    scenario_id = str(raw.get("runnerScenarioId", ""))
    if not SCENARIO_ID_PATTERN.fullmatch(scenario_id):
        raise PracticeSelectionError("INVALID_SCENARIO")
    if raw.get("schema") != "hwpx.practice-runner-manifest/v1":
        raise PracticeSelectionError("INVALID_SCENARIO")
    task_kind = str(raw.get("taskKind", ""))
    difficulty = str(raw.get("difficulty", ""))
    if task_kind not in TASK_KINDS or difficulty not in _DIFFICULTIES:
        raise PracticeSelectionError("INVALID_SCENARIO")
    family = _validate_label(raw.get("family"), "scenario")
    start_artifact = raw.get("startArtifact")
    if not isinstance(start_artifact, Mapping) or set(start_artifact) != {
        "artifactId",
        "sha256",
    }:
        raise PracticeSelectionError("INVALID_SCENARIO")
    start_artifact_id = str(start_artifact["artifactId"])
    start_artifact_sha256 = str(start_artifact["sha256"])
    if not OPAQUE_ID_PATTERN.fullmatch(start_artifact_id) or not SHA256_PATTERN.fullmatch(
        start_artifact_sha256
    ):
        raise PracticeSelectionError("INVALID_SCENARIO")
    evaluation_policy_sha256 = str(raw.get("evaluationPolicySha256", ""))
    if not SHA256_PATTERN.fullmatch(evaluation_policy_sha256):
        raise PracticeSelectionError("INVALID_SCENARIO")
    return {
        "runnerScenarioId": scenario_id,
        "scenarioSha256": _content_sha256(raw),
        "evaluationPolicySha256": evaluation_policy_sha256,
        "startArtifactId": start_artifact_id,
        "startArtifactSha256": start_artifact_sha256,
        "family": family,
        "difficulty": difficulty,
        "taskKind": task_kind,
    }


def _empty_weights() -> dict[str, Any]:
    weights = {axis: {} for axis in _WEIGHT_AXES}
    return {
        "schema": L0_WEIGHTS_SCHEMA,
        "revision": 0,
        "weights": weights,
        "weightsSha256": _content_sha256(weights),
    }


def _validate_weights_payload(value: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    if not isinstance(value, Mapping) or set(value) != set(_WEIGHT_AXES):
        raise PracticeSelectionError("INVALID_WEIGHTS")
    result: dict[str, dict[str, int]] = {}
    for axis in _WEIGHT_AXES:
        rows = value[axis]
        if not isinstance(rows, Mapping):
            raise PracticeSelectionError("INVALID_WEIGHTS")
        normalized: dict[str, int] = {}
        for key, weight in rows.items():
            label = _validate_label(key, "weight")
            if axis == "byDifficulty" and label not in _DIFFICULTIES:
                raise PracticeSelectionError("INVALID_WEIGHTS")
            if axis == "byTaskKind" and label not in TASK_KINDS:
                raise PracticeSelectionError("INVALID_WEIGHTS")
            normalized[label] = _require_int(weight)
        result[axis] = dict(sorted(normalized.items()))
    return result


def _validate_weight_state(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "revision",
        "weights",
        "weightsSha256",
    }:
        raise PracticeSelectionError("INVALID_WEIGHTS")
    if value.get("schema") != L0_WEIGHTS_SCHEMA:
        raise PracticeSelectionError("INVALID_WEIGHTS")
    revision = _require_int(value.get("revision"))
    weights = _validate_weights_payload(value.get("weights", {}))
    expected_hash = _content_sha256(weights)
    if value.get("weightsSha256") != expected_hash:
        raise PracticeSelectionError("INVALID_WEIGHTS")
    return {
        "schema": L0_WEIGHTS_SCHEMA,
        "revision": revision,
        "weights": weights,
        "weightsSha256": expected_hash,
    }


def _weights_path(practice_root: str | Path, experiment_id: str) -> Path:
    return experiment_workspace(practice_root, experiment_id) / _WEIGHT_FILE


@contextmanager
def _exclusive_weight_lock(path: Path) -> Iterator[None]:
    """Hold a process- and thread-safe lock for one experiment's CAS update."""

    lock_path = path.with_name(_WEIGHT_LOCK_FILE)
    descriptor: int | None = None
    locked = False
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = lock_path.lstat()
        opened = os.fstat(descriptor)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_nlink != 1
        ):
            raise PracticeSelectionError("WEIGHT_IO")
        os.fchmod(descriptor, 0o600)
        if os.name == "nt":  # pragma: no cover - exercised on Windows hosts
            if opened.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        after = lock_path.lstat()
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise PracticeSelectionError("WEIGHT_IO")
    except PracticeSelectionError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise PracticeSelectionError("WEIGHT_IO") from exc
    try:
        yield
    finally:
        assert descriptor is not None
        try:
            if locked:
                if os.name == "nt":  # pragma: no cover - exercised on Windows hosts
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _read_regular_json(path: Path) -> object:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 256_000:
            raise PracticeSelectionError("INVALID_WEIGHTS")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev,
                opened.st_ino,
            ) != (metadata.st_dev, metadata.st_ino):
                raise PracticeSelectionError("INVALID_WEIGHTS")
            payload = stream.read(256_001)
            after = os.fstat(stream.fileno())
        if len(payload) > 256_000 or (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ):
            raise PracticeSelectionError("INVALID_WEIGHTS")
        return json.loads(payload.decode("utf-8"))
    except PracticeSelectionError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PracticeSelectionError("INVALID_WEIGHTS") from exc


def load_l0_weights(practice_root: str | Path, experiment_id: str) -> dict[str, Any]:
    """Load and verify experiment-local weights without exposing a path."""

    path = _weights_path(practice_root, experiment_id)
    if not path.exists() and not path.is_symlink():
        return _empty_weights()
    return _validate_weight_state(_read_regular_json(path))


def _atomic_write_state(path: Path, state: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{_WEIGHT_FILE}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        if path.exists() or path.is_symlink():
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise PracticeSelectionError("WEIGHT_IO")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(_canonical_bytes(state))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600, follow_symlinks=False)
        os.replace(temporary, path)
        os.chmod(path, 0o600, follow_symlinks=False)
        directory_descriptor = os.open(
            path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except PracticeSelectionError:
        raise
    except OSError as exc:
        raise PracticeSelectionError("WEIGHT_IO") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass


def update_l0_weights(
    practice_root: str | Path,
    experiment_id: str,
    *,
    weights: Mapping[str, Any],
    expected_revision: int,
) -> dict[str, Any]:
    """CAS-replace integer L0 weights inside one opaque experiment workspace.

    Repeating an identical update is idempotent and does not advance revision.
    The returned receipt is path-free and content-addressed by ``weightsSha256``.
    """

    expected = _require_int(expected_revision)
    normalized = _validate_weights_payload(weights)
    path = _weights_path(practice_root, experiment_id)
    with _exclusive_weight_lock(path):
        current = (
            _validate_weight_state(_read_regular_json(path))
            if path.exists() or path.is_symlink()
            else _empty_weights()
        )
        if current["revision"] != expected:
            raise PracticeSelectionError("WEIGHT_CONFLICT")
        changed = current["weights"] != normalized
        state = {
            "schema": L0_WEIGHTS_SCHEMA,
            "revision": current["revision"] + (1 if changed else 0),
            "weights": normalized,
            "weightsSha256": _content_sha256(normalized),
        }
        if changed or not path.exists():
            _atomic_write_state(path, state)
    receipt = {
        "schema": L0_WEIGHTS_RECEIPT_SCHEMA,
        "revision": state["revision"],
        "changed": changed,
        "weightsSha256": state["weightsSha256"],
        "privateCoordinatesExposed": False,
        "contentIncluded": False,
    }
    _assert_public_selection(receipt)
    return receipt


def _combined_weights(
    stored: Mapping[str, Mapping[str, int]],
    recurring: Mapping[str, Any] | None,
) -> tuple[dict[str, dict[str, int]], str]:
    updates = _validate_weights_payload(
        recurring or {axis: {} for axis in _WEIGHT_AXES}
    )
    combined: dict[str, dict[str, int]] = {}
    for axis in _WEIGHT_AXES:
        keys = set(stored[axis]) | set(updates[axis])
        combined[axis] = {
            key: stored[axis].get(key, 0) + updates[axis].get(key, 0)
            for key in sorted(keys)
        }
    return combined, _content_sha256(updates)


def _candidate_weakness(candidate: Mapping[str, Any], weights: Mapping[str, Mapping[str, int]]) -> int:
    return (
        weights["byFamily"].get(candidate["family"], 0)
        + weights["byDifficulty"].get(candidate["difficulty"], 0)
        + weights["byTaskKind"].get(candidate["taskKind"], 0)
    )


def _selection_sha256(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("selectionId", None)
    payload.pop("selectionSha256", None)
    return _content_sha256(payload)


def _assert_public_selection(value: object) -> None:
    try:
        assert_redacted_payload(value)
    except ValueError as exc:
        raise PracticeSelectionError("PRIVATE_INPUT") from exc

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                normalized = _normalized_key(key)
                if (
                    normalized in _FORBIDDEN_PUBLIC_KEYS
                    or "gold" in normalized
                    or "holdout" in normalized
                    or "evaluator" in normalized
                    or normalized.endswith("path")
                    or normalized.endswith("filename")
                ):
                    raise PracticeSelectionError("PRIVATE_INPUT")
                visit(child)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
            return
        if isinstance(item, str) and (
            (not SHA256_PATTERN.fullmatch(item) and detect_pii(item))
            or _PRIVATE_COORDINATE.search(item)
            or _PRIVATE_FILENAME.search(item)
            or item.casefold() in {"gold", "holdout"}
        ):
            raise PracticeSelectionError("PRIVATE_INPUT")

    visit(value)


def validate_selection_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a public selection receipt and its canonical content address."""

    if not isinstance(value, Mapping):
        raise PracticeSelectionError("INVALID_SCENARIO")
    raw = dict(value)
    _assert_public_selection(raw)
    expected_keys = {
        "schema",
        "selectionId",
        "selectionSha256",
        "seedSha256",
        "candidateSetSha256",
        "weightsSha256",
        "recurringWeaknessSha256",
        "requestedCount",
        "selectedCount",
        "selected",
        "coverage",
        "privateCoordinatesExposed",
        "contentIncluded",
    }
    if set(raw) != expected_keys or raw.get("schema") != SELECTION_SCHEMA:
        raise PracticeSelectionError("INVALID_SCENARIO")
    for key in (
        "seedSha256",
        "candidateSetSha256",
        "weightsSha256",
        "recurringWeaknessSha256",
        "selectionSha256",
    ):
        if not SHA256_PATTERN.fullmatch(str(raw.get(key, ""))):
            raise PracticeSelectionError("INVALID_SCENARIO")
    if raw.get("privateCoordinatesExposed") is not False or raw.get("contentIncluded") is not False:
        raise PracticeSelectionError("PRIVATE_INPUT")
    requested = _require_int(raw.get("requestedCount"), minimum=1)
    selected_count = _require_int(raw.get("selectedCount"))
    selected = raw.get("selected")
    coverage = raw.get("coverage")
    if not isinstance(selected, list) or not isinstance(coverage, Mapping):
        raise PracticeSelectionError("INVALID_SCENARIO")
    if selected_count != len(selected) or selected_count > requested:
        raise PracticeSelectionError("INVALID_SCENARIO")
    if [item.get("slot") for item in selected if isinstance(item, Mapping)] != list(
        range(len(selected))
    ):
        raise PracticeSelectionError("INVALID_SCENARIO")
    ids: list[str] = []
    selected_family_counts: Counter[str] = Counter()
    selected_difficulty_counts: Counter[str] = Counter()
    selected_task_counts: Counter[str] = Counter()
    for item in selected:
        if not isinstance(item, Mapping) or set(item) != {
            "slot",
            "runnerScenarioId",
            "scenarioSha256",
            "evaluationPolicySha256",
            "startArtifactId",
            "startArtifactSha256",
            "family",
            "difficulty",
            "taskKind",
            "weaknessScore",
        }:
            raise PracticeSelectionError("INVALID_SCENARIO")
        scenario_id = str(item["runnerScenarioId"])
        if not SCENARIO_ID_PATTERN.fullmatch(scenario_id):
            raise PracticeSelectionError("INVALID_SCENARIO")
        if not SHA256_PATTERN.fullmatch(str(item["scenarioSha256"])):
            raise PracticeSelectionError("INVALID_SCENARIO")
        if not SHA256_PATTERN.fullmatch(str(item["evaluationPolicySha256"])):
            raise PracticeSelectionError("INVALID_SCENARIO")
        if not OPAQUE_ID_PATTERN.fullmatch(str(item["startArtifactId"])):
            raise PracticeSelectionError("INVALID_SCENARIO")
        if not SHA256_PATTERN.fullmatch(str(item["startArtifactSha256"])):
            raise PracticeSelectionError("INVALID_SCENARIO")
        family = _validate_label(item["family"], "result")
        difficulty = str(item["difficulty"])
        task_kind = str(item["taskKind"])
        if difficulty not in _DIFFICULTIES or task_kind not in TASK_KINDS:
            raise PracticeSelectionError("INVALID_SCENARIO")
        _require_int(item["weaknessScore"])
        ids.append(scenario_id)
        selected_family_counts[family] += 1
        selected_difficulty_counts[difficulty] += 1
        selected_task_counts[task_kind] += 1
    if len(ids) != len(set(ids)):
        raise PracticeSelectionError("INVALID_SCENARIO")

    coverage_keys = {
        "maxFamilyBasisPoints",
        "familyCaps",
        "byFamily",
        "byDifficulty",
        "byTaskKind",
        "requiredFamilies",
        "missingFamilies",
        "unselectedAvailableFamilies",
        "capReachedFamilies",
        "unfilledSlots",
    }
    if set(coverage) != coverage_keys:
        raise PracticeSelectionError("INVALID_SCENARIO")
    basis_points = _require_int(coverage.get("maxFamilyBasisPoints"), minimum=1)
    if basis_points > 10_000:
        raise PracticeSelectionError("INVALID_SCENARIO")
    family_caps = coverage.get("familyCaps")
    by_family = coverage.get("byFamily")
    by_difficulty = coverage.get("byDifficulty")
    by_task = coverage.get("byTaskKind")
    if not all(isinstance(item, Mapping) for item in (family_caps, by_family, by_difficulty, by_task)):
        raise PracticeSelectionError("INVALID_SCENARIO")
    if set(family_caps) != set(by_family):
        raise PracticeSelectionError("INVALID_SCENARIO")
    expected_cap = max(1, (requested * basis_points + 9_999) // 10_000)
    normalized_counts: dict[str, int] = {}
    for family in family_caps:
        label = _validate_label(family, "result")
        cap = _require_int(family_caps[family], minimum=1)
        count = _require_int(by_family[family])
        if cap != expected_cap or count > cap or count != selected_family_counts[label]:
            raise PracticeSelectionError("INVALID_SCENARIO")
        normalized_counts[label] = count
    expected_difficulty = {
        difficulty: selected_difficulty_counts[difficulty] for difficulty in _DIFFICULTIES
    }
    if dict(by_difficulty) != expected_difficulty:
        raise PracticeSelectionError("INVALID_SCENARIO")
    if dict(by_task) != dict(sorted(selected_task_counts.items())):
        raise PracticeSelectionError("INVALID_SCENARIO")

    def validated_labels(name: str) -> list[str]:
        values = coverage.get(name)
        if not isinstance(values, list):
            raise PracticeSelectionError("INVALID_SCENARIO")
        labels = [_validate_label(item, "result") for item in values]
        if labels != sorted(set(labels)):
            raise PracticeSelectionError("INVALID_SCENARIO")
        return labels

    required_families = validated_labels("requiredFamilies")
    missing_families = validated_labels("missingFamilies")
    unselected_families = validated_labels("unselectedAvailableFamilies")
    cap_reached_families = validated_labels("capReachedFamilies")
    available_families = set(normalized_counts)
    if missing_families != sorted(set(required_families) - available_families):
        raise PracticeSelectionError("INVALID_SCENARIO")
    if unselected_families != sorted(
        family for family, count in normalized_counts.items() if count == 0
    ):
        raise PracticeSelectionError("INVALID_SCENARIO")
    if cap_reached_families != sorted(
        family
        for family, count in normalized_counts.items()
        if count >= family_caps[family]
    ):
        raise PracticeSelectionError("INVALID_SCENARIO")
    if _require_int(coverage.get("unfilledSlots")) != requested - selected_count:
        raise PracticeSelectionError("INVALID_SCENARIO")
    expected_hash = _selection_sha256(raw)
    if raw["selectionSha256"] != expected_hash:
        raise PracticeSelectionError("INVALID_SCENARIO")
    expected_id = f"PSEL-{expected_hash[:20].upper()}"
    if not _SELECTION_ID.fullmatch(str(raw["selectionId"])) or raw["selectionId"] != expected_id:
        raise PracticeSelectionError("INVALID_SCENARIO")
    return raw


def select_campaign_scenarios(
    scenarios: Sequence[Mapping[str, Any]],
    *,
    config: SelectionConfig,
    practice_root: str | Path,
    experiment_id: str,
    recurring_weaknesses: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Select a deterministic capped campaign from redacted runner scenarios."""

    # Validate the workspace even though selection only reads its L0 state.
    experiment_workspace(practice_root, experiment_id)
    if not isinstance(scenarios, Sequence) or isinstance(scenarios, (str, bytes)) or not scenarios:
        raise PracticeSelectionError("INVALID_SCENARIO")
    candidates = [_validate_runner_scenario(item) for item in scenarios]
    candidates.sort(key=lambda item: item["runnerScenarioId"])
    ids = [item["runnerScenarioId"] for item in candidates]
    if len(ids) != len(set(ids)):
        raise PracticeSelectionError("INVALID_SCENARIO")

    weight_state = load_l0_weights(practice_root, experiment_id)
    weights, recurring_hash = _combined_weights(
        weight_state["weights"], recurring_weaknesses
    )
    available_families = sorted({item["family"] for item in candidates})
    missing_families = sorted(set(config.required_families) - set(available_families))
    family_cap = max(
        1,
        (config.requested_count * config.max_family_basis_points + 9_999) // 10_000,
    )
    family_caps = {family: family_cap for family in available_families}
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    family_counts: Counter[str] = Counter()
    difficulty_counts: Counter[str] = Counter()

    while len(selected) < config.requested_count:
        eligible = [
            item
            for item in candidates
            if item["runnerScenarioId"] not in selected_ids
            and family_counts[item["family"]] < family_cap
        ]
        if not eligible:
            break

        def rank(item: Mapping[str, Any]) -> tuple[int, int, int, int, int, str]:
            required_uncovered = int(
                item["family"] in config.required_families
                and family_counts[item["family"]] == 0
            )
            family_uncovered = int(family_counts[item["family"]] == 0)
            weakness = _candidate_weakness(item, weights)
            tie = hashlib.sha256(
                f"{config.seed}\n{item['runnerScenarioId']}".encode("utf-8")
            ).hexdigest()
            return (
                -required_uncovered,
                -family_uncovered,
                family_counts[item["family"]],
                difficulty_counts[item["difficulty"]],
                -weakness,
                tie + item["runnerScenarioId"],
            )

        chosen = min(eligible, key=rank)
        slot = len(selected)
        weakness_score = _candidate_weakness(chosen, weights)
        selected.append(
            {
                "slot": slot,
                "runnerScenarioId": chosen["runnerScenarioId"],
                "scenarioSha256": chosen["scenarioSha256"],
                "evaluationPolicySha256": chosen["evaluationPolicySha256"],
                "startArtifactId": chosen["startArtifactId"],
                "startArtifactSha256": chosen["startArtifactSha256"],
                "family": chosen["family"],
                "difficulty": chosen["difficulty"],
                "taskKind": chosen["taskKind"],
                "weaknessScore": weakness_score,
            }
        )
        selected_ids.add(chosen["runnerScenarioId"])
        family_counts[chosen["family"]] += 1
        difficulty_counts[chosen["difficulty"]] += 1

    by_task = Counter(item["taskKind"] for item in selected)
    unselected_families = sorted(set(available_families) - set(family_counts))
    cap_reached = sorted(
        family for family, cap in family_caps.items() if family_counts[family] >= cap
    )
    candidate_fingerprints = [
        {
            "runnerScenarioId": item["runnerScenarioId"],
            "scenarioSha256": item["scenarioSha256"],
            "evaluationPolicySha256": item["evaluationPolicySha256"],
            "startArtifactId": item["startArtifactId"],
            "startArtifactSha256": item["startArtifactSha256"],
        }
        for item in candidates
    ]
    result: dict[str, Any] = {
        "schema": SELECTION_SCHEMA,
        "seedSha256": hashlib.sha256(config.seed.encode("utf-8")).hexdigest(),
        "candidateSetSha256": _content_sha256(candidate_fingerprints),
        "weightsSha256": weight_state["weightsSha256"],
        "recurringWeaknessSha256": recurring_hash,
        "requestedCount": config.requested_count,
        "selectedCount": len(selected),
        "selected": selected,
        "coverage": {
            "maxFamilyBasisPoints": config.max_family_basis_points,
            "familyCaps": family_caps,
            "byFamily": {family: family_counts[family] for family in available_families},
            "byDifficulty": {difficulty: difficulty_counts[difficulty] for difficulty in _DIFFICULTIES},
            "byTaskKind": dict(sorted(by_task.items())),
            "requiredFamilies": list(config.required_families),
            "missingFamilies": missing_families,
            "unselectedAvailableFamilies": unselected_families,
            "capReachedFamilies": cap_reached,
            "unfilledSlots": config.requested_count - len(selected),
        },
        "privateCoordinatesExposed": False,
        "contentIncluded": False,
    }
    _assert_public_selection(result)
    result["selectionSha256"] = _selection_sha256(result)
    result["selectionId"] = f"PSEL-{result['selectionSha256'][:20].upper()}"
    return validate_selection_result(result)


__all__ = [
    "L0_WEIGHTS_RECEIPT_SCHEMA",
    "L0_WEIGHTS_SCHEMA",
    "SELECTION_SCHEMA",
    "PracticeSelectionError",
    "SelectionConfig",
    "experiment_workspace",
    "load_l0_weights",
    "select_campaign_scenarios",
    "update_l0_weights",
    "validate_selection_result",
]
