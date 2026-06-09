# SPDX-License-Identifier: Apache-2.0
"""Storage backends for HWPX document operations."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple
from urllib import error, parse, request

try:  # python-hwpx >= 2.10.3
    from hwpx.tools.package_validator import validate_package
except Exception as exc:  # pragma: no cover - depends on installed python-hwpx
    validate_package = None
    _PACKAGE_VALIDATOR_IMPORT_ERROR: Exception | None = exc
else:
    _PACKAGE_VALIDATOR_IMPORT_ERROR = None

try:  # python-hwpx >= 2.10.3
    from hwpx.tools.package_validator import is_editor_open_blocking_issue
except Exception as exc:  # pragma: no cover - depends on installed python-hwpx
    is_editor_open_blocking_issue = None
    _OPEN_SAFETY_CLASSIFIER_IMPORT_ERROR: Exception | None = exc
else:
    _OPEN_SAFETY_CLASSIFIER_IMPORT_ERROR = None

from .upstream import HwpxDocument, open_document, validate_document_path

_REQUIRED_HWPX_FILES = [
    "mimetype",
    "Contents/content.hpf",
    "Contents/header.xml",
    "Contents/section0.xml",
]
_SECTION_XML_RE = re.compile(r"^Contents/section\d+\.xml$")
_PLACEHOLDER_PATTERNS = [
    re.compile(r"\[[^\[\]\n]{1,100}\]"),
    re.compile(r"\[\[[^\[\]\n]{1,100}\]\]"),
    re.compile(r"\{\{[^{}\n]{1,100}\}\}"),
    re.compile(r"__[^_\n]{1,100}__"),
]
_UNESCAPED_AMP_RE = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)")
_NESTED_OPENING_TAG_RE = re.compile(r"<hp:t\b[^>]*>[^<]*(<(?!/?hp:)[^>]+>)")
_EMPTY_HP_T_RE = re.compile(r"<hp:t\b[^>]*>\s*</hp:t>")


class DocumentStorage(Protocol):
    """Protocol describing storage backends used by :class:`HwpxOps`."""

    base_directory: Path

    def resolve_path(self, path: str, *, must_exist: bool = True) -> Path:
        """Return the backend-specific absolute path for *path*."""

    def resolve_output_path(self, path: str) -> Path:
        """Return a path suitable for writing output."""

    def relative_path(self, path: Path) -> str:
        """Return a user-friendly relative representation of *path*."""

    def ensure_backup(self, path: Path) -> Optional[Path]:
        """Create a backup of *path* if it exists, returning the backup path."""

    def maybe_backup(self, path: Path) -> None:
        """Create a backup of *path* when backend policy requires it."""

    def open_document(self, path: str) -> Tuple[HwpxDocument, Path]:
        """Open the document located at *path* and return it with the resolved path."""

    def save_document(self, document: HwpxDocument, target: Path) -> Dict[str, Any]:
        """Persist *document* to *target* using backend specific rules."""


class LocalDocumentStorage:
    """Filesystem based :class:`DocumentStorage` implementation."""

    def __init__(
        self,
        *,
        base_directory: Path | None = None,
        auto_backup: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        self.base_directory = (base_directory or Path.cwd()).expanduser().resolve()
        self._auto_backup = auto_backup
        self._logger = logger or logging.getLogger(__name__)

    def resolve_path(self, path: str, *, must_exist: bool = True) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = (self.base_directory / candidate).resolve(strict=False)
        else:
            candidate = candidate.resolve(strict=False)
        if must_exist and not candidate.exists():
            raise FileNotFoundError(f"Path '{candidate}' does not exist")
        return candidate

    def resolve_output_path(self, path: str) -> Path:
        resolved = self.resolve_path(path, must_exist=False)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    def relative_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.base_directory))
        except ValueError:
            return str(path)

    def ensure_backup(self, path: Path) -> Optional[Path]:
        if not path.exists():
            return None
        if path.suffix.lower() == ".hwpx":
            require_hwpx_editor_open_safe(path, role="backup source")
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        return backup

    def maybe_backup(self, path: Path) -> None:
        if not self._auto_backup:
            return
        backup = self.ensure_backup(path)
        if backup is not None:
            self._logger.info(
                "created backup",
                extra={"path": str(path), "backup": str(backup)},
            )

    def open_document(self, path: str) -> Tuple[HwpxDocument, Path]:
        resolved = self.resolve_path(path)
        require_hwpx_editor_open_safe(resolved, role="local HWPX open")
        document = open_document(resolved)
        return document, resolved

    def save_document(self, document: HwpxDocument, target: Path) -> Dict[str, Any]:
        self.maybe_backup(target)
        pre_save_snapshot = build_hwpx_presave_snapshot(target)
        # Atomic save: write to a sibling temp file, verify it, then replace.
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            suffix=target.suffix, dir=str(target.parent)
        )
        tmp_path = Path(tmp_path_str)
        try:
            os.close(tmp_fd)
            document.save_to_path(tmp_path)
            verification_report = build_hwpx_verification_report(tmp_path, pre_save_snapshot)
            if not verification_report["openSafety"]["ok"]:
                raise RuntimeError(
                    "saved HWPX failed open-safety verification: "
                    + verification_report["openSafety"]["summary"]
                )
            os.replace(tmp_path, target)
            verification_report["filePath"] = str(target)
            return verification_report
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


class RemoteDocumentClient(Protocol):
    """Protocol describing the minimal HTTP client interface required."""

    def download(self, path: str) -> bytes:
        """Return the binary payload for *path* from the remote service."""

    def upload(self, path: str, data: bytes) -> None:
        """Persist *data* to *path* on the remote service."""


def _report_allows_editor_open(report: Mapping[str, Any]) -> bool:
    package = report.get("validatePackage", {})
    reopen = report.get("reopen", {})
    return bool(
        isinstance(package, Mapping)
        and isinstance(reopen, Mapping)
        and package.get("ok")
        and reopen.get("ok")
    )


def require_hwpx_editor_open_safe(
    path: Path,
    *,
    role: str,
) -> Dict[str, Any]:
    """Fail only on conditions expected to stop an editor from opening HWPX."""

    open_safety = build_hwpx_open_safety_report(path)
    if not _report_allows_editor_open(open_safety):
        raise RuntimeError(
            f"{role} failed open-safety verification: "
            + open_safety["summary"]
        )
    return open_safety


@dataclass(slots=True)
class _RestDocumentClient:
    """Default HTTP client used by :class:`HttpDocumentStorage`."""

    base_url: str
    timeout: float | None
    headers: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("HTTP storage requires a base URL")
        self._opener = request.build_opener()

    def download(self, path: str) -> bytes:
        url = self._build_url(path)
        req = request.Request(url, method="GET")
        for key, value in self.headers.items():
            req.add_header(key, value)
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                return response.read()
        except error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(path) from exc
            raise RuntimeError(f"HTTP download failed: {exc}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"HTTP download failed: {exc}") from exc

    def upload(self, path: str, data: bytes) -> None:
        url = self._build_url(path)
        req = request.Request(url, data=data, method="PUT")
        for key, value in self.headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/octet-stream")
        try:
            with self._opener.open(req, timeout=self.timeout):
                return None
        except error.HTTPError as exc:
            raise RuntimeError(f"HTTP upload failed: {exc}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"HTTP upload failed: {exc}") from exc

    def _build_url(self, path: str) -> str:
        query = parse.urlencode({"path": path})
        return f"{self.base_url.rstrip('/')}/documents?{query}"


class HttpDocumentStorage:
    """HTTP based :class:`DocumentStorage` implementation with local caching."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
        client: RemoteDocumentClient | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if client is None and not base_url:
            raise ValueError("HTTP storage requires either a base URL or a client")

        self.base_directory = Path("/")
        self._logger = logger or logging.getLogger(__name__)
        self._headers = dict(headers or {})
        self._client = client or _RestDocumentClient(base_url=base_url or "", timeout=timeout, headers=self._headers)
        self._cache_dir = Path(tempfile.mkdtemp(prefix="hwpx_http_cache_"))
        self._cache: Dict[str, Path] = {}

    def resolve_path(self, path: str, *, must_exist: bool = True) -> Path:
        # HTTP storage treats the provided path as an opaque identifier.
        return Path(path)

    def resolve_output_path(self, path: str) -> Path:
        return self.resolve_path(path, must_exist=False)

    def relative_path(self, path: Path) -> str:
        return str(path)

    def ensure_backup(self, path: Path) -> Optional[Path]:
        # Backups are left to the remote service.
        return None

    def maybe_backup(self, path: Path) -> None:
        # No-op; backups must be handled remotely if supported.
        return None

    def open_document(self, path: str) -> Tuple[HwpxDocument, Path]:
        try:
            payload = self._client.download(path)
        except FileNotFoundError:
            raise
        except Exception as exc:  # pragma: no cover - handled in tests for fake clients
            raise RuntimeError(f"HTTP storage open failed: {exc}") from exc

        local_path = self._cache_path(path)
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            suffix=local_path.suffix or ".hwpx",
            dir=str(local_path.parent),
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(tmp_fd, "wb") as tmp_fh:
                tmp_fh.write(payload)
            require_hwpx_editor_open_safe(tmp_path, role="HTTP storage open")
            os.replace(tmp_path, local_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        self._cache[path] = local_path

        document = open_document(local_path)
        return document, Path(path)

    def save_document(self, document: HwpxDocument, target: Path) -> Dict[str, Any]:
        remote_key = str(target)
        cache_path = self._cache.get(remote_key)
        if cache_path is None:
            cache_path = self._cache_path(remote_key)
            self._cache[remote_key] = cache_path

        pre_save_snapshot = build_hwpx_presave_snapshot(cache_path if cache_path.exists() else None)
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            suffix=cache_path.suffix or ".hwpx",
            dir=str(cache_path.parent),
        )
        tmp_path = Path(tmp_path_str)

        try:
            os.close(tmp_fd)
            document.save_to_path(tmp_path)
            verification_report = build_hwpx_verification_report(tmp_path, pre_save_snapshot)
            if not verification_report["openSafety"]["ok"]:
                raise RuntimeError(
                    "saved HWPX failed open-safety verification: "
                    + verification_report["openSafety"]["summary"]
                )
            payload = tmp_path.read_bytes()
        except Exception as exc:  # pragma: no cover - unexpected save error
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(f"HTTP storage save failed: {exc}") from exc

        try:
            self._client.upload(remote_key, payload)
        except Exception as exc:  # pragma: no cover - handled in tests for fake clients
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(f"HTTP storage save failed: {exc}") from exc
        os.replace(tmp_path, cache_path)
        return verification_report

    def _cache_path(self, path: str) -> Path:
        suffix = Path(path).suffix or ".hwpx"
        safe_name = parse.quote_plus(path)
        filename = safe_name if safe_name.endswith(suffix) else f"{safe_name}{suffix}"
        return self._cache_dir / filename


