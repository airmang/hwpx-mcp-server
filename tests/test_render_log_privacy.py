from __future__ import annotations

import json

from hwpx_mcp_server.workflow.render_security import redact_render_log


def test_all_document_secret_path_and_nested_values_are_absent_from_render_log():
    document_canary = "비공개 학생 상담기록 900101-1234567"
    secret_canary = "super-secret-render-token"
    path_canary = "/private/school/student.hwpx"
    event = redact_render_log({
        "event": "render.completed", "jobId": "render-0001", "status": "succeeded",
        "terminalReason": "SUCCEEDED", "documentText": document_canary,
        "authorization": f"Bearer {secret_canary}", "sourcePath": path_canary,
        "nested": {"prompt": document_canary, "secret": secret_canary},
    })
    wire = json.dumps(event, ensure_ascii=False)
    assert document_canary not in wire
    assert secret_canary not in wire
    assert path_canary not in wire
    assert event["event"] == "render.completed" and event["terminalReason"] == "SUCCEEDED"


def test_free_text_in_nominal_code_fields_is_redacted():
    for key in ("event", "status", "errorCode", "terminalReason"):
        value = "문서 본문이 섞인 자유 문장"
        event = redact_render_log({key: value})
        assert event[key]["redacted"] is True
        assert value not in json.dumps(event, ensure_ascii=False)
