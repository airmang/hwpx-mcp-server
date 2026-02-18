# 한글 MCP (HWPX) 서버 - 한글 자동화 HWPX 문서 자동 생성·편집·검증mcp #

이 프로젝트는 **한글 MCP(HWPX) 서버**로, HWPX 문서를 한글 워드프로세서 없이 직접 열고 자동화할 수 있도록 설계되었습니다.  
Gemini CLI, Claude Desktop과 같은 MCP 클라이언트에 연결하여 문서 생성·편집·탐색 기능을 제공합니다.

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
  * **🧩 HWP 호환 + 자동 변환**: `.hwp` 바이너리 문서를 읽기 전용으로 조회/검색할 수 있고, `convert_hwp_to_hwpx` 도구로 `.hwpx`로 자동 변환해 편집 파이프라인에 바로 연결할 수 있습니다.
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


### 4. 원격 배포용 Streamable HTTP 실행 예시

원격 MCP 클라이언트(예: 사내 게이트웨이, AI 오케스트레이터)에서 접근해야 한다면 transport를 `streamable-http`로 전환해 실행할 수 있습니다.

```bash
uvx hwpx-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080
```

권장 운영 방식은 아래와 같습니다.

- **포트 바인딩**: 컨테이너/VM에서는 `--host 0.0.0.0`으로 바인딩하고, 외부에는 방화벽 또는 보안 그룹으로 허용 대역만 열어두세요.
- **리버스 프록시**: Nginx/Traefik/Caddy 앞단에 두고 TLS 종료(HTTPS), 인증, 요청 제한(rate limit)을 프록시 레이어에서 적용하는 구성을 권장합니다.
- **프록시 타임아웃**: Streamable HTTP는 SSE 응답을 사용하므로 `proxy_read_timeout`(Nginx 기준) 같은 장기 연결 타임아웃을 충분히 크게 설정해야 합니다.
- **헬스체크 분리**: 프록시/오케스트레이터의 헬스체크 엔드포인트는 MCP 경로와 분리해, 장기 연결 트래픽과 충돌하지 않도록 구성하세요.

## ⚙️ 환경 변수

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `HWPX_MCP_PAGING_PARA_LIMIT` | 페이지네이션 도구가 반환할 최대 문단 수 | `200` |
| `HWPX_MCP_AUTOBACKUP` | `1`이면 저장 전 `<file>.bak` 백업 생성 | `0` |
| `LOG_LEVEL` | stderr에 JSONL 형식으로 출력할 로그 레벨 | `INFO` |
| `HWPX_MCP_HARDENING` | `1`로 설정 시 하드닝 편집 파이프라인과 검색/컨텍스트 도구 활성화 | `0` |
| `HWPX_MCP_TOOLSET` | 노출할 도구 카테고리 CSV(`core`,`tables`,`styles`,`pipeline`,`debug`) | 미설정 시 전체 도구 |

> ℹ️ `read_text` 도구는 기본적으로 최대 200개의 문단을 반환합니다. 더 큰 덤프가 필요하면 도구 호출 시 `limit` 인수를 직접 지정하거나 `HWPX_MCP_PAGING_PARA_LIMIT` 환경 변수를 확장하세요. 이는 Microsoft Office Word에서 필요한 범위만 순차적으로 읽는 워크플로와 동일합니다.

> 🔐 `HWPX_MCP_HARDENING=1`로 실행하면 새 편집 파이프라인(`plan → preview → apply`)과 검색/컨텍스트 도구가 함께 노출됩니다. 값이 `0` 또는 미설정이면 기존 도구 표면만 유지됩니다.

