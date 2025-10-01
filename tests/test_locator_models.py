import pytest

from hwpx_mcp_server.core.plan import PlanEditInput
from hwpx_mcp_server.tools import DocumentLocatorInput


def test_document_locator_input_accepts_legacy_path() -> None:
    payload = DocumentLocatorInput.model_validate({"path": "sample.hwpx"})
    assert payload.to_hwpx_payload() == {"path": "sample.hwpx"}


def test_document_locator_input_accepts_uri_variant() -> None:
    uri = "https://example.com/sample.hwpx"
    payload = DocumentLocatorInput.model_validate({"type": "uri", "uri": uri, "backend": "http"})
    data = payload.to_hwpx_payload()
    assert data["path"] == uri


def test_document_locator_handle_requires_explicit_opt_in() -> None:
    payload = DocumentLocatorInput.model_validate({"type": "handle", "handleId": "doc-123"})
    with pytest.raises(ValueError):
        payload.to_hwpx_payload()
    assert payload.to_hwpx_payload(require_path=False) == {}


def test_plan_edit_input_surface_doc_id_for_handle() -> None:
    payload = PlanEditInput.model_validate(
        {
            "type": "handle",
            "handleId": "registered-doc",
            "operations": [
                {
                    "target": {"nodeId": "n_deadbeef"},
                    "match": "needle",
                    "replacement": "haystack",
                }
            ],
        }
    )
    assert payload.doc_id == "registered-doc"
    assert payload.to_hwpx_payload(require_path=False)["operations"]
