from src.retrieval.knowledge_retriever import KnowledgeRetriever

def main():
    print("Indexing markdown documents into ChromaDB 'knowledge_docs' collection...")
    retriever = KnowledgeRetriever()
    count = retriever.index_documents()
    print(f"Successfully indexed {count} document sections into ChromaDB 'knowledge_docs'.")

if __name__ == "__main__":
    main()
