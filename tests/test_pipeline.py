import pytest

from hwpx_mcp_server.core.plan import PlanManager, PipelineError


def make_manager(paragraphs):
    manager = PlanManager()
    doc_id = "doc"
    manager.register_document(doc_id, "\n".join(paragraphs))
    return manager, doc_id


def make_operation(match, replacement="UPDATED", *, section=0, paragraph=0, limit=1):
    return {
        "target": {
            "section_index": section,
            "para_index": paragraph,
        },
        "match": match,
        "replacement": replacement,
        "limit": limit,
    }


def test_apply_requires_preview():
    manager, doc_id = make_manager(["alpha beta"])
    record = manager.create_plan_record(doc_id, [make_operation("alpha")], trace_id="trace-apply")
    with pytest.raises(PipelineError) as exc:
        manager.apply_plan_record(record.plan_id, confirm=True)
    assert exc.value.error_code == "PREVIEW_REQUIRED"


def test_ambiguous_target_error():
    manager, doc_id = make_manager(["target here target there"])
    record = manager.create_plan_record(
        doc_id,
        [make_operation("target", replacement="TARGET", limit=5)],
        trace_id="trace-ambiguous",
    )
    with pytest.raises(PipelineError) as exc:
        manager.preview_plan_record(record.plan_id)
    assert exc.value.error_code == "AMBIGUOUS_TARGET"
    assert exc.value.candidates


def test_limit_gate():
    manager, doc_id = make_manager(["repeat repeat repeat"])
    record = manager.create_plan_record(
        doc_id,
        [make_operation("repeat", limit=1)],
        trace_id="trace-limit",
    )
    with pytest.raises(PipelineError) as exc:
        manager.preview_plan_record(record.plan_id)
    assert exc.value.error_code == "UNSAFE_WILDCARD"


def test_idempotent_replay():
    manager, doc_id = make_manager(["replace me once"])
    record = manager.create_plan_record(doc_id, [make_operation("replace")], trace_id="trace-idem")
    preview = manager.preview_plan_record(record.plan_id)
    assert preview.plan_id == record.plan_id
    result = manager.apply_plan_record(record.plan_id, confirm=True, idempotency_key="abc")
    assert result.plan_id == record.plan_id
    with pytest.raises(PipelineError) as exc:
        manager.apply_plan_record(record.plan_id, confirm=True, idempotency_key="abc")
    assert exc.value.error_code == "IDEMPOTENT_REPLAY"


def test_atomic_rollback_preserves_document():
    manager, doc_id = make_manager(["original text"])
    record = manager.create_plan_record(doc_id, [make_operation("original")], trace_id="trace-atomic")
    manager.preview_plan_record(record.plan_id)
    manager.replace_document(doc_id, "tampered text")
    original_version = manager.doc_version(doc_id)
    with pytest.raises(PipelineError) as exc:
        manager.apply_plan_record(record.plan_id, confirm=True)
    assert exc.value.error_code == "CONFLICTING_TARGETS"
    assert manager.get_document_text(doc_id) == "tampered text"
    assert manager.doc_version(doc_id) == original_version
