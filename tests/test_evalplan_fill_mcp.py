# SPDX-License-Identifier: Apache-2.0
"""apply_evalplan_fill MCP tool — the whole-form 평가계획 recipe surface.

Skips until the installed python-hwpx provides hwpx.evalplan_fill. Uses the
in-repo public blank form fixture (no owner PII) + a synthetic review markdown.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("hwpx.evalplan_fill", reason="requires python-hwpx with evalplan_fill recipe")

from hwpx_mcp_server.hwpx_ops import HwpxOps
from hwpx_mcp_server.tools import build_tool_definitions

BLANK = (Path(__file__).parent.parent.parent / "python-hwpx" / "tests"
         / "fixtures" / "m105_evalplan" / "blank_form_3hak.hwpx")

SYNTHETIC_MD = """# 2026학년도 2학기 3학년 「합성 과목」 교수학습운영 및 평가계획 (검토용)

> **담당교사: 홍길동** · 수행평가 100% · 성취도 3단계

## Ⅰ. 교수학습 운영 계획

| 월 | 주 | 단원 | 성취기준 | 수업방법 | 주안점 |
|---|---|---|---|---|---|
| 8 | 3 | 단원1 | [12합성01-01] | 강의 | 주안점1 |

## Ⅱ. 평가 세부 계획

### 1. 평가의 목적
가. 목적 하나.

### 2. 평가의 기본 방향
가. 방향.

### 3. 평가 방침
가. 방침.

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

### 6. 평가의 종류와 반영비율 (수행평가 100%)

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
가. 처리.

### 10. 평가 유의사항
- 유의.

### 11. 평가 결과 분석 및 활용
- 활용.
"""


def test_apply_evalplan_fill_registered():
    assert "apply_evalplan_fill" in {t.name for t in build_tool_definitions()}


@pytest.mark.skipif(not BLANK.exists(), reason="public blank-form fixture not available")
def test_apply_evalplan_fill_one_shot(tmp_path):
    shutil.copy(BLANK, tmp_path / "blank.hwpx")
    (tmp_path / "review.md").write_text(SYNTHETIC_MD, encoding="utf-8")
    ops = HwpxOps(base_directory=tmp_path)
    out = ops.apply_evalplan_fill("blank.hwpx", "review.md", output="filled.hwpx")

    assert out["ok"] is True
    assert (out.get("openSafety") or {}).get("ok") is True         # opens in Hancom
    assert out["byteIdentical"] is False                           # it was edited
    assert set(out["contentReport"]).issuperset(
        {"schedule", "achievement", "levels", "rubrics", "ratio", "sections"})
    # honest-defer count is surfaced (never silent), int and consistent with notes
    assert out["rubricNeedsReview"] == len(out["needsReviewNotes"])
    assert (tmp_path / "filled.hwpx").exists()
