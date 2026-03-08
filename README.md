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

**hwpx-mcp-server**는 [모델 컨텍스트 프로토콜(MCP)](https://modelcontextprotocol.io) 표준을 따르는 서버로, [python-hwpx](https://github.com/airmang/python-hwpx) 기반에서 HWPX 문서의 열람 · 검색 · 편집 · 추출을 AI 클라이언트에서 직접 수행할 수 있게 합니다.

> **참고** 이 서버는 Open XML 기반 `.hwpx` 포맷을 지원합니다. 바이너리 `.hwp` 포맷은 직접 편집 대상이 아닙니다.

<br>

## 왜 필요한가?

국내 공공기관·학교·기업에서는 한글 문서 기반 업무가 많지만, 자동화는 오랫동안 운영체제와 프로그램에 크게 의존했습니다.

**hwpx-mcp-server**는 이 제약을 줄이는 데 초점을 둡니다.

- ✅ **운영체제 무관** — Windows, macOS, Linux에서 동작
- ✅ **한글 워드프로세서 불필요** — 순수 파이썬 기반 처리
- ✅ **AI 연동 중심** — Claude Desktop, VS Code, Gemini CLI 등 MCP 클라이언트와 직접 연결
- ✅ **일관된 호출 방식** — 도구 호출마다 `filename`을 명시하는 stateless 구조

<br>

## 사용 사례

- 실전 사용 사례: [`docs/use-cases.md`](docs/use-cases.md)
- 종합 테스트 리포트: [`tests/hwpx_mcp_report_updated.md`](tests/hwpx_mcp_report_updated.md)

<br>

## 빠른 시작

### 1. 설치 및 실행

[uv](https://docs.astral.sh/uv/getting-started/installation/) 기준:

```bash
uvx hwpx-mcp-server
```

또는 `pip` 설치 후 실행:

```bash
pip install hwpx-mcp-server
hwpx-mcp-server
```

요구 사항:

- `Python >= 3.10`
- `python-hwpx >= 2.6`

현재 저장소 기준 검증 버전은 `python-hwpx 2.7.1`입니다.

### 2. MCP 클라이언트 설정

<details>
<summary><b>Claude Desktop</b></summary>

`claude_desktop_config.json`

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

`~/.gemini/settings.json`

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
<summary><b>VS Code</b></summary>

`.vscode/mcp.json`

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

각 에디터의 MCP 설정 파일에 같은 블록을 추가하면 됩니다.

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

## 주요 기능

기본 모드에서 주요 HWPX 편집 도구를 제공하며, 고급 모드(`HWPX_MCP_ADVANCED=1`)에서는 점검·검증용 도구가 추가로 활성화됩니다.

### 📖 읽기 및 탐색

| 도구 | 설명 |
|---|---|
| `get_document_info` | 문서 메타데이터, 섹션, 문단, 표 개수 조회 |
| `get_document_text` | 문서 전체 텍스트 추출 (`max_chars` 지원) |
| `get_document_outline` | 제목과 개요 구조 추출 |
| `get_paragraph_text` | 특정 문단 텍스트 조회 |
| `get_paragraphs_text` | 문단 범위 조회 |
| `list_available_documents` | 폴더 안의 `.hwpx` 파일 목록 조회 |

### 🔎 검색 및 치환

| 도구 | 설명 |
|---|---|
| `find_text` | 키워드 검색과 주변 문맥 반환 |
| `search_and_replace` | 단일 텍스트 치환 |
| `batch_replace` | 여러 치환 작업 일괄 실행 |

### ✏️ 문서 편집

| 도구 | 설명 |
|---|---|
| `add_heading` | 제목(헤딩) 문단 추가 |
| `add_paragraph` / `insert_paragraph` / `delete_paragraph` | 문단 추가, 삽입, 삭제 |
| `add_page_break` | 페이지 나누기 추가 |
| `add_memo` / `remove_memo` | 메모 추가, 제거 |
| `copy_document` | 안전한 사본 생성 후 작업 시작 |

### 📊 표 편집

| 도구 | 설명 |
|---|---|
| `add_table` / `get_table_text` | 표 생성, 조회 |
| `set_table_cell_text` | 셀 텍스트 수정 |
| `merge_table_cells` / `split_table_cell` | 셀 병합, 분할 |
| `format_table` | 표 헤더 등 기본 서식 적용 |

### 🎨 서식 및 스타일

| 도구 | 설명 |
|---|---|
| `format_text` | 텍스트 범위 서식 적용 |
| `create_custom_style` | 커스텀 스타일 생성 |
| `list_styles` | 문서 스타일 목록 조회 |

스타일 참조 팁: `add_paragraph(..., style=...)`와 `insert_paragraph(..., style=...)`는 `list_styles`의 `id`, `create_custom_style`이 반환하는 `style_id`, 스타일 이름을 모두 받을 수 있습니다.

### 📤 추출

| 도구 | 설명 |
|---|---|
| `hwpx_to_markdown` | HWPX 문서를 Markdown으로 변환 |
| `hwpx_to_html` | HWPX 문서를 HTML로 변환 |
| `hwpx_extract_json` | HWPX 문서를 구조화된 JSON으로 추출 |

### 🔬 고급 도구

`HWPX_MCP_ADVANCED=1`일 때 활성화:

| 도구 | 설명 |
|---|---|
| `package_parts` | OPC 파트 목록 조회 |
| `package_get_xml` / `package_get_text` | 파트 XML 또는 텍스트 조회 |
| `object_find_by_tag` / `object_find_by_attr` | XML 요소 검색 |
| `plan_edit` / `preview_edit` / `apply_edit` | 편집 계획, 미리보기, 적용 |
| `validate_structure` / `lint_text_conventions` | 구조 검증, 텍스트 규칙 점검 |

<br>

## 환경 변수

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

## 테스트

```bash
# 테스트 의존성 설치
python -m pip install -e ".[test]"

# 전체 테스트
python -m pytest -q
```

추가 참고:

- 사용 사례: `docs/use-cases.md`
- 종합 리포트: `tests/hwpx_mcp_report_updated.md`
- 스킬 기반 워크플로: `docs/skill-first-workflows.md`

<br>

## 라이선스

[MIT](LICENSE)

<br>

## 작성자

**고규현** — 광교고등학교 정보·컴퓨터 교사

- 이메일: [kokyuhyun@hotmail.com](mailto:kokyuhyun@hotmail.com)
- GitHub: [@airmang](https://github.com/airmang)
