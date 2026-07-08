# 대국민 한글 문서 비서 — PlayMCP 출품 도구 설계

> Status: Design (approved direction, pending spec review)
> Date: 2026-06-24
> Context: Agentic Player 10 (PlayMCP) 예선 출품용 `hwpx-mcp-server` PlayMCP 프로필
> Supersedes the 7-tool list in `docs/2026-06-24-playmcp-agentic-player-10-plan.md` (Task 3 tool surface)

## 1. 목표와 컨셉

**제품 정체성:** "대국민 한글 문서 비서" — 시민이 받은 한글(.hwpx) 문서를 ① 쉽게 이해하고 ② 무엇을 해야 할지 알려주고 ③ 제출 전 개인정보까지 점검해 주는 채팅형 도우미.

**왜 이김(차별점):** 카카오 AI는 .hwpx 파일을 직접 열지 못한다. 따라서 도구의 가치 = "LLM이 못 여는 한글 문서를 추론 가능한 구조화 콘텐츠로 바꾸고, LLM이 못 미덥게 하는 결정적 분석(양식 빈칸 구조 파싱·개인정보 정규식 탐지)을 정확히 수행"하는 것. 단순 요약은 심사 규정상 "LLM이 웹검색으로 가능"에 걸릴 수 있으나, **.hwpx를 읽는 행위 자체가 LLM에 불가능**하므로 추출이 곧 차별점이다. 공무원용 공문서 규정 lint(범정부 오피스 유사 기능)는 **의도적으로 제외**한다 — 시민은 문서를 만드는 쪽이 아니라 받고 제출하는 쪽이다.

**본선이 이용자 투표**이므로, 평범한 시민이 "유용/신기"하다고 느껴 표를 줄 만한 체감 가치를 최우선한다.

## 2. 컨테스트 하드 제약 (2026-06-24 카카오 Notion 가이드 검증)

- 공개 Remote MCP, Streamable HTTP only, stateless 권장.
- 도구 3~10 권장 / 20 max. 각 도구 `name`·`description`·`inputSchema`·`annotations` 필수.
- `annotations` 5필드 모두 지정: `title`, `readOnlyHint`, `destructiveHint`, `openWorldHint`, `idempotentHint`.
- 도구명: 1~128자, `[A-Za-z0-9_-]`만, 대소문자 구분, 중복 불가, "kakao" 포함 불가(대소문자 무관).
- `description`: 영문 우선, 서비스 고유명사 영문+국문 병기 포함, ≤1024자.
- **단일 응답 24KB(24576 byte) 초과 시 에러 → 반려.** 한글 UTF-8 ~3 byte/char.
- 지연: 평균 ≤100ms, p99 ≤3000ms 필수.
- 반려 트리거: LLM이 웹검색으로 가능한 기능 / 유료 구독 필수 / PII 수집·전송(주민·면허·여권·외국인등록·카드·계좌번호) / 상업 유도 / Zapier류 자동생성.
- PlayMCP는 Tools만 지원(Resource/Prompt 미지원). 인증 없음/만료 시 401.

## 3. 입력/출력 아키텍처 (생사 결정 요소)

**입력 = `.hwpx` 공개 https URL** (필수 1차 경로). 추가로 `hwpx_base64`(요청측, 24KB 응답캡 무관)를 선택 입력으로 함께 지원 — 기존 로더 `_load_hwpx_payload`/`_download_hwpx_from_url`(https-only·byte cap, server.py:540/585 검증)를 재사용한다. 모든 새 도구는 이 단일 ingest 위에 얇은 래퍼로 구현한다.

- 현실: 카카오 툴즈가 업로드 파일을 서드파티 MCP에 전달하는 공식 메커니즘이 **없음**(가이드·발표·후기 전부 URL/파라미터 중심). 따라서 시민은 *정부24·지자체·학교 홈페이지·공유 클라우드*의 **다운로드 링크**를 붙여넣는 시나리오로 스코프한다.
- 한계: 이메일 첨부 그 자체는 공개 URL이 없어 직접 처리 불가 → 클라우드 업로드 후 링크 공유로 우회. 카카오 툴즈가 추후 첨부 전달을 지원하면 `hwpx_base64` 경로가 드롭인.

