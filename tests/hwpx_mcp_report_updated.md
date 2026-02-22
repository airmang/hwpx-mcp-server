# HWPX MCP 개선 보고서 (업데이트)

Date: 2026-02-22  
Base sample: `tests/hwpx_mcp_test.hwpx`  
원본 보호: 원본 파일은 수정하지 않고 `tests/_repro_before`, `tests/_repro_after` 복사본으로만 검증

## 1) 이슈별 원인/해결

### 이슈 1. `hwpx_insert_paragraph` / `hwpx_add_paragraph`에서 `style=null` 검증 실패
- 원인: 툴 시그니처가 `style: str = None` 형태라서 스키마가 nullable로 생성되지 않음.
- 해결: `src/hwpx_mcp_server/server.py`의 시그니처를 `style: str | None = None`으로 수정.
- 결과: 생략/`null`/빈 문자열 모두 MCP 호출 경로에서 성공.

### 이슈 2. `hwpx_search_and_replace`가 보이는 문자열 치환 실패
- 원인: run 단위 치환만 수행하여 문단 내 run 경계에 걸친 문자열이 치환되지 않음.
- 해결: `src/hwpx_mcp_server/core/search.py`에서 문단 가시 텍스트(`para.text`) 기준 치환으로 변경.
- 결과: 리포트 샘플의 제목 문자열 `"2026학년도 교육정보"` 치환 성공(`replaced_count=1`).

### 이슈 3. `hwpx_add_memo`의 lxml vs ElementTree 타입 충돌
- 원인: `python-hwpx` 내부에서 `xml.etree.ElementTree.SubElement` 호출 시 parent가 `lxml` element면 TypeError 가능.
- 해결:
  - `src/hwpx_mcp_server/compat.py`에서 `ET.SubElement` 호환 래퍼 추가(혼합 parent 지원).
  - `src/hwpx_mcp_server/hwpx_ops.py`에서도 문서 모듈 import 전에 호환 패치 적용.
- 결과: 혼합 parent 시나리오 회귀 테스트 추가 및 통과.

### 이슈 4. `hwpx_batch_replace` 시그니처/스키마 정합성
- 원인: 항목 타입이 느슨해 일부 클라이언트에서 스키마 해석 불일치 가능.
- 해결: `src/hwpx_mcp_server/server.py`에서 `replacements: list[dict[str, str]]`로 명시.
- 결과: 스키마에 `replacements`가 required로 유지되고, 실제 배치 치환 정상 동작.

### 이슈 5. `delete_paragraph` 후 빈 문단 잔존
- 원인: 기존 구현이 문단 노드를 삭제하지 않고 텍스트만 비워 “삭제 효과”만 냄.
- 해결: `src/hwpx_mcp_server/core/content.py`에서 섹션 XML에서 문단 노드를 실제 제거하고 `mark_dirty()` 처리.
- 결과: 삽입 후 삭제 시 문단 수가 정상 복원되고 의도치 않은 빈 placeholder가 남지 않음.

### 이슈 6. `add_table` 후 `get_document_info.tables` 불일치
- 원인: `get_document_info`의 table count가 object id 기반 dedupe를 사용해 실제 index 체계와 불일치.
- 해결:
  - `src/hwpx_mcp_server/server.py`의 `_table_count`를 문단 참조 수 합산 방식으로 변경.
  - `src/hwpx_mcp_server/core/content.py`의 table iterator에서도 dedupe 제거.
- 결과: `add_table` 반환 index와 `get_document_info.tables`가 동일 정책으로 일관.

## 2) Before / After 검증 요약

## Before (재현)
- Issue1(`style=null`): validation error 발생.
- Issue2: `"2026학년도 교육정보"` 치환 시 `replaced_count=0`.
- Issue5: insert/delete 후 문단 수 복원 실패(+1 유지).
- Issue6: table count 증가 없음(`delta=0`).

## After (수정 후)
- Issue1: 생략/null/빈문자 모두 성공.
- Issue2: `replaced_count=1`, 치환 문자열 확인.
- Issue3: 호환 패치 테스트 통과 + `add_memo` 정상.
- Issue4: 스키마 required에 `replacements` 포함, 배치 치환 성공.
- Issue5: insert 후 delete 시 문단 수 원복.
- Issue6: `before=18`, `after=19`, `delta=+1`, `table_index == after-1`.

## 3) 실행한 검증

```bash
pytest -q tests/test_hwpx_report_regressions.py
pytest -q tests/test_content.py tests/test_mcp_end_to_end.py::test_year_rollover_batch_replace_flow tests/test_hwpx_ops.py::test_add_table_returns_valid_index
```

실행 결과:
- `tests/test_hwpx_report_regressions.py`: `6 passed`
- 추가 회귀 확인: `11 passed`

## 4) 변경 파일

- `src/hwpx_mcp_server/compat.py`
- `src/hwpx_mcp_server/hwpx_ops.py`
- `src/hwpx_mcp_server/core/content.py`
- `src/hwpx_mcp_server/core/search.py`
- `src/hwpx_mcp_server/server.py`
- `tests/test_content.py`
- `tests/test_hwpx_report_regressions.py`
- `tests/hwpx_mcp_report_updated.md`
