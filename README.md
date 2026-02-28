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

**hwpx-mcp-server**는 [Model Context Protocol(MCP)](https://modelcontextprotocol.io) 표준을 따르는 서버로, [python-hwpx](https://github.com/airmang/python-hwpx) 기반에서 HWPX 문서의 열람 · 검색 · 편집 · 저장을 AI 클라이언트에서 직접 수행할 수 있게 합니다.

> **Note** — 이 서버는 Open XML 기반 `.hwpx` 포맷을 지원합니다. 레거시 바이너리 `.hwp` 포맷은 직접 편집 대상이 아닙니다.

<br>

## Why?

국내 공공기관·학교·기업에서는 한글 문서 기반 업무가 매우 많지만, 자동화는 오랫동안 OS/프로그램 의존성이 컸습니다.

**hwpx-mcp-server**는 이 제약을 줄이는 데 초점을 맞춥니다.

- ✅ **OS 무관** — Windows, macOS, Linux에서 동작
- ✅ **한글 워드프로세서 불필요** — 순수 파이썬 기반 처리
- ✅ **AI 네이티브** — Claude Desktop, VS Code, Gemini CLI 등 MCP 클라이언트와 직접 연결
- ✅ **Stateless 기본 설계** — 도구 호출마다 `filename`을 명시해 일관적으로 실행

<br>

## Use Cases

- 실전 사용 사례 9개 보기: [`docs/use-cases.md`](docs/use-cases.md)
- 종합 테스트 리포트: [`tests/hwpx_mcp_report_updated.md`](tests/hwpx_mcp_report_updated.md)

<br>

## Quick Start

### 1. 설치 & 실행

[uv](https://docs.astral.sh/uv/getting-started/installation/) 기준:

```bash
uvx hwpx-mcp-server
```

또는 pip 설치:

```bash
pip install hwpx-mcp-server
hwpx-mcp-server
```

요구사항:

- `Python >= 3.10`
- `python-hwpx >= 2.4`

### 2. MCP 클라이언트 설정

<details>
<summary><b>Claude Desktop</b></summary>

`claude_desktop_config.json`:

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

`~/.gemini/settings.json`:

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

`.vscode/mcp.json`:

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

각 에디터 MCP 설정 파일에 동일한 블록을 추가:

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

기본 모드에서 31개 도구, 고급 모드(`HWPX_MCP_ADVANCED=1`)에서 추가 도구가 활성화됩니다.

### 🧭 가이드

| 도구 | 설명 |
|---|---|
| `get_tool_guide` | 워크플로별 도구 사용 가이드 반환 (read/edit/template/export/table/style) |

### 📖 읽기 & 탐색

| 도구 | 설명 |
|---|---|
| `get_document_info` | 문서 메타데이터/섹션/문단/표 개수 조회 |
| `get_document_text` | 문서 전체 텍스트 추출 (`max_chars` 지원) |
| `get_document_outline` | 제목/개요 구조 추출 |
| `get_paragraph_text` | 특정 문단 텍스트 조회 |
| `get_paragraphs_text` | 문단 범위 조회 |
| `list_available_documents` | 폴더 내 `.hwpx` 파일 목록 조회 |

### 🔎 검색 & 치환

| 도구 | 설명 |
|---|---|
| `find_text` | 키워드 검색 + 컨텍스트 반환 |
| `search_and_replace` | 단일 치환 (split-run 보강) |
| `batch_replace` | 다중 치환 일괄 실행 |

### ✏️ 편집

| 도구 | 설명 |
|---|---|
| `add_heading` | 제목(헤딩) 문단 추가 |
| `add_paragraph` / `insert_paragraph` / `delete_paragraph` | 문단 추가/삽입/삭제 |
| `add_page_break` | 페이지 나누기 추가 |
| `add_memo` / `remove_memo` | 메모 추가/제거 |
| `copy_document` | 문서 안전 복사 |

### 📊 표

| 도구 | 설명 |
|---|---|
| `add_table` / `get_table_text` | 표 생성/조회 |
| `set_table_cell_text` | 셀 텍스트 수정 |
| `merge_table_cells` / `split_table_cell` | 셀 병합/분할 |
| `format_table` | 표 헤더 등 기본 서식 적용 |

### 🎨 스타일

| 도구 | 설명 |
|---|---|
| `format_text` | 텍스트 범위 서식 적용(굵기, 기울임, 밑줄, 색상 등) |
| `create_custom_style` | 커스텀 스타일 생성 |
| `list_styles` | 문서 스타일 목록 조회 |

### 📤 내보내기

| 도구 | 설명 |
|---|---|
| `export_text` | 문서를 순수 텍스트로 내보내기 (python-hwpx 네이티브) |
| `export_html` | 문서를 HTML로 내보내기 |
| `export_markdown` | 문서를 Markdown으로 내보내기 |

### 🔬 고급 (옵션)

`HWPX_MCP_ADVANCED=1`일 때 활성화:

| 도구 | 설명 |
|---|---|
| `package_parts` | OPC 파트 목록 조회 |
| `package_get_xml` / `package_get_text` | 파트 XML/텍스트 조회 |
| `object_find_by_tag` / `object_find_by_attr` | XML 요소 검색 |
| `plan_edit` / `preview_edit` / `apply_edit` | 편집 계획/미리보기/적용 |
| `validate_structure` / `lint_text_conventions` | 구조 검증/텍스트 린트 |

<br>

## Configuration

| 변수 | 설명 | 기본값 |
|---|---|---|
| `HWPX_MCP_MAX_CHARS` | 텍스트 반환 도구 기본 최대 길이 | `10000` |
| `HWPX_MCP_AUTOBACKUP` | `1`이면 저장 전 `.bak` 백업 생성 | `1` |
| `HWPX_MCP_ADVANCED` | `1`이면 고급 도구 활성화 | `0` |
| `LOG_LEVEL` | 로그 레벨 | `INFO` |

환경 변수 포함 MCP 설정 예시:

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"],
      "env": {
        "HWPX_MCP_MAX_CHARS": "12000",
        "HWPX_MCP_AUTOBACKUP": "1",
        "HWPX_MCP_ADVANCED": "0",
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

<br>

## Advanced

<details>
<summary><b>📦 OPC 파트 조회</b></summary>

고급 모드에서 문서 내부 파트를 직접 조회할 수 있습니다.

- `package_parts`
- `package_get_xml`
- `package_get_text`

</details>

<details>
<summary><b>🧭 편집 파이프라인</b></summary>

고급 모드에서 `plan_edit → preview_edit → apply_edit` 흐름으로 변경 계획을 검토하고 적용할 수 있습니다.

</details>

<details>
<summary><b>🧪 구조/규칙 검사</b></summary>

고급 모드에서 다음 검사 도구를 사용할 수 있습니다.

- `validate_structure`
- `lint_text_conventions`

</details>

<br>

## Testing

```bash
# 테스트 의존성 설치
python -m pip install -e ".[test]"

# 전체 테스트
python -m pytest -q
```

로컬 기준(2026-02-28) 전체 113개 테스트가 통과했습니다.

- 실전 사용 사례: `docs/use-cases.md`
- 종합 리포트: `tests/hwpx_mcp_report_updated.md`
- 회귀 테스트: `tests/test_hwpx_report_regressions.py`

<br>

## Architecture

```text
hwpx-mcp-server
├── src/hwpx_mcp_server/
│   ├── server.py            # Stateless MCP 진입점
│   ├── hwpx_ops.py          # 고급/내부 연산 래퍼
│   ├── storage.py           # 저장 백엔드 (atomic save)
│   ├── core/                # 문단/표/검색/서식 핵심 로직
│   ├── tools.py             # 확장 도구 스키마/정의
│   └── schema/              # JSON 스키마 빌더/정리기
├── tests/                   # 단위 + E2E + 회귀 테스트
└── pyproject.toml
```

<br>

## Comparison

| | hwpx-mcp-server | hwp(바이너리) COM 자동화 계열 |
|---|---|---|
| 대상 포맷 | `.hwpx` (Open XML) | `.hwp` (바이너리) 중심 |
| OS | Windows · macOS · Linux | 대체로 Windows 중심 |
| 한글 프로그램 의존 | 불필요 | 필요한 경우가 많음 |
| 연동 방식 | MCP + 파이썬 라이브러리 | 데스크톱 앱 자동화 |

<br>

## Changelog (v2.2.0)

### python-hwpx 2.4 마이그레이션

- `document.save()` → `document.save_to_path()` 전환 (deprecated API 제거)
- `from hwpx.package` → `from hwpx.opc.package` 임포트 경로 수정
- 하드코딩된 네임스페이스 문자열 → `hwpx.oxml.namespaces` 상수 임포트로 교체
- 수동 XML 조작 → 네이티브 API 전환: `doc.remove_paragraph()`, `table.merge_cells()`, `table.split_merged_cell()`
- lxml/stdlib ElementTree 혼합 타입 충돌 수정 (border-fill 생성, 메모 추가)

### 신규 기능

- **`export_text` / `export_html` / `export_markdown`** — python-hwpx 2.4 네이티브 익스포터 활용
- **`get_tool_guide`** — LLM 워크플로 가이드 도구 (읽기/편집/템플릿/내보내기/표/스타일)
- **Atomic save** — 임시파일 → 무결성 검증 → 이동 방식의 안전한 저장

<br>

## Contributing

기여를 환영합니다.

1. Fork 후 브랜치 생성
2. 변경 + 테스트 추가/수정
3. `pytest -q` 통과 확인 후 PR

<br>

## License

[MIT](LICENSE) © 고규현 (Kyuhyun Koh)

<br>

## Author

**고규현** — 광교고등학교 정보·컴퓨터 교사

- ✉️ [kokyuhyun@hotmail.com](mailto:kokyuhyun@hotmail.com)
- 🐙 [@airmang](https://github.com/airmang)

<br>

## Transport Modes (Stdio + HTTP)

Existing stdio usage is unchanged:

```bash
hwpx-mcp-server
```

Run the same MCP tool set over Streamable HTTP:

```bash
hwpx-mcp-server --transport streamable-http --host 127.0.0.1 --port 8000
```

Environment variables are also supported:

- `HWPX_MCP_TRANSPORT` (`stdio` or `streamable-http`)
- `HWPX_MCP_HOST` (default: `127.0.0.1`)
- `HWPX_MCP_PORT` (default: `8000`)

Note: HTTP auth is intentionally kept simple for now (dev mode). A production auth hook is left as a TODO in the server entrypoint.

## New Read/Extract Tools

All three tools accept exactly one input source:

- `hwpx_base64`: base64 encoded `.hwpx` bytes
- `url`: downloadable `https://...` URL

Common options:

- `output`: `full` or `chunks`
- `chunk_strategy`: `section` or `paragraph`
- `max_chars_per_chunk`: integer, default via server setting

### 1) `hwpx_to_markdown`

Response:

```json
{
  "markdown": "# Title\n\nParagraph...",
  "chunks": ["..."],
  "meta": {
    "source_type": "base64",
    "section_count": 2,
    "paragraph_count": 10,
    "table_count": 1,
    "figure_caption_count": 1
  }
}
```

### 2) `hwpx_to_html`

Response:

```json
{
  "html": "<!doctype html><html>...</html>",
  "chunks": ["<section>...</section>"],
  "meta": {
    "source_type": "url",
    "image_policy": "omitted"
  }
}
```

### 3) `hwpx_extract_json`

Response:

```json
{
  "doc": {
    "title": "Title",
    "toc": [{ "level": 1, "text": "Title", "paragraph_index": 0 }],
    "sections": [{ "index": 0, "title": "Title", "paragraphs": [] }],
    "tables": [],
    "figures": []
  },
  "chunks": [{ "chunk_index": 0, "strategy": "section", "section": {} }],
  "meta": {
    "source_type": "base64"
  }
}
```
