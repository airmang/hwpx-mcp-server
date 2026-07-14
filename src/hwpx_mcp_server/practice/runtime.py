# SPDX-License-Identifier: Apache-2.0
"""Fail-closed production assembly for the local Leap B campaign runner.

The runtime deliberately has no public path-taking API.  Two strict existing
roots come from the environment and every mutable component lives below the
fixed ``$HWPX_PRACTICE_ROOT/runtime`` layout::

    runtime/
      provenance.json
      campaigns/PCMP-.../manifest.json
      campaigns/PCMP-.../tasks/PRUN-....json
      queue/
      results/
      sandboxes/
      workflow/

Task files identify their start artifact only by scope and SHA-256.  The actual
file is resolved from a fixed content-addressed object path; a task can never
inject a filename or filesystem coordinate into workflow dispatch.
"""
from __future__ import annotations

import hashlib
import io
import importlib.util
import json
import os
import re
import stat
import threading
import uuid
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hwpx import validate_editor_open_safety
from hwpx.opc.security import HwpxSecurityError, guard_zip_file, parse_xml_stdlib
from hwpx.practice import (
    current_evaluator_code_sha256,
    validate_campaign_manifest,
    validate_exact_provenance,
)
from hwpx.tools.pii import detect_pii

from hwpx_mcp_server import quality as quality_contract
from hwpx_mcp_server.tool_contract import contract_hash
from hwpx_mcp_server.workflow.service import WorkflowService
from hwpx_mcp_server.workflow.store import WorkflowStore

from .campaign_service import PracticeCampaignService
from .dispatch import PracticeWorkflowDispatcher, ResolvedPracticeTask
from .queue import PracticeCampaignQueue
from .sandbox import PracticeSandboxManager, validate_practice_roots


_CAMPAIGN_ID = re.compile(r"PCMP-[A-F0-9]{20}\Z")
_RUN_ID = re.compile(r"PRUN-[A-F0-9]{20}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_TASK_SCHEMA = "hwpx.practice-campaign-task/v1"
_MAX_PRIVATE_JSON_BYTES = 4 * 1024 * 1024
_FIXED_RUNTIME_CHILDREN = (
    "campaigns",
    "queue",
    "results",
    "sandboxes",
    "workflow",
)
_EVALUATOR_COMPONENTS = (
    "run.py",
    "campaign.py",
    "evaluator.py",
    "domain.py",
    "aggregate.py",
)
_TERMINAL_ARTIFACT_SCHEMA = "hwpx.practice-terminal-artifact/v1"
_STARTUP_REAPER_SCHEMA = "hwpx.practice-startup-reaper/v1"
_MAX_RESULT_BYTES = 1024 * 1024 * 1024
_MAX_ATTESTED_FILE_BYTES = 256 * 1024 * 1024
_MAX_SANITIZED_HWPX_BYTES = 256 * 1024 * 1024
_MAX_SANITIZED_TEXT_CHARS = 4 * 1024 * 1024
_SANITIZED_XML_SUFFIXES = frozenset({".xml", ".hpf", ".rdf", ".xhtml", ".svg"})
_SANITIZED_TEXT_SUFFIXES = _SANITIZED_XML_SUFFIXES | frozenset(
    {".txt", ".json", ".csv", ".tsv", ".html", ".htm", ".css", ".md"}
)
_REAPER_LIMIT = 64
_REAPER_MAX_PER_STARTUP = 256
_FORBIDDEN_TASK_KEYS = frozenset(
    {
        "path",
        "filepath",
        "filename",
        "sourcepath",
        "sourcefilename",
        "outputpath",
        "outputfilename",
        "destinationpath",
        "destinationfilename",
        "corpusroot",
        "practiceroot",
        "gold",
        "holdout",
        "evaluator",
        "expectedanswer",
    }
)


class PracticeRuntimeError(RuntimeError):
    """A path-redacted runtime configuration failure safe to map publicly."""

    code = "CAMPAIGN_UNAVAILABLE"

    def __init__(self) -> None:
        super().__init__(self.code)


class _PrivateLookupError(KeyError):
    """Internal resolver failure mapped by ``PracticeCampaignService``."""

    def __init__(self) -> None:
        super().__init__("CAMPAIGN_UNAVAILABLE")


def _strict_existing_directory(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute() or ".." in raw.parts or raw == Path(raw.anchor):
        raise PracticeRuntimeError()
    try:
        metadata = raw.lstat()
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise PracticeRuntimeError() from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or resolved != raw
    ):
        raise PracticeRuntimeError()
    return resolved


def _strict_descendant_directory(parent: Path, name: str) -> Path:
    child = _strict_existing_directory(parent / name)
    if child.parent != parent:
        raise PracticeRuntimeError()
    return child


def _root_identity(path: Path) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PracticeRuntimeError() from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PracticeRuntimeError()
    return metadata.st_dev, metadata.st_ino


def _assert_root_identity(path: Path, expected: tuple[int, int]) -> None:
    if _root_identity(path) != expected:
        raise PracticeRuntimeError()


def _strict_regular_file(path: Path, root: Path) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _PrivateLookupError() from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or resolved != path
        or root not in resolved.parents
    ):
        raise _PrivateLookupError()
    return resolved