**출력 = 정제된 텍스트/구조 리포트(JSON+짧은 마크다운).** 다운로드 파일 없음 = read-only. 이전에 확인된 생성 도구의 치명적 약점("생성물 전달 경로 부재")을 원천 회피.

**공통 가드:**
- URL: https-only, byte cap, timeout(p99 3s 방어, SSRF 차단).
- 응답: **UTF-8 byte 기준** 24576 하드 가드(글자수 아님). 초과 시 절단 + "다음 청크 요청" 힌트.
- 청크는 **치환식(substitutive)** — 현재 extract/markdown 도구의 additive 청크 버그(전체 payload 항상 포함, server.py:2306-2327/2229-2236)를 새 도구에서는 반복하지 않는다.
- `MAX_CHARS_PER_CHUNK` 기본 4000(현 8000은 한글 ~24KB라 위험, server.py:280).

## 4. 도구 세트 (5개, 신규 read-only)

기존 95개 raw 도구를 노출하지 않는다. 시민의 질문 1개 = 도구 1개로 매핑한 새 도구 5개를 PlayMCP 프로필로만 노출한다. 모든 도구 입력: `{ url?: string(https), hwpx_base64?: string }` (둘 중 하나 필수). 모든 도구 annotations: `readOnlyHint:true, destructiveHint:false, openWorldHint:true, idempotentHint:true`.

### 4.1 `summarize_korean_document` — "이거 뭐야?"
- 출력: 문서종류 추정(신청서/안내문/동의서/공고/계약서 등), 쉬운 말 요약 3~5문장, 핵심 메타(제목·발신·수신/대상·일자), 분량(페이지·표·작성항목 수).
- 엔진 근거: 텍스트/아웃라인 추출 보유. 요약 자체는 호출측 LLM이 추출 텍스트로 수행하되, 도구는 구조·메타·종류를 결정적으로 제공.
- 응답: 소형(고정 상한).

### 4.2 `read_document_text` — "원문 그대로 / 추가 질문" (청크)
- 입력 추가: `chunk_index:int=0`.
- 출력: 본문 텍스트 청크(≤4000자) + `next_chunk_index` + `has_more`.
- 용도: Q&A·세부 확인 fallback. 치환식 청크로 24KB 안전.

### 4.3 `list_fillable_items` ⭐ — "내가 뭘 채워야 해?"
- 출력: 작성 항목 배열 `[{ label, kind(text|date|choice|signature|checkbox), location, hint, required }]`.
- 탐지원: ① HWPX 누름틀/클릭히어 필드(`list_form_fields` 보유) ② 표의 빈 셀(`find_cell_by_label`·표 셀 맵 보유) ③ 본문 `____`/`(   )`/밑줄 빈칸(신규 휴리스틱). 항목 수 상한(예 50) + 라벨/힌트 길이 캡으로 24KB 방어.
- 핵심 차별점: 빈칸 구조 파싱은 LLM이 .hwpx에서 불가.

### 4.4 `extract_submission_guide` — "언제까지 어디로 뭘 내?"
- 출력: `{ deadlines[], required_documents[], submit_to, contact, evidence[] }` — 마감·구비서류·제출처·문의처와 근거 문구.
- 탐지원: 텍스트 휴리스틱(신규) — "제출기한/마감/까지/구비서류/붙임/제출처/문의" 키워드 + 표 맥락. 근거 문구를 함께 반환해 신뢰 확보.
- 옵션: 4개로 줄이려면 4.1에 흡수 가능. 권장은 분리(데모 비트 분리 + 깔끔한 tool-calling).

