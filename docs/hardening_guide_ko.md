# HWPX MCP 서버 하드닝 가이드

## 개요
- 모든 MCP 툴 스키마는 draft-07 호환 Sanitizer를 거쳐 `$ref`, `anyOf`, nullable 타입 없이 노출됩니다.
- `HWPX_MCP_HARDENING=1` 플래그를 사용하면 plan → preview → apply 편집 파이프라인과 보조 검색 도구가 활성화됩니다.
- 플래그를 끄면 기존 동작을 그대로 유지하면서도 스키마 Sanitizer는 항상 적용됩니다.

## 활성화 방법
1. 서버를 시작하기 전에 환경 변수 `HWPX_MCP_HARDENING=1`을 설정합니다.
2. MCP 클라이언트에서 `ListTools` 응답을 확인하면 하드닝 전용 도구(`hwpx.plan_edit`, `hwpx.preview_edit`, `hwpx.apply_edit`, `hwpx.search`, `hwpx.get_context`)가 함께 나열됩니다.
3. 플래그를 제거하거나 `0`으로 지정하면 기존 도구 세트만 노출됩니다.

## 편집 파이프라인
| 단계 | 도구 | 설명 |
| --- | --- | --- |
| Plan | `hwpx.plan_edit` | 편집 의도와 대상 정보를 제출하면 `planId`, 예상 diff 개요, 후속 예시 호출을 반환합니다. |
| Preview | `hwpx.preview_edit` | `planId`를 검증하면서 diff, 모호성 후보, 안전 점수를 제공합니다. 이 기록이 없으면 Apply 단계로 이동할 수 없습니다. |
| Apply | `hwpx.apply_edit` | `confirm: true`와 함께 요청해야 하며, 미리보기한 동일한 `planId`만 허용됩니다. `idempotencyKey`를 지정하면 재시도 시 안전하게 무시됩니다. |

> 📌 모든 하드닝 도구는 공통 `document` 로케이터 스키마를 사용합니다. 기존 `path` 값을 그대로 전달해도 되지만, 이미 로드된 문서를 후속 요청에서 재사용하려면 `{"type": "handle", "handleId": "..."}` 형태로 안정적인 식별자를 전달하는 것이 좋습니다. HTTP 백엔드를 사용할 때는 `{"type": "uri", "uri": "...", "backend": "http"}`와 같이 명시적으로 지정할 수 있습니다.

### 오류 코드 요약
| 코드 | 의미 | 대응 |
| --- | --- | --- |
| `PREVIEW_REQUIRED` | 미리보기 없이 적용을 시도했을 때 발생 | `hwpx.preview_edit`를 먼저 호출한 뒤 다시 시도 |
| `AMBIGUOUS_TARGET` | 타겟이 모호하여 여러 후보가 있을 때 | 반환된 `candidates`를 검토 후 대상 범위를 좁혀 재계획 |
| `UNSAFE_WILDCARD` | 검색/치환 범위가 과도하게 넓을 때 | 제한 조건(`limit`, 정규식 등)을 조정하여 재계획 |
| `IDEMPOTENT_REPLAY` | 동일한 `idempotencyKey`로 이미 적용된 요청 | 결과를 신뢰하고 중복 요청을 중단 |

## 보조 도구
- **`hwpx.search`**: 정규식 또는 키워드로 문서를 검색하고, 각 결과에 안정적인 `nodeId`와 문맥 조각을 제공합니다.
- **`hwpx.get_context`**: 지정한 타깃 주변의 제한된 창(window)만 반환해 대용량 문서를 안전하게 검토할 수 있습니다.

## 테스트
- `pytest -q` 명령으로 스키마 회귀, 파이프라인 게이트, 멱등성 검증을 포함한 테스트를 실행할 수 있습니다.
- 테스트 실패 시 플래그를 비활성화하여 운영 환경을 즉시 보호할 수 있으며, 수정 후 반드시 동일 명령으로 재검증하세요.
