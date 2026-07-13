# SPDX-License-Identifier: Apache-2.0
"""Path-redacted high-level execution for private practice scenarios."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from hwpx.tools.package_validator import validate_editor_open_safety

_SCENARIO_PATTERN = "SCN-"
_RUN_PATTERN = "PRUN-"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    os.chmod(path, 0o600)


class PracticeScenarioError(RuntimeError):
    """Fail-closed practice contract error safe to return to an operator."""


class PracticeScenarioService:
    """Resolve opaque artifacts internally and emit path-redacted run receipts."""

    def __init__(
        self,
        root: str | Path,
        *,
        runner_manifest_path: str | Path | None = None,
        apply_table_ops: Callable[..., Mapping[str, Any]],
        apply_body_ops: Callable[..., Mapping[str, Any]],
        inspect_fill_residue: Callable[..., Mapping[str, Any]],
    ) -> None:
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise PracticeScenarioError("practice root is unavailable")
        self.manifest_path = Path(
            runner_manifest_path or self.root / "scenarios" / "runner-manifest.json"
        ).expanduser().resolve(strict=True)
        if self.root not in self.manifest_path.parents:
            raise PracticeScenarioError("runner manifest must stay inside the practice root")
        self._apply_table_ops = apply_table_ops
        self._apply_body_ops = apply_body_ops
        self._inspect_fill_residue = inspect_fill_residue

    def _manifest(self) -> dict[str, Any]:
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PracticeScenarioError("runner manifest is unreadable") from exc
        if manifest.get("schema") != "hwpx.practice-runner-manifest/v1":
            raise PracticeScenarioError("unsupported runner manifest schema")
        scenarios = manifest.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            raise PracticeScenarioError("runner manifest has no scenarios")
        return manifest

    def _scenario(self, scenario_id: str) -> dict[str, Any]:
        if not scenario_id.startswith(_SCENARIO_PATTERN) or len(scenario_id) != 24:
            raise PracticeScenarioError("scenario_id must be opaque")
        matches = [
            item
            for item in self._manifest()["scenarios"]
            if isinstance(item, Mapping) and item.get("runnerScenarioId") == scenario_id
        ]
        if len(matches) != 1:
            raise PracticeScenarioError("scenario is absent or duplicated")
        scenario = dict(matches[0])
        forbidden = {
            "gold", "expectedTerminalState", "lineageGroup", "sourceDocumentId",
            "sourceEligibility", "split", "visibility",
        }
        if forbidden & set(scenario):
            raise PracticeScenarioError("runner scenario exposes evaluator-only fields")
        start = scenario.get("startArtifact")
        if not isinstance(start, Mapping) or not start.get("artifactId") or not start.get("sha256"):
            raise PracticeScenarioError("runner scenario lacks an opaque start artifact")
        if scenario.get("syntheticInputs", {}).get("synthetic") is not True:
            raise PracticeScenarioError("runner scenario inputs are not synthetic")
        return scenario

    def _artifact_path(self, start: Mapping[str, Any]) -> Path:
        artifact_id = str(start["artifactId"])
        if artifact_id.startswith("ART-") and len(artifact_id) == 24:
            candidate = self.root / "artifacts" / "base" / f"HWC-{artifact_id[4:]}.hwpx"
        elif artifact_id.startswith("DER-") and len(artifact_id) == 24:
            candidate = self.root / "sanitized" / f"{artifact_id}.hwpx"
        else:
            raise PracticeScenarioError("unsupported opaque artifact identifier")
        candidate = candidate.resolve(strict=True)
        if self.root not in candidate.parents or not candidate.is_file():
            raise PracticeScenarioError("practice artifact is unavailable")
        if _sha256(candidate) != str(start["sha256"]):
            raise PracticeScenarioError("practice artifact hash mismatch")
        return candidate

    def _run_id(self, scenario_id: str, idempotency_key: str) -> str:
        if len(idempotency_key) < 8:
            raise PracticeScenarioError("idempotency_key must contain at least 8 characters")
        token = hashlib.sha256(f"{scenario_id}\n{idempotency_key}".encode()).hexdigest()[:20].upper()
        return f"{_RUN_PATTERN}{token}"

    def _state_path(self, run_id: str) -> Path:
        if not run_id.startswith(_RUN_PATTERN) or len(run_id) != 25:
            raise PracticeScenarioError("run_id must be opaque")
        return self.root / "runs" / run_id / "state.json"

    def start(self, scenario_id: str, *, idempotency_key: str) -> dict[str, Any]:
        scenario = self._scenario(scenario_id)
        self._artifact_path(scenario["startArtifact"])
        run_id = self._run_id(scenario_id, idempotency_key)
        state_path = self._state_path(run_id)
        state = {
            "schema": "hwpx.private-practice-state/v1",
            "runId": run_id,
            "scenarioId": scenario_id,
            "idempotencyKeySha256": hashlib.sha256(idempotency_key.encode()).hexdigest(),
            "scenario": scenario,
        }
        replay = state_path.exists()
        if replay:
            try:
                current = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PracticeScenarioError("practice run state is unreadable") from exc
            if current != state:
                raise PracticeScenarioError("practice run idempotency conflict")
        else:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(state_path.parent, 0o700)
            _atomic_json(state_path, state)
        return {
            "schema": "hwpx.practice-start-receipt/v1",
            "runId": run_id,
            "scenarioId": scenario_id,
            "state": "prepared",
            "taskKind": scenario.get("taskKind"),
            "family": scenario.get("family"),
            "difficulty": scenario.get("difficulty"),
            "instruction": scenario.get("instruction"),
            "syntheticInputs": scenario.get("syntheticInputs"),
            "controlledMutation": scenario.get("controlledMutation"),
            "allowedWorkflow": scenario.get("allowedWorkflow"),
            "budgets": scenario.get("budgets"),
            "requiredOracles": scenario.get("requiredOracles", []),
            "suggestedOperations": scenario.get("suggestedOperations", []),
            "sourceArtifact": {"artifactId": scenario["startArtifact"]["artifactId"]},
            "privateStorageCoordinatesExposed": False,
            "idempotentReplay": replay,
        }

    def apply(
        self,
        run_id: str,
        *,
        destination_path: str | Path,
        operation_kind: str = "table",
        operations: list[dict[str, Any]] | None = None,
        use_suggested_operations: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        state_path = self._state_path(run_id)
        if not state_path.is_file():
            raise PracticeScenarioError("practice run does not exist")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PracticeScenarioError("practice run state is unreadable") from exc
        scenario = dict(state["scenario"])
        source = self._artifact_path(scenario["startArtifact"])
        destination = Path(destination_path).expanduser().resolve(strict=False)
        if destination == source or self.root == destination or self.root in destination.parents:
            raise PracticeScenarioError("destination must be outside private practice storage")
        selected = scenario.get("suggestedOperations") if use_suggested_operations else operations
        if operation_kind not in {"table", "body"} or not isinstance(selected, list) or not selected:
            raise PracticeScenarioError("an allow-listed non-empty operation plan is required")
        if not confirm:
            return {
                "schema": "hwpx.practice-apply-preview/v1",
                "runId": run_id,
                "state": "decision",
                "operationKind": operation_kind,
                "operationCount": len(selected),
                "requiresConfirmation": True,
                "privateStorageCoordinatesExposed": False,
            }
        receipt_path = state_path.with_name("receipt.json")
        source_before = _sha256(source)
        if receipt_path.exists():
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PracticeScenarioError("practice run receipt is unreadable") from exc
            if destination.is_file() and receipt.get("outputArtifact", {}).get("contentSha256") == _sha256(destination):
                return {**receipt, "idempotentReplay": True}
            raise PracticeScenarioError("practice run output receipt conflicts with destination")
        if destination.exists():
            raise PracticeScenarioError("destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if operation_kind == "table":
                result = self._apply_table_ops(
                    str(source),
                    selected,
                    output=str(destination),
                    render_check="off",
                    dry_run=False,
                )
            else:
                result = self._apply_body_ops(
                    str(source), selected, output=str(destination), dry_run=False
                )
            if not destination.is_file():
                raise RuntimeError("practice operation produced no output")
            source_unchanged = _sha256(source) == source_before
            open_safety = validate_editor_open_safety(destination)
            residue = self._inspect_fill_residue(str(destination), blank_path=str(source))
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise PracticeScenarioError("practice operation or verification failed") from exc
        if not source_unchanged:
            destination.unlink(missing_ok=True)
            raise PracticeScenarioError("practice source integrity failed")
        if not open_safety.ok:
            destination.unlink(missing_ok=True)
            raise PracticeScenarioError("practice output failed editor-open safety")
        applied = result.get("applied") if isinstance(result, Mapping) else None
        mapping_ok = result.get("ok") is not False and isinstance(applied, list) and len(applied) == len(selected)
        residue_ok = isinstance(residue, Mapping) and residue.get("ok") is not False
        domain_ok = mapping_ok and residue_ok
        # Practice apply is deliberately pre-render. Real Hancom is a separate,
        # asynchronous oracle step; invoking the synchronous form verifier here
        # can hold an MCP request for the GUI backend's multi-minute timeout.
        render_checked = False
        completed = bool(domain_ok)
        output_hash = _sha256(destination)
        receipt = {
            "schema": "hwpx.practice-run-receipt/v1",
            "runId": run_id,
            "scenarioId": state["scenarioId"],
            "state": "completed" if completed else "needs_review",
            "sourceArtifact": {
                "artifactId": scenario["startArtifact"]["artifactId"],
                "unchanged": source_unchanged,
            },
            "outputArtifact": {
                "artifactId": f"OUT-{output_hash[:20].upper()}",
                "contentSha256": output_hash,
                "path": str(destination),
            },
            "syntheticInputsOnly": True,
            "operationCount": len(selected),
            "openSafety": {"ok": bool(open_safety.ok)},
            "domainVerification": {
                "ok": domain_ok,
                "mappingApplied": mapping_ok,
                "residueChecked": True,
                "residueOk": residue_ok,
            },
            "render": {
                "checked": render_checked,
                "status": "verified" if render_checked else "unverified",
            },
            "verificationStatus": (
                "real_hancom_verified"
                if completed and render_checked
                else "structurally_verified_render_unverified"
                if completed
                else "needs_review"
            ),
            "privateStorageCoordinatesExposed": False,
            "idempotentReplay": False,
        }
        if str(self.root) in json.dumps(receipt, ensure_ascii=False):
            destination.unlink(missing_ok=True)
            raise PracticeScenarioError("private storage coordinate entered the receipt")
        _atomic_json(receipt_path, receipt)
        return receipt


__all__ = ["PracticeScenarioError", "PracticeScenarioService"]