def _strict_regular_or_absent(path: Path, root: Path, source_root: Path) -> Path:
    """Validate a future mutable file without following its terminal component."""

    if not path.is_absolute() or path.parent != root or path == source_root:
        raise PracticeRuntimeError()
    if source_root in path.parents or path in source_root.parents:
        raise PracticeRuntimeError()
    try:
        parent = _strict_existing_directory(path.parent)
        if parent != root:
            raise PracticeRuntimeError()
        metadata = path.lstat()
    except FileNotFoundError:
        return path
    except OSError as exc:
        raise PracticeRuntimeError() from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise PracticeRuntimeError()
    try:
        if path.resolve(strict=True) != path:
            raise PracticeRuntimeError()
    except OSError as exc:
        raise PracticeRuntimeError() from exc
    return path


def _read_private_json(path: Path, root: Path) -> Mapping[str, Any]:
    target = _strict_regular_file(path, root)
    descriptor: int | None = None
    try:
        before = target.lstat()
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise _PrivateLookupError()
            payload = stream.read(_MAX_PRIVATE_JSON_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(payload) > _MAX_PRIVATE_JSON_BYTES or (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ):
            raise _PrivateLookupError()
        value = json.loads(payload.decode("utf-8"))
    except _PrivateLookupError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _PrivateLookupError() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, Mapping):
        raise _PrivateLookupError()
    return value


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _task_dispatch_sha256(value: Mapping[str, Any]) -> str:
    """Bind private execution material to the run's dispatch seed.

    ``campaignId`` and ``runId`` are intentionally excluded because both are
    derived after selection.  They are bound separately by the manifest and
    opaque task location.
    """

    payload = {
        "scenarioRef": value["scenarioRef"],
        "evaluationPolicySha256": value["evaluationPolicySha256"],
        "workflowFamily": value["workflowFamily"],
        "artifactScope": value["artifactScope"],
        "sourceArtifactSha256": value["sourceArtifactSha256"],
        "parameters": value["parameters"],
        "privacy": value["privacy"],
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _assert_task_material_safe(value: object) -> None:
    """Reject evaluator material and every filesystem-coordinate escape hatch."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(key)
            if (
                normalized in _FORBIDDEN_TASK_KEYS
                or normalized.endswith(("path", "filename", "root"))
                or any(
                    token in normalized for token in ("gold", "holdout", "evaluator")
                )
            ):
                raise ValueError("private task contains a forbidden field")
            _assert_task_material_safe(child)
        return
    if isinstance(value, list):
        for child in value:
            _assert_task_material_safe(child)
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if (
            "\x00" in value
            or lowered.startswith(("/", "~/", "file://"))
            or re.match(r"^[a-z]:[\\/]", lowered)
            or "../" in lowered
            or "..\\" in lowered
            or lowered.endswith(".hwpx")
        ):
            raise ValueError("private task contains a filesystem coordinate")
        if detect_pii(value):
            raise ValueError("private task contains high-confidence PII")


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_strict_bytes(path: Path, root: Path, *, maximum: int) -> bytes:
    target = _strict_regular_file(path, root)
    descriptor: int | None = None
    try:
        before = target.lstat()
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise PracticeRuntimeError()
            payload = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
        if len(payload) > maximum or (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ):
            raise PracticeRuntimeError()
        return payload
    except (OSError, _PrivateLookupError) as exc:
        raise PracticeRuntimeError() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _decode_sanitized_text(payload: bytes) -> str:
    """Decode the text encodings used by HWPX preview and metadata members."""

    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    pairs = max(1, len(payload) // 2)
    even_nuls = payload[0::2].count(0)
    odd_nuls = payload[1::2].count(0)
    if odd_nuls > pairs // 2:
        return payload.decode("utf-16-le")
    if even_nuls > pairs // 2:
        return payload.decode("utf-16-be")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return payload.decode("cp949")


def _validate_sanitized_hwpx(payload: bytes) -> None:
    """Independently validate sanitized package bytes before task dispatch."""

    try:
        with zipfile.ZipFile(io.BytesIO(payload), mode="r") as package:
            guard_zip_file(package)
            infos = [item for item in package.infolist() if not item.is_dir()]
            names: set[str] = set()
            extracted_text_chars = 0

            def scan_text(segments: list[str]) -> None:
                nonlocal extracted_text_chars
                bounded: list[str] = []
                for segment in segments:
                    extracted_text_chars += len(segment)
                    if extracted_text_chars > _MAX_SANITIZED_TEXT_CHARS:
                        raise HwpxSecurityError(
                            "sanitized package text exceeds limit"
                        )
                    bounded.append(segment)
                if detect_pii("".join(bounded)):
                    raise HwpxSecurityError("sanitized package contains PII")

            for info in infos:
                normalized = info.filename.replace("\\", "/")
                if normalized in names or info.flag_bits & 0x1:
                    raise HwpxSecurityError("unsafe sanitized package metadata")
                names.add(normalized)
                suffix = Path(normalized).suffix.casefold()
                text_like = (
                    suffix in _SANITIZED_TEXT_SUFFIXES or normalized == "mimetype"
                )
                if not text_like:
                    continue
                member = package.read(info)
                if suffix in _SANITIZED_XML_SUFFIXES:
                    xml_root = parse_xml_stdlib(
                        member, part_name="sanitized package XML"
                    )
                    scan_text(list(xml_root.itertext()))
                    for element in xml_root.iter():
                        for attribute in element.attrib.values():
                            scan_text([attribute])
                else:
                    scan_text([_decode_sanitized_text(member)])
        if not validate_editor_open_safety(payload).ok:
            raise HwpxSecurityError("sanitized package is not editor-open safe")
    except (
        HwpxSecurityError,
        OSError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        raise ValueError("task artifact failed sanitized HWPX validation") from exc


def _hash_files(root: Path, relative_files: list[Path]) -> str:
    """Hash a sorted, path-independent set of strict regular source files."""

    digest = hashlib.sha256()
    for relative in sorted(relative_files, key=lambda item: item.as_posix()):
        if relative.is_absolute() or ".." in relative.parts:
            raise PracticeRuntimeError()
        target = root / relative
        payload = _read_strict_bytes(
            target, root, maximum=_MAX_ATTESTED_FILE_BYTES
        )
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _attested_tree(root: Path) -> str:
    strict = _strict_existing_directory(root)
    relative_files: list[Path] = []
    try:
        for candidate in strict.rglob("*"):
            relative = candidate.relative_to(strict)
            if any(
                part in {".git", ".venv", ".hwpx-mcp-server-venv", "__pycache__"}
                for part in relative.parts
            ):
                continue
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise PracticeRuntimeError()
            if stat.S_ISREG(metadata.st_mode) and candidate.suffix.casefold() not in {
                ".pyc",
                ".pyo",
            }:
                relative_files.append(relative)
    except OSError as exc:
        raise PracticeRuntimeError() from exc
    if not relative_files:
        raise PracticeRuntimeError()
    return _hash_files(strict, relative_files)


def _skill_sha256(root: Path) -> str:
    """Hash the executable plugin/skill surface, not unrelated repository files."""

    strict = _strict_existing_directory(root)
    skill_documents = [
        strict / "SKILL.md",
        strict / "skills" / "hwpx" / "SKILL.md",
    ]
    skill_document = next(
        (candidate for candidate in skill_documents if candidate.is_file()), None
    )
    if skill_document is None or skill_document.is_symlink():
        raise PracticeRuntimeError()
    selected: set[Path] = {skill_document.relative_to(strict)}
    directories = {
        skill_document.parent / "references",
        strict / "references",
        strict / "scripts",
        strict / ".codex-plugin",
        strict / "plugins",
    }
    for directory in sorted(directories):
        if not directory.exists():
            if directory.is_symlink():
                raise PracticeRuntimeError()
            continue
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise PracticeRuntimeError() from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PracticeRuntimeError()
        for candidate in directory.rglob("*"):
            relative = candidate.relative_to(strict)
            if any(
                part in {".git", ".venv", ".hwpx-mcp-server-venv", "__pycache__"}
                for part in relative.parts
            ):
                continue
            candidate_metadata = candidate.lstat()
            if stat.S_ISLNK(candidate_metadata.st_mode):
                raise PracticeRuntimeError()
            if stat.S_ISREG(candidate_metadata.st_mode):
                selected.add(relative)
    for name in (".mcp.json", "plugin-sync.json"):
        candidate = strict / name
        if candidate.exists():
            if candidate.is_symlink() or not candidate.is_file():
                raise PracticeRuntimeError()
            selected.add(candidate.relative_to(strict))
        elif candidate.is_symlink():
            raise PracticeRuntimeError()
    return _hash_files(strict, list(selected))


def _package_root(module_name: str) -> Path:
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin is None:
            raise PracticeRuntimeError()
        origin = Path(spec.origin).resolve(strict=True)
        return _strict_existing_directory(origin.parent)
    except (ImportError, OSError) as exc:
        raise PracticeRuntimeError() from exc


def _evaluator_sha256(core_root: Path) -> str:
    practice_root = _strict_descendant_directory(core_root, "practice")
    relative_files = [
        Path(name)
        for name in _EVALUATOR_COMPONENTS
        if (practice_root / name).is_file() and not (practice_root / name).is_symlink()
    ]
    required = {"run.py", "campaign.py"}
    if not required.issubset({item.name for item in relative_files}):
        raise PracticeRuntimeError()
    installed = current_evaluator_code_sha256()
    if installed != _hash_files(practice_root, relative_files):
        raise PracticeRuntimeError()
    return installed


def installed_runtime_provenance(
    skill_root: str | Path,
    skill_version: str,
    evaluator_authentication_key_id: str,
) -> dict[str, Any]:
    """Build exact provenance from installed bytes for offline provisioning."""

    if not isinstance(skill_version, str) or not skill_version.strip():
        raise PracticeRuntimeError()
    if not re.fullmatch(r"EVK-[A-F0-9]{20}", evaluator_authentication_key_id):
        raise PracticeRuntimeError()
    core_root = _package_root("hwpx")
    server_root = _package_root("hwpx_mcp_server")
    strict_skill_root = _strict_existing_directory(skill_root)
    return {
        "stack": {
            "core": {
                "version": quality_contract.package_version("python-hwpx"),
                "sha256": _attested_tree(core_root),
            },
            "server": {
                "version": quality_contract.package_version("hwpx-mcp-server"),
                "sha256": _attested_tree(server_root),
            },
            "skill": {
                "version": skill_version,
                "sha256": _skill_sha256(strict_skill_root),
            },
        },
        "toolSpec": {"version": "tool-spec/v1", "sha256": contract_hash()},
        "evaluator": {
            "version": "practice-evaluator/v1",
            "sha256": _evaluator_sha256(core_root),
            "authenticationKeyId": evaluator_authentication_key_id,
        },
    }


def _runtime_provenance(
    runtime_root: Path, skill_root: Path, skill_version: str
) -> dict[str, Any]:
    try:
        configured = validate_exact_provenance(
            _read_private_json(runtime_root / "provenance.json", runtime_root)
        )
        attested = installed_runtime_provenance(
            skill_root,
            skill_version,
            configured["evaluator"]["authenticationKeyId"],
        )
        installed = quality_contract.capability_state()
        versions = installed["versions"]
        if not installed["ok"]:
            raise ValueError("installed capability is skewed")
        if configured["stack"]["core"]["version"] != versions["core"]:
            raise ValueError("core version mismatch")
        if configured["stack"]["server"]["version"] != versions["mcp"]:
            raise ValueError("server version mismatch")
        if configured["stack"]["skill"]["version"] != versions["plugin"]:
            raise ValueError("skill version mismatch")
        if configured != attested:
            raise ValueError("runtime byte provenance mismatch")
        return configured
    except (KeyError, TypeError, ValueError, _PrivateLookupError) as exc:
        raise PracticeRuntimeError() from exc


class _PrivateCampaignResolver:
    def __init__(
        self, source_root: Path, practice_root: Path, campaigns_root: Path
    ) -> None:
        self.source_root = source_root
        self.practice_root = practice_root
        self.campaigns_root = campaigns_root

    def _campaign_root(self, campaign_id: str) -> Path:
        if not isinstance(campaign_id, str) or not _CAMPAIGN_ID.fullmatch(campaign_id):
            raise _PrivateLookupError()
        try:
            return _strict_descendant_directory(self.campaigns_root, campaign_id)
        except PracticeRuntimeError as exc:
            raise _PrivateLookupError() from exc

    def manifest(self, campaign_id: str) -> Mapping[str, Any]:
        root = self._campaign_root(campaign_id)
        try:
            manifest = validate_campaign_manifest(
                _read_private_json(root / "manifest.json", root)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("campaign contract is invalid") from exc
        if manifest["campaignId"] != campaign_id:
            raise ValueError("campaign identity mismatch")
        return manifest

    @staticmethod
    def _run_ref(manifest: Mapping[str, Any], lease: Any) -> Mapping[str, Any]:
        slot = getattr(lease, "slot", None)
        run_id = getattr(lease, "run_id", None)
        if (
            isinstance(slot, bool)
            or not isinstance(slot, int)
            or not 0 <= slot < len(manifest["runs"])
        ):
            raise ValueError("task slot is invalid")
        run_ref = manifest["runs"][slot]
        if run_ref["runId"] != run_id or not _RUN_ID.fullmatch(str(run_id)):
            raise ValueError("task run identity mismatch")
        return run_ref

    def _artifact(self, scope: object, digest: object) -> Path:
        digest_value = str(digest or "")
        if not _SHA256.fullmatch(digest_value):
            raise ValueError("task artifact digest is invalid")
        if scope == "practice":
            root = self.practice_root
            path = (
                root
                / "sanitized"
                / "objects"
                / digest_value[:2]
                / f"{digest_value}.hwpx"
            )
        else:
            raise ValueError("task artifact scope is invalid")
        try:
            target = _strict_regular_file(path, root)
            payload = _read_strict_bytes(
                target, root, maximum=_MAX_SANITIZED_HWPX_BYTES
            )
            if hashlib.sha256(payload).hexdigest() != digest_value:
                raise ValueError("task artifact content address is invalid")
            _validate_sanitized_hwpx(payload)
            return target
        except (_PrivateLookupError, PracticeRuntimeError) as exc:
            raise ValueError("task artifact is unavailable") from exc

    def task(
        self, manifest: Mapping[str, Any], lease: Any
    ) -> ResolvedPracticeTask:
        campaign_id = str(manifest.get("campaignId", ""))
        campaign_root = self._campaign_root(campaign_id)
        run_ref = self._run_ref(manifest, lease)
        run_id = run_ref["runId"]
        try:
            tasks_root = _strict_descendant_directory(campaign_root, "tasks")
        except PracticeRuntimeError as exc:
            raise ValueError("private task storage is unavailable") from exc
        payload = dict(
            _read_private_json(tasks_root / f"{run_id}.json", tasks_root)
        )
        expected_keys = {
            "schema",
            "campaignId",
            "runId",
            "scenarioRef",
            "evaluationPolicySha256",
            "dispatch",
            "workflowFamily",
            "artifactScope",
            "sourceArtifactSha256",
            "parameters",
            "privacy",
        }
        if set(payload) != expected_keys or payload["schema"] != _TASK_SCHEMA:
            raise ValueError("private task contract is invalid")
        if payload["campaignId"] != campaign_id or payload["runId"] != run_id:
            raise ValueError("private task identity mismatch")
        privacy = payload["privacy"]
        if not isinstance(privacy, Mapping) or dict(privacy) != {
            "syntheticInputsOnly": True,
            "highConfidencePiiCount": 0,
        }:
            raise ValueError("private task privacy attestation is invalid")
        _assert_task_material_safe(payload["parameters"])
        scenario_ref = payload["scenarioRef"]
        dispatch = payload["dispatch"]
        if not isinstance(scenario_ref, Mapping) or not isinstance(dispatch, Mapping):
            raise ValueError("private task binding is invalid")
        if set(dispatch) != {"slot", "dispatchKey", "seedSha256"}:
            raise ValueError("private task dispatch contract is invalid")
        matching = {
            "scenarioId": run_ref["scenarioId"],
            "scenarioSha256": run_ref["scenarioSha256"],
            "runnerManifestSha256": run_ref["runnerManifestSha256"],
            "derivativeSha256": run_ref["derivativeSha256"],
            "startArtifactId": run_ref["startArtifactId"],
            "startArtifactSha256": run_ref["startArtifactSha256"],
        }
        if any(scenario_ref.get(key) != value for key, value in matching.items()):
            raise ValueError("private task scenario binding is invalid")
        if dispatch.get("slot") != run_ref["slot"]:
            raise ValueError("private task dispatch binding is invalid")
        if dispatch.get("seedSha256") != _task_dispatch_sha256(payload):
            raise ValueError("private task content address is invalid")
        if payload["sourceArtifactSha256"] != run_ref["startArtifactSha256"]:
            raise ValueError("private task artifact binding is invalid")
        if payload["evaluationPolicySha256"] != run_ref["evaluationPolicySha256"]:
            raise ValueError("private task evaluator policy binding is invalid")
        source = self._artifact(
            payload["artifactScope"], payload["sourceArtifactSha256"]
        )
        return ResolvedPracticeTask(
            scenario_ref=dict(scenario_ref),
            dispatch=dict(dispatch),
            source_artifact=source,
            workflow_family=str(payload["workflowFamily"]),
            parameters=dict(payload["parameters"]),
        )


def _strict_result_directory(parent: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{2}|[a-f0-9]{64}", name):
        raise PracticeRuntimeError()
    path = parent / name
    try:
        os.mkdir(path, mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise PracticeRuntimeError() from exc
    child = _strict_existing_directory(path)
    if child.parent != parent:
        raise PracticeRuntimeError()
    try:
        os.chmod(child, 0o700, follow_symlinks=False)
    except OSError as exc:
        raise PracticeRuntimeError() from exc
    return child


def _fsync_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise PracticeRuntimeError()
        os.fsync(descriptor)
    except OSError as exc:
        raise PracticeRuntimeError() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


class _TerminalArtifactStore:
    """Retain completed outputs by run and content hash before sandbox cleanup."""

    def __init__(self, results_root: Path) -> None:
        self.results_root = _strict_existing_directory(results_root)

    @staticmethod
    def _output_contract(outcome: Any) -> tuple[str, str, int, Path] | None:
        receipt = getattr(outcome, "run_receipt", None)
        output_path = getattr(outcome, "output_path", None)
        if not isinstance(receipt, Mapping) or receipt.get("state") != "completed":
            return None
        run_id = receipt.get("runId")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise PracticeRuntimeError()
        if (
            not isinstance(output_path, Path)
            or output_path.suffix.casefold() != ".hwpx"
        ):
            raise PracticeRuntimeError()
        sandbox = getattr(outcome, "sandbox", None)
        sandbox_root = getattr(sandbox, "root", None)
        if not isinstance(sandbox_root, Path):
            raise PracticeRuntimeError()
        try:
            strict_sandbox_root = _strict_existing_directory(sandbox_root)
            output_path = _strict_regular_file(output_path, strict_sandbox_root)
        except (_PrivateLookupError, PracticeRuntimeError) as exc:
            raise PracticeRuntimeError() from exc
        artifacts = receipt.get("artifacts")
        if not isinstance(artifacts, list):
            raise PracticeRuntimeError()
        outputs = [
            item
            for item in artifacts
            if isinstance(item, Mapping) and item.get("role") == "output"
        ]
        if len(outputs) != 1:
            raise PracticeRuntimeError()
        digest = outputs[0].get("sha256")
        size = outputs[0].get("bytes")
        if (
            not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 <= size <= _MAX_RESULT_BYTES
        ):
            raise PracticeRuntimeError()
        return run_id, digest, size, output_path

    @staticmethod
    def _existing_matches(path: Path, digest: str, size: int) -> bool:
        descriptor: int | None = None
        try:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(
                metadata.st_mode
            ):
                raise PracticeRuntimeError()
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    raise PracticeRuntimeError()
                hashed = hashlib.sha256()
                total = 0
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_RESULT_BYTES:
                        raise PracticeRuntimeError()
                    hashed.update(chunk)
                after = os.fstat(stream.fileno())
            if (after.st_size, after.st_mtime_ns) != (
                metadata.st_size,
                metadata.st_mtime_ns,
            ):
                raise PracticeRuntimeError()
            return total == size and hashed.hexdigest() == digest
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise PracticeRuntimeError() from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _copy_to_temp(
        source: Path, destination: Path, digest: str, size: int
    ) -> None:
        source_descriptor: int | None = None
        target_descriptor: int | None = None
        try:
            source_before = source.lstat()
            if stat.S_ISLNK(source_before.st_mode) or not stat.S_ISREG(
                source_before.st_mode
            ):
                raise PracticeRuntimeError()
            source_descriptor = os.open(
                source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            target_descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            opened = os.fstat(source_descriptor)
            if (opened.st_dev, opened.st_ino) != (
                source_before.st_dev,
                source_before.st_ino,
            ):
                raise PracticeRuntimeError()
            hashed = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(source_descriptor, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_RESULT_BYTES:
                    raise PracticeRuntimeError()
                hashed.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(target_descriptor, view)
                    if written <= 0:
                        raise PracticeRuntimeError()
                    view = view[written:]
            source_after = os.fstat(source_descriptor)
            if (
                source_after.st_size,
                source_after.st_mtime_ns,
            ) != (source_before.st_size, source_before.st_mtime_ns):
                raise PracticeRuntimeError()
            if total != size or hashed.hexdigest() != digest:
                raise PracticeRuntimeError()
            os.fsync(target_descriptor)
        except PracticeRuntimeError:
            raise
        except OSError as exc:
            raise PracticeRuntimeError() from exc
        finally:
            if source_descriptor is not None:
                os.close(source_descriptor)
            if target_descriptor is not None:
                os.close(target_descriptor)

    def __call__(
        self, _task: ResolvedPracticeTask, outcome: Any
    ) -> Mapping[str, Any] | None:
        contract = self._output_contract(outcome)
        if contract is None:
            return None
        run_id, digest, size, source = contract
        prefix_root = _strict_result_directory(self.results_root, digest[:2])
        digest_root = _strict_result_directory(prefix_root, digest)
        target = digest_root / f"{run_id}.hwpx"
        if not self._existing_matches(target, digest, size):
            temporary = digest_root / f".{run_id}.{uuid.uuid4().hex}.tmp"
            try:
                self._copy_to_temp(source, temporary, digest, size)
                try:
                    os.link(temporary, target, follow_symlinks=False)
                except FileExistsError:
                    if not self._existing_matches(target, digest, size):
                        raise PracticeRuntimeError()
                finally:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError as exc:
                        raise PracticeRuntimeError() from exc
                _fsync_directory(digest_root)
            except Exception:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        return {
            "schema": _TERMINAL_ARTIFACT_SCHEMA,
            "runId": run_id,
            "artifactId": (
                "RESULT-"
                + hashlib.sha256(f"{run_id}:{digest}".encode("ascii"))
                .hexdigest()[:20]
                .upper()
            ),
            "artifactSha256": digest,
            "bytes": size,
            "retained": True,
            "privateStorageCoordinatesExposed": False,
        }


def _startup_reap(queue: Any, sandbox: Any) -> dict[str, Any]:
    recover = getattr(queue, "recover", None)
    candidate_reader = getattr(queue, "terminal_cleanup_candidates", None)
    candidate_ack = getattr(queue, "ack_terminal_cleanup", None)
    owned_opener = getattr(sandbox, "open_owned", None)
    if not callable(recover):
        raise PracticeRuntimeError()
    try:
        recovery = recover()
        if not isinstance(recovery, Mapping):
            raise PracticeRuntimeError()
        recovered_slots = recovery.get("recoveredSlots")
        if (
            isinstance(recovered_slots, bool)
            or not isinstance(recovered_slots, int)
            or recovered_slots < 0
        ):
            raise PracticeRuntimeError()
        recovery_digest = _canonical_digest(recovery)
    except PracticeRuntimeError:
        raise
    except Exception as exc:
        raise PracticeRuntimeError() from exc
    if (
        not callable(candidate_reader)
        or not callable(candidate_ack)
        or not callable(owned_opener)
    ):
        return {
            "schema": _STARTUP_REAPER_SCHEMA,
            "supported": False,
            "recoveredSlots": recovered_slots,
            "recoveryReceiptSha256": recovery_digest,
            "candidateCount": 0,
            "cleanedCount": 0,
            "missingCount": 0,
            "failureCount": 0,
            "acknowledgedCount": 0,
            "cleanupReceiptSha256": [],
            "ackReceiptSha256": [],
            "limitReached": False,
            "privateStorageCoordinatesExposed": False,
        }
    try:
        receipts: list[str] = []
        ack_receipts: list[str] = []
        missing = 0
        failures = 0
        candidate_count = 0
        query_limit = _REAPER_LIMIT
        seen: set[str] = set()
        while candidate_count < _REAPER_MAX_PER_STARTUP:
            candidates = candidate_reader(limit=query_limit)
            if not isinstance(candidates, tuple) or len(candidates) > query_limit:
                raise PracticeRuntimeError()
            if not candidates:
                break
            new_candidates = 0
            for batch_index, candidate in enumerate(candidates):
                try:
                    fingerprint = _canonical_digest(candidate)
                except Exception:
                    type_name = type(candidate).__name__
                    fingerprint = hashlib.sha256(
                        f"unserializable:{batch_index}:{type_name}".encode(
                            "ascii", errors="replace"
                        )
                    ).hexdigest()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                new_candidates += 1
                candidate_count += 1
                try:
                    if not isinstance(candidate, Mapping) or set(candidate) != {
                        "runId",
                        "startArtifactSha256",
                    }:
                        raise ValueError("malformed cleanup candidate")
                    run_id = candidate["runId"]
                    digest = candidate["startArtifactSha256"]
                    if (
                        not isinstance(run_id, str)
                        or not _RUN_ID.fullmatch(run_id)
                        or not isinstance(digest, str)
                        or not _SHA256.fullmatch(digest)
                    ):
                        raise ValueError("malformed cleanup candidate")
                    owned = owned_opener(run_id, digest)
                    if owned is None:
                        missing += 1
                    else:
                        cleanup = sandbox.cleanup(owned)
                        if not isinstance(cleanup, Mapping):
                            raise ValueError("malformed cleanup receipt")
                        receipts.append(_canonical_digest(cleanup))
                    ack = candidate_ack(run_id, digest)
                    if (
                        not isinstance(ack, Mapping)
                        or ack.get("runId") != run_id
                        or ack.get("startArtifactSha256") != digest
                        or ack.get("acknowledged") is not True
                        or ack.get("privateStorageCoordinatesExposed") is not False
                    ):
                        raise ValueError("malformed cleanup acknowledgement")
                    ack_receipts.append(_canonical_digest(ack))
                except Exception:
                    failures += 1
                if candidate_count >= _REAPER_MAX_PER_STARTUP:
                    break
            if new_candidates == 0:
                if query_limit >= _REAPER_MAX_PER_STARTUP:
                    break
                query_limit = min(
                    _REAPER_MAX_PER_STARTUP, query_limit + _REAPER_LIMIT
                )
        return {
            "schema": _STARTUP_REAPER_SCHEMA,
            "supported": True,
            "recoveredSlots": recovered_slots,
            "recoveryReceiptSha256": recovery_digest,
            "candidateCount": candidate_count,
            "cleanedCount": len(receipts),
            "missingCount": missing,
            "failureCount": failures,
            "acknowledgedCount": len(ack_receipts),
            "cleanupReceiptSha256": receipts,
            "ackReceiptSha256": ack_receipts,
            "limitReached": candidate_count >= _REAPER_MAX_PER_STARTUP,
            "privateStorageCoordinatesExposed": False,
        }
    except PracticeRuntimeError:
        raise
    except Exception as exc:
        raise PracticeRuntimeError() from exc


def _environment_signature() -> tuple[str, str, str, str]:
    source = os.environ.get("HWPX_CORPUS_SOURCE", "")
    practice = os.environ.get("HWPX_PRACTICE_ROOT", "")
    skill = os.environ.get("HWPX_SKILL_VERSION", "")
    skill_root = os.environ.get("HWPX_SKILL_ROOT", "")
    if not source or not practice or not skill or not skill_root:
        raise PracticeRuntimeError()
    return source, practice, skill, skill_root


def build_practice_campaign_service(
    namespace: Mapping[str, Any],
    *,
    _allow_same_device_for_tests: bool = False,
) -> PracticeCampaignService:
    """Assemble one production service from strict local environment roots."""

    try:
        source_value, practice_value, skill_version, skill_root_value = (
            _environment_signature()
        )
        source_root = _strict_existing_directory(source_value)
        practice_root = _strict_existing_directory(practice_value)
        source_identity = _root_identity(source_root)
        practice_identity = _root_identity(practice_root)
        # Production hardlink safety is anchored by separate devices plus the
        # no-follow/stable-identity file checks below.  Hostile same-UID root
        # renames are a deployment-isolation threat outside this local runner.
        if (
            source_identity[0] == practice_identity[0]
            and _allow_same_device_for_tests is not True
        ):
            raise PracticeRuntimeError()
        skill_root = _strict_existing_directory(skill_root_value)
        runtime_root = _strict_descendant_directory(practice_root, "runtime")
        children = {
            name: _strict_descendant_directory(runtime_root, name)
            for name in _FIXED_RUNTIME_CHILDREN
        }
        runtime_identity = _root_identity(runtime_root)
        child_identities = {
            name: _root_identity(child) for name, child in children.items()
        }
        if runtime_identity[0] != practice_identity[0] or any(
            identity[0] != practice_identity[0]
            for identity in child_identities.values()
        ):
            raise PracticeRuntimeError()
        source_root, practice_root, sandbox_root = validate_practice_roots(
            source_root, practice_root, children["sandboxes"]
        )
        provenance = _runtime_provenance(runtime_root, skill_root, skill_version)
        workflow_paths = [
            children["workflow"] / "workflows.sqlite3",
            children["workflow"] / "workflows.sqlite3.key",
            *(
                children["workflow"] / f"workflows.sqlite3{suffix}"
                for suffix in ("-journal", "-shm", "-wal")
            ),
        ]
        for workflow_path in workflow_paths:
            _strict_regular_or_absent(
                workflow_path,
                children["workflow"],
                source_root,
            )
        workflow_db = workflow_paths[0]
        resolver = _PrivateCampaignResolver(
            source_root, practice_root, children["campaigns"]
        )
        _assert_root_identity(source_root, source_identity)
        _assert_root_identity(practice_root, practice_identity)
        _assert_root_identity(runtime_root, runtime_identity)
        for name, child in children.items():
            _assert_root_identity(child, child_identities[name])
        workflow_store = WorkflowStore(workflow_db)
        for workflow_path in workflow_paths:
            _strict_regular_or_absent(
                workflow_path,
                children["workflow"],
                source_root,
            )
        _assert_root_identity(source_root, source_identity)
        _assert_root_identity(practice_root, practice_identity)
        _assert_root_identity(runtime_root, runtime_identity)
        for name, child in children.items():
            _assert_root_identity(child, child_identities[name])
        workflow = WorkflowService(namespace, store=workflow_store, capability_ok=True)
        sandbox = PracticeSandboxManager(source_root, practice_root, sandbox_root)
        queue = PracticeCampaignQueue(
            children["queue"],
            source_root=source_root,
            practice_root=practice_root,
        )
        startup_reaper_receipt = _startup_reap(queue, sandbox)
        dispatcher = PracticeWorkflowDispatcher(
            workflow, sandbox, runtime_provenance=lambda: provenance
        )
        service = PracticeCampaignService(
            queue,
            dispatcher,
            manifest_resolver=resolver.manifest,
            task_resolver=resolver.task,
            terminal_artifact_hook=_TerminalArtifactStore(children["results"]),
        )
        service.startup_reaper_receipt = startup_reaper_receipt
        return service
    except PracticeRuntimeError:
        raise
    except Exception as exc:
        raise PracticeRuntimeError() from exc


_RUNTIME_LOCK = threading.Lock()
_RUNTIME_SIGNATURE: tuple[str, str, str, str] | None = None
_RUNTIME_SERVICE: PracticeCampaignService | None = None


def get_practice_campaign_service(
    namespace: Mapping[str, Any],
) -> PracticeCampaignService:
    """Return the process-local lazy singleton without accepting path input."""

    signature = _environment_signature()
    global _RUNTIME_SIGNATURE, _RUNTIME_SERVICE
    with _RUNTIME_LOCK:
        if _RUNTIME_SERVICE is not None:
            if signature != _RUNTIME_SIGNATURE:
                raise PracticeRuntimeError()
            return _RUNTIME_SERVICE
        service = build_practice_campaign_service(namespace)
        _RUNTIME_SIGNATURE = signature
        _RUNTIME_SERVICE = service
        return service


def _reset_practice_campaign_runtime_for_tests() -> None:
    global _RUNTIME_SIGNATURE, _RUNTIME_SERVICE
    with _RUNTIME_LOCK:
        _RUNTIME_SIGNATURE = None
        _RUNTIME_SERVICE = None


__all__ = [
    "PracticeRuntimeError",
    "build_practice_campaign_service",
    "get_practice_campaign_service",
    "installed_runtime_provenance",
]