> 🧰 `HWPX_MCP_TOOLSET`을 사용하면 도구 노출 범위를 카테고리 단위로 줄일 수 있습니다. 예: `HWPX_MCP_TOOLSET=core,tables`.
>
> ✅ 권장 프로파일은 **`core` 우선**입니다. 프로덕션에서는 `core`로 시작하고 필요 시 `tables`, `styles`, `pipeline`, `debug`를 점진적으로 추가하세요.
>
> - 최소 표면: `HWPX_MCP_TOOLSET=core`
> - 표 편집 중심: `HWPX_MCP_TOOLSET=core,tables`
> - 스타일 편집 포함: `HWPX_MCP_TOOLSET=core,styles`
> - 하드닝 파이프라인 포함: `HWPX_MCP_TOOLSET=core,pipeline` + `HWPX_MCP_HARDENING=1`

### 📁 문서 로케이터(Document Locator)

모든 도구의 입력은 이제 문서를 가리키는 **discriminated union** 로케이터를 사용합니다. 기본값은 기존과 동일하게 상위 수준의 `path` 필드이며, 별도 선언 없이도 계속 사용할 수 있습니다. 필요에 따라 명시적으로 `type`을 지정해 HTTP 백엔드나 사전 등록된 핸들을 사용할 수 있습니다.

- **로컬 파일 (기존 스키마와 동일)**

  ```jsonc
  {
    "name": "open_info",
    "arguments": {
      "path": "sample.hwpx"
    }
  }
  ```

- **HTTP 백엔드와 연계** — 서버를 HTTP 스토리지 모드로 실행한 경우, 원격 경로를 `uri` 필드로 지정하고 필요 시 `backend` 힌트를 제공할 수 있습니다.

  ```jsonc
  {
    "name": "open_info",
    "arguments": {
      "type": "uri",
      "uri": "reports/weekly.hwpx",
      "backend": "http"
    }
  }
  ```

- **사전 등록된 핸들 사용** — 이제 일반 도구(`open_info`, `read_text`, `set_table_cell_text` 등)도 `handleId`를 공식 지원합니다.

  ```jsonc
  {
    "name": "open_info",
    "arguments": {
      "type": "handle",
      "handleId": "h_0123456789abcdef"
    }
  }
  ```

- **멀티 문서 교차 작업 예시** — 문서 A의 표를 문서 B로 복사하는 흐름입니다.

  ```jsonc
  {
    "name": "copy_table_between_documents",
    "arguments": {
      "sourceDocument": {"type": "handle", "handleId": "h_source"},
      "sourceTableIndex": 0,
      "targetDocument": {"type": "handle", "handleId": "h_target"},
      "targetSectionIndex": 0,
      "autoFit": true
    }
  }
  ```

각 변형은 필요에 따라 `backend` 필드를 추가로 가질 수 있으며, 명시적으로 `document` 객체를 중첩하여 전달하는 것도 허용됩니다. 스키마는 Sanitizer를 거쳐 `$ref` 없이 평탄화된 형태로 제공됩니다.

### 🔐 하드닝 편집 파이프라인 (옵션)

하드닝 플래그를 켜면 모든 편집 요청이 **계획(Plan) → 검토(Preview) → 적용(Apply)**의 3단계를 거치도록 설계된 신규 도구가 함께 노출됩니다. 하드닝 플래그는 저렴한 LLM 모델의 요청에도 성공적인 작업을 수행하기 위해서 도입한 테스트중인 기능입니다. 

1. **`hwpx.plan_edit`**: 변경 대상과 의도한 작업을 설명하면 서버가 안정적인 `planId`와 예상 작업 요약을 제공합니다.
2. **`hwpx.preview_edit`**: 발급된 `planId`로 미리보기를 요청하면 실제 diff, 모호성 경고, 안전 점수 등을 포함한 리뷰 데이터를 반환합니다. 이 단계가 기록되지 않으면 적용 단계로 넘어갈 수 없습니다.
3. **`hwpx.apply_edit`**: `preview`를 거친 동일한 `planId`에 `confirm: true`를 명시해야 실제 문서 변경이 이루어집니다. `idempotencyKey`를 지정하면 동일 요청이 반복되더라도 안전하게 무시됩니다.

