"""Concrete HTTP adapter for the pinned Hindsight v0.8.6 API."""

from __future__ import annotations

import json
import ssl
from datetime import UTC, datetime
from typing import Any

import httpx

from hermes_tiered_memory.adapters.l2 import L2Adapter, L2Document, L2RetainResult

_METADATA_ENVELOPE_KEY = "__hermes_tiered_memory_metadata_json"


class HindsightL2Adapter(L2Adapter):
    """Fail-closed adapter for Hindsight's documented HTTP API.

    Hindsight v0.8.6 exposes metadata as string pairs.  The composite metadata
    envelope can contain nested values, integers, and nulls, so it is encoded
    into one canonical JSON string before retain and decoded on recall.  This
    keeps the upstream schema boundary explicit and preserves the envelope
    exactly rather than relying on undocumented coercion.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        api_key: str | None = None,
        verify_tls: bool = True,
        ca_file: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Hindsight base_url must be an absolute HTTP(S) URL")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not verify_tls and ca_file is not None:
            raise ValueError("ca_file requires TLS verification")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._base_url = base_url.rstrip("/")
        self._tls_verify: bool | ssl.SSLContext = (
            ssl.create_default_context(cafile=ca_file) if ca_file is not None else verify_tls
        )
        self._client = client or httpx.Client(
            base_url=self._base_url,
            timeout=timeout_seconds,
            headers=headers,
            verify=self._tls_verify,
        )
        self._owns_client = client is None
        self._closed = False
        self._operation_banks: dict[str, str] = {}

    def retain(
        self,
        document_id: str,
        content: str,
        metadata: dict[str, Any],
        async_op: bool = True,
    ) -> L2RetainResult:
        """Retain one stable document ID using Hindsight's replace semantics."""
        bank_id = self._bank_from_metadata(metadata)
        return self._retain_one(bank_id, document_id, content, metadata, async_op)

    def retain_batch(
        self,
        items: list[tuple[str, str, dict[str, Any]]],
        async_op: bool = True,
    ) -> list[L2RetainResult]:
        """Retain items independently so one failed item cannot report batch success."""
        results: list[L2RetainResult] = []
        for document_id, content, metadata in items:
            try:
                bank_id = self._bank_from_metadata(metadata)
                results.append(self._retain_one(bank_id, document_id, content, metadata, async_op))
            except (RuntimeError, ValueError) as exc:
                results.append(L2RetainResult(operation_id=None, success=False, error=str(exc)))
        return results

    def recall(self, query: str, bank_id: str, limit: int = 10) -> list[L2Document]:
        """Recall memory units and restore the composite metadata envelope."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        response = self._request(
            "POST",
            f"/v1/default/banks/{bank_id}/memories/recall",
            json={"query": query, "max_tokens": max(256, limit * 512)},
        )
        payload = self._json_object(response, "recall")
        results = payload.get("results")
        if not isinstance(results, list):
            raise RuntimeError("malformed Hindsight recall response: results must be a list")

        documents: list[L2Document] = []
        seen_ids: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                raise RuntimeError("malformed Hindsight recall response: result must be an object")
            document_id = item.get("document_id")
            content = item.get("text")
            if not isinstance(document_id, str) or not isinstance(content, str):
                raise RuntimeError(
                    "malformed Hindsight recall response: missing document_id or text"
                )
            if document_id in seen_ids:
                continue
            raw_metadata = item.get("metadata")
            metadata = self._decode_metadata(raw_metadata)
            seen_ids.add(document_id)
            mentioned_at = self._parse_datetime(item.get("mentioned_at"))
            documents.append(
                L2Document(
                    document_id=document_id,
                    content=content,
                    metadata=metadata,
                    created_at=mentioned_at,
                    updated_at=mentioned_at,
                )
            )
            if len(documents) == limit:
                break
        return documents

    def delete_document(self, bank_id: str, document_id: str) -> bool:
        """Delete a Hindsight document by bank and stable document ID."""
        response = self._request(
            "DELETE", f"/v1/default/banks/{bank_id}/documents/{document_id}", allow_statuses={404}
        )
        if response.status_code == 404:
            return True
        payload = self._json_object(response, "delete document")
        success = payload.get("success", True)
        if not isinstance(success, bool):
            raise RuntimeError("malformed Hindsight delete response: success must be a boolean")
        return success

    def get_operation_status(self, operation_id: str) -> dict[str, Any]:
        """Return the server operation status for a previously submitted async retain."""
        bank_id = self._operation_banks.get(operation_id)
        if bank_id is None:
            raise ValueError(f"unknown operation_id: {operation_id}")
        response = self._request("GET", f"/v1/default/banks/{bank_id}/operations/{operation_id}")
        payload = self._json_object(response, "operation status")
        status = payload.get("status")
        if not isinstance(status, str):
            raise RuntimeError("malformed Hindsight operation response: status must be a string")
        return payload

    def close(self) -> None:
        """Close the owned HTTP client. This operation is idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            self._client.close()

    def health(self) -> dict[str, Any]:
        """Return Hindsight's live health payload without fabricating a healthy state."""
        if self._closed:
            return {"status": "closed"}
        response = self._request("GET", "/health")
        payload = self._json_object(response, "health")
        return {"status": "healthy", "response": payload}

    def _retain_one(
        self,
        bank_id: str,
        document_id: str,
        content: str,
        metadata: dict[str, Any],
        async_op: bool,
    ) -> L2RetainResult:
        try:
            response = self._request(
                "POST",
                f"/v1/default/banks/{bank_id}/memories",
                json={
                    "async": async_op,
                    "items": [
                        {
                            "content": content,
                            "document_id": document_id,
                            "metadata": self._encode_metadata(metadata),
                            "update_mode": "replace",
                        }
                    ],
                },
            )
            payload = self._json_object(response, "retain")
            success = payload.get("success")
            if success is not True:
                raise RuntimeError("malformed Hindsight retain response: success must be true")
            operation_id = payload.get("operation_id")
            if operation_id is not None and not isinstance(operation_id, str):
                raise RuntimeError(
                    "malformed Hindsight retain response: operation_id must be a string"
                )
            if async_op and operation_id is None:
                raise RuntimeError(
                    "malformed Hindsight retain response: async retain has no operation_id"
                )
            if operation_id:
                self._operation_banks[operation_id] = bank_id
            return L2RetainResult(operation_id=operation_id, success=True)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            return L2RetainResult(operation_id=None, success=False, error=str(exc))

    def _request(
        self, method: str, path: str, *, allow_statuses: set[int] | None = None, **kwargs: Any
    ) -> httpx.Response:
        if self._closed:
            raise RuntimeError("Hindsight adapter is closed")
        try:
            response = self._client.request(method, self._base_url + path, **kwargs)
            if allow_statuses is None or response.status_code not in allow_statuses:
                response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"Hindsight timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Hindsight HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Hindsight dependency failure: {exc}") from exc

    @staticmethod
    def _bank_from_metadata(metadata: dict[str, Any]) -> str:
        bank_id = metadata.get("bank")
        if not isinstance(bank_id, str) or not bank_id:
            raise ValueError("L2 metadata must contain a non-empty string bank")
        return bank_id

    @staticmethod
    def _encode_metadata(metadata: dict[str, Any]) -> dict[str, str]:
        return {_METADATA_ENVELOPE_KEY: json.dumps(metadata, sort_keys=True, separators=(",", ":"))}

    @staticmethod
    def _decode_metadata(raw_metadata: Any) -> dict[str, Any]:
        if not isinstance(raw_metadata, dict):
            raise RuntimeError("malformed Hindsight recall response: metadata must be an object")
        encoded = raw_metadata.get(_METADATA_ENVELOPE_KEY)
        if not isinstance(encoded, str):
            raise RuntimeError("Hindsight recall result lacks the composite metadata envelope")
        try:
            metadata = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise RuntimeError("malformed composite metadata envelope from Hindsight") from exc
        if not isinstance(metadata, dict):
            raise RuntimeError("malformed composite metadata envelope from Hindsight")
        return metadata

    @staticmethod
    def _json_object(response: httpx.Response, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"malformed Hindsight {operation} response: invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"malformed Hindsight {operation} response: expected object")
        return payload

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if not isinstance(value, str):
            return datetime.now(UTC)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