### 4.5 `scan_personal_info` ⭐ — "제출 전 개인정보 점검"
- 출력: `[{ type(rrn|foreigner_id|driver_license|passport|card|account|phone|email|address), location, masked_preview, confidence }]` + 요약 카운트.
- **PII 컴플라이언스(필수):** 원문 PII 값을 절대 저장·반환하지 않는다. **마스킹 프리뷰만**(예 `주민번호 9001**-*******`, 카드 `****-****-****-1234`). 도구가 PII를 외부 전송하지 않음 → 존재/위치 보고는 카카오 PII 금지 규정과 정합(전송 금지 대상은 "값"이지 "마스킹된 탐지 사실"이 아님). 심사 제출 카피에 이 프레이밍을 명시.
- 탐지: 정규식(주민번호·외국인등록 `######-#######`, 운전면허, 여권, 카드 `#### #### #### ####`+선택적 Luhn, 계좌 휴리스틱, 전화, 이메일, 주소 휴리스틱). 결정적이라 LLM보다 정확.

## 5. 프로필/설정 변경 (구현 시 필수)

- `HWPX_MCP_PROFILE=playmcp` (또는 동등)일 때 위 5개만 노출하는 전용 app factory/allowlist. 기존 로컬 95-tool 표면은 불변.
- 5개 모두 annotations 5필드 부여(현재 전부 bare `@mcp.tool()` → 0개).
- 5개 description 영문 우선 + 서비스 고유명사 병기(예: `"<Service> (<서비스 국문>): ..."`), ≤1024자.
- 응답 UTF-8 byte 24KB 하드 가드 + 치환식 청크 + `MAX_CHARS_PER_CHUNK=4000`.
- URL 다운로드 https-only·byte cap·timeout 재확인.
- transport=Streamable HTTP, stateless, 401 on missing/expired 확인.

## 6. 명시적 비범위 (YAGNI / 리스크 회피)

- 문서 **생성·편집·다운로드 전달**(create/fill/apply 류): 출력 전달 계층 부재 → 1차 출품 제외. 추후 오브젝트 스토리지+서명 URL 구축 시 별도 스펙.
- 공무원용 **공문서 규정 lint**(`inspect_official_document_style` 등): 컨셉상 제외.
- 계약서·약관 주의보, 표 데이터 분석: 1차 제외(추후 확장 후보).
- 로컬 `filename` 입력 경로: PlayMCP 프로필에서 비노출.

## 7. 리스크

- **입력 경로**(최대 리스크): 시민이 공개 URL을 못 줄 수 있음 → 스코프를 "다운로드 가능한 공개 양식"으로 한정, 데모/카피에서 링크 사용을 명확히 안내.
- 24KB byte 오버플로: byte 가드 + 치환식 청크 없으면 한글 다중페이지에서 반려. 실제 한글 문서로 **바이트 실측** 검증.
- `scan_personal_info` 심사 인식: PII 취급 오해 가능 → 마스킹 전용·무저장·무전송을 코드와 카피에 명시.
- `extract_submission_guide`/`list_fillable_items` 휴리스틱 견고성: 양식 다양성에 따라 누락 가능 → 근거 문구 동반 + "추정" 표기로 신뢰 관리.
- p99 3s: 대용량 문서/URL에 입력 크기·타임아웃 상한.

## 8. 미해결 결정 (사용자 확인 필요)

- **서비스 고유명사(이름):** description 병기에 쓸 영문/국문 정식 명칭. 후보: "Hangeul Doc Helper (한글 문서 비서)" 등. 도구명에 'AI/Bot/Service' 중복 키워드·'kakao' 금지. (현 plan의 "HWPX Document Agent" 유지할지 결정)
- **도구 개수:** 5개 권장 vs `extract_submission_guide`를 요약에 흡수한 4개.
- **인증:** 공개 무인증 vs 토큰 게이트(401). 1차는 무인증 가정.

## 9. 대략 단계 (구현 계획은 writing-plans에서)

1. PlayMCP 프로필 전용 app factory + annotations 모델.
2. 공통 ingest 래퍼(url/base64) + 24KB byte 가드 + 치환식 청크.
3. 5개 도구 구현(요약/원문청크/작성항목/제출가이드/개인정보) + 단위 테스트.
4. Dockerfile(linux/amd64) + Streamable HTTP 기동 + MCP Inspector 검증.
5. 제출 카피(영문 description·예시 프롬프트·PII 프레이밍) + 실측 24KB/지연 검증 리포트.