각 단계는 표준 `ServerResponse` 래퍼를 사용하며, 오류 발생 시 `PREVIEW_REQUIRED`, `AMBIGUOUS_TARGET`, `UNSAFE_WILDCARD`, `IDEMPOTENT_REPLAY` 등의 코드와 함께 후속 행동 예시(`next_actions`)를 반환합니다. 모든 스키마는 draft-07 호환 Sanitizer를 통해 `$ref`, `anyOf` 없이 평탄화되어 노출됩니다.

또한 하드닝 모드에서는 지원 도구가 확장됩니다.

- **`hwpx.search`**: 정규식 또는 키워드 기반으로 문서 전반을 검색하여 노출 가능한 문맥만 포함한 일치 결과를 제공합니다.
- **`hwpx.get_context`**: 특정 문단 주변의 제한된 창(window)만 추출하여 프라이버시를 유지한 채 리뷰에 활용할 수 있습니다.


## 🧠 Prompt 템플릿 (prompts/list, prompts/get)

서버는 바로 호출 가능한 버전드 프롬프트 ID를 제공합니다. 버전 호환성 관리를 위해 `prompt_id@vN` 네이밍 규칙을 사용합니다.

- `summary@v1`: 문서 전체 텍스트 요약
- `table_to_csv@v1`: 표 추출 후 CSV 변환
- `document_lint@v1`: 텍스트 규칙 린트

각 프롬프트는 설명에 **도구 이름/인자명 ↔ 템플릿 변수 매핑**, 인자 스키마, 예시 입력/출력을 포함합니다.

### 예시 1) prompts/get으로 요약 프롬프트 요청

```json
{
  "method": "prompts/get",
  "params": {
    "name": "summary@v1",
    "arguments": {
      "path": "sample.hwpx",
      "summaryStyle": "임원 보고",
      "maxSentences": "4"
    }
  }
}
```

### 예시 2) prompts/get으로 표 CSV 프롬프트 요청

```json
{
  "method": "prompts/get",
  "params": {
    "name": "table_to_csv@v1",
    "arguments": {
      "path": "sample.hwpx",
      "tableIndex": "0",
      "delimiter": ",",
      "headerPolicy": "첫 행 헤더 유지"
    }
  }
}
```

### 예시 3) prompts/get으로 문서 린트 프롬프트 요청

```json
{
  "method": "prompts/get",
  "params": {
    "name": "document_lint@v1",
    "arguments": {
      "path": "sample.hwpx",
      "maxLineLen": "100",
      "forbidPatterns": "\"TODO\", \"TBD\""
    }
  }
}
```

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
      - `analyze_template_structure`: 양식 문서를 헤더/본문/푸터(휴리스틱)로 요약하고 플레이스홀더 후보를 탐지
      - `find`, `find_runs_by_style`: 텍스트 검색 및 스타일 기반 검색
      - `hwpx.search` *(플래그 활성 시)*: 정규식/키워드 검색과 안정적인 노드 식별자 반환
      - `hwpx.get_context` *(플래그 활성 시)*: 문단 전후 문맥만 제한적으로 조회
  - **문서 편집**
      - `replace_text_in_runs`: 스타일을 보존하며 텍스트 치환 (기본적으로 문서를 저장하므로,
        미리보기만 원하면 `dryRun: true`를 지정하세요.)
      - `add_paragraph`, `insert_paragraphs_bulk`: 문단 추가
      - `add_table`, `get_table_cell_map`, `set_table_cell_text`, `replace_table_region`, `split_table_cell`: 표 생성·편집 및 병합 해제
      - `add_shape`, `add_control`: 개체 추가
      - `add_memo`, `attach_memo_field`, `add_memo_with_anchor`, `remove_memo`: 메모 관리
      - `hwpx.plan_edit`, `hwpx.preview_edit`, `hwpx.apply_edit` *(플래그 활성 시)*: 검증된 3단계 편집 파이프라인
  - **스타일링**
      - `ensure_run_style`, `list_styles_and_bullets`: 스타일 및 글머리표 목록 확인/생성
      - `apply_style_to_text_ranges`, `apply_style_to_paragraphs`: 단어/문단 단위 스타일 적용
  - **파일 관리**
      - `save`, `save_as`: 문서 저장
      - `fill_template`: 템플릿 사본 생성 + 다중 치환을 1회 호출로 수행
      - `make_blank`: 새 빈 문서 생성
      - `convert_hwp_to_hwpx`: HWP 바이너리를 HWPX로 변환(기본 텍스트/표 중심)
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

