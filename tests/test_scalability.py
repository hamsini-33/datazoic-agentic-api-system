import time
import tempfile
import os
import gc
import hashlib
import json
import math
import re
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
import pytest
from huggingface_hub import snapshot_download
from huggingface_hub.errors import LocalEntryNotFoundError
from sentence_transformers import SentenceTransformer
from src.registry.models import ToolMetadata, ToolCategory, HTTPMethod
from src.registry.db import ToolRegistryDB
from src.retrieval.embeddings import VectorStoreManager, EmbeddingManager
from src.registry.retriever import SemanticToolRetriever


_SEMANTIC_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
_SEMANTIC_MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
_PINNED_RETRIEVAL_DEPENDENCIES = {
    "chromadb": "1.5.9",
    "sentence-transformers": "6.2.0",
    "transformers": "5.22.2",
    "huggingface-hub": "2.4.0",
}
_DETERMINISTIC_EMBEDDING_DIMENSIONS = 256


@lru_cache(maxsize=16384)
def _deterministic_token_vector(token: str) -> tuple[float, ...]:
    """Dense, stable token projection suitable for exercising HNSW traversal."""
    values = []
    counter = 0
    while len(values) < _DETERMINISTIC_EMBEDDING_DIMENSIONS:
        digest = hashlib.sha256(token.encode("utf-8") + counter.to_bytes(4, "big")).digest()
        for byte in digest:
            values.extend(1.0 if byte & (1 << bit) else -1.0 for bit in range(8))
        counter += 1
    return tuple(values[:_DETERMINISTIC_EMBEDDING_DIMENSIONS])


def deterministic_token_embedding(text: str) -> list[float]:
    """Stable dense token hashing for testing vector indexing/retrieval mechanics."""
    vector = [0.0] * _DETERMINISTIC_EMBEDDING_DIMENSIONS
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    for token in tokens:
        token_vector = _deterministic_token_vector(token)
        for index, value in enumerate(token_vector):
            vector[index] += value

    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector

def generate_synthetic_tools(count: int) -> list[ToolMetadata]:
    """Generates synthetic tool definitions across multiple simulated business domains."""
    domains = [ToolCategory.INVOICING, ToolCategory.PAYMENTS, ToolCategory.DISPUTES, ToolCategory.REPORTING]
    tools = []
    for i in range(count):
        cat = domains[i % len(domains)]
        tools.append(ToolMetadata(
            tool_id=f"synth_tool_{i:04d}",
            name=f"simulated_{cat.value}_operation_{i:04d}",
            category=cat,
            endpoint=f"/v1/{cat.value}/ops/{i}",
            http_method=HTTPMethod.POST if i % 2 == 0 else HTTPMethod.GET,
            description=f"Automated service {i} for handling {cat.value} workflows, validation, and analytics.",
            parameters_schema={
                "type": "object",
                "properties": {"account_id": {"type": "string"}, "amount": {"type": "number"}},
                "required": ["account_id"]
            },
            is_sensitive=(i % 5 == 0),
            trigger_phrases=[f"execute operation {i}", f"manage {cat.value} step {i}"]
        ))
    return tools

def test_scalability_retrieval_benchmarks(monkeypatch):
    """
    Empirical Scalability Verification:
    Benchmarks dynamic tool retrieval performance across 50, 500, and 2,000 tools.
    Demonstrates:
    1. Sub-100ms retrieval latency even at 2,001 tools.
    2. Constant O(1) LLM prompt token footprint (capped at top-K <= 4).
    """
    scale_sizes = [50, 500, 2000]
    benchmarks = {}

    # Use deterministic vectors here so the benchmark measures Chroma index,
    # approximate retrieval, top-K, and latency behavior rather than model drift.
    monkeypatch.setattr(
        EmbeddingManager,
        "embed_texts",
        staticmethod(lambda texts: [deterministic_token_embedding(text) for text in texts]),
    )
    monkeypatch.setattr(
        EmbeddingManager,
        "embed_query",
        staticmethod(deterministic_token_embedding),
    )

    for scale in scale_sizes:
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, f"scale_{scale}.db")
        chroma_dir = os.path.join(temp_dir, f"chroma_{scale}")

        try:
            db = ToolRegistryDB(db_path)
            tools = generate_synthetic_tools(scale)

            # Insert target tool to test discovery recall
            target_tool = ToolMetadata(
                tool_id=f"target_tool_{scale}",
                name="specialized_chargeback_investigator",
                category=ToolCategory.DISPUTES,
                endpoint="/v1/customer/special-chargebacks",
                http_method=HTTPMethod.GET,
                description="Investigates high-priority customer dispute claims and complex chargeback records.",
                parameters_schema={"type": "object", "properties": {"claim_id": {"type": "string"}}},
                trigger_phrases=["investigate high priority chargeback", "special dispute claim"]
            )
            tools.append(target_tool)
            db.register_tools_bulk(tools)

            collection_name = f"api_tools_{scale}"
            # This setting is deliberately local to the deterministic benchmark.
            # Production VectorStoreManager defaults remain unchanged.
            v_mgr = VectorStoreManager(persist_dir=chroma_dir)
            v_mgr.client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
                configuration={"hnsw": {"space": "cosine", "ef_search": 800}},
            )

            # Measure Indexing
            index_start = time.perf_counter()
            v_mgr.index_tools(db, collection_name=collection_name)
            index_latency = round(time.perf_counter() - index_start, 2)

            # Measure Retrieval Latency over 5 queries
            retriever = SemanticToolRetriever(db, v_mgr)
            retriever.collection = v_mgr.get_or_create_collection(collection_name)
            assert retriever.collection.configuration_json["hnsw"]["ef_search"] == 800

            test_query = "investigate high priority chargeback"
            latencies = []
            retrieved_count = 0
            found_target = False
            last_result = None

            for _ in range(5):
                q_start = time.perf_counter()
                res = retriever.retrieve_candidates(test_query, top_k=4)
                last_result = res
                latencies.append((time.perf_counter() - q_start) * 1000)
                retrieved_count = len(res.tools)
                if any(t.name == "specialized_chargeback_investigator" for t in res.tools):
                    found_target = True

            avg_latency_ms = round(sum(latencies) / len(latencies), 2)

            benchmarks[scale] = {
                "total_tools": scale + 1,
                "index_time_s": index_latency,
                "avg_query_latency_ms": avg_latency_ms,
                "tools_passed_to_llm": retrieved_count,
                "target_discovered": found_target
            }

            candidate_diagnostics = [
                f"{tool.name} (similarity={last_result.scores.get(tool.tool_id, 'n/a')})"
                for tool in last_result.tools
            ] if last_result else []
            diagnostic_context = (
                f"catalog_size={scale + 1}, expected_target='specialized_chargeback_investigator', "
                f"candidates={candidate_diagnostics}, index_time_s={index_latency}, "
                f"avg_query_latency_ms={avg_latency_ms}"
            )

            # Invariant 1: Top-K bound strictly maintained (O(1) prompt context)
            assert retrieved_count <= 4, diagnostic_context

            # Invariant 2: Target tool successfully retrieved
            assert found_target is True, diagnostic_context

            # Invariant 3: Query latency is sub-100ms
            assert avg_latency_ms < 100.0, diagnostic_context

        finally:
            del db
            gc.collect()

    print("\n--- Empirical Scalability Benchmark Results ---")
    for s, data in benchmarks.items():
        print(f"Scale: {data['total_tools']:4d} tools | Latency: {data['avg_query_latency_ms']:5.2f}ms | Tools to LLM: {data['tools_passed_to_llm']} | Discovered: {data['target_discovered']}")


