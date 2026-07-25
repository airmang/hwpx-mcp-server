# SPDX-License-Identifier: Apache-2.0
"""Explicit immutable binding of every installed ToolSpec to one handler."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Callable, Mapping

from .handlers import agent_document
from .handlers import read_export
from .handlers import authoring
from .handlers import content_edit
from .handlers import layout_style
from .handlers import form_fill
from .handlers import tracked_changes
from .handlers import specialized
from .handlers import quality_render
from .handlers import workflow
from .tool_contract import TOOL_SPECS


def _build_tool_bindings() -> Mapping[str, Callable[..., Any]]:
    candidates: dict[str, Callable[..., Any]] = {
        "get_document_node": agent_document.get_document_node,
        "query_document_nodes": agent_document.query_document_nodes,
        "apply_document_commands": agent_document.apply_document_commands,
        "dump_document_blueprint": agent_document.dump_document_blueprint,
        "replay_document_blueprint": agent_document.replay_document_blueprint,
        "get_document_text": read_export.get_document_text,
        "get_document_info": read_export.get_document_info,
        "get_document_outline": read_export.get_document_outline,
        "get_document_map": read_export.get_document_map,
        "get_paragraph_text": read_export.get_paragraph_text,
        "get_paragraphs_text": read_export.get_paragraphs_text,
        "get_location_text": read_export.get_location_text,
        "get_table_text": read_export.get_table_text,
        "get_table_map": read_export.get_table_map,
        "find_text": read_export.find_text,
        "list_available_documents": read_export.list_available_documents,
        "hwpx_to_markdown": read_export.hwpx_to_markdown,
        "document_to_markdown": read_export.document_to_markdown,
        "hwpx_to_html": read_export.hwpx_to_html,
        "hwpx_extract_json": read_export.hwpx_extract_json,
        "document_extract_json": read_export.document_extract_json,
        "package_parts": read_export.package_parts,
        "package_get_text": read_export.package_get_text,
        "package_get_xml": read_export.package_get_xml,
        "object_find_by_attr": read_export.object_find_by_attr,
        "object_find_by_tag": read_export.object_find_by_tag,
        "create_document": authoring.create_document,
        "create_document_from_plan": authoring.create_document_from_plan,
        "copy_document": authoring.copy_document,
        "create_government_report_document": authoring.create_government_report_document,
        "create_proposal_document": authoring.create_proposal_document,
        "create_comparison_table_document": authoring.create_comparison_table_document,
        "get_document_plan_schema": authoring.get_document_plan_schema,
        "validate_document_plan": authoring.validate_document_plan,
        "analyze_document_plan": authoring.analyze_document_plan,
        "markdown_to_document_plan": authoring.markdown_to_document_plan,
        "parse_government_report_text": authoring.parse_government_report_text,
        "compute_report_value": authoring.compute_report_value,
        "register_template": authoring.register_template,
        "list_templates": authoring.list_templates,
        "describe_template": authoring.describe_template,
        "add_heading": content_edit.add_heading,
        "add_paragraph": content_edit.add_paragraph,
        "insert_paragraph": content_edit.insert_paragraph,
        "delete_paragraph": content_edit.delete_paragraph,
        "add_page_break": content_edit.add_page_break,
        "apply_edits": content_edit.apply_edits,
        "undo_last_edit": content_edit.undo_last_edit,
        "replace_by_anchor": content_edit.replace_by_anchor,
        "replace_in_paragraph": content_edit.replace_in_paragraph,
        "search_and_replace": content_edit.search_and_replace,
        "batch_replace": content_edit.batch_replace,
        "byte_preserving_patch": content_edit.byte_preserving_patch,
        "insert_picture": content_edit.insert_picture,
        "replace_picture": content_edit.replace_picture,
        "add_table": content_edit.add_table,
        "set_table_cell_text": content_edit.set_table_cell_text,
        "merge_table_cells": content_edit.merge_table_cells,
        "split_table_cell": content_edit.split_table_cell,
        "format_table": content_edit.format_table,
        "table_compute": content_edit.table_compute,
        "list_styles": layout_style.list_styles,
        "create_custom_style": layout_style.create_custom_style,
        "set_paragraph_format": layout_style.set_paragraph_format,
        "set_list_format": layout_style.set_list_format,
        "format_text": layout_style.format_text,
        "extract_style_profile": layout_style.extract_style_profile,
        "apply_style_profile_to_plan": layout_style.apply_style_profile_to_plan,
        "compare_style_profiles": layout_style.compare_style_profiles,
        "set_page_setup": layout_style.set_page_setup,
        "set_header_footer": layout_style.set_header_footer,
        "set_page_number": layout_style.set_page_number,
        "add_toc": layout_style.add_toc,
        "add_cross_reference": layout_style.add_cross_reference,
        "verify_toc": layout_style.verify_toc,
        "add_memo": layout_style.add_memo,
        "add_memo_by_anchor": layout_style.add_memo_by_anchor,
        "remove_memo": layout_style.remove_memo,
        "scan_form_guidance": form_fill.scan_form_guidance,
        "apply_table_ops": form_fill.apply_table_ops,
        "apply_body_ops": form_fill.apply_body_ops,
        "inspect_fill_residue": form_fill.inspect_fill_residue,
        "verify_form_fill": form_fill.verify_form_fill,
        "list_form_fields": form_fill.list_form_fields,
        "fill_form_field": form_fill.fill_form_field,
        "find_cell_by_label": form_fill.find_cell_by_label,
        "fill_by_path": form_fill.fill_by_path,
        "analyze_form_fill": form_fill.analyze_form_fill,
        "apply_form_fill": form_fill.apply_form_fill,
        "analyze_template_formfit": form_fill.analyze_template_formfit,
        "apply_template_formfit": form_fill.apply_template_formfit,
        "apply_evalplan_fill": form_fill.apply_evalplan_fill,
        "score_form_fill": form_fill.score_form_fill,
        "add_tracked_edit": tracked_changes.add_tracked_edit,
        "scan_personal_info": specialized.scan_personal_info,
        "compose_exam": specialized.compose_exam,
        "verify_question_splits": specialized.verify_question_splits,
        "place_seal": specialized.place_seal,
        "check_seal_compliance": specialized.check_seal_compliance,
        "mail_merge": specialized.mail_merge,
        "inspect_mail_merge_placeholders": specialized.inspect_mail_merge_placeholders,
        "build_image_grid": specialized.build_image_grid,
        "build_meeting_nameplates": specialized.build_meeting_nameplates,
        "build_organization_chart": specialized.build_organization_chart,
        "render_submit": quality_render.render_submit,
        "render_status": quality_render.render_status,
        "render_cancel": quality_render.render_cancel,
        "render_health": quality_render.render_health,
        "render_preview": quality_render.render_preview,
        "repair_hwpx": quality_render.repair_hwpx,
        "mcp_server_health": quality_render.mcp_server_health,
        "describe_capabilities": quality_render.describe_capabilities,
        "doc_diff": quality_render.doc_diff,
        "validate_structure": quality_render.validate_structure,
        "lint_text_conventions": quality_render.lint_text_conventions,
        "inspect_document_quality": quality_render.inspect_document_quality,
        "inspect_document_authoring_quality": quality_render.inspect_document_authoring_quality,
        "inspect_operating_plan_quality": quality_render.inspect_operating_plan_quality,
        "inspect_official_document_style": quality_render.inspect_official_document_style,
        "inspect_reference_consistency": quality_render.inspect_reference_consistency,
        "start_workflow": workflow.start_workflow,
        "get_workflow": workflow.get_workflow,
        "get_workflow_result": workflow.get_workflow_result,
        "continue_workflow": workflow.continue_workflow,
        "approve_workflow_decision": workflow.approve_workflow_decision,
        "cancel_workflow": workflow.cancel_workflow,
        "resume_workflow": workflow.resume_workflow,
    }
    expected = tuple(spec.callable_name for spec in TOOL_SPECS)
    expected_names = tuple(name for name in expected if name is not None)
    missing = [name for name in expected_names if name not in candidates]
    unexpected = sorted(set(candidates) - set(expected_names))
    noncallable = sorted(
        name for name, value in candidates.items() if not callable(value)
    )
    if missing or unexpected or noncallable:
        raise RuntimeError(
            "explicit ToolSpec binding mismatch: "
            f"missing={missing}, unexpected={unexpected}, noncallable={noncallable}"
        )
    return MappingProxyType({name: candidates[name] for name in expected_names})


TOOL_BINDINGS = _build_tool_bindings()


__all__ = ["TOOL_BINDINGS"]
