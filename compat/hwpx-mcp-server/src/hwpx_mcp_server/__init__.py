# SPDX-License-Identifier: Apache-2.0
"""옛 import 경로. ``hwpx_automation``으로 넘긴다.

모듈 ``__getattr__``만으로는 부족하다. ``import hwpx_mcp_server.office`` 처럼
점이 들어간 import는 그 훅을 **거치지 않고** import 시스템이 곧장 서브모듈을
찾으러 간다. 처음 만든 shim은 그래서 ``hwpx_mcp_server``는 되고
``hwpx_mcp_server.office``는 안 되는 반쪽이었다 — 실제로 코드가 쓰는 형태가
후자인데도.

그래서 meta path finder를 둔다. ``hwpx_mcp_server``로 시작하는 이름을 가로채
대응하는 ``hwpx_automation`` 모듈을 로드해 옛 이름으로도 등록한다. 서브모듈을
하나씩 베껴 적는 방식이면 새 이름에 무언가 추가될 때마다 여기가 낡는다.

한 major 동안만 산다. 그동안 접근할 때마다 새 경로를 알려준다.
"""

from __future__ import annotations

import importlib
import sys
import warnings
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Any, Sequence

__all__: list[str] = []

_OLD = "hwpx_mcp_server"
_NEW = "hwpx_automation"


def _warn(old_name: str) -> None:
    warnings.warn(
        f"{old_name}은(는) {old_name.replace(_OLD, _NEW, 1)}으로 이동했습니다. "
        f"이 경로는 다음 major에서 사라집니다.",
        DeprecationWarning,
        stacklevel=3,
    )


class _AliasLoader(Loader):
    """이미 로드된 대상 모듈을 옛 이름에 그대로 물린다."""

    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        return importlib.import_module(self._target)

    def exec_module(self, module: ModuleType) -> None:
        # 다시 실행하지 않는다. 두 번 실행하면 같은 코드의 상태가 두 벌이 되고,
        # 서로 다른 클래스 객체를 들고 있으면 isinstance 검사가 조용히 깨진다.
        return None


class _AliasFinder(MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if not fullname.startswith(f"{_OLD}."):
            return None
        new_name = fullname.replace(_OLD, _NEW, 1)
        try:
            importlib.import_module(new_name)
        except ImportError:
            return None
        _warn(fullname)
        return ModuleSpec(fullname, _AliasLoader(new_name))


if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())


def __getattr__(name: str) -> Any:
    """``from hwpx_mcp_server import office`` 형태를 받는다."""

    _warn(f"{_OLD}.{name}")
    module = importlib.import_module(_NEW)
    try:
        return getattr(module, name)
    except AttributeError:
        return importlib.import_module(f"{_NEW}.{name}")


def __dir__() -> list[str]:
    return dir(importlib.import_module(_NEW))
