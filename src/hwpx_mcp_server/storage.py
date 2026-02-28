"""Storage backends for HWPX document operations."""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Protocol, Tuple
from urllib import error, parse, request

from hwpx.document import HwpxDocument


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

    def save_document(self, document: HwpxDocument, target: Path) -> None:
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
        document = HwpxDocument.open(resolved)
        return document, resolved

    def save_document(self, document: HwpxDocument, target: Path) -> None:
        self.maybe_backup(target)
        # Atomic save: write to temp file → verify → rename
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            suffix=target.suffix, dir=str(target.parent)
        )
        tmp_path = Path(tmp_path_str)
        try:
            import os
            os.close(tmp_fd)
            document.save_to_path(tmp_path)
            # Verify the saved file is a valid HWPX package
            HwpxDocument.open(tmp_path)
            shutil.move(str(tmp_path), str(target))
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


class RemoteDocumentClient(Protocol):
    """Protocol describing the minimal HTTP client interface required."""

    def download(self, path: str) -> bytes:
        """Return the binary payload for *path* from the remote service."""

    def upload(self, path: str, data: bytes) -> None:
        """Persist *data* to *path* on the remote service."""


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
        local_path.write_bytes(payload)
        self._cache[path] = local_path

        document = HwpxDocument.open(local_path)
        return document, Path(path)

    def save_document(self, document: HwpxDocument, target: Path) -> None:
        remote_key = str(target)
        cache_path = self._cache.get(remote_key)
        if cache_path is None:
            cache_path = self._cache_path(remote_key)
            self._cache[remote_key] = cache_path

        try:
            document.save_to_path(cache_path)
            payload = cache_path.read_bytes()
        except Exception as exc:  # pragma: no cover - unexpected save error
            raise RuntimeError(f"HTTP storage save failed: {exc}") from exc

        try:
            self._client.upload(remote_key, payload)
        except Exception as exc:  # pragma: no cover - handled in tests for fake clients
            raise RuntimeError(f"HTTP storage save failed: {exc}") from exc

    def _cache_path(self, path: str) -> Path:
        suffix = Path(path).suffix or ".hwpx"
        safe_name = parse.quote_plus(path)
        filename = safe_name if safe_name.endswith(suffix) else f"{safe_name}{suffix}"
        return self._cache_dir / filename
