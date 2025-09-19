# HWPX MCP Server

`hwpx-mcp-server`는 순수 파이썬으로 작성된 [Model Context Protocol](https://github.com/modelcontextprotocol/specification) 서버로,
[`python-hwpx`](https://github.com/airmang/python-hwpx) 라이브러리를 기반으로 로컬 HWPX 문서를 열람·검색·편집·저장할 수
있는 다양한 도구를 제공합니다. Gemini, Claude 등의 MCP 호환 클라이언트에서 바로 사용할 수 있도록 표준 입력/출력 기반
전송을 구현했습니다.

## 주요 기능

- 공식 `mcp` 파이썬 SDK로 구현한 표준 입력/출력 기반 MCP 서버.
- 추가 설정 없이 현재 작업 디렉터리를 기준으로 경로를 처리.
- 텍스트 추출, 페이지네이션, 스타일 기반 검색/치환 기능 제공.
- 문단·표·메모·개체·OPC 파트를 다루는 고급 편집 도구.
- 변경 전 자동 백업 옵션(`HWPX_MCP_AUTOBACKUP`).
- [`uvx`](https://github.com/astral-sh/uv)를 이용한 즉시 실행 지원.

## 빠른 시작

```bash
uvx hwpx-mcp-server
```

MCP 클라이언트 설정에 추가할 때는 다음 예시를 활용하세요.

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"],
      "env": {
        "HWPX_MCP_PAGING_PARA_LIMIT": "2000",
        "HWPX_MCP_AUTOBACKUP": "1",
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

서버는 실행된 현재 디렉터리를 기준으로 경로를 해석하며, 별도의 작업 디렉터리 설정 없이 바로 사용할 수 있습니다.

## 환경 변수

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `HWPX_MCP_PAGING_PARA_LIMIT` | 페이지네이션 도구가 반환할 최대 문단 수 | `2000` |
| `HWPX_MCP_AUTOBACKUP` | `1`이면 저장 전 `<file>.bak` 백업 생성 | `0` |
| `HWPX_MCP_ENABLE_OPC_WRITE` | `package_set_text` / `package_set_xml` 사용을 허용할지 여부 | `0` |
| `LOG_LEVEL` | JSON 라인 형태로 stderr에 출력할 로그 레벨 | `INFO` |

## 제공 도구

서버는 다음과 같은 MCP 도구를 등록합니다.

- **open_info** – 문서 메타데이터 및 단락·헤더 개수 요약.
- **list_sections**, **list_headers** – 섹션/헤더 구조 탐색.
- **read_text**, **text_extract_report** – 페이지네이션 및 주석 포함 텍스트 추출.
- **find**, **find_runs_by_style**, **replace_text_in_runs** – 검색 및 스타일 보존 치환.
- **add_paragraph**, **insert_paragraphs_bulk**, **add_table**, **set_table_cell_text**, **replace_table_region** – 문단·표 편집.
- **add_shape**, **add_control**, **add_memo**, **attach_memo_field**, **add_memo_with_anchor**, **remove_memo** – 개체와 메모 관리.
- **ensure_run_style**, **list_styles_and_bullets**, **apply_style_to_paragraphs** – 스타일 생성 및 적용.
- **save**, **save_as**, **make_blank** – 저장 및 새 문서 생성.
- **package_parts**, **package_get_text**, **package_set_text**, **package_get_xml**, **package_set_xml** – OPC 파트 접근(쓰기 도구는 `HWPX_MCP_ENABLE_OPC_WRITE` 필요).
- **object_find_by_tag**, **object_find_by_attr** – XML 요소 검색.
- **validate_structure**, **lint_text_conventions** – 문서 구조 검증 및 텍스트 린트.
- **list_master_pages_histories_versions** – 마스터 페이지/히스토리/버전 요약.

각 도구는 `ListTools` 응답에 JSON 스키마로 노출되며, 클라이언트에서 호출 전에 입력을 검증할 수 있습니다.

## 테스트

핵심 문서 조작을 검증하는 pytest 스위트가 포함되어 있습니다. 의존성을 설치한 뒤 아래 명령으로
테스트를 실행하세요.

```bash
python -m pip install -e .[test]
python -m pytest
```

## 개발 참고

- 서버는 전적으로 파이썬으로 작성되었으며 `python-hwpx`, `mcp`, `anyio`, `pydantic`, `modelcontextprotocol`에 의존합니다.
- 모든 도구 핸들러는 `HwpxOps`의 경로 헬퍼를 사용해 입력 경로를 해석하고, `HwpxDocument` API로 문서를 조작합니다.
- 파괴적 작업에는 `dryRun` 플래그를 기본 제공하며, 자동 백업 옵션이 활성화되어 있으면 `.bak` 파일을 생성합니다.

## 라이선스

이 프로젝트는 MIT 라이선스로 배포됩니다. 자세한 내용은 [LICENSE](LICENSE)를 확인하세요.