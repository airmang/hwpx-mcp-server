# Phase 3 진행 보고서: 양식(템플릿) 처리 능력 개선

## 1) Phase 2 완료 상태 재검토 및 Phase 3 진행 여부

- `PHASE2_REPORT.md`와 코드 상태를 기준으로 점검한 결과, `.hwp` 읽기/변환 경로와 기존 `.hwpx` 편집 경로가 이미 분리되어 있어 Phase 3 기능을 추가해도 영향 범위를 관리할 수 있었습니다.
- 핵심 편집 도구(`replace_text_in_runs`, `set_table_cell_text`, `replace_table_region`, `fill_template`)가 이미 안정화되어 있어 양식 전용 고수준 도구 보강이 가능한 상태였습니다.
- 결론: **Phase 3 진행 가능**.

## 2) 이번 Phase 3 구현 내역

### 2.1 양식 구조 분석 도구 추가
- 신규 도구: `analyze_template_structure`
- 구현 위치:
  - `src/hwpx_mcp_server/hwpx_ops.py`
  - `src/hwpx_mcp_server/tools.py`
- 제공 정보:
  - `summary`: 템플릿 여부, 문단 수, 플레이스홀더 수, 잠금 문단 수, 추출 소스
  - `regions`: `header/body/footer` 3개 논리 영역(휴리스틱)
  - `placeholders`: 토큰, 문단 인덱스, 영역, 수정 가능 여부, 문맥
- 사용자 입력 확장:
  - `placeholderPatterns`: 프로젝트/업무 양식에 맞춘 플레이스홀더 패턴 주입
  - `lockKeywords`: 수정 금지 텍스트 키워드 정의

### 2.2 양식 워크플로 문서화
- `README.md`에 다음을 추가했습니다.
  - 도구 목록에 `analyze_template_structure`, `fill_template`를 명시
  - “양식(템플릿) 문서 작업” 섹션 신설
  - 구조 분석/한 번에 채우기(`fill_template`) 예시 요청 JSON 제공

### 2.3 테스트 추가
- `tests/test_hwpx_ops.py`
  - `test_analyze_template_structure_returns_regions_and_placeholders`
- `tests/test_tool_schemas.py`
  - `test_analyze_template_structure_tool_is_exposed`

## 3) 표 편집 안정성 관련 점검

Phase 3 요구사항의 표 안정성(병합 셀 논리 좌표, 스타일 보존)은 기존 구현의 다음 경로에서 유지됨을 확인했습니다.
- `get_table_cell_map`: 병합 포함 논리 격자 반환
- `set_table_cell_text(logical/splitMerged/autoFit)`
- `replace_table_region(logical/splitMerged/autoFit)`

해당 경로는 기존 테스트와 이번 회귀 테스트 실행에서 문제 없음을 확인했습니다.

## 4) 개선 전/후 워크플로 비교

### 이전(대표 시나리오)
- `save_as` → `find` → `replace_text_in_runs`(여러 번) → `save`
- 보통 5~10회 tool call 필요

### 이후(권장 시나리오)
- 구조 파악: `analyze_template_structure` 1회
- 채우기: `fill_template` 1회
- 합계 2회(필요 시 검증용 read 1회 추가)

## 5) 제한사항 및 후속 제안

- 현재 `analyze_template_structure`의 영역 분리는 **휴리스틱 기반**입니다.
  - 실제 양식별 고정 규칙(예: 학교 공문 템플릿 고정 영역 정의)이 있으면 정확도를 더 높일 수 있습니다.
- 여러 줄 치환 시 “새 문단 삽입 + 스타일 상속”은 `python-hwpx`의 문단 삽입 API 확장 여부에 따라 추가 고도화가 필요합니다.
  - 필요 시 upstream 제안 항목으로 별도 정리 가능합니다.
