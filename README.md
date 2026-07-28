<p align="center">
  <h1 align="center">python-hwpx-automation</h1>
  <p align="center">
    <strong>HWPX 저작·양식 채움·업무 워크플로를 위한 Python 자동화 계층</strong>
  </p>
  <p align="center">
    <a href="https://pypi.org/project/python-hwpx-automation/"><img src="https://img.shields.io/pypi/v/python-hwpx-automation?color=blue&label=PyPI" alt="PyPI"></a>
    <a href="https://pepy.tech/project/python-hwpx-automation"><img src="https://static.pepy.tech/badge/python-hwpx-automation/month" alt="Downloads"></a>
    <a href="https://pypi.org/project/python-hwpx-automation/"><img src="https://img.shields.io/pypi/pyversions/python-hwpx-automation" alt="Python"></a>
    <a href="https://github.com/airmang/python-hwpx-automation/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/airmang/python-hwpx-automation/tests.yml?branch=main&label=tests" alt="Tests"></a>
    <a href="https://github.com/airmang/python-hwpx-automation/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License"></a>
  </p>
</p>

<!-- release-state: release-approved -->
> [!WARNING]
> **릴리스 상태 — `release-approved`: 6.0.2 train 발행이 승인되어 진행
> 중입니다(2026-07-28).** 이 checkout은
> `python-hwpx 5.0.1 → python-hwpx-automation 6.0.2 → hwpx-plugin 1.0.0`
> train과 계약 해시 `0ce938371f0b55a6`을 설명하지만, **원격 truth는 아직
> 관찰 전**입니다. 마지막으로 plugin 설치까지 함께 검증한 완전한 공개
> 트레인은 여전히
> `python-hwpx 4.2.0 → hwpx-mcp-server 5.1.0 → hwpx-plugin 0.8.0`,
> 계약 해시 `429cb6706323e762`입니다. 아래 canonical 설치 명령은 발행
> readback이 끝나기 전에는 공개 설치로 간주하지 마세요.
> 상태 승격은 `unreleased-candidate → release-approved → released` 세 단계이며,
> `released`와 current-public 좌표는 core·canonical·compatibility와 plugin GitHub
> Release·marketplace·실제 marketplace 설치까지 3스택 전체 원격 정본을 관찰한 뒤
> 후속 commit에서만 바꿉니다. 절차는
> [릴리스 상태 runbook](docs/release-runbook.md)에 있습니다.

