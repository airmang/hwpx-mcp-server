<p align="center">
  <h1 align="center">📄 hwpx-mcp-server</h1>
  <p align="center">
    <strong>AI 에이전트가 HWPX 문서를 바로 읽고, 찾고, 채우고, 만들게 하는 MCP 서버</strong>
  </p>
  <p align="center">
    핵심 파일 처리는 한글 워드프로세서 없이 · 순수 파이썬 · 크로스 플랫폼
  </p>
  <p align="center">
    <a href="https://pypi.org/project/hwpx-mcp-server/"><img src="https://img.shields.io/pypi/v/hwpx-mcp-server?style=flat-square&color=blue" alt="PyPI"></a>
    <a href="https://pypi.org/project/hwpx-mcp-server/"><img src="https://img.shields.io/pypi/pyversions/hwpx-mcp-server?style=flat-square" alt="Python"></a>
    <a href="https://github.com/airmang/hwpx-mcp-server/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License"></a>
    <a href="https://github.com/airmang/hwpx-mcp-server/actions/workflows/tests.yml"><img src="https://github.com/airmang/hwpx-mcp-server/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  </p>
</p>

---

## 🧩 HWPX Stack (3종)

| 계층 | 레포 | 역할 |
|---|---|---|
| 📦 라이브러리 | [`python-hwpx`](https://github.com/airmang/python-hwpx) | 순수 파이썬 HWPX 파싱·편집·생성 코어 |
| 🔌 MCP 서버 | **[`hwpx-mcp-server`](https://github.com/airmang/hwpx-mcp-server)** | MCP 클라이언트(Claude Desktop, VS Code 등)에서 HWPX 조작 |
| 🎯 에이전트 스킬 | [`hwpx-plugin`](https://github.com/airmang/hwpx-plugins) | 에이전트가 HWPX를 바로 쓰게 해주는 first-party 플러그인·스킬 번들 |

`hwpx-mcp-server`는 python-hwpx 프로젝트가 직접 유지보수하는 first-party MCP 서버입니다.
이는 한컴의 공식 제품 또는 인증 서버라는 뜻이 아닙니다.

S-079 릴리스 후보는 `hwpx-mcp-server 4.0.0`이며 최소 호환 코어는 `python-hwpx 3.1.0`입니다.
`hwpx-plugin 0.3.0` 후보 설치 번들은 재현성을 위해 MCP를 `==4.0.0`, 코어를 `==3.1.0`으로 고정합니다.
이 버전 좌표는 별도 소유자 승인 전까지 공개 또는 배포를 뜻하지 않습니다.
MCP 서버는 현재 별도의 Development Status classifier를 선언하지 않습니다. 릴리스 버전과
지원 버전은 문서화하되, classifier가 추가되기 전까지 성숙도를 임의로 승격하지 않습니다.

---

## 왜 / 무엇을 하나

**hwpx-mcp-server**는 [모델 컨텍스트 프로토콜(MCP)](https://modelcontextprotocol.io) 표준 서버로, [python-hwpx](https://github.com/airmang/python-hwpx) 위에서 HWPX 문서의 **열람·검색·편집·양식채움·생성·추출**을 AI 클라이언트에서 직접 수행하게 합니다.

- **운영체제 무관** — Windows, macOS, Linux에서 동작
- **핵심 파일 처리에 한글 워드프로세서 불필요** — 순수 파이썬 처리, 서버·CI·로컬 어디서나 같은 흐름
- **AI 연동 중심** — Claude Desktop, VS Code, Gemini CLI 등 MCP 클라이언트와 직접 연결
- **문서 조작을 도구 호출로 표준화** — 읽기·편집·복제·양식채움·검증을 하나의 도구 집합으로 노출
- **명시적인 파일 위치 계약** — 파일 원시 도구마다 locator와 입력·출력 경로를 지정하는 일관된 방식

파일 원시 도구는 명시적인 locator와 입력·출력 경로를 사용합니다. 반면 장기 workflow,
렌더 큐, revision/idempotency 기록은 의도적으로 상태를 보유합니다. 따라서 서버 전체를
stateless라고 표현하지 않습니다.

> **참고** 이 서버는 Open XML 기반 `.hwpx` 포맷을 지원합니다. 바이너리 `.hwp`는 직접 편집 대상이 아닙니다.

## 빠른 시작

### 설치 및 실행

[uv](https://docs.astral.sh/uv/getting-started/installation/) 기준:

```bash
uvx hwpx-mcp-server
```

또는 `pip` 설치 후 실행:

```bash
pip install hwpx-mcp-server
hwpx-mcp-server
```

비-HWPX 문서(PDF/DOCX/XLSX/HTML/TXT)를 `document_to_markdown` 경로로 읽으려면 MarkItDown adapter extra를 함께 설치합니다.

```bash
pip install "hwpx-mcp-server[ingest]"
```

요구 사항: `Python >= 3.10` · `python-hwpx >= 3.1.0`

### MCP 클라이언트 설정

설정 파일에 아래 블록 하나만 넣으면 `hwpx` 서버를 바로 잡습니다.

- **Claude Desktop** — `claude_desktop_config.json`
- **Gemini CLI** — `~/.gemini/settings.json`
- **VS Code** — `.vscode/mcp.json` (키가 `mcpServers` 대신 `servers`)
- **Cursor / Windsurf** — 각 에디터의 MCP 설정 파일

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

환경 변수를 넘길 때는 `env` 블록을 추가합니다.

```json
{
  "mcpServers": {
    "hwpx": {
      "command": "uvx",
      "args": ["hwpx-mcp-server"],
      "env": {
        "HWPX_MCP_MAX_CHARS": "12000",
        "HWPX_MCP_ADVANCED": "0",
        "HWPX_MCP_WORKSPACE_ROOTS": "[\"/absolute/path/to/workspace\"]",
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

> `hwpx-plugins` 번들의 런처(`plugins/<host>/hwpx-plugin/scripts/hwpx-mcp-server`)로도 실행할 수 있습니다. 로컬 개발 시 레포가 공통 부모 아래에 없으면 `HWPX_MCP_SERVER_REPO`, `PYTHON_HWPX_REPO`를 절대경로로 지정하세요.

### 하나의 흐름부터

처음부터 모든 도구를 외울 필요는 없습니다. 보통 아래 하나로 시작합니다.

1. **읽기** — `get_document_info` → `get_document_outline` / `get_document_text` → `find_text`, `get_table_map` 등으로 필요한 부분만. (저장하지 않음)
2. **안전 수정** — `copy_document`로 사본 생성 → 읽기 도구로 대상 확인 → `search_and_replace`, `set_table_cell_text` 같은 전문 도구 또는 이종 편집용 `apply_document_commands`로 가장 작은 변경 → 다시 읽어 확인 → 검토가 끝난 복사본을 handoff.

핵심은 **copy first · smallest edit · re-read after edits**입니다.

## 무엇을 하나

기본 모드에서 다수의 HWPX 도구를 제공하며, 고급 모드(`HWPX_MCP_ADVANCED=1`)에서 점검·검증용 도구가 추가됩니다. 아래는 테마별 대표 하이라이트입니다. **전체 도구 목록·시그니처는 [`docs/use-cases.md`](docs/use-cases.md)와 [`docs/skill-first-workflows.md`](docs/skill-first-workflows.md)를 참고하세요.**

- **📖 읽기·탐색** — `get_document_info`, `get_document_text`, `get_document_outline`, `find_text`, `get_table_text`, `get_table_map`, `find_cell_by_label`, `list_styles`. `get_document_map`은 아웃라인·표 지도·누름틀·앵커를 한 호출로 반환(왕복 최소화). (저장하지 않음)
- **📥 로컬 문서 ingest** — `document_to_markdown`, `document_extract_json`, `markdown_to_document_plan`이 로컬 문서를 Markdown/JSON/document plan으로 변환합니다. HWPX는 `python-hwpx` 엔진을 우선 사용하고, 비-HWPX는 `[ingest]` extra 설치 시 MarkItDown adapter로 처리합니다.
- **🔎 검색·치환** — `search_and_replace`, `batch_replace`, `replace_in_paragraph`, `replace_by_anchor`. (`find_text` 외 즉시 저장)
- **✏️ 편집·트랜잭션** — `add_heading`, `add_paragraph`/`insert_paragraph`/`delete_paragraph`, `add_page_break`, `add_memo` 계열. `apply_document_commands`는 이종 command 목록을 한 번에 원자 적용하며 전체 롤백·`dry_run`·`expected_revision`·`idempotency_key`를 지원합니다. `apply_edits`는 전환기 호환 facade입니다. `undo_last_edit`는 `.bak` 복원, `byte_preserving_patch`는 미수정 영역 바이트 보존, `add_tracked_edit`는 변경 추적(redline)을 담당합니다.
- **📊 표·양식채움** — `add_table`, `set_table_cell_text`, `merge_table_cells`/`split_table_cell`, `format_table`, `table_compute`(합계·평균·소계). 혼합 양식의 기본 경로는 `analyze_form_fill` → `apply_form_fill` → `verify_form_fill`이며 셀·필드·본문 미수정 영역의 바이트 보존 증거를 함께 반환합니다. `fill_by_path`와 template-formfit 쌍은 전환기 호환 경로입니다.
- **🖊️ 혼합 양식 단일 트랜잭션** — `list_form_fields`와 읽기 도구로 대상을 확인한 뒤 `analyze_form_fill(plan=...)` → `apply_form_fill(plan=compiledPlan)` → `verify_form_fill(plan=...)`로 native field·label cell·canonical path·body anchor를 한 번에 원자 적용합니다. `fill_form_field`는 단일 네이티브 필드 호환 경로이고, 평가계획은 `apply_evalplan_fill`, 시험지는 `compose_exam`의 독립 전문 경로를 유지합니다.
- **🧾 선언형 문서 생성** — `hwpx.document_plan.v1`로 OWPML을 직접 만지지 않고 선언: `validate_document_plan`/`analyze_document_plan`(파일 미생성) → `create_document_from_plan`(생성+검증), `inspect_document_authoring_quality`, `inspect_operating_plan_quality`. 제안서도 document plan 경로가 기본이며 `create_proposal_document`는 호환 facade입니다. `compose_exam`은 양식 HWPX+마크다운 → 시험지 재조판(`verify_question_splits`).
- **🏛️ 공문서·비교·대량 생산** — `inspect_official_document_style`(행정업무 규정 lint: 항목기호 위계·"끝."·붙임·날짜), `inspect_reference_consistency`, `doc_diff` 후 comparison document plan을 `create_document_from_plan`으로 생성하는 신구대조표, `mail_merge`(템플릿+CSV/JSON → N부·파일명 패턴·zip), `inspect_mail_merge_placeholders`. `create_comparison_table_document`는 호환 facade입니다.
- **🎨 서식·그림·생성기** — `set_paragraph_format`, `set_page_setup`, `set_header_footer`, `set_page_number`, `set_list_format`, `format_text`, `create_custom_style`(입력은 pt/mm/% 인간 단위); `insert_picture`(manifest 자동 등록)/`replace_picture`; `build_image_grid`(사진대지)·`build_meeting_nameplates`·`build_organization_chart`.
- **🖥️ 프리뷰·추출·복구·진단** — `render_preview`(HTML/PNG 시각 자기검증), `hwpx_to_markdown`/`hwpx_to_html`/`hwpx_extract_json`(payload·URL 입력, 파일 미수정), `repair_hwpx`(원본 보존·mimetype-first 재패킹, `recover=true`로 손상 ZIP LFH scan 복구; 응답 `crcOk`/`validatePackage.ok`/`recovered` 확인), `mcp_server_health`(배포 스큐 자기진단·capability handshake).
- **🔬 고급(`HWPX_MCP_ADVANCED=1`)** — `package_parts`, `package_get_xml`/`package_get_text`, `object_find_by_tag`/`object_find_by_attr`, `validate_structure`, `lint_text_conventions` 등 저수준 점검 도구. `plan_edit`/`preview_edit`/`apply_edit`는 한 전환 기간만 유지하는 deprecated stub이며 신규 흐름은 `apply_document_commands`를 사용합니다.

> **위치 계약** — `paragraph_index`는 본문 직속 문단의 0-based 인덱스입니다. 표 안 문단은 이 인덱스에 섞지 않고 `{"kind":"table_cell_paragraph","table_index":0,"row":0,"col":1,"cell_paragraph_index":0}` 같은 `location` 객체로 지정하며, `get_table_map`/`find_text`가 반환한 값을 그대로 넘길 수 있습니다.

## 안전 사용 원칙

이 서버의 공개 표면은 **위 MCP 도구 집합**입니다. 워크플로 문서나 스킬 예시는 이 도구들의 조합 패턴이지 별도 계약이 아닙니다.

- **read first** — 먼저 읽기 도구로 문서를 파악한다.
- **copy before risky edits** — 결과물을 보존해야 하면 `copy_document`를 먼저 호출한다.
- **mutating tools persist immediately** — 수정 도구는 호출 즉시 저장되므로 검토용은 복사본에서 작업한다.
- **explicit handoff uses the reviewed copy** — 납품·handoff는 검토가 끝난 복사본 파일 기준.
- **advanced mode는 점검/검증용으로 분리** — package inspection·edit planning·validation은 `HWPX_MCP_ADVANCED=1`일 때만 쓰고 기본 흐름과 섞지 않는다.

### 품질 게이트 계약 (no raw XML)

모델은 **operation/plan만** 보내고 raw XML을 직접 편집하지 않습니다. `raw_xml_replace`, `arbitrary_xpath_mutation` 같은 원시 XML 표면은 public 도구로 노출되지 않습니다. 일반 문서 저장 경로는 python-hwpx의 **`SavePipeline` 게이트**를 사용해 무결성·XML·OPC/ID·열림안전을 검사하고, 요청한 품질 수준에 따라 FormFit·레이아웃·시각 오라클까지 적용합니다.

- `quality`를 받는 일반 저장 도구는 응답에 `visualComplete`를 싣습니다: `ok`, `status`(verified/unverified/failed), `errorCodes`, `warnings`, `suggestedRetry`. `quality`를 생략하면 transparent(열림안전), `"strict"` 또는 `{"mode":"strict","overflowPolicy":"fail","layoutLint":"strict"}`로 올리면 추가 게이트를 적용합니다.
- `byte_preserving_patch`, `apply_table_ops`, `apply_body_ops`, `apply_evalplan_fill`은 untouched package byte를 유지하기 위한 명시적 carveout입니다. 이 경로는 guarded publication, open-safety, byte/member diff와 공통 verification receipt를 사용하며 `visualComplete`를 주장하지 않습니다. 표·평가계획은 요청 시 실제 렌더 검사를 별도 수행하고, 본문 fast path는 전체 재렌더를 하지 않습니다.
- 해당 경로의 게이트가 실패하면 저장이 **보류**되거나 소유한 후보만 원복되고(`ok=false`), 구조화된 오류 코드로 재시도 여부를 알립니다.
- **capability handshake**가 core/mcp/plugin 버전+해시를 점검하고, skew 시 쓰기를 **fail-closed**로 차단합니다(`mcp_server_health.capability`). 진단/우회는 `HWPX_MCP_REQUIRE_CAPABILITY=0`.

> `visual_review_required=true`는 구조/파일 기반 검증은 통과했지만 렌더링된 시각 레이아웃은 아직 증명되지 않았다는 뜻입니다. 운영 계획서·template-formfit 결과를 최종 제출 가능(submission-ready)으로 주장하려면 열린 문서를 검토해 `hwpx.visual-review.v1` 증거를 남기고 `current.status="observed_pass"`여야 합니다.

## 환경 변수

| 변수 | 설명 | 기본값 |
|---|---|---|
| `HWPX_MCP_MAX_CHARS` | 텍스트 반환 도구 기본 최대 길이 | `10000` |
| `HWPX_MCP_AUTOBACKUP` | `1`이면 저장 전 `.bak` 백업 생성 | `1` |
| `HWPX_MCP_ADVANCED` | `1`이면 고급 도구 활성화 | `0` |
| `HWPX_MCP_WORKSPACE_ROOTS` | 허용할 workspace 절대경로의 JSON 배열(복수 root 지원). 상대경로는 첫 root 기준 | unset(프로세스 cwd) |
| `HWPX_MCP_SANDBOX_ROOT` | 단일 root 구버전 호환 변수. 새 설정에서는 `HWPX_MCP_WORKSPACE_ROOTS` 사용 | unset |
| `HWPX_MCP_FETCH_TIMEOUT_SECONDS` | URL 기반 HWPX fetch timeout | `20.0` |
| `HWPX_MCP_ALLOW_PRIVATE_NETWORK` | `1`이면 신뢰된 사설/루프백 HTTPS 대상 허용. 링크로컬·metadata·예약 주소는 계속 차단 | `0` |
| `HWPX_MCP_QUALITY` | 전역 기본 저장 게이트 정책(`transparent`/`strict`). 도구별 `quality`가 우선 | `transparent` |
| `HWPX_MCP_REQUIRE_CAPABILITY` | `0`이면 capability skew fail-closed를 끔(진단/전문가용) | `1` |
| `LOG_LEVEL` | 로그 레벨 | `INFO` |

경로는 기본적으로 workspace 밖 traversal과 symlink escape를 거부합니다. URL 입력과
HTTP storage/render transport는 HTTPS 및 공개 IP만 허용하고, 리다이렉트와 실제 연결
피어를 다시 검사합니다. 사설망 opt-in은 운영자가 대상을 신뢰하고 네트워크 경계를
별도로 통제할 때만 사용하세요.

macOS와 `renameat2`를 제공하는 Linux의 canonical mixed-form 및 byte-preserving form 게시 경로는 root/parent descriptor와
대상 byte·identity snapshot에 묶여 동시 rename·symlink 교체도 실패 후 원복합니다.
Windows와 그 원자 교환 primitive를 제공하지 않는 기타 POSIX 호스트의 표준 라이브러리
fallback은 동일한 handle-relative 보장을 제공하지 않습니다. 이 호스트에서는 로컬
workspace를 신뢰된 디렉터리로 취급하고 ACL 또는 host sandbox로 같은 사용자 권한의
동시 reparse/rename을 막아야 합니다.

## 더 보기

- 실전 사용 사례: [`docs/use-cases.md`](docs/use-cases.md)
- 스킬 기반 워크플로: [`docs/skill-first-workflows.md`](docs/skill-first-workflows.md)
- 보안·하드닝 가이드: [`docs/hardening_guide_ko.md`](docs/hardening_guide_ko.md)
- 도구 계약: [`docs/tool-contract.md`](docs/tool-contract.md)
- 현재 4.0.0 릴리스 후보 기계 판독 계약 delta: [`docs/tool-contract-delta-4.0.0.json`](docs/tool-contract-delta-4.0.0.json)
- 역사적 3.0.0 제품 경계 전환: [`docs/product-boundary-migration-3.0.0.md`](docs/product-boundary-migration-3.0.0.md)
- 역사적 3.0.0 기계 판독 계약 delta: [`docs/tool-contract-delta-3.0.0.json`](docs/tool-contract-delta-3.0.0.json)
- 변경 이력: [`CHANGELOG.md`](CHANGELOG.md)

### 테스트

```bash
python -m pip install -e ".[test]"   # 테스트 의존성 설치
python -m pytest -q                   # 전체 테스트
```

## 감사의 말

코어 라이브러리 [python-hwpx](https://github.com/airmang/python-hwpx) 위에서 동작하며, 아래 공개 표준·프로젝트에 빚지고 있습니다.

- **[OWPML — 개방형 워드프로세서 마크업 언어 (KS X 6101)](https://www.kssn.net/search/stddetail.do?itemNo=K001010119985)** — HWPX가 기반하는 한국 산업 표준
- **[hancom-io/hwpx-owpml-model](https://github.com/hancom-io/hwpx-owpml-model)** — OWPML 요소 구조 참조 모델 · **[neolord0/hwpxlib](https://github.com/neolord0/hwpxlib)** — 오라클 샘플 코퍼스
- **[edwardkim/rhwp](https://github.com/edwardkim/rhwp)** — 멱등성·검증 게이트 설계 영감

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## 작성자

**고규현** — 광교고등학교 정보·컴퓨터 교사

- 이메일: [kokyuhyun@hotmail.com](mailto:kokyuhyun@hotmail.com)
- GitHub: [@airmang](https://github.com/airmang)
