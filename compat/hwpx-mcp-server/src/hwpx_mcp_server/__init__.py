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

    6.x 동안 지원한다. 제거는 7.0 이전에는 하지 않으며 최소 90일 공개
    관찰과 별도 오너 승인이 필요하다.
"""

from __future__ import annotations

import importlib
import sys
import warnings
from collections.abc import Sequence
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from importlib.metadata import PackageNotFoundError, version
from types import ModuleType
from typing import Any

__all__ = ["__version__"]

_OLD = "hwpx_mcp_server"
_NEW = "hwpx_automation"
_MISSING = object()
_IMPORT_METADATA = (
    "__name__",
    "__spec__",
    "__loader__",
    "__package__",
    "__file__",
    "__cached__",
    "__path__",
)

try:
    __version__ = version("hwpx-mcp-server")
except PackageNotFoundError:
    # Source-tree execution only. A valid installed compatibility shell always
    # has its own metadata and an exact canonical dependency.
    __version__ = importlib.import_module(_NEW).__version__


def _warn(old_name: str) -> None:
    warnings.warn(
        f"{old_name}은(는) {old_name.replace(_OLD, _NEW, 1)}으로 이동했습니다. "
        f"이 경로는 다음 major에서 사라집니다.",
        DeprecationWarning,
        stacklevel=3,
    )


class _AliasLoader(Loader):
    """이미 로드된 대상 모듈을 옛 이름에 물리되 정본 메타데이터는 보존한다.

    importlib는 ``create_module``이 돌려준 객체에 legacy ``ModuleSpec``을
    덮어쓴다. 그 객체가 정본 모듈과 동일하면 ``__spec__``/loader/package가
    오염되어 ``importlib.resources``와 이후 deep import가 깨진다. 실행은
    한 번만 유지하면서, ``exec_module``에서 정본 속성을 원상 복구한다.
    """

    def __init__(self, target: str, module: ModuleType) -> None:
        self._target = target
        self._module = module
        self._canonical_spec = module.__spec__
        self._metadata = {
            name: getattr(module, name, _MISSING) for name in _IMPORT_METADATA
        }

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        return self._module

    def exec_module(self, module: ModuleType) -> None:
        # 다시 실행하지 않는다. 두 번 실행하면 같은 코드의 상태가 두 벌이 되고,
        # 서로 다른 클래스 객체를 들고 있으면 isinstance 검사가 조용히 깨진다.
        if module is not self._module:  # pragma: no cover - importlib invariant
            raise ImportError(f"legacy alias returned an unexpected module: {module!r}")
        for name, value in self._metadata.items():
            if value is _MISSING:
                module.__dict__.pop(name, None)
            else:
                setattr(module, name, value)

    def get_code(self, fullname: str) -> Any:
        """Delegate runpy (``python -m hwpx_mcp_server``) to canonical code."""

        loader = getattr(self._canonical_spec, "loader", None)
        get_code = getattr(loader, "get_code", None)
        if get_code is None:
            raise ImportError(f"canonical loader cannot provide code for {self._target}")
        return get_code(self._target)

    def get_source(self, fullname: str) -> str | None:
        loader = getattr(self._canonical_spec, "loader", None)
        get_source = getattr(loader, "get_source", None)
        return None if get_source is None else get_source(self._target)

    def is_package(self, fullname: str) -> bool:
        spec = self._canonical_spec
        return bool(spec and spec.submodule_search_locations is not None)


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
            canonical = importlib.import_module(new_name)
        except ModuleNotFoundError as exc:
            # Only translate "the requested canonical module does not exist"
            # into the normal legacy-module-not-found result.  A dependency
            # imported *inside* an existing canonical module must retain its
            # original traceback; swallowing it hid broken optional/runtime
            # dependencies as a misleading migration error.
            if exc.name == new_name:
                return None
            raise
        _warn(fullname)
        canonical_spec = canonical.__spec__
        is_package = bool(
            canonical_spec
            and canonical_spec.submodule_search_locations is not None
        )
        spec = ModuleSpec(
            fullname,
            _AliasLoader(new_name, canonical),
            origin=getattr(canonical_spec, "origin", None),
            is_package=is_package,
        )
        spec.has_location = bool(
            canonical_spec and canonical_spec.has_location
        )
        if (
            spec.submodule_search_locations is not None
            and canonical_spec is not None
            and canonical_spec.submodule_search_locations is not None
        ):
            spec.submodule_search_locations[:] = (
                canonical_spec.submodule_search_locations
            )
        return spec


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
