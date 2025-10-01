"""Storage backends for HWPX document operations."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Protocol, Tuple
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
        document.save(target)


class HttpDocumentStorage:
    """HTTP based :class:`DocumentStorage` implementation.

    The backend expects endpoints that accept binary payloads using a simple REST
    contract:

    - ``GET {base_url}/documents`` with ``path`` query parameter to download a
      document.
    - ``PUT {base_url}/documents`` with ``path`` query parameter and raw binary
      body to persist a document.

    Backups are not handled automatically for the HTTP backend.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("HTTP storage requires a base URL")
        self.base_directory = Path("/")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._logger = logger or logging.getLogger(__name__)

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
        url = self._build_url(path)
        try:
            with request.urlopen(url, timeout=self._timeout) as response:
                data = response.read()
        except error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(path) from exc
            raise RuntimeError(f"HTTP storage open failed: {exc}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"HTTP storage open failed: {exc}") from exc

        local_path = self._materialize_local_copy(path, data)
        document = HwpxDocument.open(local_path)
        return document, Path(path)

    def save_document(self, document: HwpxDocument, target: Path) -> None:
        with tempfile.NamedTemporaryFile(suffix=target.suffix or ".hwpx", delete=False) as tmp:
            temp_path = Path(tmp.name)
        try:
            document.save(temp_path)
            data = temp_path.read_bytes()
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

        url = self._build_url(str(target))
        req = request.Request(url, data=data, method="PUT")
        req.add_header("Content-Type", "application/octet-stream")
        try:
            with request.urlopen(req, timeout=self._timeout):
                pass
        except error.HTTPError as exc:
            raise RuntimeError(f"HTTP storage save failed: {exc}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"HTTP storage save failed: {exc}") from exc

    def _build_url(self, path: str) -> str:
        query = parse.urlencode({"path": path})
        return f"{self._base_url}/documents?{query}"

    def _materialize_local_copy(self, path: str, data: bytes) -> Path:
        suffix = Path(path).suffix or ".hwpx"
        directory = Path(tempfile.mkdtemp(prefix="hwpx_http_"))
        local_path = directory / (Path(path).name or f"document{suffix}")
        local_path.write_bytes(data)
        return local_path