`set_table_cell_text`와 `replace_table_region`은 선택적인 `logical`/`splitMerged` 플래그를 지원합니다. `logical: true`로 지정하면 방금 확인한 논리 좌표계를 그대로 사용할 수 있고, `splitMerged: true`를 함께 전달하면 쓰기 전에 자동으로 해당 병합 영역을 분할합니다. 긴 텍스트를 채울 때는 `autoFit: true`를 추가로 지정하면 각 열 너비가 셀 내용 길이에 맞춰 다시 계산되어 표 전체 폭(`hp:sz`)과 셀 크기(`hp:cellSz`)가 함께 업데이트됩니다. 병합을 직접 해제해야 할 때는 `split_table_cell` 도구가 원래 범위를 알려주면서 셀을 분할합니다.

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
    "autoFit": true,
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


## 🔁 HWP → HWPX 자동 변환

`convert_hwp_to_hwpx` 도구는 내부적으로 `hwp5proc xml` 결과를 매핑해 `.hwp` 문서를 `.hwpx`로 변환합니다.

- 입력: `source`(필수, `.hwp` 경로), `output`(선택, 미지정 시 같은 경로에 `.hwpx`)
- 출력: 변환 성공 여부, 변환된 문단/표 개수, 변환 제외 요소 목록, 경고 메시지

예시:

```json
{
  "name": "convert_hwp_to_hwpx",
  "arguments": {
    "source": "legacy/report.hwp",
    "output": "legacy/report.hwpx"
  }
}
```

### 지원 범위

- **P0**: 일반 문단 텍스트
- **P1(부분 지원)**: 표의 행/열과 셀 텍스트
- **P2/P3**: OLE, 각주/미주, 변경 추적, 양식 컨트롤 등은 경고와 함께 스킵될 수 있음

### 알려진 제한사항

- 변환 목표는 100% 시각 재현이 아니라 **텍스트 보존 + 기본 구조 이관**입니다.
- 복잡한 서식(세밀한 스타일, 고급 개체, 일부 병합 표)은 결과 문서에서 수동 보정이 필요할 수 있습니다.
- `hwp5proc` 실행 환경이 없으면 변환 도구는 실패하며 설치 안내 오류를 반환합니다.

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
  * JSON 스키마는 내부 `schema.builder` 경로를 통해 draft-07 호환 Sanitizer를 거친 후 노출되므로 `$ref`/`anyOf`가 제거된 평탄한 구조를 기대할 수 있습니다.

### 🔒 서버 하드닝 & JSON 스키마 (draft-07) — 선택 사용

- `HWPX_MCP_HARDENING=1`을 설정하면 plan/preview/apply 파이프라인, `hwpx.search`, `hwpx.get_context`가 활성화됩니다.
- 플래그를 끄면 (`0` 또는 미설정) 기존 도구만 유지하면서도 강화된 스키마 Sanitizer는 계속 적용됩니다.
- `pytest -q`를 실행하면 스키마 회귀, 파이프라인 게이트, 멱등성 검증 테스트가 함께 수행되어 배포 전 안전성을 확인할 수 있습니다.

## 📜 라이선스

