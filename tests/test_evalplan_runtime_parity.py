# SPDX-License-Identifier: Apache-2.0
"""Behavior parity between the MCP owner and the frozen core 4.x copy."""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
from hwpx import evalplan_fill as frozen

from hwpx_mcp_server.office.evalplan import runtime as canonical

SYNTHETIC_MD = """# 2026학년도 2학기 3학년 「합성 과목」 교수학습운영 및 평가계획

> **담당교사: 홍길동**

## Ⅰ. 교수학습 운영 계획

| 월 | 주 | 단원 | 성취기준 | 수업방법 | 주안점 |
|---|---|---|---|---|---|
| 8 | 3 | 단원1 | [12합성01-01] | 강의 | 주안점1 |

## Ⅱ. 평가 세부 계획

### 1. 평가의 목적
가. 목적 하나.

### 2. 평가의 기본 방향
가. 방향 하나.

### 3. 평가 방침
가. 방침 하나.

### 4. 성취기준 및 성취수준
**가. 교육과정 성취기준·평가기준(상/중/하)**

| 성취기준 | 상 | 중 | 하 |
|---|---|---|---|
| [12합성01-01] 표준 하나 | 상1 | 중1 | 하1 |

**나. 영역별 성취수준(A/B/C)**

| 영역 | A | B | C |
|---|---|---|---|
| 영역 가 | A1 | B1 | C1 |

### 5. 기준 성취율과 성취도
| 성취율(원점수) | 성취도 |
|---|---|
| 80% 이상 | A |
| 60% 이상 ~ 80% 미만 | B |
| 60% 미만 | C |

### 6. 평가의 종류와 반영비율
| 구분 | ① 영역 가 | ② 영역 나 | 합계 |
|---|---|---|---|
| 영역 만점 | 60점(60%) | 40점(40%) | 100% |

### 7. 수행평가 세부기준
**① 영역 가 (60점)** · [12합성01-01]

| 평가항목 | 채점 기준(배점) |
|---|---|
| 항목1 | 완비 **40** / 부분 **20** |
| 기본점수 **18** · 장기 미인정 결석 **17** | |

### 8. 정의적 능력 평가
- 요소 하나.

### 9. 수행평가 미응시자
가. 처리 하나.

### 10. 평가 유의사항
- 유의 하나.

### 11. 평가 결과 분석 및 활용
- 활용 하나.
"""

DETAILED_S7 = """### 7. 수행평가 세부기준

#### ① 영역 가

- **평가 영역명**: 영역 가 ｜ **영역 만점**: 60점
- **수행과제**: 합성 과제
- **성취기준 / 성취수준(A~E)**: [12합성01-01]
- **평가 방법**: ☑ 산출물

**［세부 영역 가. 세부 하나 (60점)］ 평가요소 ｜ 수행수준(채점 기준) ｜ 배점**

| 평가요소 | 수행수준(채점 기준) | 배점 |
|---|---|---|
| 항목1 | 완비하여 산출함 | 40 |
| | 부분만 완성함 | 20 |
| **가. 소계** | | **60** |

**［영역 공통］**

| 구분 | 배점 |
|---|---|
| 기본점수 | 18 |
| 장기 미인정 결석자 | 17 |

"""

DETAILED_MD = (
    SYNTHETIC_MD[: SYNTHETIC_MD.index("### 7. 수행평가 세부기준")]
    + DETAILED_S7
    + SYNTHETIC_MD[SYNTHETIC_MD.index("### 8. 정의적 능력 평가") :]
)

CORE_FIXTURES = (
    Path(frozen.__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "m105_evalplan"
)
BLANK_3HAK = CORE_FIXTURES / "blank_form_3hak.hwpx"


def _dataclass_shape(value: type[object]) -> list[tuple[str, str, object]]:
    return [
        (field.name, str(field.type), field.default)
        for field in dataclasses.fields(value)
    ]


def test_public_api_and_dataclass_shape_parity() -> None:
    assert canonical.__all__ == frozen.__all__

    for name in frozen.__all__:
        canonical_binding = getattr(canonical, name)
        frozen_binding = getattr(frozen, name)
        if callable(frozen_binding):
            assert inspect.signature(canonical_binding) == inspect.signature(
                frozen_binding
            )

    for name in ("RubricItem", "RubricSubArea", "Rubric", "EvalPlanContent"):
        assert _dataclass_shape(getattr(canonical, name)) == _dataclass_shape(
            getattr(frozen, name)
        )


@pytest.mark.parametrize("markdown", [SYNTHETIC_MD, DETAILED_MD])
def test_synthetic_parse_and_skeleton_parity(markdown: str) -> None:
    canonical_content = canonical.parse_review_md(markdown)
    frozen_content = frozen.parse_review_md(markdown)

    assert canonical_content.to_dict() == frozen_content.to_dict()
    assert dataclasses.asdict(canonical_content) == dataclasses.asdict(frozen_content)
    assert canonical.expected_skeleton(canonical_content) == frozen.expected_skeleton(
        frozen_content
    )


def test_empty_and_partial_input_parity() -> None:
    for markdown in ("", "# 제목만", "### 7. 수행평가 세부기준\n"):
        canonical_content = canonical.parse_review_md(markdown)
        frozen_content = frozen.parse_review_md(markdown)
        assert dataclasses.asdict(canonical_content) == dataclasses.asdict(
            frozen_content
        )


@pytest.mark.skipif(
    not BLANK_3HAK.is_file(),
    reason="public evaluation-plan blank fixture is unavailable",
)
@pytest.mark.parametrize(
    ("phase", "markdown"),
    [
        ("structural", SYNTHETIC_MD),
        ("all", SYNTHETIC_MD),
        ("clean", SYNTHETIC_MD),
        ("clean", DETAILED_MD),
    ],
)
def test_plan_fill_and_cleanup_byte_parity(phase: str, markdown: str) -> None:
    canonical_content = canonical.parse_review_md(markdown)
    frozen_content = frozen.parse_review_md(markdown)

    assert canonical.plan_structural_ops(
        BLANK_3HAK, canonical_content
    ) == frozen.plan_structural_ops(BLANK_3HAK, frozen_content)

    canonical_result = canonical.fill_evalplan(
        BLANK_3HAK,
        canonical_content,
        phase=phase,
    )
    frozen_result = frozen.fill_evalplan(
        BLANK_3HAK,
        frozen_content,
        phase=phase,
    )

    assert canonical_result == frozen_result
    assert canonical_result["_data"] == frozen_result["_data"]


def _failure(
    callable_: Callable[..., object],
    *args: object,
) -> tuple[type[FileNotFoundError], str]:
    try:
        callable_(*args)
    except FileNotFoundError as exc:
        return type(exc), str(exc)
    raise AssertionError("call unexpectedly succeeded")


def test_missing_input_refusal_parity(tmp_path: Path) -> None:
    missing = tmp_path / "missing-review.md"
    assert _failure(canonical.parse_review_file, missing) == _failure(
        frozen.parse_review_file,
        missing,
    )
