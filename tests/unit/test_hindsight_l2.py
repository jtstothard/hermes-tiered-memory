"""Tests for the fail-closed concrete Hindsight L2 adapter."""

from __future__ import annotations

import json
import ssl
from datetime import datetime

import httpx
import pytest

from hermes_tiered_memory.adapters.hindsight_l2 import HindsightL2Adapter


def response(request: httpx.Request, status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=request)


@pytest.fixture
def requests() -> list[httpx.Request]:
    return []


@pytest.fixture
def adapter(requests: list[httpx.Request]) -> HindsightL2Adapter:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return response(request, 200, {"service": "healthy"})
        if request.url.path.endswith("/operations/op-1"):
            return response(request, 200, {"operation_id": "op-1", "status": "completed"})
        if request.method == "DELETE":
            return response(request, 200, {"success": True})
        return response(
            request,
            200,
            {
                "success": True,
                "bank_id": "alpha",
                "items_count": 1,
                "async": True,
                "operation_id": "op-1",
            },
        )

    return HindsightL2Adapter(
        "http://hindsight.test", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_retain_uses_stable_document_id_replace_and_encoded_envelope(adapter, requests):
    metadata = {"bank": "alpha", "origin": "mnemosyne", "revision": 2, "nested": {"x": [1, None]}}

    result = adapter.retain("doc-1", "revised synthetic content", metadata, async_op=True)

    assert result.success is True
    assert result.operation_id == "op-1"
    assert requests[-1].url.path == "/v1/default/banks/alpha/memories"
    payload = json.loads(requests[-1].content)
    assert payload["async"] is True
    assert payload["items"][0]["document_id"] == "doc-1"
    assert payload["items"][0]["update_mode"] == "replace"
    assert adapter._decode_metadata(payload["items"][0]["metadata"]) == metadata
    assert adapter.get_operation_status("op-1")["status"] == "completed"


def test_retain_sync_needs_no_operation_id(adapter):
    result = adapter.retain("doc-1", "content", {"bank": "alpha"}, async_op=False)
    assert result.success is True
    assert result.operation_id == "op-1"


def test_retain_batch_reports_per_item_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["items"][0]["document_id"] == "bad":
            return response(request, 422, {"detail": "invalid"})
        return response(
            request, 200, {"success": True, "bank_id": "alpha", "items_count": 1, "async": False}
        )

    adapter = HindsightL2Adapter(
        "http://hindsight.test", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    results = adapter.retain_batch(
        [
            ("good", "content", {"bank": "alpha"}),
            ("missing-bank", "content", {}),
            ("bad", "content", {"bank": "alpha"}),
        ],
        async_op=False,
    )

    assert [result.success for result in results] == [True, False, False]
    assert results[1].error == "L2 metadata must contain a non-empty string bank"
    assert results[2].error is not None
    assert "422" in results[2].error


def test_owned_client_uses_configured_tls_verification(monkeypatch):
    created: dict[str, object] = {}
    tls_context = object()

    class CapturingClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(httpx, "Client", CapturingClient)
    monkeypatch.setattr(ssl, "create_default_context", lambda *, cafile: tls_context)

    adapter = HindsightL2Adapter(
        "https://hindsight.test", api_key="key", ca_file="/disposable/ca.crt"
    )

    assert adapter._tls_verify is tls_context
    assert created["verify"] is tls_context
    assert created["headers"] == {"Authorization": "Bearer key"}


def test_ca_file_cannot_disable_tls_verification():
    with pytest.raises(ValueError, match="ca_file requires TLS verification"):
        HindsightL2Adapter("https://hindsight.test", verify_tls=False, ca_file="/ca.crt")


def test_recall_restores_composite_envelope_and_deduplicates_documents():
    metadata = {"bank": "alpha", "mnemosyne_revision": 2, "nested": {"preserved": True}}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/default/banks/alpha/memories/recall"
        return response(
            request,
            200,
            {
                "results": [
                    {
                        "id": "memory-1",
                        "document_id": "doc-1",
                        "text": "revised synthetic content",
                        "metadata": HindsightL2Adapter._encode_metadata(metadata),
                        "mentioned_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "id": "memory-2",
                        "document_id": "doc-1",
                        "text": "duplicate unit",
                        "metadata": HindsightL2Adapter._encode_metadata(metadata),
                    },
                ]
            },
        )

    adapter = HindsightL2Adapter(
        "http://hindsight.test", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    documents = adapter.recall("synthetic", "alpha")

    assert len(documents) == 1
    assert documents[0].document_id == "doc-1"
    assert documents[0].metadata == metadata
    assert documents[0].created_at == datetime.fromisoformat("2026-01-01T00:00:00+00:00")


def test_http_timeout_and_malformed_success_never_report_success():
    timeout_adapter = HindsightL2Adapter(
        "http://hindsight.test",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request))
            )
        ),
    )
    timeout = timeout_adapter.retain("doc", "content", {"bank": "alpha"})
    assert timeout.success is False
    assert timeout.error is not None
    assert "timeout" in timeout.error.lower()

    malformed_adapter = HindsightL2Adapter(
        "http://hindsight.test",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"not json", request=request)
            )
        ),
    )
    malformed = malformed_adapter.retain("doc", "content", {"bank": "alpha"})
    assert malformed.success is False
    assert malformed.error is not None
    assert "malformed" in malformed.error.lower()


def test_delete_health_and_close(adapter):
    assert adapter.delete_document("alpha", "doc-1") is True
    assert adapter.health() == {"status": "healthy", "response": {"service": "healthy"}}
    adapter.close()
    assert adapter.health() == {"status": "closed"}


def test_recall_rejects_lost_or_malformed_metadata_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return response(
            request, 200, {"results": [{"document_id": "doc", "text": "content", "metadata": {}}]}
        )

    adapter = HindsightL2Adapter(
        "http://hindsight.test", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(RuntimeError, match="envelope"):
        adapter.recall("content", "alpha")
