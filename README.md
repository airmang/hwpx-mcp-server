<p align="center">
  <h1 align="center">📄 hwpx-mcp-server</h1>
  <p align="center">
    <strong>한글(HWPX) 문서를 AI로 자동화하는 MCP 서버</strong>
  </p>
  <p align="center">
    한글 워드프로세서 없이 · 순수 파이썬 · 크로스 플랫폼
  </p>
  <p align="center">
    <a href="https://pypi.org/project/hwpx-mcp-server/"><img src="https://img.shields.io/pypi/v/hwpx-mcp-server?style=flat-square&color=blue" alt="PyPI"></a>
    <a href="https://pypi.org/project/hwpx-mcp-server/"><img src="https://img.shields.io/pypi/pyversions/hwpx-mcp-server?style=flat-square" alt="Python"></a>
    <a href="https://github.com/airmang/hwpx-mcp-server/blob/main/LICENSE"><img src="https://img.shields.io/github/license/airmang/hwpx-mcp-server?style=flat-square" alt="License"></a>
    <a href="https://github.com/airmang/hwpx-mcp-server/actions"><img src="https://img.shields.io/github/actions/workflow/status/airmang/hwpx-mcp-server/test.yml?style=flat-square&label=tests" alt="Tests"></a>
  </p>
</p>

---

**hwpx-mcp-server**는 [Model Context Protocol(MCP)](https://modelcontextprotocol.io) 표준을 따르는 서버로, [python-hwpx](https://github.com/airmang/python-hwpx) 라이브러리 기반으로 HWPX 문서의 열람 · 검색 · 편집 · 저장을 AI 클라이언트에서 직접 수행할 수 있게 합니다.

> **Note** — 이 서버는 Open XML 기반 `.hwpx` 포맷을 지원합니다. 레거시 바이너리 `.hwp` 포맷은 지원하지 않습니다.

<br>

## Why?

한국의 공공기관·학교·기업에서 매일 수만 건의 한글 문서가 오갑니다. 그런데 이 문서를 프로그래밍으로 다루려면? 한글 워드프로세서가 설치된 Windows에서만, COM 자동화로만 가능했습니다.

**hwpx-mcp-server**는 이 제약을 없앱니다.

- ✅ **OS 무관** — Windows, macOS, Linux 어디서든 동작
- ✅ **한글 워드프로세서 불필요** — 순수 파이썬으로 HWPX를 직접 파싱
- ✅ **AI 네이티브** — Claude Desktop, Gemini CLI 등 MCP 클라이언트와 바로 연결

<br>

## Quick Start

### 1. 설치 & 실행

[uv](https://docs.astral.sh/uv/getting-started/installation/)만 있으면 한 줄로 시작합니다.

```bash
uvx hwpx-mcp-server
```

> `python-hwpx >= 1.9, < 2.0` 버전이 필요하며, 첫 실행 시 자동 설치됩니다.

### 2. MCP 클라이언트 설정

<details>
<summary><b>Claude Desktop</b></summary>

`claude_desktop_config.json`에 추가:

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"]
    }
  }
}
```

</details>

<details>
<summary><b>Gemini CLI</b></summary>

`~/.gemini/settings.json`에 추가:

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"]
    }
  }
}
```

</details>

<details>
<summary><b>VS Code (Copilot Chat)</b></summary>

`.vscode/mcp.json`에 추가:

```json
{
  "servers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"]
    }
  }
}
```

</details>

<details>
<summary><b>Cursor / Windsurf</b></summary>