이 프로젝트는 [MIT 라이선스](https://www.google.com/search?q=LICENSE)로 배포됩니다. 자세한 내용은 라이선스 파일을 확인하세요.

## 이메일

광교고등학교 교사 고규현 : kokyuhyun@hotmail.com


## 🧩 양식(템플릿) 문서 작업

### 1) 구조 파악: `analyze_template_structure`

양식 문서를 열자마자 수정 가능/불가 영역과 플레이스홀더 후보를 파악하려면 아래 도구를 사용합니다.

```json
{
  "name": "analyze_template_structure",
  "arguments": {
    "path": "sample.hwpx",
    "placeholderPatterns": ["\\{\\{[^{}]+\\}\\}", "본문 영역"],
    "lockKeywords": ["학교장", "직인", "로고"]
  }
}
```

응답에는 `summary`(문단 수/플레이스홀더 수), `regions`(header/body/footer), `placeholders`(토큰/문단 인덱스/수정 가능 여부)가 포함됩니다.

### 2) 한 번에 채우기: `fill_template`

기존의 `save_as -> find -> replace...` 다단계 대신, `fill_template` 하나로 템플릿 복사와 다중 치환을 처리할 수 있습니다.

```json
{
  "name": "fill_template",
  "arguments": {
    "source": "forms/notice_template.hwpx",
    "output": "out/notice_2026.hwpx",
    "replacements": {
      "본문 영역": "실제 안내문 본문",
      "제2025년": "제2026년",
      "2025. 1. 1.": "2026. 3. 5."
    }
  }
}
```

## 🗂️ 문서 Handle Registry 도구 및 세션 수명 정책

### 신규 도구

- `open_document_handle`: 로케이터(path/uri/handleId)를 등록하고 표준 `handle` 객체를 반환
- `list_open_documents`: 현재 프로세스에 등록된 handle 목록과 세션 정책(`sessionPolicy`) 반환
- `close_document_handle`: 특정 handle을 레지스트리에서 해제
- `copy_table_between_documents`: 문서 A의 표를 읽어 문서 B에 복사(교차 문서 작업)

### 세션 수명 정책

- **레지스트리 범위**: 프로세스 단위(`registryScope=process`)
- **요청 처리 단위**: 요청 단위(`requestScope=request`) — 각 MCP 요청은 독립 실행되지만, handle 레지스트리는 프로세스 내에서 유지
- **캐시/레지스트리 해제 조건**
  1. `close_document_handle` 호출 시 해당 handle 즉시 해제
  2. 서버 프로세스 종료/재시작 시 전체 레지스트리 해제

## 📚 Resources 사용 예시 및 URI 계약

MCP Resources를 통해 **등록된 handle 기반 읽기 전용 조회**를 사용할 수 있습니다. 서버는 도구 호출 과정에서 실제 문서 경로를 해석할 때 handle을 자동 등록하며, `resources/list`에서는 현재 등록된 handle만 노출합니다.

### URI 스킴

- `hwpx://documents/{handle}/metadata`: 문서 메타데이터/섹션·문단·헤더 개수
- `hwpx://documents/{handle}/paragraphs`: 문서 전체 문단 텍스트
- `hwpx://documents/{handle}/tables`: 표 인덱스/행/열 요약

`{handle}`은 `h_<16자리해시>` 형태의 불투명 식별자이며, 등록되지 않은 handle은 표준화된 `HANDLE_NOT_FOUND` 에러로 반환됩니다.

### 호출 흐름 예시

1. 먼저 `open_document_handle`(또는 `open_info`/`read_text`)를 호출해 문서를 열면 해당 문서 handle이 등록됩니다.
2. `resources/list`를 호출하면 해당 handle의 `metadata/paragraphs/tables` URI가 나타납니다.
3. `resources/read`로 원하는 URI를 읽어 JSON(`application/json`) 본문을 받습니다.

예시 URI:

```text
hwpx://documents/h_0123456789abcdef/metadata
hwpx://documents/h_0123456789abcdef/paragraphs
hwpx://documents/h_0123456789abcdef/tables
```
