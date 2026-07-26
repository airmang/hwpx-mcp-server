# SPDX-License-Identifier: Apache-2.0
"""Stable Python facade for HWPX task automation.

The base distribution deliberately does not import the optional MCP SDK.  MCP
transport users install ``python-hwpx-automation[mcp]`` and start
``hwpx-automation-mcp``; Python callers can use the curated task facade below.
"""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Static re-exports give installed-package type checkers the same curated
    # signatures as ``hwpx_automation.api``.  Runtime access remains lazy so a
    # base import does not pull in the task graph or the optional MCP adapter.
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

_CANONICAL_DISTRIBUTION = "python-hwpx-automation"


def _resolve_version() -> str:
    try:
        return version(_CANONICAL_DISTRIBUTION)
    except PackageNotFoundError:
        return "0+unknown"  # 체크아웃에서 직접 실행하는 개발 경로


__version__ = _resolve_version()

_API_EXPORTS = {
    "CanonicalFormFillInput",
    "ComposeResult",
    "DocumentPlan",
    "DocumentStylePreset",
    "EvalPlanContent",
    "FormFillAnalyzeOptions",
    "FormFillPlanInput",
    "Rubric",
    "analyze_form_fill",
    "apply_form_fill",
    "compose_exam",
    "create_document_from_plan",
    "fill_evalplan",
    "finalize_evalplan",
    "normalize_document_plan",
    "parse_evalplan_review",
    "parse_exam_markdown",
    "validate_document_plan",
}


def __getattr__(name: str) -> Any:
    """Load the task facade on first use without creating package cycles."""

    if name not in _API_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.api"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _API_EXPORTS)


__all__ = ["__version__", *sorted(_API_EXPORTS)]
