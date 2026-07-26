# SPDX-License-Identifier: Apache-2.0
"""core가 안내하는 이행 경로가 실제로 이 패키지 안에 있는지 본다.

``hwpx`` 5.0은 제거된 이름 47개에 "여기로 가라"는 메시지를 붙였다. 그 표를
사람이 손으로 적었고, **대상에 그 이름이 정말 있는지는 아무도 확인하지
않았다.** 12개가 실제로는 서브모듈에 있는데 패키지를 가리키고 있었다 — 안내를
따른 사용자가 두 번째 ImportError를 만나는 상태였다.

이 검사가 core가 아니라 여기 있는 이유는 하나다. core는 이 패키지를 import하지
않고 앞으로도 하면 안 된다(그 단방향이 5.0의 요점이다). 그러니 "core의 약속이
지켜지는가"는 **약속을 지켜야 하는 쪽**에서 확인해야 한다.
"""

from __future__ import annotations

import importlib

import pytest

hwpx = pytest.importorskip("hwpx")

def test_every_core_migration_hint_points_somewhere_real() -> None:
    table = getattr(hwpx, "_MOVED_TO_COMPANION", None)
    assert table, "core에 이행 안내 표가 없다 — 이름이 바뀌었는지 확인할 것"

    broken: list[str] = []
    for name, target in sorted(table.items()):
        kind = getattr(target, "kind", None)
        target_module = getattr(target, "target_module", None)
        target_name = getattr(target, "target_name", None)
        statement = target.import_statement(name)
        if (
            kind not in {"symbol", "module", "renamed"}
            or not isinstance(target_module, str)
            or not target_module
            or not isinstance(statement, str)
            or not statement
        ):
            broken.append(f"{name}: 구조화된 migration hint가 아님 ({target!r})")
            continue
        try:
            module = importlib.import_module(target_module)
        except ImportError as exc:
            broken.append(f"{name}: {target_module} import 실패 ({exc})")
            continue
        if kind == "symbol" or target_name is not None:
            resolved_name = target_name or name
            if not hasattr(module, resolved_name):
                broken.append(f"{name}: {target_module}에 {resolved_name}이(가) 없다")
                continue
        namespace: dict[str, object] = {}
        try:
            exec(statement, namespace)
        except (ImportError, AttributeError) as exc:
            broken.append(f"{name}: `{statement}` 실행 실패 ({exc})")
            continue
        if name not in namespace:
            broken.append(f"{name}: `{statement}`가 legacy 이름을 bind하지 않음")

    assert not broken, (
        f"core의 이행 안내 {len(broken)}건이 없는 곳을 가리킨다 — 안내를 따르면 "
        "두 번째 ImportError를 만난다:\n  " + "\n  ".join(broken)
    )
