"""지정된 작업 디렉터리를 강제하는 파일 시스템 유틸리티."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class WorkdirError(PermissionError):
    """경로가 설정된 작업 디렉터리를 벗어날 때 발생하는 예외."""


@dataclass
class WorkdirGuard:
    """모든 경로가 지정된 루트 아래에 있도록 보장하는 도우미."""

    root: Path

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()

    def ensure_ready(self) -> None:
        """루트 경로가 존재하며 쓰기 가능함을 검증한다."""

        if not self.root.exists():
            raise FileNotFoundError(f"Workdir '{self.root}' does not exist")
        if not self.root.is_dir():
            raise NotADirectoryError(f"Workdir '{self.root}' is not a directory")
        if not os.access(self.root, os.R_OK | os.W_OK):
            raise PermissionError(f"Workdir '{self.root}' must be readable and writable")

    def resolve_path(self, user_path: str, *, must_exist: bool = True) -> Path:
        """작업 디렉터리 안으로 한정된 절대 경로를 반환한다."""

        candidate = Path(user_path).expanduser()
        if not candidate.is_absolute():
            candidate = (self.root / candidate).resolve(strict=False)
        else:
            candidate = candidate.resolve(strict=False)

        if not self._contains(candidate):
            raise WorkdirError(f"Path '{user_path}' escapes configured workdir")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(f"Path '{candidate}' does not exist within workdir")
        return candidate

    def resolve_output_path(self, user_path: str) -> Path:
        """상위 디렉터리를 생성한 뒤 출력 경로를 해석한다."""

        resolved = self.resolve_path(user_path, must_exist=False)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    def relative(self, path: Path) -> str:
        """가능하면 작업 디렉터리를 기준으로 한 상대 경로를 돌려준다."""

        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def ensure_backup(self, path: Path) -> Optional[Path]:
        """경로가 존재하면 ``.bak`` 복사본을 생성한다."""

        if not path.exists():
            return None
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        return backup

    def _contains(self, candidate: Path) -> bool:
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return False
        return True