def test_semantic_retrieval_finds_specialized_chargeback_target(monkeypatch, tmp_path):
    """Validate real semantic embeddings separately, using only a pinned local snapshot."""
    for package, expected_version in _PINNED_RETRIEVAL_DEPENDENCIES.items():
        actual_version = version(package)
        assert actual_version == expected_version, (
            f"Semantic retrieval test expects {package}=={expected_version}; found {actual_version}. "
            "Install the pinned requirements before evaluating semantic recall."
        )

    try:
        snapshot = Path(snapshot_download(
            repo_id=_SEMANTIC_MODEL_ID,
            revision=_SEMANTIC_MODEL_REVISION,
            local_files_only=True,
        ))
    except LocalEntryNotFoundError as exc:
        pytest.skip(
            f"Pinned semantic model {_SEMANTIC_MODEL_ID}@{_SEMANTIC_MODEL_REVISION} "
            f"is not fully cached locally: {exc}"
        )

    with (snapshot / "modules.json").open(encoding="utf-8") as modules_file:
        modules = json.load(modules_file)
    required_assets = [snapshot / "config.json", snapshot / "modules.json"]
    required_assets.extend(
        snapshot / module["path"] / "config.json"
        for module in modules
        if module.get("path")
    )
    model_weights = [snapshot / "model.safetensors", snapshot / "pytorch_model.bin"]
    missing_assets = [str(path.relative_to(snapshot)) for path in required_assets if not path.is_file()]
    if not any(path.is_file() for path in model_weights):
        missing_assets.append("model.safetensors or pytorch_model.bin")
    if missing_assets:
        pytest.skip(
            f"Pinned semantic model snapshot is incomplete in the local cache; missing: "
            f"{', '.join(missing_assets)}"
        )

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    model = SentenceTransformer(str(snapshot), local_files_only=True)
    monkeypatch.setattr(EmbeddingManager, "_model_instance", model)

    db = ToolRegistryDB(str(tmp_path / "semantic_retrieval.db"))
    tools = generate_synthetic_tools(50)
    target = ToolMetadata(
        tool_id="target_tool_semantic",
        name="specialized_chargeback_investigator",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/special-chargebacks",
        http_method=HTTPMethod.GET,
        description="Investigates high-priority customer dispute claims and complex chargeback records.",
        parameters_schema={"type": "object", "properties": {"claim_id": {"type": "string"}}},
        trigger_phrases=["investigate high priority chargeback", "special dispute claim"],
    )
    db.register_tools_bulk([*tools, target])
    vector_store = VectorStoreManager(persist_dir=str(tmp_path / "semantic_chroma"))
    vector_store.index_tools(db, collection_name="semantic_api_tools")
    retriever = SemanticToolRetriever(db, vector_store)
    retriever.collection = vector_store.get_or_create_collection("semantic_api_tools")

    result = retriever.retrieve_candidates("investigate high priority chargeback", top_k=4)
    candidates = [
        f"{tool.name} (similarity={result.scores.get(tool.tool_id, 'n/a')})"
        for tool in result.tools
    ]
    assert any(tool.name == target.name for tool in result.tools), (
        f"Pinned semantic retrieval missed {target.name}; candidates={candidates}, "
        f"top_confidence={result.top_confidence}"
    )
