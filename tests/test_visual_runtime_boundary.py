# SPDX-License-Identifier: Apache-2.0
"""렌더 계열 안에서 중립 기하 계약과 한컴 실행부가 섞이지 않는지 검사한다.

이 파일은 원래 core의 ``hwpx.visual`` 안에서 그 둘이 갈라져 있는지를 봤다
(S-104). 5.0 경계 마감으로 ``hwpx.visual`` 전체가 이 소유자에게 왔으므로,
"core 안에서 갈라졌는가"와 "core 4.x 표면이 동결됐는가"는 **대상이 없어져**
사라졌다 — 분리가 끝나서지 검사가 불편해서가 아니다. 동결 데이터
``visual_runtime_4x_freeze.json``은 그 시점의 기록으로 레포에 남는다.

살아남은 질문은 그대로다. **중립 기하 계약(glyph→block 분할, 이미지 diff,
PDF 래스터화)은 한컴을 실행하지 않아야 한다.** 그 성질은 모듈이 어느 패키지에
있든 참이어야 하고, 실제로 그것 때문에 exam 조판이 렌더 없이도 문항 분할을
계산할 수 있다.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from hwpx_mcp_server.office.rendering.block_splits import Block, BlockSplit, detect_block_splits

ROOT = Path(__file__).resolve().parents[1]
RENDERING = ROOT / "src" / "hwpx_mcp_server" / "office" / "rendering"

#: 렌더러를 구동하지 않는 순수 기하·이미지 계약.
NEUTRAL = ("block_splits.py", "detectors.py", "diff.py", "qa_contracts.py")

#: 한컴을 찾거나 띄우거나, 그 산출물을 오케스트레이션하는 실행부.
APPLICATION = ("oracle.py", "worker.py", "page_qa.py", "qa_metrics.py", "fixture_corpus.py")

#: 중립 계약이 손대면 안 되는 것들. subprocess·COM은 곧 한컴 실행이다.
FORBIDDEN_IN_NEUTRAL = {"subprocess", "win32com", "comtypes"}


class _Glyph:
    def __init__(self, page: int, x0: float, x1: float) -> None:
        self.page, self.x0, self.x1 = page, x0, x1


def _module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_split_survived_the_move() -> None:
    """양쪽 파일이 다 제자리에 있어야 아래 검사들이 의미를 가진다."""
    for name in NEUTRAL + APPLICATION:
        assert (RENDERING / name).is_file(), f"{name}이 렌더 소유자에 없다"


def test_neutral_contract_never_reaches_a_renderer() -> None:
    """중립 계약이 한컴을 실행하는 수단을 import하면 안 된다.

    이게 깨지면 "렌더 없이 문항 분할을 계산한다"가 거짓이 되고, 오라클이 없는
    환경에서 조판이 조용히 다른 답을 낸다.
    """
    offences: list[str] = []
    for name in NEUTRAL:
        for module in _module_names(RENDERING / name):
            if module.split(".")[0] in FORBIDDEN_IN_NEUTRAL:
                offences.append(f"{name}: {module}")
    assert not offences, "중립 계약이 렌더러 실행 수단에 닿는다:\n  " + "\n  ".join(offences)


def test_neutral_contract_does_not_import_the_application_side() -> None:
    """의존은 한 방향이다 — 실행부가 계약을 쓰지, 그 반대가 아니다."""
    application_modules = {name.removesuffix(".py") for name in APPLICATION}
    offences: list[str] = []
    for name in NEUTRAL:
        for module in _module_names(RENDERING / name):
            if module.rsplit(".", 1)[-1] in application_modules:
                offences.append(f"{name} → {module}")
    assert not offences, "중립 계약이 실행부를 import한다:\n  " + "\n  ".join(offences)


def test_importing_the_contract_does_not_start_the_runtime() -> None:
    """정적 검사만으로는 부족하다 — 실제로 띄워보고 확인한다.

    지연 import는 AST에 안 잡힌다. 자식 프로세스에서 중립 계약만 import한 뒤
    ``sys.modules``를 보면 실행부가 딸려 왔는지 알 수 있다.
    """
    script = """
import json
import sys
import hwpx_mcp_server.office.rendering.block_splits
import hwpx_mcp_server.office.rendering.detectors
import hwpx_mcp_server.office.rendering.diff
import hwpx_mcp_server.office.rendering.qa_contracts
application = [
    "hwpx_mcp_server.office.rendering.oracle",
    "hwpx_mcp_server.office.rendering.worker",
    "hwpx_mcp_server.office.rendering.page_qa",
    "hwpx_mcp_server.office.rendering.qa_metrics",
    "hwpx_mcp_server.office.rendering.fixture_corpus",
]
print(json.dumps([name for name in application if name in sys.modules]))
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert process.returncode == 0, process.stderr
    activated = json.loads(process.stdout.strip().splitlines()[-1])
    assert activated == [], f"중립 계약 import가 실행부를 깨웠다: {activated}"


def test_block_split_geometry_is_behavior_compatible() -> None:
    """분할 판정이 이동 전과 같은 답을 내는지 — 계약의 본체."""
    blocks = [
        Block("page", [_Glyph(0, 1, 2), _Glyph(1, 1, 2)]),
        Block("column", [_Glyph(0, 1, 2), _Glyph(0, 11, 12)]),
        Block("clean", [_Glyph(0, 1, 2), _Glyph(0, 2, 3)]),
    ]
    assert detect_block_splits(blocks, [(0, 5), (10, 15)], 100) == [
        BlockSplit("page", "page"),
        BlockSplit("column", "column"),
    ]
