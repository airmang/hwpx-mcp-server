# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..storage import LocalDocumentStorage
from ..workspace import (
    WorkspaceMissingParentGuard,
    WorkspaceOutputGuard,
)

from .context import DocumentContext
from .save_policy import SavePolicy
from .transactions import TransactionService

logger = logging.getLogger("hwpx_mcp_server.hwpx_ops")


class FormFieldService:
    def __init__(
        self,
        context: DocumentContext,
        save: SavePolicy,
        transactions: TransactionService,
    ) -> None:
        self._context = context
        self._save = save
        self._transactions = transactions

    def list_form_fields(
        self,
        path: str,
    ) -> Dict[str, Any]:
        document, _resolved = self._context._open_document(path)
        fields = document.list_form_fields()
        return {
            "fieldCount": len(fields),
            "fields": fields,
            "fallback": "table-label" if not fields else None,
        }

    def fill_form_field(
        self,
        path: str,
        *,
        value: str,
        field_index: Optional[int] = None,
        field_id: Optional[str] = None,
        name: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        document, resolved = self._context._open_document(path)
        result = document.fill_form_field(
            value,
            field_index=field_index,
            field_id=field_id,
            name=name,
        )
        result.update({"ok": True, "filename": path})
        return self._transactions._with_transaction_verification(
            result, document, resolved, dry_run=dry_run
        )

    def apply_table_ops(
        self,
        path: str,
        ops: Sequence[Dict[str, Any]],
        *,
        output: Optional[str] = None,
        render_check: str = "off",
        dry_run: bool = False,
        output_guard: (
            WorkspaceOutputGuard | WorkspaceMissingParentGuard | None
        ) = None,
        publication_sink: Callable[[WorkspaceOutputGuard], None] | None = None,
    ) -> Dict[str, Any]:
        """Byte-preserving structural form-fill: apply cell fills + table structure
        ops (delete_column/row/table, insert_row_by_clone, insert_block_by_clone)
        preserving every untouched byte. Cells/tables may be addressed by
        tableAnchor/cellAnchor (unique-or-skip) as well as index. Optional
        real-Hancom render gate.

        dryRun=true: identical pipeline (resolution/validation/fail-closed all
        real) but writes NOTHING — returns transcript (per-op resolution + before/
        after dims) and applied old→new texts as approval evidence for the user
        consult loop. renderCheck still works on the would-be bytes."""
        try:
            from hwpx.table_patch import apply_table_ops as _apply
        except Exception as exc:  # pragma: no cover - dependency compatibility
            raise self._context._new_error(
                "TABLE_OPS_UNAVAILABLE",
                "installed python-hwpx does not provide hwpx.table_patch.apply_table_ops",
            ) from exc

        source_path = self._context._resolve_path(path)
        output_precondition = None
        if isinstance(self._context.storage, LocalDocumentStorage):
            output_precondition = (
                output_guard
                or self._context.storage.capture_output_precondition(
                    output if output else source_path
                )
            )
            target_path = output_precondition.path
        else:
            target_path = (
                self._context._resolve_path(output, must_exist=False)
                if output and dry_run
                else self._context._resolve_output_path(output)
                if output
                else source_path
            )
        result = _apply(source_path, list(ops), dry_run=dry_run)
        payload = result.to_dict()
        if dry_run:
            payload["dryRun"] = True
            payload["outputPath"] = None
        else:
            payload["outputPath"] = str(target_path)

        if render_check and render_check != "off":
            try:
                from hwpx.table_patch import verify_fill

                report = verify_fill(
                    source_path, result.data, require=(render_check == "required")
                )
                payload["renderVerdict"] = {
                    "renderChecked": report.render_checked,
                    "ok": report.ok,
                    "overflowDetected": report.overflow_detected,
                    "overlapDetected": report.overlap_detected,
                    "pageCountChanged": report.page_count_changed,
                    "warnings": list(report.warnings),
                    "errors": list(report.errors),
                }
                if report.render_checked and not report.ok:
                    payload["ok"] = False
                    if render_check == "required":
                        raise self._context._new_error(
                            "RENDER_CHECK_REQUIRED_FAILED",
                            "required render detected overflow, overlap, or layout regression",
                        )
            except Exception as exc:
                if render_check == "required":
                    raise self._context._new_error(
                        "RENDER_CHECK_REQUIRED_FAILED", str(exc)
                    ) from exc
                payload["renderVerdict"] = {"renderChecked": False, "note": str(exc)}
        # A required render is a pre-publication gate: only replace the
        # destination after the candidate has passed it.
        if (
            payload.get("ok") is not False
            and not dry_run
            and (
                not result.byte_identical
                or target_path.resolve(strict=False)
                != source_path.resolve(strict=False)
            )
        ):
            payload = self._save._write_patched(
                target_path,
                result.data,
                payload,
                output_guard=output_guard,
                output_precondition=output_precondition,
                publication_sink=publication_sink,
            )
        return payload

    def verify_form_fill(
        self,
        path: str,
        before_path: str,
        *,
        require: bool = False,
    ) -> Dict[str, Any]:
        """Render before/after in REAL Hancom and judge overflow/overlap/layout.
        Honest degrade (renderChecked=false) with no oracle unless require=true."""
        try:
            from hwpx.table_patch import verify_fill
        except Exception as exc:  # pragma: no cover - dependency compatibility
            raise self._context._new_error(
                "VERIFY_UNAVAILABLE",
                "installed python-hwpx does not provide hwpx.table_patch.verify_fill",
            ) from exc
        after = self._context._resolve_path(path)
        before = self._context._resolve_path(before_path)
        report = verify_fill(before, after, require=require)
        return {
            "renderChecked": report.render_checked,
            "ok": report.ok,
            "overflowDetected": report.overflow_detected,
            "overlapDetected": report.overlap_detected,
            "pageCountChanged": report.page_count_changed,
            "warnings": list(report.warnings),
            "errors": list(report.errors),
        }

    def score_form_fill(
        self,
        path: str,
        gold_path: str,
        blank_path: str,
        *,
        run_render: bool = True,
        expected_pages: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Score a filled evaluation-plan form against a gold reference on 5 axes
        (A render cleanliness / B byte fidelity vs blank / C structure vs gold /
        D content completeness / E compliance) -> weighted 0-100 + per-axis gap
        report. The fitness function of the form-fill quality loop.

        ``path`` = produced fill, ``goldPath`` = accepted reference of the same
        form family, ``blankPath`` = the empty province form. A requires a real
        Hancom render (renderCheck); with no oracle A is ``unverified`` (never a
        silent pass). Set runRender=false for a fast structural-only pass."""
        try:
            from hwpx.formfill_quality import score_form_fill as _score
        except Exception as exc:  # pragma: no cover - dependency compatibility
            raise self._context._new_error(
                "SCORE_UNAVAILABLE",
                "installed python-hwpx does not provide hwpx.formfill_quality.score_form_fill",
            ) from exc
        produced = self._context._resolve_path(path)
        gold = self._context._resolve_path(gold_path)
        blank = self._context._resolve_path(blank_path)
        card = _score(
            produced,
            gold,
            blank,
            run_render=run_render,
            expected_pages=expected_pages,
        )
        return card.to_dict()

    def apply_body_ops(
        self,
        path: str,
        ops: Sequence[Dict[str, Any]],
        *,
        output: Optional[str] = None,
        dry_run: bool = False,
        output_guard: (
            WorkspaceOutputGuard | WorkspaceMissingParentGuard | None
        ) = None,
        publication_sink: Callable[[WorkspaceOutputGuard], None] | None = None,
    ) -> Dict[str, Any]:
        """Byte-preserving BODY(표 밖 직속 문단) ops — Stage 2 결정표의 본문 어휘.

        ops: replace_text{find,replace,count=1: <hp:t> 텍스트 안에서만, 개수 불일치
        refuse} · delete_paragraph{index: 표 품은 문단 refuse} ·
        insert_paragraph_by_clone{ref_index,count,texts?: 참조 문단 서식 verbatim
        상속} · reorder_paragraphs{start,end,order}. index는 op 실행 시점 기준.
        dryRun=true면 아무것도 쓰지 않고 transcript만(승인 근거)."""
        try:
            from hwpx.body_patch import apply_body_ops as _apply
        except Exception as exc:  # pragma: no cover - dependency compatibility
            raise self._context._new_error(
                "BODY_OPS_UNAVAILABLE",
                "installed python-hwpx does not provide hwpx.body_patch.apply_body_ops",
            ) from exc
        source_path = self._context._resolve_path(path)
        output_precondition = None
        if isinstance(self._context.storage, LocalDocumentStorage):
            output_precondition = (
                output_guard
                or self._context.storage.capture_output_precondition(
                    output if output else source_path
                )
            )
            target_path = output_precondition.path
        else:
            target_path = (
                self._context._resolve_path(output, must_exist=False)
                if output and dry_run
                else self._context._resolve_output_path(output)
                if output
                else source_path
            )
        result = _apply(source_path, list(ops), dry_run=dry_run)
        payload = result.to_dict()
        if dry_run:
            payload["dryRun"] = True
            payload["outputPath"] = None
        else:
            payload["outputPath"] = str(target_path)
            if not result.byte_identical or target_path.resolve(
                strict=False
            ) != source_path.resolve(strict=False):
                payload = self._save._write_patched(
                    target_path,
                    result.data,
                    payload,
                    output_guard=output_guard,
                    output_precondition=output_precondition,
                    publication_sink=publication_sink,
                )
        return payload

    def inspect_fill_residue(
        self,
        path: str,
        *,
        blank_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """채움본 잔존물 zero-체크(비변형) — 제출 전 기계 게이트(Stage 3).

        blank를 주면 범례 기반 신호까지: 삭제색 잔존·미수정 샘플(수정색이 blank와
        동일=prose 샘플 미교체) = ERROR. placeholder ◯◯◯/□□□=ERROR, **=각주와
        중의적이라 needs_review, 고아 마커=needs_review. ok=true는 필요조건일 뿐 —
        제출 확언은 렌더 PDF를 사람이 전 페이지 본 뒤에만."""
        try:
            from hwpx.fill_residue import inspect_fill_residue as _inspect
        except Exception as exc:  # pragma: no cover - dependency compatibility
            raise self._context._new_error(
                "FILL_RESIDUE_UNAVAILABLE",
                "installed python-hwpx does not provide hwpx.fill_residue",
            ) from exc
        produced = self._context._resolve_path(path)
        blank = self._context._resolve_path(blank_path) if blank_path else None
        return _inspect(produced, blank=blank).to_dict()

    def scan_form_guidance(self, path: str, *, max_items: int = 60) -> Dict[str, Any]:
        """Recon an unfamiliar form (NON-MUTATING) — universal form-fill Stage 1.

        Walks every run INCLUDING table-cell interiors and table captions, parses
        the form's own colour legend (e.g. "검정 유지/파랑 수정/빨강 삭제"), and
        reports candidates with table_patch-compatible addresses: delete candidates
        (legend-delete colour + guidance keywords), modify targets, placeholder
        tokens (◯◯◯/**/□□□), conditional-choice blocks, empty cells with neighbour
        label + charPr format context, and an honest question list. Candidates are
        proposals — destructive ops still require user approval."""
        try:
            from hwpx.guidance_scan import scan_form_guidance as _scan
        except Exception as exc:  # pragma: no cover - dependency compatibility
            raise self._context._new_error(
                "GUIDANCE_SCAN_UNAVAILABLE",
                "installed python-hwpx does not provide hwpx.guidance_scan",
            ) from exc
        resolved = self._context._resolve_path(path)
        report = _scan(resolved)
        limit = max(1, int(max_items))

        def _cand(c) -> Dict[str, Any]:
            cell = None
            if c.cell is not None:
                cell = {
                    "tableIndex": c.cell.table_index,
                    "row": c.cell.row,
                    "col": c.cell.col,
                }
            return {
                "location": c.location,
                "signals": c.signals,
                "confidence": c.confidence,
                "textPreview": c.text_preview,
                "cell": cell,
            }

        def _cap(items) -> List[Dict[str, Any]]:
            return [_cand(c) for c in items[:limit]]

        return {
            "legend": [
                {
                    "colorWord": b.color_word,
                    "family": b.family,
                    "exactHex": b.exact_hex,
                    "action": b.action,
                    "sourceText": b.source_text,
                }
                for b in report.legend
            ],
            "colorInventory": report.color_inventory,
            "deleteCandidates": _cap(report.delete_candidates),
            "deleteCandidatesTotal": len(report.delete_candidates),
            "modifyCandidatesByTable": report.modify_candidates_by_table,
            "emptyCellCandidates": _cap(report.empty_cell_candidates),
            "emptyCellTotal": len(report.empty_cell_candidates),
            "placeholderCandidates": _cap(report.placeholder_candidates),
            "conditionalChoices": _cap(report.conditional_choices),
            "questions": report.questions,
            "stats": report.stats,
            "limitations": report.limitations,
            "markdownReport": report.to_markdown(),
        }

    def apply_evalplan_fill(
        self,
        path: str,
        review_md: str,
        *,
        output: Optional[str] = None,
        render_check: str = "off",
        score_gold_path: Optional[str] = None,
        expected_pages: Optional[int] = None,
        output_guard: (
            WorkspaceOutputGuard | WorkspaceMissingParentGuard | None
        ) = None,
        publication_sink: Callable[[WorkspaceOutputGuard], None] | None = None,
    ) -> Dict[str, Any]:
        """Whole-form 평가계획 fill: {blank province form + review markdown} ->
        byte-preserving gold-quality 채움본 in ONE call. Runs the structure-driven
        recipe (delete red/optional tables + 정기시험 column, fill 운영계획/성취기준/
        성취수준/성취율/반영비율/rubrics incl. 채점기준 ladder/prose sections),
        preserving the blank's formatting (never regenerates). Works on BOTH the
        2015-개정 and 2022-개정 form families (auto-detected from the blank + review).

        ``path`` = blank form, ``reviewMd`` = the structured review markdown
        (Ⅰ 운영계획 + [1]~[11]). Returns the produced path + per-region contentReport
        with rubricNeedsReview (honest-defer count, never silent). Set
        renderCheck='required' to gate on a real Hancom render; pass scoreGoldPath
        (an accepted form of the same family) to also return the 5-axis scorecard."""
        try:
            from hwpx.evalplan_fill import (
                parse_review_file,
                fill_evalplan,
                expected_skeleton,
            )
        except Exception as exc:  # pragma: no cover - dependency compatibility
            raise self._context._new_error(
                "EVALPLAN_FILL_UNAVAILABLE",
                "installed python-hwpx does not provide hwpx.evalplan_fill.fill_evalplan",
            ) from exc

        blank = self._context._resolve_path(path)
        md = self._context._resolve_path(review_md)
        output_precondition = None
        if isinstance(self._context.storage, LocalDocumentStorage):
            output_precondition = (
                output_guard
                or self._context.storage.capture_output_precondition(
                    output if output else blank
                )
            )
            target_path = output_precondition.path
        else:
            target_path = (
                self._context._resolve_output_path(output) if output else blank
            )
        content = parse_review_file(md)
        res = fill_evalplan(blank, content, phase="all")
        data = res["_data"]

        report = res.get("content_report", {})
        rubric_nr = [
            s
            for s in report.get("rubrics", {}).get("skipped", [])
            if "NEEDS_REVIEW" in s
        ]
        payload: Dict[str, Any] = {
            "ok": bool(res.get("ok")),
            "outputPath": str(target_path),
            "byteIdentical": bool(res.get("byteIdentical")),
            "transcript": res.get("transcript", []),
            "expectedSkeleton": res.get("expected_skeleton"),
            "contentReport": report,
            "rubricNeedsReview": len(rubric_nr),
            "needsReviewNotes": rubric_nr,
            "changedParts": res.get("changedParts", []),
            "skipped": res.get("skipped", []),
        }
        if render_check and render_check != "off":
            try:
                from hwpx.table_patch import verify_fill

                verdict = verify_fill(blank, data, require=(render_check == "required"))
                payload["renderVerdict"] = {
                    "renderChecked": verdict.render_checked,
                    "ok": verdict.ok,
                    "overflowDetected": verdict.overflow_detected,
                    "overlapDetected": verdict.overlap_detected,
                    "pageCountChanged": verdict.page_count_changed,
                    "warnings": list(verdict.warnings),
                    "errors": list(verdict.errors),
                }
                if verdict.render_checked and not verdict.ok:
                    payload["ok"] = False
                    if render_check == "required":
                        raise self._context._new_error(
                            "RENDER_CHECK_REQUIRED_FAILED",
                            "required evalplan render detected overflow, overlap, or layout regression",
                        )
            except Exception as exc:
                if render_check == "required":
                    raise self._context._new_error(
                        "RENDER_CHECK_REQUIRED_FAILED", str(exc)
                    ) from exc
                payload["renderVerdict"] = {"renderChecked": False, "note": str(exc)}

        # Do not publish a partial/failed domain result. Required rendering is
        # evaluated against candidate bytes before the atomic destination swap.
        if payload["ok"] and (
            not res.get("byteIdentical", True)
            or target_path.resolve(strict=False) != blank.resolve(strict=False)
        ):
            payload = self._save._write_patched(
                target_path,
                data,
                payload,
                output_guard=output_guard,
                output_precondition=output_precondition,
                publication_sink=publication_sink,
            )

        if score_gold_path:
            try:
                from hwpx.formfill_quality import score_form_fill as _score

                card = _score(
                    target_path,
                    self._context._resolve_path(score_gold_path),
                    blank,
                    content=md,
                    expected_skeleton=expected_skeleton(content, blank),
                    run_render=(render_check and render_check != "off"),
                    expected_pages=expected_pages,
                )
                payload["scorecard"] = card.to_dict()
            except Exception as exc:  # pragma: no cover - scoring optional
                payload["scorecard"] = {"error": str(exc)}
        return payload
