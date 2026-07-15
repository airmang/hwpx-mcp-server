# SPDX-License-Identifier: Apache-2.0
"""Single release-facing contract for the FastMCP HWPX tool surface.

The legacy server inventory is intentionally not represented here.  The console
entrypoint launches :mod:`hwpx_mcp_server.server`, so only tools registered from
this module are callable by installed plugin users.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


MIN_PYTHON_HWPX = "2.29.2"
MIN_MCP_VERSION = "2.23.1"
MIN_SKILL_VERSION = "0.1.31"


@dataclass(frozen=True, slots=True)
class DomainSpec:
    key: str
    title: str
    intent: str
    when_to_use: str
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    domain: str
    profile: str = "default"
    skill_required: bool = False


_ADVANCED_TOOLS = {
    "package_parts",
    "package_get_xml",
    "package_get_text",
    "object_find_by_tag",
    "object_find_by_attr",
    "plan_edit",
    "preview_edit",
    "apply_edit",
    "validate_structure",
    "lint_text_conventions",
}

_SKILL_REQUIRED_TOOLS = {
    "get_document_node",
    "query_document_nodes",
    "apply_document_commands",
    "dump_document_blueprint",
    "replay_document_blueprint",
    "create_document_from_plan",
    "create_government_report_document",
    "mail_merge",
    "table_compute",
    "extract_style_profile",
    "list_templates",
    "get_document_map",
    "repair_hwpx",
    "replace_by_anchor",
    "add_memo_by_anchor",
    "byte_preserving_patch",
    "render_preview",
    "apply_edits",
    "add_tracked_edit",
    "undo_last_edit",
    "compose_exam",
    "scan_form_guidance",
    "apply_table_ops",
    "apply_body_ops",
    "inspect_fill_residue",
    "verify_form_fill",
    "apply_evalplan_fill",
    "score_form_fill",
    "run_fixture_benchmark",
    "export_fixture_benchmark",
    "start_practice_scenario",
    "apply_practice_scenario",
}


DOMAIN_SPECS: tuple[DomainSpec, ...] = (
    DomainSpec(
        "agent_document",
        "에이전트 문서 인터페이스",
        "공유 semantic node/path/query/command 계약으로 낯선 HWPX를 탐색하고 이종 편집을 한 번에 원자 적용.",
        "get_document_node/query_document_nodes로 revision-bound canonical path를 찾은 뒤 "
        "apply_document_commands 한 배치로 set/add/remove/move/copy를 적용한다. 양식·시험·PII 등 "
        "도메인 증거가 필요한 작업은 기존 전문 도구를 사용한다.",
        (
            "get_document_node",
            "query_document_nodes",
            "apply_document_commands",
            "dump_document_blueprint",
            "replay_document_blueprint",
        ),
    ),
    DomainSpec(
        "private_practice",
        "비공개 코퍼스 합성 연습",
        "불투명 scenario/campaign ID로 private 경로와 평가 gold를 숨긴 채 합성 HWPX 연습을 준비·실행.",
        "start_practice_scenario로 합성 work order를 확인하고 decision preview 뒤 "
        "apply_practice_scenario(confirm=true)로 별도 destination에만 적용한다. Leap B 캠페인은 "
        "start_practice_campaign(confirm=true) 뒤 bounded continue와 명시적 decision receipt로만 진행한다.",
        (
            "start_practice_scenario",
            "apply_practice_scenario",
            "start_practice_campaign",
            "get_practice_campaign",
            "continue_practice_campaign",
            "cancel_practice_campaign",
            "export_practice_campaign",
        ),
    ),
    DomainSpec(
        "real_hancom_render",
        "실한컴 비동기 렌더",
        "인증된 private queue로 HWPX를 제출하고 실한컴 PDF·페이지 영수증을 비동기로 조회.",
        "render_submit 후 즉시 job id를 받고 render_status로 폴링한다. render_health가 unavailable/degraded이면 "
        "로컬 미리보기를 실한컴 검증으로 간주하지 말고 unverified로 보류한다.",
        (
            "render_submit",
            "render_status",
            "render_cancel",
            "render_health",
        ),
    ),
    DomainSpec(
        "workflow",
        "자율 문서 워크플로",
        "서버가 상태·정책·결정·예산·검증을 강제하는 재시작 가능한 고수준 문서 작업.",
        "호스트별 스킬 지식 없이 HWPX 작업을 안전하게 시작·진행·승인·재개할 때.",
        (
            "start_workflow",
            "get_workflow",
            "get_workflow_result",
            "continue_workflow",
            "approve_workflow_decision",
            "cancel_workflow",
            "resume_workflow",
        ),
    ),
    DomainSpec(
        "visual_qa",
        "전 페이지 비전 검수",
        "버전 고정 fixture 페이지를 독립 어댑터로 검수하고 제한적으로 수정.",
        "visual_review_fixture로 모든 페이지와 어댑터 disagreement를 확인한 뒤, 안전하게 매핑된 결함만 visual_repair_fixture로 최대 3회 수정한다. fixture 결과는 항상 실렌더 미검증이다.",
        ("visual_review_fixture", "visual_repair_fixture"),
    ),
    DomainSpec(
        "blind_eval",
        "블라인드 fixture 실무 평가",
        "동결된 fixture 실행·판정 증거를 검증하고 출처를 숨긴 심사용 번들을 내보냄.",
        "run_fixture_benchmark로 실행·ToolSpec·판정·익명화 coverage를 검증한 뒤 "
        "export_fixture_benchmark로 opaque 번들을 내보낸다. fixture 결과는 사람·실에이전트·실한컴 "
        "또는 사람 대체 근거로 승격할 수 없다.",
        ("run_fixture_benchmark", "export_fixture_benchmark"),
    ),
    DomainSpec(
        "read",
        "읽기·추출·변환",
        "HWPX 문서를 읽어 텍스트·구조·서식을 뽑거나 Markdown/HTML/JSON으로 변환.",
        "문서 내용을 이해·인용·재작업하거나 다른 형식으로 내보낼 때.",
        (
            "get_document_text", "get_document_info", "get_document_outline", "get_document_map",
            "get_paragraph_text", "get_paragraphs_text", "get_location_text", "get_table_text",
            "get_table_map", "find_text", "list_available_documents", "hwpx_to_markdown",
            "document_to_markdown", "hwpx_to_html", "hwpx_extract_json", "document_extract_json",
        ),
    ),
    DomainSpec(
        "form_fill",
        "양식 채움 (동적)",
        "처음 보는 실양식을 서식일치·바이트보존으로 정찰, 채움, 검증.",
        "scan_form_guidance → 사용자 승인 → apply_table_ops/apply_body_ops → residue/render 검증.",
        (
            "scan_form_guidance", "apply_table_ops", "apply_body_ops", "inspect_fill_residue",
            "verify_form_fill", "list_form_fields", "fill_form_field", "find_cell_by_label",
            "fill_by_path", "analyze_form_fill", "apply_form_fill", "analyze_template_formfit",
            "apply_template_formfit", "analyze_quality_generation", "apply_quality_generation",
            "apply_evalplan_fill", "score_form_fill",
        ),
    ),
    DomainSpec(
        "author",
        "문서 생성",
        "계획·브리프로 새 HWPX 문서를 생성.",
        "빈 문서에서 공문·보고서·제안서·비교표 등을 저작할 때.",
        (
            "create_document", "create_document_from_plan", "copy_document",
            "create_government_report_document", "create_proposal_document",
            "create_comparison_table_document", "get_document_plan_schema", "validate_document_plan",
            "analyze_document_plan", "markdown_to_document_plan", "parse_government_report_text",
            "compute_report_value", "register_template", "list_templates", "describe_template",
        ),
    ),
    DomainSpec(
        "edit",
        "편집",
        "기존 문서의 문단·그림·텍스트를 안전하게 편집.",
        "기존 문서를 고치거나 byte-preserving 수술을 적용할 때.",
        (
            "add_heading", "add_paragraph", "insert_paragraph", "delete_paragraph", "add_page_break",
            "apply_edits", "plan_edit", "preview_edit", "apply_edit", "undo_last_edit",
            "replace_by_anchor", "replace_in_paragraph", "search_and_replace", "batch_replace",
            "byte_preserving_patch", "insert_picture", "replace_picture",
        ),
    ),
    DomainSpec(
        "tables", "표 조작", "표 생성·셀 편집·병합·계산.", "표를 직접 만들거나 값을 바꿀 때.",
        ("add_table", "set_table_cell_text", "merge_table_cells", "split_table_cell", "format_table", "table_compute"),
    ),
    DomainSpec(
        "styles", "서식·스타일", "문단/런 서식과 스타일 프로파일을 편집.", "글꼴·정렬·목록·서식을 바꿀 때.",
        (
            "list_styles", "create_custom_style", "set_paragraph_format", "set_list_format",
            "format_text", "extract_style_profile", "apply_style_profile_to_plan", "compare_style_profiles",
        ),
    ),
    DomainSpec(
        "layout", "페이지·머리글·쪽번호", "페이지 레이아웃 요소를 설정.", "용지·머리글·쪽번호를 바꿀 때.",
        ("set_page_setup", "set_header_footer", "set_page_number"),
    ),
    DomainSpec(
        "toc_xref", "차례·상호참조", "한컴 네이티브 차례와 쪽 상호참조를 저작·검증.", "자동 목차/상호참조가 필요할 때.",
        ("add_toc", "add_cross_reference", "verify_toc"),
    ),
    DomainSpec("pii", "개인정보", "개인정보를 탐지·마스킹.", "배포 전 PII를 감사할 때.", ("scan_personal_info",)),
    DomainSpec("redline", "변경추적", "수락/거부 가능한 추적 변경을 저작.", "검토용 redline이 필요할 때.", ("add_tracked_edit",)),
    DomainSpec("exam", "시험지 조판", "문항 Markdown을 학교 양식에 조판.", "시험지 원안지에 문항을 앉힐 때.", ("compose_exam", "verify_question_splits")),
    DomainSpec("seal", "직인·관인", "직인을 배치하고 규정을 검사.", "공문 직인 처리에.", ("place_seal", "check_seal_compliance")),
    DomainSpec(
        "generators", "대량생산·특수 산출", "메일머지·사진대지·명패·조직도를 생성.", "반복 문서나 특수 레이아웃을 만들 때.",
        ("mail_merge", "inspect_mail_merge_placeholders", "build_image_grid", "build_meeting_nameplates", "build_organization_chart"),
    ),
    DomainSpec("memo", "메모·주석", "검토 메모를 추가·삭제.", "문서에 코멘트를 달 때.", ("add_memo", "add_memo_by_anchor", "remove_memo")),
    DomainSpec(
        "verify_quality", "검증·품질·복구", "렌더·품질·정합·복구·서버 상태를 검사.", "산출물 확언 전 검증하거나 문제를 진단할 때.",
        (
            "render_preview", "repair_hwpx", "mcp_server_health", "describe_capabilities", "doc_diff",
            "validate_structure", "lint_text_conventions", "inspect_document_quality",
            "inspect_document_authoring_quality", "inspect_operating_plan_quality",
            "inspect_official_document_style", "inspect_reference_consistency",
        ),
    ),
    DomainSpec(
        "package_io", "패키지 조회", "고급 OPC 파트와 XML 객체를 조회.", "저수준 진단이 필요할 때.",
        ("package_parts", "package_get_text", "package_get_xml", "object_find_by_attr", "object_find_by_tag"),
    ),
)


def _build_tool_specs() -> tuple[ToolSpec, ...]:
    seen: set[str] = set()
    specs: list[ToolSpec] = []
    for domain in DOMAIN_SPECS:
        for name in domain.tools:
            if name in seen:
                raise RuntimeError(f"duplicate ToolSpec name: {name}")
            seen.add(name)
            specs.append(
                ToolSpec(
                    name=name,
                    domain=domain.key,
                    profile="advanced" if name in _ADVANCED_TOOLS else "default",
                    skill_required=name in _SKILL_REQUIRED_TOOLS,
                )
            )
    if _ADVANCED_TOOLS - seen:
        raise RuntimeError(f"advanced tools absent from domains: {sorted(_ADVANCED_TOOLS - seen)}")
    if _SKILL_REQUIRED_TOOLS - seen:
        raise RuntimeError(f"required tools absent from domains: {sorted(_SKILL_REQUIRED_TOOLS - seen)}")
    return tuple(specs)


TOOL_SPECS = _build_tool_specs()


def active_tool_specs(*, advanced: bool) -> tuple[ToolSpec, ...]:
    return tuple(spec for spec in TOOL_SPECS if spec.profile == "default" or advanced)


def expected_tool_names(*, advanced: bool) -> set[str]:
    return {spec.name for spec in active_tool_specs(advanced=advanced)}


def skill_required_tool_names() -> set[str]:
    return {spec.name for spec in TOOL_SPECS if spec.skill_required}


def contract_payload() -> dict[str, Any]:
    return {
        "schemaVersion": "hwpx.tool-contract.v1",
        "minPythonHwpx": MIN_PYTHON_HWPX,
        "minMcpVersion": MIN_MCP_VERSION,
        "minSkillVersion": MIN_SKILL_VERSION,
        "tools": [
            {
                "name": spec.name,
                "domain": spec.domain,
                "profile": spec.profile,
                "skillRequired": spec.skill_required,
            }
            for spec in TOOL_SPECS
        ],
    }


def contract_hash() -> str:
    raw = json.dumps(contract_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def register_fastmcp_tools(mcp: Any, namespace: Mapping[str, Any], *, advanced: bool) -> None:
    """Register the exact active ToolSpec set from functions in *namespace*."""

    missing: list[str] = []
    for spec in active_tool_specs(advanced=advanced):
        func = namespace.get(spec.name)
        if not callable(func):
            missing.append(spec.name)
            continue
        mcp.tool(name=spec.name)(func)
    if missing:
        raise RuntimeError(f"ToolSpec functions missing from server module: {missing}")


__all__ = [
    "DOMAIN_SPECS",
    "MIN_PYTHON_HWPX",
    "MIN_MCP_VERSION",
    "MIN_SKILL_VERSION",
    "TOOL_SPECS",
    "ToolSpec",
    "active_tool_specs",
    "contract_hash",
    "contract_payload",
    "expected_tool_names",
    "register_fastmcp_tools",
    "skill_required_tool_names",
]
