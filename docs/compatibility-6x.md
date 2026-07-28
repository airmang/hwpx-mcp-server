# 6.x 호환 표면

`hwpx-mcp-server`가 `python-hwpx-automation`으로 이름을 바꾼 뒤에도, 6.x 동안
기존 설치·설정·연동이 깨지지 않도록 유지하는 표면의 정본 목록입니다.

새 canonical 표면은 배포 `python-hwpx-automation`, import `hwpx_automation`,
콘솔 `hwpx-automation-mcp`입니다. 아래의 옛 표면은 6.x 내내 그대로 동작합니다.

## 배포·import·콘솔

- `pip install hwpx-mcp-server` — 같은 버전의 canonical 배포를 정확 핀으로
  끌어오는 호환 배포입니다.
- 설치하면 import `hwpx_mcp_server`(canonical 모듈로의 alias, `__version__`·
  하위 모듈 포함)와 콘솔 `hwpx-mcp-server`가 그대로 동작합니다.

## Wire·영수증 식별자

6.x 응답과 영수증에서 아래 식별자는 정확히 보존됩니다. canonical 표기는
`versions.automation` · `minAutomationVersion` · `MIN_AUTOMATION_VERSION`이며,
보존된 MCP 철자는 제품 소유권 표기가 아니라 wire/receipt 호환 식별자입니다.

| 분류 | 보존 식별자 |
|---|---|
| 응답 alias | `versions.mcp`, `minMcpVersion` — canonical `versions.automation`, `minAutomationVersion`의 alias |
| 상수 | `MIN_MCP_VERSION` — `MIN_AUTOMATION_VERSION`과 동일 값 |
| 공개 오류 schema | `hwpx.mcp-error/v1` |
| architecture receipt ID | 동결된 `hwpx-mcp.*` 계열 |
| parity receipt 필드 | `mcpRuntimeMembers` |

## 환경 변수

- 동일 suffix의 기존 `HWPX_MCP_*` 키는 6.x 동안 fallback으로 유지됩니다. 두
  키가 함께 있으면 `HWPX_AUTOMATION_*`이 우선합니다.
- durable workflow DB의 기존 `.../hwpx-mcp-server/workflows.sqlite3` 기본
  경로도 데이터 유실을 막기 위해 6.x에서 그대로 사용합니다.

제품-neutral integration/capability 키는 옛 MCP 제품명을 담지 않으므로 이름을
바꾸지 않으며, `HWPX_AUTOMATION_*` alias를 새로 만들지 않습니다. 이 키들은
`integration-preserved` 또는 `capability-preserved` 정본입니다.

| 분류 | 보존하는 정확한 키 |
|---|---|
| render integration | `HWPX_RENDER_QUEUE_ROOT`, `HWPX_RENDER_QUEUE_URL`, `HWPX_RENDER_QUEUE_SECRET`, `HWPX_RENDER_TRANSPORT_AUTH`, `HWPX_RENDER_CA_FILE`, `HWPX_RENDER_CLIENT_CERT_FILE`, `HWPX_RENDER_CLIENT_KEY_FILE` |
| workflow security | `HWPX_WORKFLOW_ENCRYPTION_KEY` |
| oracle capability | `HWPX_ORACLE_STRUCTURAL_ONLY`, `HWPX_ORACLE_BUDGET_SECONDS` |
| plugin integration | `HWPX_SKILL_VERSION`, `HWPX_PLUGIN_ROOT` |

## 제거 정책

옛 distribution/import/console/설정 키 제거는 7.0 이전에는 하지 않으며, 최소
90일 공개 공지와 별도 오너 승인이 필요합니다.
