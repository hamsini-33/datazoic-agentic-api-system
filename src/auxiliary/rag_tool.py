from typing import Dict, Any, Optional
from src.retrieval.knowledge_retriever import KnowledgeRetriever
from src.config import settings
from src.orchestrator.llm_client import LLMPlanningClient

class RAGPipelineTool:
    """
    Dedicated tool for Retrieval-Augmented Generation over domain knowledge documents
    (policy guides, fee schedules, integration procedures).
    """
    def __init__(self, retriever: Optional[KnowledgeRetriever] = None, answerer: Optional[LLMPlanningClient] = None):
        self.retriever = retriever or KnowledgeRetriever()
        self.answerer = answerer or LLMPlanningClient()

    def run(self, query: str, top_k: int = 2) -> Dict[str, Any]:
        """Queries the documentation collection and returns grounded contextual answers."""
        retrieved_chunks = self.retriever.query_knowledge(query, top_k=top_k)
        chunks = [chunk for chunk in retrieved_chunks if chunk.get("similarity", 0.0) >= settings.RAG_SIMILARITY_THRESHOLD]
        if not chunks:
            return {
                "query": query,
                "found": False,
                "answer": "I couldn't find relevant documentation to answer that question.",
                "answer_mode": "no_evidence",
                "evidence_sufficient": False,
                "answer_context": "No matching knowledge base documents found for your query.",
                "sources": []
            }

        formatted_context = []
        sources = []
        for c in chunks:
            formatted_context.append(f"[{c['source']} - {c['section']}]:\n{c['text']}")
            sources.append({"file": c["source"], "section": c["section"], "relevance": c["similarity"]})

        context_str = "\n\n---\n\n".join(formatted_context)
        generated = self.answerer.answer_from_context(query, chunks)

        return {
            "query": query,
            "found": True,
            "answer": generated["answer"],
            "answer_mode": generated["answer_mode"],
            "evidence_sufficient": generated["evidence_sufficient"],
            "answer_context": context_str,
            "sources": sources,
            "top_relevance": chunks[0]["similarity"] if chunks else 0.0
        }
