# SPDX-License-Identifier: Apache-2.0
"""옛 import 경로. ``hwpx_automation``으로 넘긴다.

재내보내기가 아니라 **위임**이다. 이름을 하나씩 베껴두면 새 이름에 무언가
추가될 때마다 여기가 낡는다. ``__getattr__``은 그럴 일이 없다.

한 major 동안만 산다. 그동안 접근할 때마다 새 경로를 알려준다.
"""

from __future__ import annotations

import importlib
import warnings
from typing import Any

__all__: list[str] = []

_TARGET = "hwpx_automation"


def __getattr__(name: str) -> Any:
    warnings.warn(
        f"hwpx_mcp_server.{name}은(는) {_TARGET}.{name}으로 이동했습니다. "
        f"이 경로는 다음 major에서 사라집니다.",
        DeprecationWarning,
        stacklevel=2,
    )
    try:
        return getattr(importlib.import_module(_TARGET), name)
    except AttributeError:
        # 서브모듈일 수 있다. hwpx_mcp_server.office 같은 경로가 여기로 온다.
        return importlib.import_module(f"{_TARGET}.{name}")


def __dir__() -> list[str]:
    return dir(importlib.import_module(_TARGET))
