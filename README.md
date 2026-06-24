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
    <a href="https://github.com/airmang/hwpx-mcp-server/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License"></a>
    <a href="https://github.com/airmang/hwpx-mcp-server/actions"><img src="https://img.shields.io/github/actions/workflow/status/airmang/hwpx-mcp-server/test.yml?style=flat-square&label=tests" alt="Tests"></a>
  </p>
</p>

---

## 🧩 HWPX Stack (3종)

| 계층 | 레포 | 역할 |
|---|---|---|
| 📦 라이브러리 | [`python-hwpx`](https://github.com/airmang/python-hwpx) | 순수 파이썬 HWPX 파싱·편집·생성 코어 |
| 🔌 MCP 서버 | **[`hwpx-mcp-server`](https://github.com/airmang/hwpx-mcp-server)** | MCP 클라이언트(Claude Desktop, VS Code 등)에서 HWPX 조작 |
| 🎯 에이전트 스킬 | [`hwpx-skill`](https://github.com/airmang/hwpx-skill) | 에이전트가 HWPX를 바로 쓰게 해주는 공식 온보딩 스킬 |

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

## Claude Desktop 5분 연결

아래 설정 하나만 넣으면 Claude Desktop에서 `hwpx` MCP 서버를 바로 잡는다.

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

> Screenshot placeholder: Claude Desktop 설정 화면에서 `hwpx` 서버가 활성화된 장면을 여기에 넣을 예정.

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
- `python-hwpx >= 2.11.1`

현재 저장소 기준 검증 버전은 `python-hwpx 2.11.1`입니다 (2026-06-12 검증).
최소 지원 버전은 `python-hwpx >= 2.11.1`입니다.

### 2. MCP 클라이언트 설정

#### HWPX plugin companion launcher

The `hwpx-plugins` repository builds per-host bundles whose MCP launcher
(`plugins/<host>/hwpx-plugin/scripts/hwpx-mcp-server`) can run this server. In local development,
set `HWPX_MCP_SERVER_REPO=/absolute/path/to/hwpx-mcp-server` and
`PYTHON_HWPX_REPO=/absolute/path/to/python-hwpx` when the repositories are not under a common
parent. The launcher otherwise discovers them by walking up from the bundle directory and uses
`uv run --project "$HWPX_MCP_SERVER_REPO" --with-editable "$PYTHON_HWPX_REPO" --with-editable "$HWPX_MCP_SERVER_REPO" hwpx-mcp-server`.

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

처음부터 모든 도구를 외울 필요는 없다. 보통은 아래 네 흐름 중 하나로 시작하면 된다.

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

### 4. 한컴에서 열리지 않는 HWPX를 복구할 때

1. 원본을 직접 덮어쓰지 않고 `repair_hwpx(source_filename, output_filename)`로 복구 복사본을 만든다.
2. 일반 ZIP open이 실패하거나 central directory 손상이 의심되면 `recover=true`를 지정한다.
3. 반환값의 `crcOk`, `validatePackage.ok`, `reordered`, `recovered`를 확인한다.
4. 최종 handoff 전에는 가능하면 Hancom Office HWP 또는 viewer로 실제 열람한다.

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

### 품질 게이트 계약 (no raw XML)

모델은 **operation/plan만** 보내고 raw XML을 직접 편집하지 않는다. `raw_xml_replace`,
`arbitrary_xpath_mutation` 같은 원시 XML 표면은 이 서버의 public 도구로 노출되지 않는다.
모든 쓰기는 python-hwpx의 **단일 `SavePipeline` 게이트**(VisualComplete)를 통과하며, 우회
경로가 없다(무결성·XML·OPC/ID·열림안전·FormFit·레이아웃·시각 오라클 → 하나의 리포트).

- **모든 쓰기 응답에 `visualComplete` 블록**이 실린다: `ok`, `status`(verified/unverified/
  failed), `errorCodes`, `warnings`, `suggestedRetry`.
- `quality` 블록으로 게이트를 올릴 수 있다(생략 시 transparent = 열림안전만). 예:
  `apply_edits(..., quality="strict")` 또는
  `quality={"mode":"strict","overflowPolicy":"fail","layoutLint":"strict"}`.
- 게이트가 실패하면 저장이 **보류**되고(`ok=false`), 모델은 구조화된 오류 코드
  (`FIELD_OVERFLOW`, `STALE_LINESEG_DETECTED`, `VISUAL_COMPLETE_FAILED`, …)와 `suggestedRetry`로
  재시도할 수 있다.
- **capability handshake**가 core(python-hwpx)/mcp/plugin 버전 + 해시를 점검하고, skew 시
  쓰기를 **fail-closed**로 차단한다(`mcp_server_health.capability` 참고). 진단/우회는
  `HWPX_MCP_REQUIRE_CAPABILITY=0`.

## 도구 동작 빠른 감각

| 구분 | 대표 도구 | 특징 |
|---|---|---|
| 파일 기반 읽기 전용 | `get_document_info`, `get_document_text`, `get_paragraph_text`, `get_location_text`, `get_paragraphs_text`, `find_text`, `get_table_text`, `get_table_map`, `find_cell_by_label`, `list_styles`, `list_available_documents` | 기존 `.hwpx` 파일을 읽거나 탐색만 한다. 저장하지 않는다. |
| 파일 기반 즉시 저장 생성/편집 | `create_document`, `create_document_from_plan`, `create_proposal_document`, `search_and_replace`, `batch_replace`, `replace_in_paragraph`, `replace_by_anchor`, `add_heading`, `add_paragraph`, `insert_paragraph`, `delete_paragraph`, `add_table`, `fill_by_path`, `set_table_cell_text`, `add_page_break`, `add_memo`, `add_memo_by_anchor`, `remove_memo`, `format_text`, `create_custom_style`, `merge_table_cells`, `split_table_cell`, `format_table` | 호출 결과가 곧 대상 파일 변경이다. 검토용이면 먼저 복사본에서 작업한다. |
| 선언형 생성 검증 | `validate_document_plan`, `analyze_document_plan`, `inspect_document_authoring_quality`, `inspect_operating_plan_quality`, `inspect_document_quality`, `analyze_template_formfit` | agent가 만든 `hwpx.document_plan.v1`, P6 baseline, 또는 생성 결과를 검증한다. `validate_document_plan`, `analyze_document_plan`, `analyze_template_formfit`은 파일을 쓰지 않는다. |
| repair/recover | `repair_hwpx` | 원본을 보존하고 새 output에 mimetype-first repair-repack 또는 Local File Header scan 복구를 수행한다. |
| 복제 / handoff 경계 | `copy_document` | 원본 보호와 reviewable working copy 분리에 쓴다. 현재 FastMCP surface에는 별도 public `save` / `save_as` tool이 없다. |
| payload/url 기반 추출 | `hwpx_to_markdown`, `hwpx_to_html`, `hwpx_extract_json` | 파일명을 직접 수정하지 않는다. HWPX payload 또는 URL을 읽어 변환 결과만 돌려준다. |
| 고급 점검/검증 | `package_parts`, `package_get_xml`, `package_get_text`, `object_find_by_tag`, `object_find_by_attr`, `plan_edit`, `preview_edit`, `apply_edit`, `validate_structure`, `lint_text_conventions` | `HWPX_MCP_ADVANCED=1`일 때만 활성화한다. package/구조 점검용이다. |

## 주요 기능

기본 모드에서 85개 HWPX 도구를 제공하며, 고급 모드(`HWPX_MCP_ADVANCED=1`)에서는 점검·검증용 도구까지 총 95개가 활성화됩니다.

### 위치 계약

`paragraph_index`는 문서 본문 직속 문단의 0-based 인덱스다. 표 안 문단은 이 인덱스에 섞지 않고 `location` 객체로 지정한다. 표 셀 문단 location은 `{"kind":"table_cell_paragraph","table_index":0,"row":0,"col":1,"cell_paragraph_index":0}` 형태이며, `get_table_map`과 `find_text`가 반환한 값을 `get_paragraph_text`, `get_location_text`, `add_memo`, `replace_in_paragraph`에 그대로 넘길 수 있다.

### 📖 읽기 및 탐색

| 도구 | 설명 |
|---|---|
| `get_document_info` | 문서 메타데이터, 섹션, 문단, 표 개수 조회 |
| `get_document_text` | 문서 전체 텍스트 추출 (`max_chars` 지원) |
| `get_document_outline` | 제목과 개요 구조 추출 |
| `get_paragraph_text` | 본문 문단 또는 `location`으로 지정한 표 셀 문단 텍스트 조회 |
| `get_location_text` | `get_table_map`/`find_text`가 반환한 `location`으로 텍스트 조회 |
| `get_paragraphs_text` | 문단 범위 조회 |
| `list_available_documents` | 폴더 안의 `.hwpx` 파일 목록 조회 |
| `get_document_map` | 아웃라인·표 지도·누름틀·앵커를 단일 호출로 반환 (왕복 최소화) |

### 🔎 검색 및 치환

`find_text`는 읽기 전용이고, `search_and_replace` / `batch_replace`는 호출 즉시 저장된다.

| 도구 | 설명 |
|---|---|
| `find_text` | 키워드 검색과 주변 문맥, 재사용 가능한 `location`/`anchor` 반환 |
| `search_and_replace` | 단일 텍스트 치환 |
| `replace_in_paragraph` | 본문/표 셀 문단 하나에서 run 서식을 유지하며 부분 치환 |
| `replace_by_anchor` | `find_text`가 반환한 anchor의 정확한 위치에서 부분 치환 |
| `batch_replace` | 여러 치환 작업 일괄 실행 |

### ✏️ 문서 편집

이 카테고리의 도구는 대체로 대상 파일에 즉시 반영된다. 구조 변경 전에는 `copy_document`로 작업용 사본을 만들고, handoff는 검토가 끝난 복사본 파일 기준으로 잡는 편이 안전하다.

| 도구 | 설명 |
|---|---|
| `add_heading` | 제목(헤딩) 문단 추가 — 템플릿 내장 `개요 N` 스타일 적용(한컴 개요 번호 자동), 텍스트에 `#` 미저장 |
| `add_paragraph` / `insert_paragraph` / `delete_paragraph` | 문단 추가, 삽입, 삭제 |
| `add_page_break` | 페이지 나누기 추가 |
| `add_memo` / `add_memo_by_anchor` / `remove_memo` | 본문 문단 또는 표 셀 문단에 메모 추가, 검색 anchor 기반 메모 추가, 제거 |

### 🔄 트랜잭션 편집과 안전망

쓰기 응답은 기본 `verbosity="compact"`로 `openSafety` 요약·시맨틱 diff·`document_revision`을 포함하고, 실패 시에만 상세 증거를 펼친다.

| 도구 | 설명 |
|---|---|
| `apply_edits` | 연산 목록 원자 적용 — 중간 실패 시 전체 롤백, `dry_run` 미리보기, `expected_revision` 동시성 가드, `idempotency_key` 재시도 안전 |
| `undo_last_edit` | 자동 `.bak` 로테이션 기반 직전 편집 복원 |
| `byte_preserving_patch` | section XML 바이트 splice 문단 패치 — 미수정 영역 바이트 보존 |

동시성: 읽기 도구는 `document_revision`(콘텐츠 해시)을 반환하고, 쓰기 도구는 `expected_revision` 불일치 시 거부한다. 한컴오피스가 파일을 열어둔 정황(잠금)도 경고로 알려준다.

### 🖥️ 렌더 프리뷰 (에이전트의 눈)

| 도구 | 설명 |
|---|---|
| `render_preview` | 페이지 박스·표·여백을 근사한 HTML/페이지별 PNG 생성 — 편집 결과 시각 자기검증용 |

### 📐 페이지·문단 서식 편집 (기존 문서)

입력은 전부 인간 단위(pt/mm/%)다.

| 도구 | 설명 |
|---|---|
| `set_paragraph_format` | 정렬·줄간격(%)·들여쓰기·문단 앞뒤 간격 |
| `set_page_setup` | 용지·여백·방향 |
| `set_header_footer` | 머리말/꼬리말 설정 |
| `set_page_number` | 쪽번호 설정 |
| `set_list_format` | 불릿·번호 목록 형식 변경 |

### 🖼️ 그림

| 도구 | 설명 |
|---|---|
| `insert_picture` | 이미지 삽입 (manifest 자동 등록·검증) |
| `replace_picture` | 기존 그림 자산 안전 치환 |

### 🖊️ 누름틀 양식

| 도구 | 설명 |
|---|---|
| `list_form_fields` | 문서 내 누름틀(클릭히어 필드) 목록 — 이름·안내문·현재값 |
| `fill_form_field` | 서식 보존 누름틀 채움 |

`analyze_form_fill`은 항목별 매칭 신뢰도 등급(label-exact/label-fuzzy/position-guess)을 반환한다.

### 🏛️ 공문서·정합 lint

| 도구 | 설명 |
|---|---|
| `inspect_official_document_style` | 행정업무 규정 기반 lint — 항목기호 위계, "끝." 표시, 붙임 표기, 날짜 표기 + 수정 제안 |
| `inspect_reference_consistency` | 붙임·표/그림 번호 등 문서 내 참조 정합 검사 |

### 🆚 문서 비교 (신구대조)

| 도구 | 설명 |
|---|---|
| `doc_diff` | 두 문서 문단 LCS diff — equal/added/removed/changed 분류 |
| `create_comparison_table_document` | 신구대조표(좌 구/우 신) 문서 생성 |

### 📨 대량 생산·표 계산

| 도구 | 설명 |
|---|---|
| `mail_merge` | 템플릿 + CSV/JSON 데이터 → N부 일괄 생성, 파일명 패턴·zip 출력 |
| `inspect_mail_merge_placeholders` | 템플릿 placeholder·데이터 결측 사전 점검 |
| `table_compute` | 표 합계·평균·소계 행/열 계산 |

### 🪄 서식 이식·템플릿 레지스트리

| 도구 | 설명 |
|---|---|
| `extract_style_profile` | 참조 문서에서 스타일·페이지 설정·표 프로파일 추출 |
| `apply_style_profile_to_plan` | 추출한 프로파일을 document plan에 적용 |
| `compare_style_profiles` | 두 프로파일 비교 |
| `register_template` / `list_templates` / `describe_template` | 사용자 서식 등록·조회 |

### 📷 고급 생성기

| 도구 | 설명 |
|---|---|
| `build_image_grid` | 사진대지(현장 사진 격자 표) 블록 생성 |
| `build_meeting_nameplates` | 회의 명패 표 생성 |
| `build_organization_chart` | 표 기반 조직도 생성 |

### 🧾 선언형 문서 생성

`hwpx.document_plan.v1`은 agent가 OWPML을 직접 만지지 않고 제목, 문단, bullet, 표, 품질 게이트를 선언하는 생성 계약이다.

| 도구 | 설명 |
|---|---|
| `validate_document_plan` | plan을 검증하고 정규화 결과를 반환한다. 파일은 생성하지 않는다. |
| `analyze_document_plan` | 파일을 쓰지 않고 생성 가능 여부, 정규화 plan, 선택한 품질 프로필의 preview를 반환한다. |
| `create_document_from_plan` | plan을 HWPX로 생성하고 즉시 저장한 뒤 reopen/package/schema 검증과 품질 프로필 결과를 반환한다. |
| `inspect_document_authoring_quality` | 생성된 HWPX의 document-plan 품질 근거를 다시 조회한다. `quality_profile="operating_plan"`을 지정할 수 있다. |
| `inspect_operating_plan_quality` | 운영 계획서 제출 후보의 섹션, 일정표, 사업비 근거, 기대 효과, 마감 문구, placeholder 잔여물을 직접 검사한다. |
| `analyze_template_formfit` | P6 기준선 기반 양식 보존 생성 계획을 비파괴 분석한다. |
| `apply_template_formfit` | 분석된 계획을 원본과 다른 destination 복사본에만 적용하고 검증한다. |
| `create_proposal_document` / `inspect_document_quality` | 제안서/기획안 특화 호환 경로 |

`visual_review_required=true`는 구조/파일 기반 검증은 통과했지만 렌더링된
시각 레이아웃은 아직 증명되지 않았다는 뜻이다. 운영 계획서와 template-formfit
결과를 최종 제출 가능(submission-ready) 상태로 주장하려면
`../hwpx-skill/scripts/visual_review.py` 또는 ComputerUse로 열린 문서를 검토해
`hwpx.visual-review.v1` 증거를 남기고 `current.status="observed_pass"`여야
한다. HWPX viewer가 없는 환경에서는 fallback 증거를
`current.status="blocked"`로 기록하고 잔여 위험을 유지한다.

`validate_document_plan` 결과가 `ok=false`이면 `issues[].code`,
`issues[].path`, `repairHints[]`를 보고 plan을 수정한 뒤 다시 검증한다.
이때 `can_create=false`이므로 `create_document_from_plan`을 호출하지 않는다.
table 오류는 `columns[].key`와 `rows[]` key를 맞추고, style warning은 지원
token으로 바꾸거나 생략한다. 생성 후 package/schema 오류가 있으면
`quality.validation.*.issues[]`와 `quality.recovery.repair_hints[]`를 확인해
재저장 또는 재생성한다.

운영 계획서 작성은 다음 순서를 권장한다.

1. `validate_document_plan(document_plan)`으로 schema와 block/table 오류를 먼저 고친다.
2. `analyze_document_plan(document_plan, quality_profile="operating_plan")`으로 파일 생성 없이 품질 preview를 확인한다.
3. `handoff_status="ready"`이면 `create_document_from_plan(filename, document_plan, quality_profile="operating_plan")`으로 명시한 경로에만 생성한다.
4. `handoff_status="needs_revision"`이면 `quality.gaps[]`와 `quality.profiles.operating_plan.repair_hints[]`를 반영해 plan을 보강한다.
5. 생성 후 `get_document_text`, `get_table_text`로 결과를 다시 읽는다.
6. `visual_review_required=true`이면 `visual_review.py` 또는 열린 문서 검토로
   `hwpx.visual-review.v1` 증거를 생성한다. `current.status="observed_pass"`가
   아니면 `handoff_status="ready"`를 최종 제출 가능(submission-ready)으로
   설명하지 않는다.

승인된 HWPX 양식을 보존해야 하는 경우에는 `create_document_from_plan`으로 새
문서를 만들지 말고 P6 baseline 기반 form-fit 경로를 사용한다.

1. `analyze_template_formfit(source_filename, baseline, content, destination_filename)`으로 원본이 변경되지 않는지와 required anchor 해소 여부를 확인한다.
2. `unresolved_count > 0`이면 `unresolved[].reason`에 따라 content 또는 baseline locator를 보강한다.
3. `apply_template_formfit(analysis=..., confirm=true)`로 원본과 다른 destination에만 적용한다.
4. `source.preserved`, `validation.validate_package.ok`, `validation.validate_document.ok`, `residual_markers.blocking == []`를 확인한다.
5. `visual_review_required=true`이면 `visual_review.py` 또는 열린 문서 검토로
   `hwpx.visual-review.v1` 증거를 생성한다. `current.status="observed_pass"`가
   아니면 `handoff_status="ready"`를 최종 제출 가능(submission-ready)으로
   설명하지 않는다.

### 🧯 패키지 복구

`repair_hwpx`는 원본을 직접 수정하지 않고 새 output 경로에 복구 복사본을 만든다.

| 도구 | 설명 |
|---|---|
| `repair_hwpx` | `recover=false`이면 mimetype 첫 엔트리/ZIP_STORED 재패킹과 CRC self-check를 수행한다. `recover=true`이면 central directory 손상 ZIP을 Local File Header scan으로 복구한 뒤 재패킹한다. |

확인할 응답 필드는 `crcOk`, `validatePackage.ok`, `reordered`, `recovered`, `entryCount`다.
기계 검증 후에도 제출·납품 전에는 가능하면 Hancom Office HWP 또는 viewer에서 실제 열람한다.

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
| `get_table_map` | 표가 많은 문서에서 표 위치, 크기, caption/직전 문단 문맥, 셀 문단 location을 빠르게 탐색 |
| `find_cell_by_label` | 한국어 양식/템플릿에서 라벨 셀 기준으로 입력 대상 셀 찾기 |
| `fill_by_path` | `성명 > right`, `소속 > right`, `합계 > down > right` 같은 경로 구문으로 셀 채우기 |
| `set_table_cell_text` | 셀 텍스트 수정. 기본값은 기존 charPr 서식 보존이며, `split_paragraphs=true`로 멀티라인을 여러 `hp:p`로 분리 |
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

### 🩺 서버 진단

| 도구 | 설명 |
|---|---|
| `mcp_server_health` | 서버·python-hwpx·스킬 번들 버전과 도구 수(배포 스큐 자기진단), transport, timeout, sandbox 경로 안내, disconnect 가능 조건과 keepalive 점검 정보를 반환 |

<br>

## 환경 변수

| 변수 | 설명 | 기본값 |
|---|---|---|
| `HWPX_MCP_MAX_CHARS` | 텍스트 반환 도구 기본 최대 길이 | `10000` |
| `HWPX_MCP_AUTOBACKUP` | `1`이면 저장 전 `.bak` 백업 생성 | `1` |
| `HWPX_MCP_ADVANCED` | `1`이면 고급 도구 활성화 | `0` |
| `HWPX_MCP_SANDBOX_ROOT` | 설정 시 이 root 내부 상대경로 또는 내부 절대경로만 허용 | unset |
| `HWPX_MCP_FETCH_TIMEOUT_SECONDS` | URL 기반 HWPX fetch timeout | `20.0` |
| `HWPX_MCP_QUALITY` | 전역 기본 저장 게이트 정책(`transparent`/`strict`). 도구별 `quality` 블록이 우선 | `transparent` |
| `HWPX_MCP_REQUIRE_CAPABILITY` | `0`이면 capability skew fail-closed를 끔(진단/전문가용) | `1` |
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

## License
Apache License 2.0. See LICENSE and NOTICE.

<br>

## 작성자

**고규현** — 광교고등학교 정보·컴퓨터 교사

- 이메일: [kokyuhyun@hotmail.com](mailto:kokyuhyun@hotmail.com)
- GitHub: [@airmang](https://github.com/airmang)