def build_hwpx_presave_snapshot(path: Path | None) -> Dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return _collect_hwpx_snapshot(path)


def _issue_messages(report: Any, attr_name: str = "issues") -> List[str]:
    return [str(issue) for issue in getattr(report, attr_name, ())]


def _open_safety_dependency_error() -> str | None:
    if validate_package is None:
        detail = (
            str(_PACKAGE_VALIDATOR_IMPORT_ERROR)
            if _PACKAGE_VALIDATOR_IMPORT_ERROR is not None
            else "hwpx.tools.package_validator.validate_package is unavailable"
        )
        return f"python-hwpx>=2.10.3 is required for HWPX open-safety validation: {detail}"
    if is_editor_open_blocking_issue is None:
        detail = (
            str(_OPEN_SAFETY_CLASSIFIER_IMPORT_ERROR)
            if _OPEN_SAFETY_CLASSIFIER_IMPORT_ERROR is not None
            else "hwpx.tools.package_validator.is_editor_open_blocking_issue is unavailable"
        )
        return f"python-hwpx>=2.10.3 is required for HWPX open-safety validation: {detail}"
    return None


def build_hwpx_open_safety_report(path: Path) -> Dict[str, Any]:
    package_payload: Dict[str, Any]
    document_payload: Dict[str, Any]
    reopen_payload: Dict[str, Any]

    dependency_error = _open_safety_dependency_error()
    if dependency_error is not None:
        package_payload = {
            "ok": False,
            "validatorOk": False,
            "errors": [dependency_error],
            "warnings": [],
            "validatorErrors": [dependency_error],
        }
    else:
        try:
            assert validate_package is not None
            assert is_editor_open_blocking_issue is not None
            package_report = validate_package(path)
            package_errors = _issue_messages(package_report, "errors")
            blocking_issues = [
                issue for issue in package_report.errors if is_editor_open_blocking_issue(issue)
            ]
            advisory_issues = [
                issue for issue in package_report.errors if not is_editor_open_blocking_issue(issue)
            ]
            blocking_package_errors = [str(issue) for issue in blocking_issues]
            compatibility_warnings = [str(issue) for issue in advisory_issues]
            package_payload = {
                "ok": not blocking_package_errors,
                "validatorOk": bool(package_report.ok),
                "errors": blocking_package_errors,
                "warnings": [*_issue_messages(package_report, "warnings"), *compatibility_warnings],
                "validatorErrors": package_errors,
            }
        except Exception as exc:  # noqa: BLE001
            package_payload = {
                "ok": False,
                "validatorOk": False,
                "errors": [str(exc)],
                "warnings": [],
                "validatorErrors": [str(exc)],
            }

    try:
        document_report = validate_document_path(path)
        document_payload = {
            "ok": bool(document_report.ok),
            "errors": _issue_messages(document_report, "errors"),
            "warnings": _issue_messages(document_report, "warnings"),
        }
    except Exception as exc:  # noqa: BLE001
        document_payload = {
            "ok": False,
            "errors": [str(exc)],
            "warnings": [],
        }

    try:
        reopened = open_document(path)
        close = getattr(reopened, "close", None)
        if callable(close):
            close()
        reopen_payload = {"ok": True, "error": None}
    except Exception as exc:  # noqa: BLE001
        reopen_payload = {"ok": False, "error": str(exc)}

    ok = bool(package_payload["ok"] and document_payload["ok"] and reopen_payload["ok"])
    failures: List[str] = []
    if not package_payload["ok"]:
        failures.append("package validation failed")
    if not document_payload["ok"]:
        failures.append("document validation failed")
    if not reopen_payload["ok"]:
        failures.append("reopen failed")

    return {
        "ok": ok,
        "summary": "open-safety verification passed" if ok else "; ".join(failures),
        "validatePackage": package_payload,
        "validateDocument": document_payload,
        "reopen": reopen_payload,
    }


