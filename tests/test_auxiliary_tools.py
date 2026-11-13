import sqlite3
import pytest
import uuid
from src.config import settings
from src.registry.db import ToolRegistryDB
from src.auxiliary.rag_tool import RAGPipelineTool
from src.auxiliary.system_search_tool import SystemSearchTool
from src.orchestrator.llm_client import GroundedAnswer, LLMPlanningClient

@pytest.fixture
def rag_tool():
    return RAGPipelineTool()

@pytest.fixture
def system_search_tool():
    db = ToolRegistryDB(settings.DATABASE_PATH)
    return SystemSearchTool(db)

def test_rag_dispute_policy_query(rag_tool):
    """Test RAG retrieval for dispute response deadline policy."""
    query = "What is the dispute response timeframe?"
    res = rag_tool.run(query)

    assert res["found"] is True
    assert len(res["sources"]) > 0
    # Must retrieve the 10-day seller response window from dispute_policy.md
    assert "10 calendar days" in res["answer_context"]
    assert any(s["file"] == "dispute_policy.md" for s in res["sources"])

def test_rag_fee_schedule_query(rag_tool):
    """Test RAG retrieval for commercial fee rates."""
    query = "What are the standard merchant transaction fees?"
    res = rag_tool.run(query)

    assert res["found"] is True
    assert "2.9% + $0.30" in res["answer_context"]
    assert any(s["file"] == "fee_schedule.md" for s in res["sources"])

def test_system_search_capabilities_invoicing(system_search_tool):
    """Test System Search in capabilities mode for invoicing tools."""
    query = "What tools are available for managing invoices?"
    res = system_search_tool.run(query)

    assert res["search_type"] == "capabilities"
    assert res["category_filtered"] == "invoicing"
    assert res["total_matching_tools"] >= 15
    tool_names = [t["name"] for t in res["tools"]]
    assert "create_draft_invoice" in tool_names
    assert "send_invoice" in tool_names
    assert "get_invoice_details" in tool_names

def test_system_search_capabilities_disputes(system_search_tool):
    """Test System Search in capabilities mode for dispute tools."""
    query = "Which dispute tools are registered?"
    res = system_search_tool.run(query)

    assert res["search_type"] == "capabilities"
    assert res["category_filtered"] == "disputes"
    assert res["total_matching_tools"] >= 10
    tool_names = [t["name"] for t in res["tools"]]
    assert "list_disputes" in tool_names
    assert "submit_dispute_evidence" in tool_names

def test_system_search_audit_history(system_search_tool):
    """Test System Search in execution history mode."""
    test_trace = f"trace_test_hist_{uuid.uuid4().hex[:6]}"
    system_search_tool.db.log_execution(
        trace_id=test_trace,
        user_request="Test historical audit lookup",
        retrieved_tools=["get_account_balances"],
        selected_tool="get_account_balances",
        arguments={},
        approval_status="AUTOMATIC",
        http_status=200,
        response={"balances": {"available": 18450.00}},
        latency_ms=1.5
    )

    query = "What was the status of my last request?"
    res = system_search_tool.run(query)

    assert res["search_type"] == "history"
    assert res["latest_execution"] is not None
    assert test_trace in res["latest_execution"]["trace_id"]
    assert res["latest_execution"]["selected_tool"] == "get_account_balances"
    assert res["latest_execution"]["http_status"] == 200


def test_system_search_empty_history_returns_explicit_empty_result(system_search_tool, monkeypatch):
    monkeypatch.setattr(system_search_tool.db, "get_recent_logs", lambda limit: [])

    result = system_search_tool.run("What was the status of my last request?")

    assert result["search_type"] == "history"
    assert result["found"] is False
    assert result["latest_execution"] is None
    assert result["recent_history"] == []
    assert "No matching execution records were found" in result["message"]


def test_system_search_database_failure_is_not_reported_as_empty(system_search_tool, monkeypatch):
    def fail_query(limit):
        raise sqlite3.OperationalError("database is unavailable")

    monkeypatch.setattr(system_search_tool.db, "get_recent_logs", fail_query)

    with pytest.raises(sqlite3.OperationalError, match="database is unavailable"):
        system_search_tool.run("What was the status of my last request?")


class StaticKnowledgeRetriever:
    def __init__(self, chunks):
        self.chunks = chunks

    def query_knowledge(self, query, top_k=3):
        return self.chunks[:top_k]


class FakeAnswerModel:
    def __init__(self, result):
        self.result = result
        self.schema = None
        self.messages = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, messages):
        self.messages = messages
        return self.result


def test_rag_generates_structured_answer_from_retrieved_context():
    chunk = {
        "text": "Seller responses to a dispute are due within 10 calendar days.",
        "source": "dispute_policy.md",
        "section": "Response deadlines",
        "similarity": 0.91,
    }
    model = FakeAnswerModel({
        "answer": "The seller must respond within 10 calendar days.",
        "evidence_sufficient": True,
    })
    tool = RAGPipelineTool(
        retriever=StaticKnowledgeRetriever([chunk]),
        answerer=LLMPlanningClient(model=model),
    )

    result = tool.run("How long do I have to respond to a dispute?")

    assert model.schema is GroundedAnswer
    assert "10 calendar days" in str(model.messages)
    assert "only the supplied retrieved document excerpts" in model.messages[0][1]
    assert result["answer_mode"] == "llm_grounded"
    assert result["answer"] == "The seller must respond within 10 calendar days."
    assert result["sources"] == [{
        "file": "dispute_policy.md",
        "section": "Response deadlines",
        "relevance": 0.91,
    }]


def test_rag_abstains_and_skips_generation_without_relevant_evidence():
    class AnswererMustNotRun:
        def answer_from_context(self, query, chunks):
            raise AssertionError("Answer generation should not run without relevant evidence")

    tool = RAGPipelineTool(
        retriever=StaticKnowledgeRetriever([{
            "text": "Unrelated account information.",
            "source": "account.md",
            "section": "Profile",
            "similarity": 0.05,
        }]),
        answerer=AnswererMustNotRun(),
    )

    result = tool.run("What is the dispute response deadline?")

    assert result["found"] is False
    assert result["answer_mode"] == "no_evidence"
    assert result["evidence_sufficient"] is False
    assert result["sources"] == []


def test_rag_model_abstention_is_preserved():
    model = FakeAnswerModel({
        "answer": "This excerpt is insufficient to answer.",
        "evidence_sufficient": False,
    })
    tool = RAGPipelineTool(
        retriever=StaticKnowledgeRetriever([{
            "text": "A general account guide with no dispute timeframe.",
            "source": "account_guide.md",
            "section": "Overview",
            "similarity": 0.8,
        }]),
        answerer=LLMPlanningClient(model=model),
    )

    result = tool.run("What is the dispute response deadline?")

    assert result["evidence_sufficient"] is False
    assert result["answer"] == "The retrieved documentation does not provide enough evidence to answer that question."
