import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import chromadb
from sentence_transformers import SentenceTransformer
from src.config import settings
from src.registry.models import ToolMetadata
from src.registry.db import ToolRegistryDB

class EmbeddingManager:
    _model_instance: Optional[SentenceTransformer] = None

    @classmethod
    def get_model(cls) -> SentenceTransformer:
        if cls._model_instance is None:
            cls._model_instance = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
        return cls._model_instance

    @classmethod
    def embed_texts(cls, texts: List[str]) -> List[List[float]]:
        model = cls.get_model()
        embeddings = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return embeddings.tolist()

    @classmethod
    def embed_query(cls, text: str) -> List[float]:
        return cls.embed_texts([text])[0]


class VectorStoreManager:
    def __init__(self, persist_dir: Optional[str] = None):
        self.persist_dir = persist_dir or settings.CHROMA_PERSIST_DIR
        Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.persist_dir)

    def get_or_create_collection(self, name: str):
        """Retrieves or creates a collection with cosine distance metric."""
        return self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"}
        )

    def index_tools(self, db: ToolRegistryDB, collection_name: str = "api_tools") -> int:
        """Indexes all tools from SQLite into the ChromaDB api_tools collection."""
        collection = self.get_or_create_collection(collection_name)
        tools = db.list_tools()
        if not tools:
            return 0

        ids = []
        documents = []
        metadatas = []

        for tool in tools:
            triggers = " ".join(tool.trigger_phrases) if tool.trigger_phrases else ""
            # Natural query-aligned text representation
            doc_text = (
                f"{tool.name} in {tool.category}. "
                f"{tool.description} "
                f"Common user requests: {triggers}"
            )
            ids.append(tool.tool_id)
            documents.append(doc_text)
            metadatas.append({
                "tool_id": tool.tool_id,
                "name": tool.name,
                "category": tool.category if isinstance(tool.category, str) else tool.category.value,
                "is_sensitive": int(tool.is_sensitive),
                "endpoint": tool.endpoint,
                "http_method": tool.http_method if isinstance(tool.http_method, str) else tool.http_method.value
            })

        embeddings = EmbeddingManager.embed_texts(documents)

        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )
        return len(ids)
