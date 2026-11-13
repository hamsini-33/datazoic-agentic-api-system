import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from src.config import settings
from src.retrieval.embeddings import EmbeddingManager, VectorStoreManager

class KnowledgeRetriever:
    """
    RAG retrieval engine querying domain documentation (guides, dispute policies, fee schedules).
    Indexed into ChromaDB collection 'knowledge_docs' strictly isolated from 'api_tools'.
    """
    def __init__(self, docs_dir: Optional[str] = None, vector_mgr: Optional[VectorStoreManager] = None):
        self.docs_dir = Path(docs_dir or settings.DOCS_DIR)
        self.vector_mgr = vector_mgr or VectorStoreManager()
        self.collection = self.vector_mgr.get_or_create_collection("knowledge_docs")

    def index_documents(self) -> int:
        """Loads, chunks, embeds, and stores all markdown documents from data/docs/."""
        if not self.docs_dir.exists():
            return 0

        chunks: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        ids: List[str] = []

        chunk_counter = 0
        for md_file in self.docs_dir.glob("*.md"):
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()

            # Split document by markdown section headers (##)
            sections = content.split("## ")
            doc_title = sections[0].strip().split("\n")[0].replace("#", "").strip()

            for sec in sections[1:]:
                header = sec.split("\n")[0].strip()
                body = "\n".join(sec.split("\n")[1:]).strip()
                chunk_text = f"Document: {doc_title}\nSection: {header}\nContent: {body}"

                chunk_id = f"{md_file.stem}_{chunk_counter}"
                chunk_counter += 1

                chunks.append(chunk_text)
                ids.append(chunk_id)
                metadatas.append({
                    "source": md_file.name,
                    "title": doc_title,
                    "section": header
                })

        if not chunks:
            return 0

        embeddings = EmbeddingManager.embed_texts(chunks)
        self.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas
        )
        return len(chunks)

    def query_knowledge(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Queries the knowledge base for semantically relevant documentation chunks."""
        if self.collection.count() == 0:
            self.index_documents()

        query_embedding = EmbeddingManager.embed_query(query)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k
        )

        matches: List[Dict[str, Any]] = []
        if results and results["documents"] and len(results["documents"][0]) > 0:
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            distances = results["distances"][0] if "distances" in results else [0.0] * len(docs)

            for doc, meta, dist in zip(docs, metas, distances):
                similarity = round(max(0.0, min(1.0, 1.0 - dist)), 4)
                matches.append({
                    "text": doc,
                    "source": meta.get("source", "unknown"),
                    "section": meta.get("section", ""),
                    "similarity": similarity
                })

        return matches
