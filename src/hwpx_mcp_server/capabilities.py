"""Capability map — task-oriented index of the hwpx MCP toolset.

An agent connecting to this server sees ~150 flat tool names. This module groups
them by TASK DOMAIN so an agent can orient itself with one ``describe_capabilities``
call: "what can this toolkit do, and which tools are the entry points."

The map is curated (intent + when-to-use per domain). A coverage test
(``test_capabilities.py``) asserts every registered tool appears in exactly one
domain, so the map can never silently drift as tools are added.
"""

from __future__ import annotations

from typing import Any

# domain key -> (title, intent, when_to_use, [tool names])
DOMAINS: list[dict[str, Any]] = [
    {
        "key": "read",
        "title": "읽기·추출·변환",
        "intent": "HWPX 문서를 읽어 텍스트·구조·서식을 뽑거나 Markdown/HTML/JSON으로 변환.",
        "whenToUse": "문서 내용을 이해·인용·재작업하거나 다른 형식으로 내보낼 때. 서식·각주까지 보려면 hwpx_to_markdown/hwpx_extract_json.",
        "tools": [
            "get_document_text", "get_document_info", "get_document_outline", "get_document_map",
            "get_paragraph_text", "get_paragraphs_text", "get_location_text",
            "get_table_text", "get_table_map", "find_text", "list_available_documents",
            "hwpx_to_markdown", "document_to_markdown", "hwpx_to_html",
            "hwpx_extract_json", "document_extract_json",
        ],
    },
    {
        "key": "form_fill",
        "title": "양식 채움 (동적)",
        "intent": "처음 보는 실양식(예시·안내문·지울 것 섞임)을 사람이 편집한 듯 서식일치·바이트보존으로 채운다.",
        "whenToUse": "빈 양식이 아닌 실양식을 채울 때. 먼저 scan_form_guidance로 정찰→상의→apply_table_ops/apply_body_ops로 실행→inspect_fill_residue/verify_form_fill로 검증. 워크플로 상세는 스킬 workflows-forms.md.",
        "tools": [
            "scan_form_guidance", "apply_table_ops", "apply_body_ops", "inspect_fill_residue",
            "verify_form_fill", "list_form_fields", "fill_form_field", "find_cell_by_label",
            "fill_by_path", "fill_template", "analyze_form_fill", "apply_form_fill",
            "analyze_template_formfit", "apply_template_formfit", "analyze_template_structure",
            "analyze_quality_generation", "apply_quality_generation",
            "apply_evalplan_fill", "score_form_fill",
        ],
    },
    {
        "key": "author",
        "title": "문서 생성 (from scratch/plan)",
        "intent": "계획(plan)·브리프로 새 HWPX 문서를 만든다(공문·보고서·제안서·비교표 등).",
        "whenToUse": "빈 문서에서 새로 저작할 때. 구조화 plan은 create_document_from_plan, 도메인 프리셋은 government_report/proposal 등.",
        "tools": [
            "create_document", "create_document_from_plan", "copy_document", "make_blank",
            "create_government_report_document", "create_proposal_document",
            "create_comparison_table_document",
            "get_document_plan_schema", "validate_document_plan", "analyze_document_plan",
            "markdown_to_document_plan", "parse_government_report_text", "compute_report_value",
            "register_template", "list_templates", "describe_template",
        ],
    },
    {
        "key": "edit",
        "title": "편집 (기존 문서)",
        "intent": "기존 문서에 문단·그림·페이지나눔을 넣거나 텍스트를 치환·수술한다.",
        "whenToUse": "이미 있는 문서를 고칠 때. 바이트보존 수술은 byte_preserving_patch/replace_by_anchor, 대량 치환은 batch_replace.",
        "tools": [
            "add_heading", "add_paragraph", "insert_paragraph", "insert_paragraphs_bulk",
            "delete_paragraph", "add_page_break", "add_control", "add_shape",
            "apply_edits", "plan_edit", "preview_edit", "apply_edit", "undo_last_edit",
            "replace_by_anchor", "replace_in_paragraph",
            "search_and_replace", "batch_replace", "byte_preserving_patch",
            "insert_picture", "replace_picture", "read_paragraphs", "read_text",
        ],
    },
    {
        "key": "tables",
        "title": "표 조작",
        "intent": "표 셀 채움·병합/분할·테두리·너비·계산·표 간 복사.",
        "whenToUse": "표를 직접 만들거나 구조를 바꿀 때(양식 채움 맥락이면 form_fill 도메인의 apply_table_ops 우선).",
        "tools": [
            "add_table", "get_table_cell_map", "set_table_cell_text", "merge_table_cells",
            "split_table_cell", "set_table_border_fill", "format_table", "table_compute",
            "copy_table_between_documents", "replace_table_region",
        ],
    },
    {
        "key": "styles",
        "title": "서식·스타일",
        "intent": "문단/런 서식·목록·스타일 정의·서식 프로파일 추출·비교·이식.",
        "whenToUse": "글꼴·크기·정렬·줄간격·목록·스타일을 다룰 때.",
        "tools": [
            "list_styles", "list_styles_and_bullets", "create_custom_style",
            "apply_style_to_paragraphs", "apply_style_to_text_ranges",
            "set_paragraph_format", "set_list_format", "format_text",
            "ensure_run_style", "find_runs_by_style", "replace_text_in_runs",
            "extract_style_profile", "apply_style_profile_to_plan", "compare_style_profiles",
        ],
    },
    {
        "key": "layout",
        "title": "페이지·머리글·쪽번호",
        "intent": "용지 설정·머리글/바닥글·쪽번호.",
        "whenToUse": "페이지 레이아웃 요소를 설정할 때.",
        "tools": ["set_page_setup", "set_header_footer", "set_page_number"],
    },
    {
        "key": "toc_xref",
        "title": "차례·상호참조",
        "intent": "네이티브 자동 차례·상호참조(한컴이 재페이지네이션 시 재번호).",
        "whenToUse": "목차/그림 번호/상호참조가 필요할 때.",
        "tools": ["add_toc", "add_cross_reference", "verify_toc"],
    },
    {
        "key": "pii",
        "title": "개인정보(PII)",
        "intent": "개인정보 탐지·마스킹.",
        "whenToUse": "배포 전 개인정보를 찾거나 가릴 때. mask 파라미터는 fill/mail_merge/extract에도 있음.",
        "tools": ["scan_personal_info"],
    },
    {
        "key": "redline",
        "title": "변경추적(레드라인)",
        "intent": "추적된 삽입/삭제/교체(한컴 검토 리본 수락/거부 가능).",
        "whenToUse": "수정 이력을 남기는 신구대조·교정에.",
        "tools": ["add_tracked_edit"],
    },
    {
        "key": "exam",
        "title": "시험지 조판",
        "intent": "양식.hwpx + 문항 MD → 양식맞춤 재조판(문항 분할 검증).",
        "whenToUse": "시험지 원안지 양식에 문항을 앉힐 때.",
        "tools": ["compose_exam", "verify_question_splits"],
    },
    {
        "key": "seal",
        "title": "직인·관인",
        "intent": "발신명의 끝글자에 직인 날인·날인 규정 검사.",
        "whenToUse": "공문 직인 처리에.",
        "tools": ["place_seal", "check_seal_compliance"],
    },
    {
        "key": "generators",
        "title": "대량생산·특수 산출",
        "intent": "메일머지(상장·통신문)·사진대지·회의명패·조직도.",
        "whenToUse": "같은 틀로 여러 부를 찍거나 특수 레이아웃을 생성할 때.",
        "tools": [
            "mail_merge", "inspect_mail_merge_placeholders",
            "build_image_grid", "build_meeting_nameplates", "build_organization_chart",
        ],
    },
    {
        "key": "memo",
        "title": "메모·주석",
        "intent": "문서에 메모(코멘트) 추가·삭제.",
        "whenToUse": "검토 코멘트를 달 때.",
        "tools": ["add_memo", "add_memo_by_anchor", "add_memo_with_anchor", "attach_memo_field", "remove_memo"],
    },
    {
        "key": "verify_quality",
        "title": "검증·품질·복구",
        "intent": "실한컴 렌더·구조 검증·품질 점검·문서 비교·손상 복구·서버 상태.",
        "whenToUse": "산출물 확언 전 검증하거나 문제를 진단할 때. 제출 확언은 렌더+사람 확인 후에만.",
        "tools": [
            "render_preview", "repair_hwpx", "mcp_server_health", "describe_capabilities",
            "doc_diff", "validate_structure", "lint_text_conventions",
            "inspect_document_quality", "inspect_document_authoring_quality",
            "inspect_operating_plan_quality", "inspect_official_document_style",
            "inspect_reference_consistency",
        ],
    },
    {
        "key": "package_io",
        "title": "패키지·핸들·저장",
        "intent": "문서 열기/저장/핸들 관리·OPC 파트 접근·HWP→HWPX 변환·저수준 조회.",
        "whenToUse": "파일 I/O·핸들 생명주기·원시 XML/파트 접근·포맷 변환에.",
        "tools": [
            "open_document_handle", "close_document_handle", "list_open_documents",
            "open_info", "save", "save_as", "convert_hwp_to_hwpx",
            "package_parts", "package_get_text", "package_get_xml",
            "list_sections", "list_headers", "list_master_pages_histories_versions",
            "object_find_by_attr", "object_find_by_tag", "find", "get_tool_guide",
            "export_text", "export_markdown", "export_html", "text_extract_report",
        ],
    },
]


def build_capability_report(domain: str | None = None) -> dict[str, Any]:
    """Task-grouped capability map. ``domain`` filters to one domain's detail."""
    domains = DOMAINS if domain is None else [d for d in DOMAINS if d["key"] == domain]
    out_domains = [
        {
            "key": d["key"],
            "title": d["title"],
            "intent": d["intent"],
            "whenToUse": d["whenToUse"],
            "toolCount": len(d["tools"]),
            "tools": d["tools"],
        }
        for d in domains
    ]
    return {
        "domains": out_domains,
        "domainCount": len(DOMAINS),
        "note": "작업 종류별 도구 지도. 양식 채움의 상세 절차는 스킬 references/workflows-forms.md(⓪정찰→상의→실행→검증). 특정 도구 설명은 get_tool_guide 또는 각 도구 스키마 참조.",
    }


def coverage_against(tool_names: set[str]) -> dict[str, Any]:
    """Return {unmapped: [...], mappedNotRegistered: [...]} vs a live tool set."""
    mapped: set[str] = set()
    for d in DOMAINS:
        mapped.update(d["tools"])
    return {
        "unmapped": sorted(tool_names - mapped),
        "mappedNotRegistered": sorted(mapped - tool_names),
    }
