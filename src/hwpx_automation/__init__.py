# SPDX-License-Identifier: Apache-2.0
"""HWPX 문서 자동화 — 업무 워크플로·에이전트 런타임, MCP 어댑터 포함."""

from importlib.metadata import version, PackageNotFoundError

#: 배포명 후보. 새 이름이 정본이고, 옛 이름은 호환 셸만 설치된 환경을 위한
#: 대비다 — 셸은 새 배포를 끌어오므로 보통은 첫 번째에서 끝난다.
_DISTRIBUTIONS = ("python-hwpx-automation", "hwpx-mcp-server")


def _resolve_version() -> str:
    for name in _DISTRIBUTIONS:
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return "0.0.0"  # 체크아웃에서 직접 실행하는 개발 경로


__version__ = _resolve_version()

__all__ = ["__version__"]
