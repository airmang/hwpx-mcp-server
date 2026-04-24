<p align="center">
  <h1 align="center">📄 hwpx-mcp-server</h1>
  <p align="center">
    <strong>AI 에이전트가 HWPX 문서를 바로 읽고, 찾고, 수정하게 만드는 MCP 서버</strong>
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

## 이 서버가 바로 해결하는 일

- **Claude Desktop, VS Code, Gemini CLI 같은 MCP 클라이언트에서 HWPX를 바로 읽기**
- **복사본을 만든 뒤 안전하게 검색·치환·표 편집·문단 추가 수행**
- **문서 개요, 표 맵, 패키지 구조를 AI가 직접 조회하고 후속 작업으로 연결**
- **한글 워드프로세서 없이 서버·CI·로컬 개발 환경에서 같은 흐름 유지**
- **고급 모드에서 검증, package inspection, edit planning까지 확장**

## 왜 필요한가?

국내 공공기관·학교·기업에서는 한글 문서 기반 업무가 많지만, 자동화는 오랫동안 운영체제와 프로그램에 크게 의존했습니다.

**hwpx-mcp-server**는 이 제약을 줄이는 데 초점을 둡니다.

- ✅ **운영체제 무관** — Windows, macOS, Linux에서 동작
- ✅ **한글 워드프로세서 불필요** — 순수 파이썬 기반 처리
- ✅ **AI 연동 중심** — Claude Desktop, VS Code, Gemini CLI 등 MCP 클라이언트와 직접 연결
- ✅ **문서 편집을 도구 호출로 표준화** — 읽기, 편집, 복제, 검증을 MCP 도구 집합으로 노출
- ✅ **실전 작업 흐름에 맞춘 설계** — read, copy, edit, inspect, validate를 한 서버 표면으로 정리
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

현재 저장소 기준 검증 버전은 `python-hwpx 2.9.0`입니다 (2026-04-15 검증).
최소 지원 버전은 `python-hwpx >= 2.6`입니다.

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

## 작업별 빠른 경로

처음부터 모든 도구를 외울 필요는 없다. 보통은 아래 세 흐름 중 하나로 시작하면 된다.

### 1. 읽기 전용으로 문서를 파악할 때

1. `get_document_info`
2. `get_document_outline` 또는 `get_document_text`
3. `find_text`, `get_table_text`, `get_table_map` 같은 읽기 도구로 필요한 부분만 더 본다.

이 흐름은 원본을 저장하지 않는다.

### 2. 안전하게 수정할 때

1. `copy_document`로 작업용 사본을 만든다.
2. 읽기 도구로 수정 대상을 다시 확인한다.
3. `search_and_replace`, `batch_replace`, `set_table_cell_text`, `add_paragraph` 같은 가장 작은 변경 도구만 쓴다.
4. 수정 후 다시 읽기 도구로 결과를 확인한다.
5. 납품이나 handoff가 필요하면 검토가 끝난 복사본 파일을 그대로 넘긴다.

핵심은 `copy first`, `smallest edit`, `re-read after edits`다.

### 3. 구조 점검과 검증이 목적일 때

1. MCP 설정에서 `HWPX_MCP_ADVANCED=1`
2. `package_parts`, `package_get_xml`, `package_get_text`로 내부 파트를 본다.
3. `validate_structure`, `lint_text_conventions`, `plan_edit`, `preview_edit`는 기본 편집 흐름과 섞지 않고 점검/검증 단계에서만 사용한다.

## 안전한 사용 원칙

이 서버의 공개 표면은 **현재 README에 적힌 MCP 도구 집합**이다. 워크플로 문서나 스킬 예시는 이 도구들을 조합하는 사용 패턴이지, 별도의 새 public tool 계약이 아니다.

실전에서는 아래 순서가 가장 안전하다.

1. 먼저 `get_document_info`, `get_document_text`, `find_text` 같은 읽기 도구로 문서를 파악한다.
2. 수정 전 결과물을 보존해야 하면 `copy_document`를 먼저 호출한다.
3. 수정 도구는 **호출 즉시 저장**되므로, 검토용 경로가 필요하면 원본 대신 복사본에서 작업한다.
4. 결과물을 따로 넘겨야 하면 검토가 끝난 복사본 파일을 handoff 경계로 사용한다.
5. package inspection, edit planning, validation은 `HWPX_MCP_ADVANCED=1`일 때만 쓰고, 기본 흐름과 섞어 쓰지 않는다.

짧게 말하면:
- **read first**
- **copy before risky edits**
- **mutating tools persist immediately**
- **explicit handoff uses the reviewed copy**
- **advanced mode는 점검/검증용으로 분리**

## 도구 동작 빠른 감각

