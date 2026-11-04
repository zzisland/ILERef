from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from refactoring.smell_name_normalization import to_knowledge_base_smell


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWLEDGE_BASE = PROJECT_ROOT / "test_refactoring_knowledge_base"

def index_smell_name(test_smell: str) -> str:
    """Translate pipeline canonical names with the shared reviewed-index vocabulary."""
    return to_knowledge_base_smell(test_smell)


def retrieve_from_knowledge_base(
    context_json: Path,
    index: int,
    test_smell: str,
    top_k: int,
    knowledge_base_dir: Path = DEFAULT_KNOWLEDGE_BASE,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Query the standalone reviewed-case knowledge base without modifying it."""
    records = _read_json(Path(context_json).resolve())
    if not isinstance(records, list) or index < 0 or index >= len(records):
        raise IndexError(f"Context record index is out of range: {index}")
    record = records[index]
    payload = retrieve_record_from_knowledge_base(
        record, test_smell, top_k,
        knowledge_base_dir=knowledge_base_dir,
        context_source={"context_json": str(Path(context_json).resolve()), "index": index},
    )
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def retrieve_record_from_knowledge_base(
    record: dict[str, Any],
    test_smell: str,
    top_k: int,
    knowledge_base_dir: Path = DEFAULT_KNOWLEDGE_BASE,
    *,
    context_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Query from one normalized record without requiring a context file locator."""
    if not isinstance(record, dict):
        raise TypeError("Normalized RAG context record must be an object.")
    knowledge_base_dir = Path(knowledge_base_dir).resolve()
    kb_config = _read_json(knowledge_base_dir / "config" / "knowledge_base_config.json")
    embedding_config = _read_json(_resolve(knowledge_base_dir, kb_config["embedding_config_path"]))

    index_filter = index_smell_name(test_smell)
    query_text = build_query_text(record, index_filter)
    query_embedding = _embed_query(query_text, embedding_config, index_context=(knowledge_base_dir, kb_config))
    results = _query_chroma(
        query_embedding=query_embedding,
        test_smell=index_filter,
        top_k=top_k,
        db_dir=_resolve(knowledge_base_dir, kb_config["vector_db_path"]),
        collection_name=kb_config["collection_name"],
        metadata_field=kb_config["metadata_smell_field"],
        documents_path=_resolve(knowledge_base_dir, kb_config["documents_path"]),
    )
    payload = {
        "query": {
            "context_source": context_source or {"kind": "NORMALIZED_RECORD"},
            "test_smell": test_smell,
            "index_smell_filter": index_filter,
            "top_k": top_k,
            "knowledge_base": str(knowledge_base_dir),
            "collection": kb_config["collection_name"],
            "metadata_field": kb_config["metadata_smell_field"],
        },
        "results": results,
    }
    return payload


def build_query_text(record: dict[str, Any], test_smell: str) -> str:
    test_context = record.get("testContext") or record
    production_context = record.get("productionContext") or {}
    return "\n".join([
        "retrieve similar reviewed before-after Java test refactoring cases",
        f"test_smell: {test_smell}",
        f"test_filename: {test_context.get('testFilename', '')}",
        f"split_filename: {test_context.get('splitFilename', '')}",
        "test_code:",
        str(test_context.get("testCode", "")),
        "production_class:",
        str(production_context.get("productionClassName", "")),
        "focal_method_signatures:",
        "\n".join(production_context.get("focalMethodSignatures") or []),
    ])


def verify_existing_collection(root, kb_config, embedding_config):
    """Check the persisted vector shape before spending an embedding request."""
    from pipeline.model_config import ModelConfigurationError, resolve_role, validate_rag_compatibility
    effective = resolve_role('rag', legacy_embedding=embedding_config)
    validate_rag_compatibility(effective, embedding_config)
    db_dir = _resolve(Path(root), kb_config['vector_db_path'])
    if not db_dir.is_dir():
        raise ModelConfigurationError('rag: existing vector database is missing; automatic creation is forbidden.')
    import chromadb
    collection = chromadb.PersistentClient(path=str(db_dir)).get_collection(kb_config['collection_name'])
    metadata = collection.metadata or {}
    for key in ('model_name', 'embedding_model', 'model'):
        if key in metadata and metadata[key] != effective.model:
            raise ModelConfigurationError('rag: persisted collection model does not match query model.')
    rows = collection.peek(limit=1).get('embeddings')
    if rows is None or len(rows) != 1 or len(rows[0]) != effective.dimensions:
        raise ModelConfigurationError('rag: existing vector dimensions are missing or incompatible; no automatic rebuild.')


def _query_chroma(
    query_embedding,
    test_smell,
    top_k,
    db_dir,
    collection_name,
    metadata_field,
    documents_path,
):
    import chromadb

    client = chromadb.PersistentClient(path=str(db_dir))
    # get_collection is intentionally read-only; never create or rebuild the user's KB.
    collection = client.get_collection(collection_name)
    query_result = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where={metadata_field: test_smell},
        include=["documents", "metadatas", "distances"],
    )
    documents_by_id = {
        document["doc_id"]: document
        for document in _read_json(documents_path)
    }
    ids = _first(query_result.get("ids"))
    documents = _first(query_result.get("documents"))
    metadatas = _first(query_result.get("metadatas"))
    distances = _first(query_result.get("distances"))
    results = []
    for position, doc_id in enumerate(ids):
        source = documents_by_id.get(doc_id, {})
        results.append({
            "rank": position + 1,
            "doc_id": doc_id,
            "distance": distances[position] if position < len(distances) else None,
            "metadata": metadatas[position] if position < len(metadatas) else {},
            "document": documents[position] if position < len(documents) else "",
            "prompt_payload": source.get("prompt_payload") or {},
        })
    return results


def _embed_query(text: str, config: dict[str, Any], *, index_context=None) -> list[float]:
    if index_context is not None:
        verify_existing_collection(*index_context, config)
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The OpenAI SDK is required for knowledge-base embeddings.") from exc

    from pipeline.model_config import resolve_role, validate_rag_compatibility
    effective = resolve_role('rag', legacy_embedding=config, require=True)
    validate_rag_compatibility(effective, config)
    from pipeline.resume_transport import ACTIVE
    journal = ACTIVE.get()
    client = OpenAI(api_key=effective.api_key, base_url=effective.base_url,
                    timeout=effective.timeout, max_retries=0 if journal is not None else effective.max_retries)
    request = {'model': effective.model, 'input': [text], 'dimensions': effective.dimensions}
    def invoke():
        response = client.embeddings.create(**request)
        return [float(value) for value in response.data[0].embedding]
    vector = journal.invoke_with_retries('rag_embedding', {'request': request, 'model_identity': effective.identity()},
                                         invoke, effective.max_retries) if journal is not None else invoke()
    if len(vector) != effective.dimensions or not all(math.isfinite(x) for x in vector):
        raise ValueError('Embedding response dimension or numeric values do not match the index.')
    if config.get("normalize_embeddings", False):
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
    return vector


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


def _first(value):
    return value[0] if value else []