각 에디터의 MCP 설정 파일에 동일한 JSON 블록을 추가하세요:

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"]
    }
  }
}
```

</details>

<br>

## Features

### 📖 읽기 & 탐색

| 도구 | 설명 |
|------|------|
| `open_info` | 문서 메타데이터 · 문단 수 · 헤더 개수 요약 |
| `read_text` | 페이지네이션 기반 텍스트 추출 (기본 200문단 단위) |
| `read_paragraphs` | 특정 문단만 선택적으로 추출 |
| `text_extract_report` | 주석 포함 전체 텍스트 리포트 |
| `find` | 키워드 검색 + 전후 컨텍스트 스니펫 반환 |
| `find_runs_by_style` | 스타일 기반 런(run) 검색 |
| `list_sections` / `list_headers` | 섹션 · 헤더 구조 탐색 |

### ✏️ 편집

| 도구 | 설명 |
|------|------|
| `replace_text_in_runs` | 스타일 보존 텍스트 치환 (`dryRun` 지원) |
| `add_paragraph` / `insert_paragraphs_bulk` | 문단 추가 (단일 · 벌크) |
| `add_table` / `set_table_cell_text` / `replace_table_region` | 표 생성 · 셀 편집 · 영역 치환 |
| `get_table_cell_map` / `split_table_cell` | 병합 셀 매핑 · 분할 |
| `add_memo` / `add_memo_with_anchor` / `remove_memo` | 메모 관리 |
| `add_shape` / `add_control` | 개체 · 컨트롤 추가 |

### 🎨 스타일링

| 도구 | 설명 |
|------|------|
| `ensure_run_style` | 스타일 존재 확인 · 생성 |
| `apply_style_to_text_ranges` | 단어 단위 스타일 적용 |
| `apply_style_to_paragraphs` | 문단 단위 스타일 적용 |
| `list_styles_and_bullets` | 사용 가능한 스타일 · 글머리표 목록 |

### 💾 파일 관리

| 도구 | 설명 |
|------|------|
| `save` / `save_as` | 저장 (자동 `.bak` 백업 옵션) |
| `make_blank` | 새 빈 문서 생성 |
| `validate_structure` / `lint_text_conventions` | 구조 검증 · 텍스트 린트 |

### 🔬 OPC 패키지 내부 (읽기 전용)

| 도구 | 설명 |
|------|------|
| `package_parts` | 패키지 내 모든 파트 경로 목록 |
| `package_get_text` / `package_get_xml` | 파트를 텍스트 또는 XML로 조회 |
| `object_find_by_tag` / `object_find_by_attr` | XML 요소 검색 |

<br>

## Configuration

환경 변수로 서버 동작을 조정합니다.

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `HWPX_MCP_PAGING_PARA_LIMIT` | `read_text`가 반환하는 최대 문단 수 | `200` |
| `HWPX_MCP_AUTOBACKUP` | `1`이면 저장 전 `.bak` 백업 생성 | `0` |
| `LOG_LEVEL` | stderr JSONL 로그 레벨 | `INFO` |
| `HWPX_MCP_HARDENING` | `1`이면 3단계 편집 파이프라인 + 검색/컨텍스트 도구 활성화 | `0` |

환경 변수를 포함한 전체 설정 예시:

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"],
      "env": {
        "HWPX_MCP_PAGING_PARA_LIMIT": "200",
        "HWPX_MCP_AUTOBACKUP": "1",
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

<br>

## Advanced

<details>
<summary><b>📁 문서 로케이터 (Document Locator)</b></summary>

모든 도구는 문서를 가리키는 **discriminated union** 로케이터를 사용합니다.

**로컬 파일** (기본값 — 기존 스키마와 동일):

```json
{ "path": "sample.hwpx" }
```

**HTTP 백엔드 연계**:

```json
{ "type": "uri", "uri": "reports/weekly.hwpx", "backend": "http" }
```

**사전 등록된 핸들** (하드닝 모드에서 후속 편집 시):

```json
{ "type": "handle", "handleId": "doc-1234" }
```

</details>

<details>
<summary><b>🔐 하드닝 편집 파이프라인</b></summary>

`HWPX_MCP_HARDENING=1`로 활성화하면 모든 편집이 **Plan → Preview → Apply** 3단계를 거칩니다.

| 단계 | 도구 | 설명 |
|------|------|------|
| 1. 계획 | `hwpx.plan_edit` | 변경 대상과 의도를 설명하면 `planId`와 작업 요약을 반환 |
| 2. 검토 | `hwpx.preview_edit` | diff, 모호성 경고, 안전 점수를 포함한 미리보기 |
| 3. 적용 | `hwpx.apply_edit` | `confirm: true` 명시 시 실제 문서 변경 수행 |

추가 지원 도구:

- **`hwpx.search`** — 정규식/키워드 기반 문서 검색
- **`hwpx.get_context`** — 특정 문단 주변 제한 창(window) 추출

에러 코드: `PREVIEW_REQUIRED` · `AMBIGUOUS_TARGET` · `UNSAFE_WILDCARD` · `IDEMPOTENT_REPLAY`

</details>

<details>
<summary><b>📐 표 편집 고급 옵션</b></summary>

`get_table_cell_map`으로 전체 격자를 직렬화하여 각 위치의 앵커 셀, 병합 범위를 확인합니다.

```json
{
  "name": "set_table_cell_text",
  "arguments": {
    "path": "sample.hwpx",
    "tableIndex": 0,
    "row": 1, "col": 1,
    "text": "논리 좌표 편집",
    "logical": true,
    "splitMerged": true,
    "autoFit": true
  }
}
```

- `logical: true` — 논리 좌표계 사용
- `splitMerged: true` — 쓰기 전 병합 영역 자동 분할
- `autoFit: true` — 셀 내용에 맞춰 열 너비 재계산

</details>

<details>
<summary><b>🔍 검색 컨텍스트 길이 조절</b></summary>

`find` 도구는 기본 전후 80자의 컨텍스트 스니펫을 반환합니다. `contextRadius` 인수로 조정 가능합니다:

```json
{
  "name": "find",
  "arguments": {
    "path": "sample.hwpx",
    "query": "HWPX",
    "contextRadius": 200
  }
}
```

</details>

<br>

## Testing

```bash
# 의존성 설치
python -m pip install -e ".[test]"

