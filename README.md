# HWPX MCP Server 한글 자동화 mcp #

[](https://www.google.com/search?q=https://pypi.org/project/hwpx-mcp-server/)
[](https://opensource.org/licenses/MIT)
[](https://www.google.com/search?q=https://github.com/your-repo/hwpx-mcp-server/actions/workflows/ci.yml)

**순수 파이썬으로 HWPX 문서를 자유롭게 다루는 가장 강력한 방법.**

`hwpx-mcp-server`는 [Model Context Protocol](https://github.com/modelcontextprotocol/specification) 표준을 따르는 서버로, 강력한 [`python-hwpx`](https://www.google.com/search?q=%5Bhttps://github.com/airmang/python-hwpx%5D\(https://github.com/airmang/python-hwpx\)) 라이브러리를 기반으로 합니다. Gemini, Claude와 같은 최신 AI 클라이언트와 완벽하게 연동하여 한글 워드 프로세서 로컬 HWPX 문서를 열람, 검색, 편집, 저장하는 풍부한 기능을 제공합니다.

-----

## ✨ 주요 기능

  * **✅ 표준 MCP 서버 구현**: 공식 `mcp` SDK를 사용하여 안정적인 표준 입/출력 기반 서버를 제공합니다.
  * **📂 제로 설정**: 별도 설정 없이 현재 작업 디렉터리를 기준으로 즉시 경로를 처리합니다.
  * **📄 강력한 문서 편집**: 텍스트 추출, 페이지네이션부터 스타일, 표, 메모, 개체 편집까지 모두 가능합니다.
  * **🛡️ 안전한 저장**: 자동 백업(`*.bak`) 옵션으로 예기치 않은 데이터 손실을 방지합니다.
  * **🚀 즉시 실행**: `uv`만 있으면 `uvx hwpx-mcp-server` 한 줄로 바로 시작할 수 있습니다.

## 🚀 빠른 시작

### 1\. `uv` 설치

가장 먼저 파이썬 패키지 설치 도구인 `uv`를 설치하세요.
[👉 Astral uv 설치 가이드](https://docs.astral.sh/uv/getting-started/installation/)


### 2\. MCP 클라이언트 설정

사용 중인 MCP 클라이언트 설정에 아래와 같이 서버 정보를 추가하세요.

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
### 3\. 서버 실행 (로컬 환경에서 사용할 경우)

터미널에서 아래 명령어를 실행하면 서버가 바로 시작됩니다.

```bash
uvx hwpx-mcp-server
```

> `uvx` 명령은 첫 실행 시 필요한 종속성을 자동으로 설치하며, 반드시 `python-hwpx 1.9` 이상의 버전이 준비되어야 합니다.


> 서버는 실행된 현재 디렉터리를 기준으로 경로를 해석하므로, 별도의 작업 디렉터리 설정 없이 바로 사용할 수 있습니다.

## ⚙️ 환경 변수

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `HWPX_MCP_PAGING_PARA_LIMIT` | 페이지네이션 도구가 반환할 최대 문단 수 | `200` |
| `HWPX_MCP_AUTOBACKUP` | `1`이면 저장 전 `<file>.bak` 백업 생성 | `0` |
| `LOG_LEVEL` | stderr에 JSONL 형식으로 출력할 로그 레벨 | `INFO` |

> ℹ️ `read_text` 도구는 기본적으로 최대 200개의 문단을 반환합니다. 더 큰 덤프가 필요하면 도구 호출 시 `limit` 인수를 직접 지정하거나 `HWPX_MCP_PAGING_PARA_LIMIT` 환경 변수를 확장하세요. 이는 Microsoft Office Word에서 필요한 범위만 순차적으로 읽는 워크플로와 동일합니다.

## 🛠️ 제공 도구

다양한 문서 편집 및 관리 도구를 제공합니다. 각 도구의 상세한 입출력 형식은 `ListTools` 응답에 포함된 JSON 스키마를 통해 확인할 수 있습니다.

\<details\>
\<summary\>\<b\>전체 도구 목록 펼쳐보기...\</b\>\</summary\>

  - **문서 정보 및 탐색**
      - `open_info`: 문서 메타데이터 및 단락·헤더 개수 요약
      - `list_sections`, `list_headers`: 섹션/헤더 구조 탐색
      - `list_master_pages_histories_versions`: 마스터 페이지/히스토리/버전 요약
  - **콘텐츠 추출 및 검색**
      - `read_text`, `read_paragraphs`, `text_extract_report`: 페이지네이션, 선택 문단, 주석 포함 텍스트 추출
      - `find`, `find_runs_by_style`: 텍스트 검색 및 스타일 기반 검색
  - **문서 편집**
      - `replace_text_in_runs`: 스타일을 보존하며 텍스트 치환
      - `add_paragraph`, `insert_paragraphs_bulk`: 문단 추가
      - `add_table`, `get_table_cell_map`, `set_table_cell_text`, `replace_table_region`, `split_table_cell`: 표 생성·편집 및 병합 해제
      - `add_shape`, `add_control`: 개체 추가
      - `add_memo`, `attach_memo_field`, `add_memo_with_anchor`, `remove_memo`: 메모 관리
  - **스타일링**
      - `ensure_run_style`, `list_styles_and_bullets`: 스타일 및 글머리표 목록 확인/생성
      - `apply_style_to_text_ranges`, `apply_style_to_paragraphs`: 단어/문단 단위 스타일 적용
  - **파일 관리**
      - `save`, `save_as`: 문서 저장
      - `make_blank`: 새 빈 문서 생성
  - **구조 검증 및 고급 검색**
      - `object_find_by_tag`, `object_find_by_attr`: XML 요소 검색
      - `validate_structure`, `lint_text_conventions`: 문서 구조 검증 및 텍스트 린트

\</details\>

### 🎯 필요한 문단만 빠르게 읽기

대용량 문서를 순차적으로 확인할 때는 `read_text` 페이지네이션이 편리하지만, 특정 문단만 바로 확인하고 싶을 때는 `read_paragraphs` 도구가 더 적합합니다. `paragraphIndexes` 배열에 원하는 문단 번호만 전달하면, 요청한 문단만 순서대로 반환합니다. 각 항목에는 원본 문단 인덱스(`paragraphIndex`)와 추출된 텍스트가 함께 포함되므로, 이전 호출에서 기억한 문단을 정확히 다시 불러올 수 있습니다.

```jsonc
{
  "name": "read_paragraphs",
  "arguments": {
    "path": "sample.hwpx",
    "paragraphIndexes": [1, 4, 9],
    "withHighlights": false,
    "withFootnotes": false
  }
}
```

선택된 문단만 처리하므로 큰 문서를 반복해서 탐색할 때 불필요한 텍스트 복사를 줄이고, 하이라이트/각주 옵션도 `read_text`와 동일하게 활용할 수 있습니다. 존재하지 않는 인덱스를 요청하면 오류가 발생하므로, 이전에 받은 문단 개수 정보를 활용해 안전하게 요청하세요.

### 🔍 검색 문맥 길이 조절

`find` 도구는 각 일치 항목 주변의 전후 80자를 기본으로 잘라 `context` 스니펫을 반환하며, 잘린 경우 문자열 앞뒤에 `...`이 붙습니다. 더 넓은 범위가 필요하면 `contextRadius` 인수를 사용해 유지할 문자 수를 조정할 수 있습니다.

```jsonc
{
  "name": "find",
  "arguments": {
    "path": "sample.hwpx",
    "query": "HWPX",
    "contextRadius": 200
  }
}
```

`contextRadius` 값은 일치 구간 앞뒤 각각에 포함할 문자 수를 의미합니다.

### 📐 표 편집 고급 옵션

`get_table_cell_map` 도구를 사용하면 표의 전체 격자를 그대로 직렬화하여 각 위치가 어느 앵커 셀(`anchor`)에 속하는지, 병합 범위(`rowSpan`, `colSpan`)는 얼마인지 한눈에 확인할 수 있습니다. 응답은 항상 행×열 전체를 채우며, 각 위치에 대해 `row`/`column` 좌표와 병합된 앵커 셀의 텍스트를 알려 줍니다.

```jsonc
{
  "name": "get_table_cell_map",
  "arguments": {"path": "sample.hwpx", "tableIndex": 0},
  "result": {
    "rowCount": 3,
    "columnCount": 3,
    "grid": [
      [
        {"row": 0, "column": 0, "anchor": {"row": 0, "column": 0}, "rowSpan": 2, "colSpan": 2, "text": "제목"},
        {"row": 0, "column": 1, "anchor": {"row": 0, "column": 0}, "rowSpan": 2, "colSpan": 2, "text": "제목"},
        {"row": 0, "column": 2, "anchor": {"row": 0, "column": 2}, "rowSpan": 3, "colSpan": 1, "text": "요약"}
      ],
      [
        {"row": 1, "column": 0, "anchor": {"row": 0, "column": 0}, "rowSpan": 2, "colSpan": 2, "text": "제목"},
        {"row": 1, "column": 1, "anchor": {"row": 0, "column": 0}, "rowSpan": 2, "colSpan": 2, "text": "제목"},
        {"row": 1, "column": 2, "anchor": {"row": 0, "column": 2}, "rowSpan": 3, "colSpan": 1, "text": "요약"}
      ],
      "... 생략 ..."
    ]
  }
}
```

`set_table_cell_text`와 `replace_table_region`은 선택적인 `logical`/`splitMerged` 플래그를 지원합니다. `logical: true`로 지정하면 방금 확인한 논리 좌표계를 그대로 사용할 수 있고, `splitMerged: true`를 함께 전달하면 쓰기 전에 자동으로 해당 병합 영역을 분할합니다. 병합을 직접 해제해야 할 때는 `split_table_cell` 도구가 원래 범위를 알려주면서 셀을 분할합니다.

```jsonc
{
  "name": "set_table_cell_text",
  "arguments": {
    "path": "sample.hwpx",
    "tableIndex": 0,
    "row": 1,
    "col": 1,
    "text": "논리 좌표 편집",
    "logical": true,
    "splitMerged": true,
    "dryRun": false
  }
}
```

위 예시는 2×2로 병합된 셀에 논리 좌표 `(1, 1)`을 지정하여 자동 분할 후 텍스트를 기록합니다. 분할 여부와 원래 범위를 확인하려면 `split_table_cell`을 호출하세요.

```jsonc
{
  "name": "split_table_cell",
  "arguments": {"path": "sample.hwpx", "tableIndex": 0, "row": 0, "col": 0},
  "result": {"startRow": 0, "startCol": 0, "rowSpan": 2, "colSpan": 2}
}
```

응답의 `rowSpan`/`colSpan` 값은 분할되기 전 병합 범위를 알려주므로, 프런트엔드 클라이언트가 UI 상태를 즉시 갱신할 수 있습니다.

## ☢️ 고급 기능: OPC 패키지 내부 살펴보기

> **⚠️ 경고:** 아래 도구들은 HWPX 문서의 내부 OPC 파트를 그대로 노출합니다. 구조를 잘못 해석하면 문서를 오해할 수 있으니, 스키마와 관계를 충분히 이해한 상태에서 활용하세요. 현재 MCP 서버는 의도치 않은 손상을 막기 위해 **읽기 전용 도구만** 제공합니다.

  * `package_parts`: 패키지에 포함된 모든 OPC 파트의 경로 목록을 확인합니다.
  * `package_get_text`: 지정한 파트를 텍스트로 읽어옵니다 (인코딩 지정 가능).
  * `package_get_xml`: 지정한 파트를 XML 문자열로 반환합니다.

#### 시나리오 예시

스타일 정의 XML 파일(`Styles.xml`)의 내용을 확인하고 싶다면:

1.  `package_parts` 도구에 `{"path": "sample.hwpx"}`를 전달하여 `Contents/Styles.xml`과 같은 파트 이름을 찾습니다.
2.  `package_get_xml` 도구에 `{"path": "sample.hwpx", "partName": "Contents/Styles.xml"}`을 전달하여 해당 파트의 원본 XML을 안전하게 검토합니다.

## 🧪 테스트

핵심 기능부터 모든 MCP 도구의 실제 호출까지 검증하는 엔드투엔드 테스트 스위트가 포함되어 있습니다.

```bash
# 1. 테스트 의존성 설치
python -m pip install -e .[test]

# 2. 테스트 실행
python -m pytest
```

`tests/test_mcp_end_to_end.py`는 서버가 노출하는 대부분의 도구를 실제로 호출하여 텍스트, 표, 메모 편집, OPC 패키지 읽기, 자동 백업 생성 등 핵심 동작을 완벽하게 검증합니다.

## 🧑‍💻 개발 참고

  * 이 서버는 `python-hwpx>=1.9`, `mcp`, `anyio`, `pydantic` 등 순수 파이썬 라이브러리로만 구성됩니다.
  * 모든 도구 핸들러는 `HwpxOps`의 경로 헬퍼와 `HwpxDocument` API를 통해 문서를 안전하게 조작합니다.
  * 파괴적 작업(수정/저장)에는 `dryRun` 플래그를 우선 제공하며, 자동 백업 옵션이 활성화되어 있으면 `.bak` 파일을 생성하여 안정성을 높입니다.

## 📜 라이선스

이 프로젝트는 [MIT 라이선스](https://www.google.com/search?q=LICENSE)로 배포됩니다. 자세한 내용은 라이선스 파일을 확인하세요.

## 이메일

광교고등학교 교사 고규현 : kokyuhyun@hotmail.com
