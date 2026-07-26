"""Typed surface for the lazy :mod:`hwpx_automation` package facade."""

from .api import (
    CanonicalFormFillInput as CanonicalFormFillInput,
    ComposeResult as ComposeResult,
    DocumentPlan as DocumentPlan,
    DocumentStylePreset as DocumentStylePreset,
    EvalPlanContent as EvalPlanContent,
    FormFillAnalyzeOptions as FormFillAnalyzeOptions,
    FormFillPlanInput as FormFillPlanInput,
    Rubric as Rubric,
    analyze_form_fill as analyze_form_fill,
    apply_form_fill as apply_form_fill,
    compose_exam as compose_exam,
    create_document_from_plan as create_document_from_plan,
    fill_evalplan as fill_evalplan,
    finalize_evalplan as finalize_evalplan,
    normalize_document_plan as normalize_document_plan,
    parse_evalplan_review as parse_evalplan_review,
    parse_exam_markdown as parse_exam_markdown,
    validate_document_plan as validate_document_plan,
)

__version__: str
__all__: list[str]

def __dir__() -> list[str]: ...