# 전체 테스트 실행
python -m pytest
```

엔드투엔드 테스트가 서버의 대부분의 도구를 실제 호출하여 텍스트 · 표 · 메모 편집, OPC 패키지 읽기, 자동 백업 등을 검증합니다.

<br>

## Architecture

```
hwpx-mcp-server
├── src/
│   └── hwpx_mcp_server/
│       ├── server.py          # MCP 서버 진입점
│       ├── tools/             # 도구 핸들러
│       └── schema/            # JSON 스키마 빌더 (draft-07, $ref 제거)
├── tests/
│   └── test_mcp_end_to_end.py # E2E 테스트 스위트
├── docs/                      # 문서
└── pyproject.toml
```

- **순수 파이썬**: `python-hwpx >= 1.9, < 2.0` · `mcp` · `anyio` · `pydantic`
- **경로 해석**: 서버 실행 디렉터리 기준, 별도 설정 불필요
- **안전 장치**: 파괴적 작업에 `dryRun` 플래그 우선 제공 · 자동 `.bak` 백업
- **스키마**: draft-07 호환 Sanitizer를 거쳐 `$ref` / `anyOf` 없이 평탄화

<br>

## Comparison

| | hwpx-mcp-server | [hwp-mcp](https://github.com/jkf87/hwp-mcp) |
|---|---|---|
| **포맷** | `.hwpx` (Open XML) | `.hwp` (바이너리) |
| **OS** | Windows · macOS · Linux | Windows only |
| **한글 필요** | ❌ 불필요 | ✅ 필수 (COM 자동화) |
| **방식** | 순수 파이썬 파싱 | 한글 프로세스 제어 |
| **편집 도구 수** | 30+ | ~10 |

<br>

## Roadmap

- [ ] Streamable HTTP transport 지원
- [ ] MCP Resources 노출 (문서 메타데이터 URI)
- [ ] MCP Prompts 템플릿 (문서 요약, 표 추출 등)
- [ ] Docker 이미지 배포
- [ ] GitHub Actions CI/CD 파이프라인
- [ ] MCP Registry 등록

<br>

## Contributing

기여를 환영합니다! 버그 리포트, 기능 요청, PR 모두 감사합니다.

1. Fork → Branch (`feat/amazing-feature`) → Commit → PR
2. 테스트를 추가하거나 기존 테스트가 통과하는지 확인해주세요
3. 한글/영문 모두 OK

<br>

## License

[MIT](LICENSE) © 고규현 (Kyuhyun Koh)

<br>

## Author

**고규현** — 광교고등학교 정보·컴퓨터 교사

- ✉️ [kokyuhyun@hotmail.com](mailto:kokyuhyun@hotmail.com)
- 🐙 [@airmang](https://github.com/airmang)
