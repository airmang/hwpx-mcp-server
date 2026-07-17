# SPDX-License-Identifier: Apache-2.0
"""Explicit composition root for bounded ``HwpxOps`` services."""

from __future__ import annotations

from dataclasses import dataclass

from .content_layout import ContentLayoutService
from .context import DocumentContext
from .form_fields import FormFieldService
from .media import MediaService
from .memo_style import MemoStyleService
from .package_validation import PackageValidationService
from .planning import PlanningService
from .preview_export import PreviewExportService
from .read_query import ReadQueryService
from .save_policy import SavePolicy
from .tables import TableService
from .transactions import TransactionService


@dataclass(frozen=True, slots=True)
class HwpxOpsServices:
    context: DocumentContext
    save: SavePolicy
    transactions: TransactionService
    read_query: ReadQueryService
    content_layout: ContentLayoutService
    tables: TableService
    form_fields: FormFieldService
    media: MediaService
    memo_style: MemoStyleService
    preview_export: PreviewExportService
    package_validation: PackageValidationService
    planning: PlanningService


def build_hwpx_ops_services(context: DocumentContext) -> HwpxOpsServices:
    save = SavePolicy(context)
    transactions = TransactionService(context, save)
    memo_style = MemoStyleService(context, save)
    return HwpxOpsServices(
        context=context,
        save=save,
        transactions=transactions,
        read_query=ReadQueryService(context),
        content_layout=ContentLayoutService(context, save, transactions, memo_style),
        tables=TableService(context, save, memo_style),
        form_fields=FormFieldService(context, save, transactions),
        media=MediaService(context, save),
        memo_style=memo_style,
        preview_export=PreviewExportService(context),
        package_validation=PackageValidationService(context),
        planning=PlanningService(context),
    )
