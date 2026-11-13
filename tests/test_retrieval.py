import pytest
from src.config import settings
from src.registry.db import ToolRegistryDB
from src.retrieval.embeddings import VectorStoreManager
from src.registry.retriever import SemanticToolRetriever

@pytest.fixture(scope="module")
def retriever():
    db = ToolRegistryDB(settings.DATABASE_PATH)
    vector_mgr = VectorStoreManager(settings.CHROMA_PERSIST_DIR)
    return SemanticToolRetriever(db, vector_mgr)

def test_scenario1_invoice_retrieval(retriever):
    """Scenario 1: Send an invoice for $50 to bob@example.com for Web Design"""
    query = "Send an invoice for $50 to bob@example.com for Web Design services"
    result = retriever.retrieve_candidates(query, top_k=4)

    assert len(result.tools) <= 4
    tool_names = [t.name for t in result.tools]
    
    # Must retrieve invoice creation / dispatch tools
    assert "create_draft_invoice" in tool_names or "send_invoice" in tool_names
    assert result.top_confidence >= 0.45
    assert result.clarification_needed is False

def test_scenario2_sales_volume_retrieval(retriever):
    """Scenario 2: What was my total sales volume last month?"""
    query = "What was my total sales volume last month?"
    result = retriever.retrieve_candidates(query, top_k=4)

    tool_names = [t.name for t in result.tools]
    # Must retrieve sales volume or transaction reporting tools
    assert "get_transaction_summary" in tool_names or "get_monthly_sales_volume" in tool_names
    assert result.top_confidence >= 0.45

def test_scenario3_dispute_retrieval(retriever):
    """Scenario 3: Is there a dispute open from user_123?"""
    query = "Is there a dispute open from user_123?"
    result = retriever.retrieve_candidates(query, top_k=4)

    tool_names = [t.name for t in result.tools]
    # Must retrieve dispute listing / inquiry tools
    assert "list_disputes" in tool_names or "get_dispute_details" in tool_names
    assert result.top_confidence >= 0.40

def test_category_filter(retriever):
    """Verify that category filtering strictly scopes results."""
    query = "Check details"
    result = retriever.retrieve_candidates(query, top_k=3, category_filter="disputes")

    assert len(result.tools) > 0
    for tool in result.tools:
        assert tool.category == "disputes"

def test_top_k_bounding(retriever):
    """Verify that no more than K tools are ever returned, protecting context space."""
    query = "payment refund capture"
    result = retriever.retrieve_candidates(query, top_k=3)
    assert len(result.tools) <= 3

def test_unrelated_query_confidence(retriever):
    """Verify that completely unrelated queries yield low confidence or trigger clarification."""
    query = "What is the capital city of France?"
    result = retriever.retrieve_candidates(query, top_k=3)
    assert result.top_confidence < 0.35 or result.fallback_triggered is True
