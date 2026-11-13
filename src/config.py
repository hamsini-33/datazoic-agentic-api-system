import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM Settings
    LLM_PROVIDER: str = Field(default="mock", description="LLM provider: 'openai', 'gemini', or 'mock'")
    LLM_MODEL: str = Field(default="gpt-4o-mini", description="Model identifier")
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API key")
    GOOGLE_API_KEY: str = Field(default="", description="Google API key")

    # Embeddings & ChromaDB
    EMBEDDING_MODEL_NAME: str = Field(default="all-MiniLM-L6-v2", description="Sentence Transformers model")
    CHROMA_PERSIST_DIR: str = Field(default=str(BASE_DIR / "data" / "chroma"), description="ChromaDB storage path")
    
    # SQLite Database
    DATABASE_PATH: str = Field(default=str(BASE_DIR / "data" / "system.db"), description="SQLite database path")
    
    # Knowledge Base
    DOCS_DIR: str = Field(default=str(BASE_DIR / "data" / "docs"), description="Markdown documentation path")

    # Orchestrator & Safety
    TOP_K_TOOLS: int = Field(default=4, description="Maximum tools injected per LLM turn")
    SIMILARITY_THRESHOLD: float = Field(default=0.35, description="Minimum similarity score for candidate tools")
    RAG_SIMILARITY_THRESHOLD: float = Field(default=0.35, description="Minimum relevance for using a retrieved document chunk")
    ENABLE_APPROVAL_GATES: bool = Field(default=True, description="Require human confirmation for sensitive APIs")

settings = Settings()