def build_hwpx_verification_report(path: Path, pre_save_snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    snapshot = _collect_hwpx_snapshot(path)
    open_safety = build_hwpx_open_safety_report(path)
    totals = snapshot["totals"]
    missing_files = snapshot["missing_files"]
    warnings: List[str] = []
    if missing_files:
        warnings.append(f"missing required files: {', '.join(missing_files)}")
    if totals["placeholders"]:
        warnings.append("placeholder-like tokens remain in saved document")
    if totals["suspiciousPatterns"]:
        warnings.append("suspicious XML/text patterns detected in saved document")

    diff_summary = {
        "xmlLength": 0,
        "hpTabs": 0,
        "paragraphs": 0,
        "tables": 0,
    }
    if pre_save_snapshot is not None:
        before = pre_save_snapshot["totals"]
        diff_summary = {
            "xmlLength": totals["xmlLength"] - before["xmlLength"],
            "hpTabs": totals["hpTabs"] - before["hpTabs"],
            "paragraphs": totals["paragraphs"] - before["paragraphs"],
            "tables": totals["tables"] - before["tables"],
        }

    ok = (
        open_safety["ok"]
        and not missing_files
        and not totals["placeholders"]
        and not totals["suspiciousPatterns"]
    )
    summary = "verification passed"
    if not ok:
        summary = "; ".join(warnings) if warnings else "verification failed"

    return {
        "ok": ok,
        "summary": summary,
        "filePath": str(path),
        "fileSizeBytes": path.stat().st_size,
        "requiredFilesChecked": list(_REQUIRED_HWPX_FILES),
        "missingFiles": missing_files,
        "openSafety": open_safety,
        "sectionReports": snapshot["section_reports"],
        "totals": {
            "sections": totals["sections"],
            "xmlLength": totals["xmlLength"],
            "hpTabs": totals["hpTabs"],
            "paragraphs": totals["paragraphs"],
            "tables": totals["tables"],
            "placeholders": totals["placeholders"],
            "suspiciousPatterns": totals["suspiciousPatterns"],
        },
        "diffSummary": diff_summary,
        "warnings": warnings,
    }


def _collect_hwpx_snapshot(path: Path) -> Dict[str, Any]:
    missing_files: List[str] = []
    section_reports: List[Dict[str, Any]] = []
    totals = {
        "sections": 0,
        "xmlLength": 0,
        "hpTabs": 0,
        "paragraphs": 0,
        "tables": 0,
        "placeholders": 0,
        "suspiciousPatterns": 0,
    }

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for required in _REQUIRED_HWPX_FILES:
            if required not in names:
                missing_files.append(required)

        section_names = sorted(name for name in names if _SECTION_XML_RE.match(name))
        totals["sections"] = len(section_names)
        for section_name in section_names:
            xml_text = archive.read(section_name).decode("utf-8", errors="replace")
            section_index = int(section_name.removeprefix("Contents/section").removesuffix(".xml"))
            placeholder_examples: List[str] = []
            placeholder_count = 0
            for pattern in _PLACEHOLDER_PATTERNS:
                for match in pattern.findall(xml_text):
                    placeholder_count += 1
                    if match not in placeholder_examples and len(placeholder_examples) < 5:
                        placeholder_examples.append(match)

            suspicious_patterns: List[str] = []
            if _UNESCAPED_AMP_RE.search(xml_text):
                suspicious_patterns.append("unescaped_ampersand")
            if _EMPTY_HP_T_RE.search(xml_text):
                suspicious_patterns.append("empty_hp_t")
            if _NESTED_OPENING_TAG_RE.search(xml_text):
                suspicious_patterns.append("nested_opening_tag_in_text")
            if ">>" in xml_text or "<<" in xml_text:
                suspicious_patterns.append("double_angle_marker")

            paragraph_count = xml_text.count("<hp:p")
            table_count = xml_text.count("<hp:tbl")
            hp_tab_count = xml_text.count("<hp:tab")
            xml_length = len(xml_text)

            totals["xmlLength"] += xml_length
            totals["hpTabs"] += hp_tab_count
            totals["paragraphs"] += paragraph_count
            totals["tables"] += table_count
            totals["placeholders"] += placeholder_count
            totals["suspiciousPatterns"] += len(suspicious_patterns)

            section_reports.append(
                {
                    "section": section_index,
                    "xmlDeclaration": xml_text.startswith("<?xml"),
                    "truncatedXml": bool(re.search(r"<[^>]*$", xml_text)),
                    "brokenTagPattern": bool(re.search(r"<[^>]*<", xml_text)),
                    "xmlLength": xml_length,
                    "hpTabs": hp_tab_count,
                    "paragraphs": paragraph_count,
                    "tables": table_count,
                    "placeholderCount": placeholder_count,
                    "placeholderExamples": placeholder_examples,
                    "suspiciousPatternCount": len(suspicious_patterns),
                    "suspiciousPatterns": suspicious_patterns,
                }
            )

    return {
        "missing_files": missing_files,
        "section_reports": section_reports,
        "totals": totals,
    }
