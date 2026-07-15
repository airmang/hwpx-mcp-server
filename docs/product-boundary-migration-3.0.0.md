# 3.0.0 public product boundary migration

`hwpx-mcp-server` 3.0.0은 공개 제품이 아닌 practice scenario/campaign 실행기를 공개
런타임과 ToolSpec에서 제거하는 breaking release다. 지원되는 문서 생성·편집·검증,
시험지, 평가계획/양식 채움, workflow, fixture benchmark, 실한컴 렌더 기능은 유지된다.

## 계약

| | 이전 | 3.0.0 |
|---|---:|---:|
| `python-hwpx` | 2.29.2 | 3.0.0 |
| `hwpx-mcp-server` | 2.23.1 | 3.0.0 |
| skill/plugin | 0.1.31 | 0.2.0 |
| 기본 도구 | 133 | 126 |
| 고급 포함 | 143 | 136 |
| 계약 해시 | `0f9e1dcb7c646202` | `76d143ccc0787828` |

## 삭제와 작업 경로

삭제된 이름은 alias나 실패 전용 stub으로 남지 않는다. 이전 이름을 호출하면 일반적인
미등록 MCP 도구 오류가 발생한다. 7개 도구 모두 1:1 공개 대체 도구가 없으며, 내부
scenario/campaign 작업은 workspace-private QA harness로 이동한다.

| 삭제된 도구 | 1:1 공개 대체 |
|---|---|
| `start_practice_scenario` | 없음 |
| `apply_practice_scenario` | 없음 |
| `start_practice_campaign` | 없음 |
| `get_practice_campaign` | 직접 대체 없음 |
| `continue_practice_campaign` | 직접 대체 없음 |
| `cancel_practice_campaign` | 직접 대체 없음 |
| `export_practice_campaign` | 직접 대체 없음 |

공개 문서 작업에는 목적에 맞게 `apply_document_commands`, `apply_evalplan_fill`,
`scan_form_guidance`, `apply_table_ops`, `apply_body_ops`, `verify_form_fill`을 사용한다.
이 목록은 private scenario/campaign ID의 호환 대체가 아니라 지원되는 공개 문서 경로다.

정확한 삭제 목록, 대체 분류, 호출부·런타임 제거 확인은 기계 판독 가능한
[`tool-contract-delta-3.0.0.json`](tool-contract-delta-3.0.0.json)에 있다. 이 파일은
`python scripts/render_contract_delta.py`로 결정론적으로 생성하며 `--check`로 drift를 검사한다.

## 릴리스/설치

호환 버전을 의존성 순서대로 맞춘다: `python-hwpx==3.0.0`,
`hwpx-mcp-server==3.0.0`, skill/plugin `0.2.0`. 2.x MCP나 0.1.x skill을 섞어
운영하지 말고, 업그레이드 뒤 새 호스트 세션에서 126/136과 계약 해시를 다시 확인한다.