| 구분 | 대표 도구 | 특징 |
|---|---|---|
| 파일 기반 읽기 전용 | `get_document_info`, `get_document_text`, `get_paragraph_text`, `get_paragraphs_text`, `find_text`, `get_table_text`, `get_table_map`, `find_cell_by_label`, `list_styles`, `list_available_documents` | 기존 `.hwpx` 파일을 읽거나 탐색만 한다. 저장하지 않는다. |
| 파일 기반 즉시 저장 편집 | `create_document`, `search_and_replace`, `batch_replace`, `add_heading`, `add_paragraph`, `insert_paragraph`, `delete_paragraph`, `add_table`, `fill_by_path`, `set_table_cell_text`, `add_page_break`, `add_memo`, `remove_memo`, `format_text`, `create_custom_style`, `merge_table_cells`, `split_table_cell`, `format_table` | 호출 결과가 곧 대상 파일 변경이다. 검토용이면 먼저 복사본에서 작업한다. |
| 복제 / handoff 경계 | `copy_document` | 원본 보호와 reviewable working copy 분리에 쓴다. 현재 FastMCP surface에는 별도 public `save` / `save_as` tool이 없다. |
| payload/url 기반 추출 | `hwpx_to_markdown`, `hwpx_to_html`, `hwpx_extract_json` | 파일명을 직접 수정하지 않는다. HWPX payload 또는 URL을 읽어 변환 결과만 돌려준다. |
| 고급 점검/검증 | `package_parts`, `package_get_xml`, `package_get_text`, `object_find_by_tag`, `object_find_by_attr`, `plan_edit`, `preview_edit`, `apply_edit`, `validate_structure`, `lint_text_conventions` | `HWPX_MCP_ADVANCED=1`일 때만 활성화한다. package/구조 점검용이다. |

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

`find_text`는 읽기 전용이고, `search_and_replace` / `batch_replace`는 호출 즉시 저장된다.

| 도구 | 설명 |
|---|---|
| `find_text` | 키워드 검색과 주변 문맥 반환 |
| `search_and_replace` | 단일 텍스트 치환 |
| `batch_replace` | 여러 치환 작업 일괄 실행 |

### ✏️ 문서 편집

이 카테고리의 도구는 대체로 대상 파일에 즉시 반영된다. 구조 변경 전에는 `copy_document`로 작업용 사본을 만들고, handoff는 검토가 끝난 복사본 파일 기준으로 잡는 편이 안전하다.

| 도구 | 설명 |
|---|---|
| `add_heading` | 제목(헤딩) 문단 추가 |
| `add_paragraph` / `insert_paragraph` / `delete_paragraph` | 문단 추가, 삽입, 삭제 |
| `add_page_break` | 페이지 나누기 추가 |
| `add_memo` / `remove_memo` | 메모 추가, 제거 |

### 💾 복제

이 카테고리는 수정 워크플로의 안전 장치다. 원본 보호와 reviewable working copy 분리에 쓴다.

| 도구 | 설명 |
|---|---|
| `copy_document` | 원본을 건드리기 전에 작업용 사본 생성 |

### 📊 표 편집

`get_table_text`, `get_table_map`, `find_cell_by_label`는 읽기 전용이다. `fill_by_path`, `set_table_cell_text`, `merge_table_cells`, `split_table_cell`, `format_table`은 호출 즉시 저장된다.

| 도구 | 설명 |
|---|---|
| `add_table` / `get_table_text` | 표 생성, 조회 |
| `get_table_map` | 표가 많은 문서에서 표 위치, 크기, 헤더 문맥을 빠르게 탐색 |
| `find_cell_by_label` | 한국어 양식/템플릿에서 라벨 셀 기준으로 입력 대상 셀 찾기 |
| `fill_by_path` | `성명 > right`, `소속 > right`, `합계 > down > right` 같은 경로 구문으로 셀 채우기 |
| `set_table_cell_text` | 셀 텍스트 수정 |
| `merge_table_cells` / `split_table_cell` | 셀 병합, 분할 |
| `format_table` | 표 헤더 등 기본 서식 적용 |

변경 도구는 호출 시 즉시 저장됩니다. 검토용 사본이 필요하면 `copy_document`를 먼저 사용하고, 납품본은 검토가 끝난 복사본 파일을 기준으로 관리하세요.

### 🎨 서식 및 스타일

`list_styles`는 읽기 전용이다. `format_text`, `create_custom_style`은 문서를 직접 갱신한다.

| 도구 | 설명 |
|---|---|
| `format_text` | 텍스트 범위 서식 적용 |
| `create_custom_style` | 커스텀 스타일 생성 |
| `list_styles` | 문서 스타일 목록 조회 |

스타일 참조 팁: `add_paragraph(..., style=...)`와 `insert_paragraph(..., style=...)`는 `list_styles`의 `id`, `create_custom_style`이 반환하는 `style_id`, 스타일 이름을 모두 받을 수 있습니다.

### 📤 추출

이 카테고리는 filename 기반 편집 도구가 아니다. HWPX payload 또는 URL을 읽어 변환 결과를 반환한다.

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
