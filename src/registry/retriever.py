from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from src.config import settings
from src.registry.models import ToolMetadata
from src.registry.db import ToolRegistryDB
from src.retrieval.embeddings import EmbeddingManager, VectorStoreManager

class ToolRetrievalResult(BaseModel):
    query: str
    tools: List[ToolMetadata]
    scores: Dict[str, float] = Field(default_factory=dict, description="Relevance score per tool (0.0 to 1.0)")
    top_confidence: float = 0.0
    fallback_triggered: bool = False
    clarification_needed: bool = False
    clarification_message: Optional[str] = None

class SemanticToolRetriever:
    def __init__(self, db: ToolRegistryDB, vector_mgr: Optional[VectorStoreManager] = None):
        self.db = db
        self.vector_mgr = vector_mgr or VectorStoreManager()
        self.collection = self.vector_mgr.get_or_create_collection("api_tools")

    def ensure_indexed(self) -> int:
        if self.collection.count() == 0:
            return self.vector_mgr.index_tools(self.db, "api_tools")
        return self.collection.count()

    def retrieve_candidates(
        self,
        query: str,
        top_k: Optional[int] = None,
        category_filter: Optional[str] = None
    ) -> ToolRetrievalResult:
        """
        Dynamically retrieves the top-K relevant API tools using dense vector similarity,
        enforcing confidence thresholding and fallback logic.
        """
        self.ensure_indexed()
        k = top_k or settings.TOP_K_TOOLS
        threshold = settings.SIMILARITY_THRESHOLD

        query_embedding = EmbeddingManager.embed_query(query)
        where_clause = {"category": category_filter} if category_filter else None

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(k * 3, 15),
            where=where_clause
        )

        tools: List[ToolMetadata] = []
        scores: Dict[str, float] = {}
        top_confidence = 0.0

        if results and results["ids"] and len(results["ids"][0]) > 0:
            ids = results["ids"][0]
            distances = results["distances"][0] if "distances" in results else [0.0] * len(ids)

            for tool_id, dist in zip(ids, distances):
                # Cosine distance to similarity (0.0 to 1.0)
                similarity = max(0.0, min(1.0, 1.0 - dist))
                scores[tool_id] = round(similarity, 4)

            top_confidence = max(scores.values()) if scores else 0.0
            sorted_tool_ids = sorted(scores.keys(), key=lambda tid: scores[tid], reverse=True)

            for tid in sorted_tool_ids[:k]:
                tool_meta = self.db.get_tool(tid)
                if tool_meta:
                    if category_filter and tool_meta.category != category_filter:
                        continue
                    tools.append(tool_meta)

        fallback_triggered = False
        clarification_needed = False
        clarification_message = None

        # Check if confidence meets threshold
        if top_confidence < threshold:
            fallback_triggered = True

            # Use keyword search in SQLite as backup
            keyword_tools = self.db.search_tools_keyword(query, limit=k)
            for kw_tool in keyword_tools:
                if category_filter and kw_tool.category != category_filter:
                    continue
                if kw_tool.tool_id not in scores:
                    tools.append(kw_tool)
                    scores[kw_tool.tool_id] = 0.30

            # If still low confidence, mark clarification needed
            if top_confidence < 0.25 and len(tools) == 0:
                clarification_needed = True
                clarification_message = (
                    f"I couldn't confidently identify a matching tool for: '{query}'. "
                    f"Please clarify whether you want to manage invoices, process payments, or check disputes."
                )

        return ToolRetrievalResult(
            query=query,
            tools=tools[:k],
            scores=scores,
            top_confidence=top_confidence,
            fallback_triggered=fallback_triggered,
            clarification_needed=clarification_needed,
            clarification_message=clarification_message
        )
