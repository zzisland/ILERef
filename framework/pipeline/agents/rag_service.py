from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


class RagContextError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RagContextPayload:
    instance_id: str
    smell_type: str
    test_method_source: str
    test_class_context: str
    production_context: dict[str, Any]
    rule_evidence: tuple[dict[str, Any], ...]
    context_schema_version: str
    context_source_reference: dict[str, Any]
    source_record: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_rag_context(request: dict[str, Any]) -> RagContextPayload:
    """Normalize current embedded and legacy locator-based RAG inputs once."""
    inputs = request.get("input") or {}
    instance = inputs.get("instance") or {}
    context = inputs.get("context") or {}
    instance_id = str(instance.get("instance_id") or request.get("instance_id") or "")
    smell_type = str(instance.get("smell_type") or "")
    method_source = str(instance.get("test_code") or instance.get("method_source") or "")
    if not instance_id:
        raise RagContextError("RAG_INSTANCE_ID_MISSING", "RAG input requires instance_id.")
    if not smell_type:
        raise RagContextError("RAG_SMELL_TYPE_MISSING", "RAG input requires smell_type.")
    if not method_source.strip():
        raise RagContextError("RAG_TEST_METHOD_SOURCE_MISSING", "RAG input requires test method source.")

    source_record = instance.get("source_record")
    source_reference: dict[str, Any] = {"kind": "EMBEDDED_INSTANCE_SOURCE_RECORD"}
    if not isinstance(source_record, dict) or not source_record:
        locator = context.get("context_json")
        record_index = context.get("record_index")
        if locator is not None and record_index is not None:
            try:
                records = json.loads(Path(locator).read_text(encoding="utf-8-sig"))
                index = int(record_index)
                source_record = records[index]
            except (OSError, ValueError, TypeError, IndexError, json.JSONDecodeError) as exc:
                raise RagContextError("RAG_LEGACY_CONTEXT_INVALID", f"Legacy RAG context cannot be loaded: {type(exc).__name__}: {exc}") from exc
            if not isinstance(source_record, dict):
                raise RagContextError("RAG_LEGACY_RECORD_INVALID", "Legacy RAG context record must be an object.")
            source_reference = {"kind": "LEGACY_CONTEXT_JSON", "context_json": str(Path(locator).resolve()), "record_index": index}
        else:
            source_record = {
                "schemaVersion": str(instance.get("source_schema_version") or "4.1"),
                "testContext": {
                    "testCode": method_source,
                    "testFilename": str(instance.get("test_filename") or ""),
                    "splitFilename": str(instance.get("split_filename") or ""),
                },
                "productionContext": instance.get("production_context") or {},
            }
            source_reference = {"kind": "NORMALIZED_INSTANCE_FIELDS"}

    test_context = source_record.get("testContext") if isinstance(source_record, dict) else None
    production = instance.get("production_context") or source_record.get("productionContext") or {}
    risk_result = inputs.get("risk_result") or {}
    evidence = ((risk_result.get("result") or {}).get("evidence") or [])
    class_context = str(context.get("test_class_source") or (test_context or {}).get("testClassSource") or "")
    schema_version = str(instance.get("source_schema_version") or source_record.get("schemaVersion") or "UNKNOWN")
    normalized_record = dict(source_record)
    normalized_test = dict(test_context or {})
    normalized_test["testCode"] = method_source
    normalized_record["testContext"] = normalized_test
    normalized_record["productionContext"] = production
    return RagContextPayload(
        instance_id=instance_id, smell_type=smell_type,
        test_method_source=method_source, test_class_context=class_context,
        production_context=dict(production),
        rule_evidence=tuple(dict(item) for item in evidence if isinstance(item, dict)),
        context_schema_version=schema_version,
        context_source_reference=source_reference, source_record=normalized_record,
    )


class OptionalRagService:
    def __init__(self, retriever: Callable[..., dict[str, Any]] | None = None):
        self._retriever = retriever

    def retrieve(self, *, enabled: bool, top_k: int, request: dict[str, Any],
                 required: bool = False,
                 failure_policy: str = "llm_only_fallback") -> tuple[list[dict[str, Any]], dict[str, Any]]:
        instance = (request.get("input") or {}).get("instance") or {}
        query_hash = hashlib.sha256(
            (str(instance.get("smell_type") or "") + "\n" + str(instance.get("test_code") or "")).encode("utf-8")
        ).hexdigest()
        if not enabled:
            return [], self._metadata(attempted=False, request_built=False, status="DISABLED", query_hash=None,
                                      top_k=top_k, required=required, fallback_used=False)
        try:
            payload = normalize_rag_context(request)
            retriever = self._retriever
            if retriever is None:
                from refactoring.rag_integration.knowledge_base_client import retrieve_record_from_knowledge_base
                retriever = retrieve_record_from_knowledge_base
            response = retriever(payload.source_record, payload.smell_type, top_k)
            if not isinstance(response, dict) or not isinstance(response.get("results"), list):
                raise RagContextError("RAG_RESPONSE_SCHEMA_INVALID", "RAG response results must be an array.")
            cases = [{"case_id": item.get("doc_id"), "summary": str(item.get("document") or "")[:500]}
                     for item in response["results"] if isinstance(item, dict)]
            return cases, self._metadata(
                attempted=True, request_built=True, status="RAG_SUCCEEDED" if cases else "RAG_ZERO_HIT",
                query_hash=query_hash, top_k=top_k, required=required, fallback_used=False,
                case_ids=[item["case_id"] for item in cases], retrieved_case_count=len(cases),
                retrieval_completed=True, response_schema_valid=True, context=payload,
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, RagContextError) else type(exc).__name__
            from pipeline.model_config import ModelConfigurationError
            blocked = bool(required) or failure_policy == "block" or isinstance(exc, ModelConfigurationError)
            fallback = not blocked and failure_policy == "llm_only_fallback"
            return [], self._metadata(
                attempted=True,
                request_built=not isinstance(exc, RagContextError) or code == "RAG_RESPONSE_SCHEMA_INVALID",
                status="RAG_REQUIRED_BUT_UNAVAILABLE" if blocked else "RAG_FAILED",
                query_hash=query_hash, top_k=top_k, required=required, fallback_used=fallback,
                error_type=code, error_message=__import__('pipeline.model_config', fromlist=['safe_failure']).safe_failure(exc), blocked=blocked,
            )

    @staticmethod
    def _metadata(*, attempted: bool, request_built: bool, status: str,
                  query_hash: str | None, top_k: int, required: bool,
                  fallback_used: bool, case_ids: list[Any] | None = None,
                  retrieved_case_count: int = 0, retrieval_completed: bool = False,
                  response_schema_valid: bool = False,
                  context: RagContextPayload | None = None,
                  error_type: str | None = None, error_message: str | None = None,
                  blocked: bool = False) -> dict[str, Any]:
        return {
            "attempted": attempted, "request_built": request_built,
            "retrieval_completed": retrieval_completed, "response_schema_valid": response_schema_valid,
            "used": retrieved_case_count > 0, "status": status, "query_hash": query_hash,
            "case_ids": list(case_ids or []), "retrieved_case_count": retrieved_case_count,
            "top_k": top_k, "required": required, "fallback_used": fallback_used,
            "blocked": blocked, "error_type": error_type, "error_message": error_message,
            "context_schema_version": context.context_schema_version if context else None,
            "context_source_reference": context.context_source_reference if context else None,
        }
