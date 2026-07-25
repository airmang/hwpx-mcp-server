#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI가 못박은 core 커밋이 이 패키지의 바닥 버전을 만족하는지 본다.

워크플로는 ``python-hwpx``를 **정확한 커밋으로** 체크아웃한다. 재현성을 위한
선택이지만, 대가가 있다 — 이 패키지의 바닥을 올려도 그 핀은 손으로 갱신하기
전까지 옛 커밋을 가리킨다. 그러면 CI는 조용히 **틀린 core로** 빌드하고
테스트한다. 6.0.0을 준비하는 동안 두 워크플로가 4.2.0 커밋을 가리키고 있었고,
로컬 스위트는 워크트리를 쓰므로 전부 통과했다. 태그를 단 뒤에야 터졌을 것이다.

그래서 CI 안에서 한 번 더 묻는다. 체크아웃된 core가 실제로 선언한 바닥을
만족하는가. 아니면 여기서 크게 실패한다.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


def _satisfies(installed: str, specifier: str) -> bool:
    """PEP 440으로 판정한다.

    숫자 세 개만 뽑아 비교하면 ``5.0.0rc1``과 ``5.0.0``이 같은 값이 되어,
    릴리스 후보가 정식 릴리스를 만족한다고 잘못 통과한다. packaging이 있으면
    그걸 쓰고, 없으면 그 사실을 말하고 실패한다 — 조용히 헐거운 비교로
    내려가는 것이 이 스크립트가 막으려는 바로 그 부류의 사고다.
    """
    from packaging.specifiers import SpecifierSet
    from packaging.version import InvalidVersion, Version

    try:
        return Version(installed) in SpecifierSet(specifier, prereleases=True) and (
            Version(installed) >= Version(specifier.lstrip(">="))
        )
    except InvalidVersion:
        return False


def main() -> int:
    here = Path(__file__).resolve().parents[1]
    core = Path(sys.argv[1] if len(sys.argv) > 1 else here.parent / "python-hwpx")

    core_pyproject = core / "pyproject.toml"
    if not core_pyproject.is_file():
        print(f"core 체크아웃을 찾지 못했다: {core}", file=sys.stderr)
        return 2

    core_version = tomllib.loads(core_pyproject.read_text(encoding="utf-8"))["project"]["version"]
    declared = tomllib.loads((here / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    floor = next(
        (dep for dep in declared["dependencies"] if dep.startswith("python-hwpx")), ""
    )
    match = re.search(r">=\s*([\d.]+)", floor)
    if not match:
        print(f"python-hwpx 바닥을 읽지 못했다: {floor!r}", file=sys.stderr)
        return 2

    if not _satisfies(core_version, f">={match.group(1)}"):
        print(
            f"핀이 낡았다: 체크아웃된 python-hwpx는 {core_version}인데 "
            f"{declared['name']} {declared['version']}은(는) {floor}을(를) 요구한다.\n"
            f"  .github/workflows/*.yml의 `ref:`를 새 core 릴리스 커밋으로 갱신할 것.",
            file=sys.stderr,
        )
        return 1

    print(f"핀 정합: python-hwpx {core_version} >= {match.group(1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
