<p align="center">
  <h1 align="center">hwpx-mcp-server</h1>
  <p align="center">
    <strong>AI 에이전트가 HWPX 문서를 바로 읽고, 찾고, 채우고, 만들게 하는 MCP 서버</strong>
  </p>
  <p align="center">
    <a href="https://pypi.org/project/hwpx-mcp-server/"><img src="https://img.shields.io/pypi/v/hwpx-mcp-server?color=blue&label=PyPI" alt="PyPI"></a>
    <a href="https://pepy.tech/project/hwpx-mcp-server"><img src="https://static.pepy.tech/badge/hwpx-mcp-server/month" alt="Downloads"></a>
    <a href="https://pypi.org/project/hwpx-mcp-server/"><img src="https://img.shields.io/pypi/pyversions/hwpx-mcp-server" alt="Python"></a>
    <a href="https://github.com/airmang/hwpx-mcp-server/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/airmang/hwpx-mcp-server/tests.yml?branch=main&label=tests" alt="Tests"></a>
    <a href="https://github.com/airmang/hwpx-mcp-server/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License"></a>
  </p>
</p>

[python-hwpx](https://github.com/airmang/python-hwpx) 위에서 HWPX 문서의 열람·검색·편집·양식채움·생성·추출을
[모델 컨텍스트 프로토콜(MCP)](https://modelcontextprotocol.io) 도구로 노출합니다. Claude Desktop·VS Code 등
MCP 클라이언트에서 자연어로 문서를 다루며, 핵심 파일 처리는 한/글 없이 순수 파이썬으로 하므로
Windows·macOS·Linux·CI 어디서나 같은 흐름으로 동작합니다.

| | 레포 | 역할 |
|---|---|---|
| 📦 | [`python-hwpx`](https://github.com/airmang/python-hwpx) | 순수 파이썬 HWPX 코어 |
| 🔌 | **`hwpx-mcp-server`** | MCP 클라이언트에서 HWPX 조작 (이 레포) |
| 🎯 | [`hwpx-plugin`](https://github.com/airmang/hwpx-plugins) | 에이전트용 플러그인·스킬 번들 |

## 시작하기

```bash
uvx hwpx-mcp-server          # 또는: pip install hwpx-mcp-server && hwpx-mcp-server
```

MCP 클라이언트 설정 파일에 아래 블록 하나면 `hwpx` 서버를 잡습니다 — Claude Desktop은
`claude_desktop_config.json`, VS Code는 `.vscode/mcp.json`(키가 `mcpServers` 대신 `servers`),
Gemini CLI는 `~/.gemini/settings.json`, Cursor·Windsurf는 각 에디터의 MCP 설정 파일입니다.

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"],
      "env": {
        "HWPX_MCP_WORKSPACE_ROOTS": "[\"~/Documents\"]"
      }
    }
  }
}
```

`HWPX_MCP_WORKSPACE_ROOTS`에는 문서가 있는 폴더(절대경로 또는 `~`)를 지정하세요. Windows는
`"[\"C:\\\\hwpx\"]"`처럼 씁니다. 값을 비워 두면 서버는 실행 위치(cwd)를 root로 쓰려 하지만,
Claude Desktop 같은 GUI 클라이언트는 서버를 시스템 디렉터리(Windows `C:\Windows\System32`,
macOS `/`)에서 띄우므로 이런 degenerate cwd는 거부되고 모든 문서 경로가 막힙니다. 그래서 처음부터
이 값을 설정하는 것을 권장합니다. 나머지 옵션은 [환경 변수](#환경-변수) 표를 참고하세요.

> 비-HWPX 문서(PDF/DOCX/XLSX/HTML/TXT)를 `document_to_markdown`으로 읽으려면
> `pip install "hwpx-mcp-server[ingest]"`로 MarkItDown adapter를 함께 설치합니다.
> 요구 사항: `Python >= 3.10` · `python-hwpx >= 3.3.1`.

## 무엇을 하나

기본 모드에서 다수의 HWPX 도구를 제공하며, 고급 모드(`HWPX_MCP_ADVANCED=1`)에서 점검·검증용 도구가 추가됩니다.

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
소유 후보만 원복합니다. capability handshake가 core/mcp/plugin 버전+해시 skew를 fail-closed로 차단합니다.
보안 상세는 [하드닝 가이드](docs/hardening_guide_ko.md)에 있습니다.

> **위치 계약** — `paragraph_index`는 본문 직속 문단의 0-based 인덱스입니다. 표 안 문단은 여기 섞지 않고
> `{"kind":"table_cell_paragraph","table_index":0,"row":0,"col":1,"cell_paragraph_index":0}` 같은 `location`
> 객체로 지정하며, `get_table_map`/`find_text`가 반환한 값을 그대로 넘길 수 있습니다.

## 환경 변수

| 변수 | 설명 | 기본값 |
|---|---|---|
| `HWPX_MCP_WORKSPACE_ROOTS` | 허용할 workspace 절대경로의 JSON 배열(복수 root 지원). 상대경로는 첫 root 기준 | unset → 프로세스 cwd. degenerate cwd(파일시스템 루트·Windows 시스템 디렉터리)는 `WORKSPACE_ROOT_INVALID`로 거부되니 설정 권장 |
| `HWPX_MCP_MAX_CHARS` | 텍스트 반환 도구 기본 최대 길이 | `10000` |
| `HWPX_MCP_AUTOBACKUP` | `1`이면 저장 전 `.bak` 백업 생성 | `1` |
| `HWPX_MCP_ADVANCED` | `1`이면 고급 도구 활성화 | `0` |
| `HWPX_MCP_SANDBOX_ROOT` | 단일 root 구버전 호환 변수. 새 설정에서는 `HWPX_MCP_WORKSPACE_ROOTS` 사용 | unset |
| `HWPX_MCP_FETCH_TIMEOUT_SECONDS` | URL 기반 HWPX fetch timeout | `20.0` |
| `HWPX_MCP_ALLOW_PRIVATE_NETWORK` | `1`이면 신뢰된 사설/루프백 HTTPS 대상 허용. 링크로컬·metadata·예약 주소는 계속 차단 | `0` |
| `HWPX_MCP_QUALITY` | 전역 기본 저장 게이트 정책(`transparent`/`strict`). 도구별 `quality`가 우선 | `transparent` |
| `HWPX_MCP_REQUIRE_CAPABILITY` | `0`이면 capability skew fail-closed를 끔(진단/전문가용) | `1` |
| `LOG_LEVEL` | 로그 레벨 | `INFO` |

경로는 기본적으로 workspace 밖 traversal과 symlink escape를 거부하고, URL 입력은 HTTPS·공개 IP만
허용합니다. 원자 rename을 제공하지 않는 호스트의 동시성 주의사항은 [하드닝 가이드](docs/hardening_guide_ko.md)를 보세요.

## 기여하기

[good first issue](https://github.com/airmang/hwpx-mcp-server/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) ·
[마일스톤](https://github.com/airmang/hwpx-mcp-server/milestones) ·
[Discussions](https://github.com/airmang/hwpx-mcp-server/discussions) ·
[CONTRIBUTING](CONTRIBUTING.md) ·
[CHANGELOG](CHANGELOG.md)

```bash
python -m pip install -e ".[test]"   # 테스트 의존성
python -m pytest -q                   # 전체 테스트
```

## 감사의 말

코어 라이브러리 [python-hwpx](https://github.com/airmang/python-hwpx) 위에서 동작하며, 아래 공개 표준·프로젝트에 빚지고 있습니다.

- **[OWPML — 개방형 워드프로세서 마크업 언어 (KS X 6101)](https://www.kssn.net/search/stddetail.do?itemNo=K001010119985)** — HWPX가 기반하는 한국 산업 표준
- **[hancom-io/hwpx-owpml-model](https://github.com/hancom-io/hwpx-owpml-model)** — OWPML 요소 구조 참조 모델 · **[neolord0/hwpxlib](https://github.com/neolord0/hwpxlib)** — 오라클 샘플 코퍼스
- **[edwardkim/rhwp](https://github.com/edwardkim/rhwp)** — 멱등성·검증 게이트 설계 영감

## License · Maintainer

Apache-2.0 ([LICENSE](LICENSE) · [NOTICE](NOTICE)) — **Kohkyuhyun** [@airmang](https://github.com/airmang) · [kokyuhyun@hotmail.com](mailto:kokyuhyun@hotmail.com)
