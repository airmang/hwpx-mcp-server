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
        "HWPX_MCP_ENABLE_OPC_WRITE": "1",
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
- **object_find_by_tag**, **object_find_by_attr** – XML 요소 검색.
- **validate_structure**, **lint_text_conventions** – 문서 구조 검증 및 텍스트 린트.
- **list_master_pages_histories_versions** – 마스터 페이지/히스토리/버전 요약.

### 고급: OPC 패키지 조작

이 도구들은 `HwpxPackage`를 통해 OPC 파트를 직접 읽고 쓰며, 내부 구조에 손을 대는 강력한 기능입니다. 쓰기 작업은 기본적으로 차단되어 있어 `HWPX_MCP_ENABLE_OPC_WRITE` 환경 변수를 명시적으로 `1`로 설정하지 않으면 실행할 수 없습니다. 또한 `HwpxOps.package_set_text` 및 `HwpxOps.package_set_xml`은 기본적으로 `dryRun`이 `true`인 상태로 동작하여 패키지에 대한 변경 사항을 계산만 하고 파일에는 저장하지 않습니다. `dryRun`을 `false`로 전환하고 쓰기 권한을 열면 즉시 OPC 파일이 덮어써지므로, 잘못 사용하면 문서가 손상될 수 있다는 점에 유의하세요.

- **package_parts** – 패키지에 포함된 OPC 파트 경로 목록을 확인합니다.
- **package_get_text** – 지정한 파트를 텍스트로 읽어옵니다(필요 시 인코딩 지정).
- **package_set_text** – 텍스트 파트를 교체합니다(`dryRun` 해제 및 쓰기 권한 필요).
- **package_get_xml** – 지정한 파트를 XML 문자열로 반환합니다.
- **package_set_xml** – XML 파트를 교체합니다(`dryRun` 해제 및 쓰기 권한 필요).

예시 시나리오(읽기 전용): 스타일 정의가 파트 어디에 배치되었는지 확인하고 싶다면 다음과 같이 호출합니다.

1. `package_parts` 도구에 `{"path": "sample.hwpx"}`를 전달해 `Contents/Styles.xml`과 같은 대상 파트 이름을 찾습니다.
2. 이어서 `package_get_xml` 도구에 `{"path": "sample.hwpx", "partName": "Contents/Styles.xml"}`을 전달해 해당 파트의 원본 XML을 읽기 전용으로 검토합니다.

이 조합은 문서 구조를 직접 손대지 않고도 고급 진단 작업을 수행해야 할 때 유용합니다.

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
