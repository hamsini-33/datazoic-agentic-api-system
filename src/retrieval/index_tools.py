from src.config import settings
from src.registry.db import ToolRegistryDB
from src.retrieval.embeddings import VectorStoreManager

def main():
    print("Connecting to SQLite database...")
    db = ToolRegistryDB(settings.DATABASE_PATH)
    total_tools = db.count_tools()
    print(f"Total tools in SQLite: {total_tools}")

    print("Initializing ChromaDB and embedding tools into 'api_tools' collection...")
    vector_mgr = VectorStoreManager()
    count = vector_mgr.index_tools(db, "api_tools")
    print(f"Successfully embedded and indexed {count} tools into ChromaDB 'api_tools' collection.")

if __name__ == "__main__":
    main()