[python-hwpx](https://github.com/airmang/python-hwpx)의 format/object-model 위에서
저작·양식 채움·시험지 조판·평가계획·안전한 에이전트 워크플로를 제공합니다.
기본 설치는 MCP SDK 없이 Python API와 `hwpx` CLI를 사용할 수 있습니다.
[모델 컨텍스트 프로토콜(MCP)](https://modelcontextprotocol.io)은 `[mcp]` extra로
추가하는 선택 어댑터입니다.

한컴오피스도 Windows도 필요하지 않으므로 **ChatGPT 채팅 환경에서도 HWPX
문서를 다룰 수 있습니다.** 파이썬이 도는 곳이면 저작·양식 채움 워크플로가
그대로 동작합니다.

| 계층 | 저장소 | 정본 책임 |
|---|---|---|
| Core | [`python-hwpx`](https://github.com/airmang/python-hwpx) | HWPX package/object model·OPC/OXML·직렬화·재사용 primitive |
| Automation | [`python-hwpx-automation`](https://github.com/airmang/python-hwpx-automation) | Python 자동화·워크플로·profile/policy·렌더·선택형 MCP adapter |
| Judgment | [`hwpx-plugins`](https://github.com/airmang/hwpx-plugins) | 에이전트 intent/genre 판단·ambiguity 처리·plugin/skill 가이드 |

이 저장소는 위 표의 Automation 정본입니다.

## Python 자동화 시작하기 (6.0 후보)

```bash
pip install python-hwpx-automation
```

```python
from hwpx_automation import create_document_from_plan

document = create_document_from_plan(
    {
        "schemaVersion": "hwpx.document_plan.v1",
        "title": "회의 결과",
        "blocks": [{"type": "paragraph", "text": "결정 사항"}],
    }
)
document.save_to_path("meeting-result.hwpx")
```

`python -m hwpx_automation --help`와 `hwpx help`는 같은 task CLI를 실행합니다.

## MCP 어댑터 시작하기 (6.0 후보)

```bash
pip install "python-hwpx-automation[mcp]"
hwpx-automation-mcp
```

6.x 호환 기간에는 `pip install hwpx-mcp-server`와 `hwpx-mcp-server`도
동작합니다. 옛 distribution/import/console 제거는 7.0 이전에는 하지 않으며,
최소 90일 공개 관찰과 별도 오너 승인이 필요합니다.

MCP 클라이언트 설정 파일에 아래 블록 하나면 `hwpx` 서버를 잡습니다 — Claude Desktop은
`claude_desktop_config.json`, VS Code는 `.vscode/mcp.json`(키가 `mcpServers` 대신 `servers`),
Gemini CLI는 `~/.gemini/settings.json`, Cursor·Windsurf는 각 에디터의 MCP 설정 파일입니다.

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": [
        "--from",
        "python-hwpx-automation[mcp]==6.0.2",
        "hwpx-automation-mcp"
      ],
      "env": {
        "HWPX_AUTOMATION_WORKSPACE_ROOTS": "[\"~/Documents\"]"
      }
    }
  }
}
```

`HWPX_AUTOMATION_WORKSPACE_ROOTS`에는 문서가 있는 폴더(절대경로 또는 `~`)를 지정하세요. Windows는
`"[\"C:\\\\hwpx\"]"`처럼 씁니다. 값을 비워 두면 서버는 실행 위치(cwd)를 root로 쓰려 하지만,
Claude Desktop 같은 GUI 클라이언트는 서버를 시스템 디렉터리(Windows `C:\Windows\System32`,
macOS `/`)에서 띄우므로 이런 degenerate cwd는 거부되고 모든 문서 경로가 막힙니다. 그래서 처음부터
이 값을 설정하는 것을 권장합니다. 나머지 옵션은 [환경 변수](#환경-변수) 표를 참고하세요.

> 비-HWPX 문서(PDF/DOCX/XLSX/HTML/TXT)를 `document_to_markdown`으로 읽으려면
> `pip install "python-hwpx-automation[ingest]"`(또는 옛 이름 `"hwpx-mcp-server[ingest]"`)로
> MarkItDown adapter를 함께 설치합니다.
> 요구 사항: `Python >= 3.10` · `python-hwpx >= 5.0.0`.

## 무엇을 하나

기본 모드에서 다수의 HWPX 도구를 제공하며, 고급 모드(`HWPX_AUTOMATION_ADVANCED=1`)에서 점검·검증용 도구가 추가됩니다.

- **읽기·탐색** — `get_document_info`, `get_document_map`(아웃라인·표 지도·앵커를 한 호출로), `find_text` (저장하지 않음)
- **검색·치환·편집** — `search_and_replace`, `apply_document_commands`(이종 편집 원자 적용·dry-run·롤백·멱등키), `add_tracked_edit`(변경 추적)
- **표·양식 채움** — `analyze_form_fill` → `apply_form_fill` → `verify_form_fill` 바이트 보존 트랜잭션, `table_compute`(합계·소계)
- **문서 생성·공문** — 선언형 `create_document_from_plan`, `inspect_official_document_style`(행정 규정 lint), `mail_merge`
- **서식·그림·생성기** — `set_paragraph_format`·`set_page_setup`, `insert_picture`, 사진대지·명패·조직도
- **프리뷰·추출·복구·진단** — `render_preview`(HTML/PNG 자기검증), `hwpx_to_markdown`, `repair_hwpx`, `mcp_server_health`

자세한 내용: [사용 사례](docs/use-cases.md) · [스킬 우선 워크플로](docs/skill-first-workflows.md)

### 하나의 흐름부터

처음부터 모든 도구를 외울 필요는 없습니다. 보통 아래 하나로 시작합니다.

1. **읽기** — `get_document_info` → `get_document_outline`/`get_document_text` → `find_text`, `get_table_map`으로 필요한 부분만. (저장하지 않음)
2. **안전 수정** — `copy_document`로 사본 생성 → 읽기 도구로 대상 확인 → `search_and_replace`, `set_table_cell_text` 같은 전문 도구나 `apply_document_commands`로 가장 작은 변경 → 다시 읽어 확인 → 검토가 끝난 복사본을 handoff.

핵심은 **copy first · smallest edit · re-read after edits**입니다.

## 안전과 품질 게이트

- **read first** — 먼저 읽기 도구로 문서를 파악합니다.
- **copy before risky edits** — 결과물을 보존해야 하면 `copy_document`를 먼저 호출합니다.
- **mutating tools persist immediately** — 수정 도구는 호출 즉시 저장되므로 검토용은 복사본에서 작업합니다.
- **explicit handoff uses the reviewed copy** — 납품·handoff는 검토가 끝난 복사본 파일 기준입니다.

모델은 operation/plan만 보내고 raw XML을 직접 편집하지 않습니다. 일반 저장 경로는 python-hwpx의
단일 `SavePipeline` 게이트를 통과해 무결성·XML·OPC/ID·열림안전을 검사하고, `quality`에 따라
`visualComplete` 블록(`ok`·`status`·`errorCodes`)을 응답에 실으며, 게이트가 실패하면 아무것도 쓰지 않고
소유 후보만 원복합니다. capability handshake가 core/automation/plugin
버전+해시 skew를 fail-closed로 차단합니다. 6.x 응답의 `versions.mcp`와
`minMcpVersion`은 기존 소비자를 위한 alias로만 유지됩니다.
같은 이유로 `MIN_MCP_VERSION`, 공개 오류 schema `hwpx.mcp-error/v1`,
동결된 architecture receipt ID `hwpx-mcp.*`, parity receipt 필드
`mcpRuntimeMembers`도 6.x에서 정확히 보존합니다. canonical 표기는
`versions.automation`·`minAutomationVersion`·`MIN_AUTOMATION_VERSION`이며,
보존된 MCP 철자는 제품 소유권이 아니라 wire/receipt 호환 식별자입니다.
보안 상세는 [하드닝 가이드](docs/hardening_guide_ko.md)에 있습니다.

> **위치 계약** — `paragraph_index`는 본문 직속 문단의 0-based 인덱스입니다. 표 안 문단은 여기 섞지 않고
> `{"kind":"table_cell_paragraph","table_index":0,"row":0,"col":1,"cell_paragraph_index":0}` 같은 `location`
> 객체로 지정하며, `get_table_map`/`find_text`가 반환한 값을 그대로 넘길 수 있습니다.

## 환경 변수

| 변수 | 설명 | 기본값 |
|---|---|---|
| `HWPX_AUTOMATION_WORKSPACE_ROOTS` | 허용할 workspace 절대경로의 JSON 배열(복수 root 지원). 상대경로는 첫 root 기준 | unset → 프로세스 cwd. degenerate cwd는 `WORKSPACE_ROOT_INVALID`로 거부 |
| `HWPX_AUTOMATION_MAX_CHARS` | 텍스트 반환 도구 기본 최대 길이 | `10000` |
| `HWPX_AUTOMATION_AUTOBACKUP` | `1`이면 저장 전 `.bak` 백업 생성 | `1` |
| `HWPX_AUTOMATION_ADVANCED` | `1`이면 고급 도구 활성화 | `0` |
| `HWPX_AUTOMATION_FETCH_TIMEOUT_SECONDS` | URL 기반 HWPX fetch timeout | `20.0` |
| `HWPX_AUTOMATION_ALLOW_PRIVATE_NETWORK` | `1`이면 신뢰된 사설/루프백 HTTPS 대상 허용. 링크로컬·metadata·예약 주소는 계속 차단 | `0` |
| `HWPX_AUTOMATION_QUALITY` | 전역 기본 저장 게이트 정책(`transparent`/`strict`). 도구별 `quality`가 우선 | `transparent` |
| `HWPX_AUTOMATION_REQUIRE_CAPABILITY` | `0`이면 capability skew fail-closed를 끔(진단/전문가용) | `1` |
| `HWPX_AUTOMATION_WORKFLOW_STORE` | durable workflow SQLite 경로. 기존 `HWPX_WORKFLOW_STORE`보다 우선 | 기존 6.x 상태 경로 |
| `LOG_LEVEL` | 로그 레벨 | `INFO` |

동일 suffix의 기존 `HWPX_MCP_*` 키는 6.x 동안 fallback으로 유지됩니다.
두 키가 함께 있으면 `HWPX_AUTOMATION_*`이 우선합니다. workflow DB의 기존
`.../hwpx-mcp-server/workflows.sqlite3` 기본 경로도 6.x에서는 데이터 유실을
막기 위해 그대로 사용합니다.

제품-neutral integration/capability 키는 옛 MCP 제품명을 담지 않으므로 이름을
바꾸지 않습니다.

| 분류 | 보존하는 정확한 키 |
|---|---|
| render integration | `HWPX_RENDER_QUEUE_ROOT`, `HWPX_RENDER_QUEUE_URL`, `HWPX_RENDER_QUEUE_SECRET`, `HWPX_RENDER_TRANSPORT_AUTH`, `HWPX_RENDER_CA_FILE`, `HWPX_RENDER_CLIENT_CERT_FILE`, `HWPX_RENDER_CLIENT_KEY_FILE` |
| workflow security | `HWPX_WORKFLOW_ENCRYPTION_KEY` |
| oracle capability | `HWPX_ORACLE_STRUCTURAL_ONLY`, `HWPX_ORACLE_BUDGET_SECONDS` |
| plugin integration | `HWPX_SKILL_VERSION`, `HWPX_PLUGIN_ROOT` |

이 키들은 `integration-preserved` 또는 `capability-preserved` 정본이며
`HWPX_AUTOMATION_*` alias를 새로 만들지 않습니다.

경로는 기본적으로 workspace 밖 traversal과 symlink escape를 거부하고, URL 입력은 HTTPS·공개 IP만
허용합니다. 원자 rename을 제공하지 않는 호스트의 동시성 주의사항은 [하드닝 가이드](docs/hardening_guide_ko.md)를 보세요.

## 기여하기

[good first issue](https://github.com/airmang/python-hwpx-automation/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) ·
[마일스톤](https://github.com/airmang/python-hwpx-automation/milestones) ·
[Discussions](https://github.com/airmang/python-hwpx-automation/discussions) ·
[CONTRIBUTING](CONTRIBUTING.md) ·
[CHANGELOG](CHANGELOG.md)

```bash
python -m pip install -e ".[test]"   # 테스트 의존성
python -m pytest -q                   # 전체 테스트
python scripts/run_conformance.py run \
  --tier structural --check tests/conformance/golden/structural.json
# 공개 합성 코퍼스를 다시 만들 때만:
python scripts/conformance_corpus_build.py
```

## 감사의 말

코어 라이브러리 [python-hwpx](https://github.com/airmang/python-hwpx) 위에서 동작하며, 아래 공개 표준·프로젝트에 빚지고 있습니다.

- **[OWPML — 개방형 워드프로세서 마크업 언어 (KS X 6101)](https://www.kssn.net/search/stddetail.do?itemNo=K001010119985)** — HWPX가 기반하는 한국 산업 표준
- **[hancom-io/hwpx-owpml-model](https://github.com/hancom-io/hwpx-owpml-model)** — OWPML 요소 구조 참조 모델 · **[neolord0/hwpxlib](https://github.com/neolord0/hwpxlib)** — 오라클 샘플 코퍼스
- **[edwardkim/rhwp](https://github.com/edwardkim/rhwp)** — 멱등성·검증 게이트 설계 영감

## License · Maintainer

Apache-2.0 ([LICENSE](LICENSE) · [NOTICE](NOTICE)) — **Kohkyuhyun** [@airmang](https://github.com/airmang) · [kokyuhyun@hotmail.com](mailto:kokyuhyun@hotmail.com)
