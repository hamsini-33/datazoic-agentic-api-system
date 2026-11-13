import pytest
import os
import gc
import tempfile
from src.registry.models import ToolMetadata, ToolCategory, HTTPMethod
from src.registry.db import ToolRegistryDB
from src.registry.seed_data import seed_tools, PAYPAL_TOOLS

@pytest.fixture
def temp_db():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_system.db")
    db = ToolRegistryDB(db_path)
    yield db
    # Force garbage collection to close any lingering SQLite handles on Windows
    del db
    gc.collect()
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
        if os.path.exists(temp_dir):
            os.rmdir(temp_dir)
    except Exception:
        pass

def test_db_initialization(temp_db):
    assert temp_db.count_tools() == 0

def test_register_and_get_tool(temp_db):
    tool = ToolMetadata(
        tool_id="test_tool",
        name="test_function",
        category=ToolCategory.INVOICING,
        endpoint="/v2/test",
        http_method=HTTPMethod.POST,
        description="A test tool description",
        parameters_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
        is_sensitive=True,
        required_permissions=["test_scope"],
        trigger_phrases=["test phrase"]
    )
    temp_db.register_tool(tool)
    assert temp_db.count_tools() == 1

    fetched = temp_db.get_tool("test_tool")
    assert fetched is not None
    assert fetched.name == "test_function"
    assert fetched.category == ToolCategory.INVOICING
    assert fetched.is_sensitive is True
    assert fetched.parameters_schema["properties"]["msg"]["type"] == "string"

def test_seed_tools_count(temp_db):
    count = seed_tools(temp_db)
    assert count == len(PAYPAL_TOOLS)
    assert count >= 50
    assert temp_db.count_tools() == count

def test_category_filtering(temp_db):
    seed_tools(temp_db)
    invoicing_tools = temp_db.list_tools(category="invoicing")
    payments_tools = temp_db.list_tools(category="payments")
    disputes_tools = temp_db.list_tools(category="disputes")
    reporting_tools = temp_db.list_tools(category="reporting")

    assert len(invoicing_tools) >= 15
    assert len(payments_tools) >= 15
    assert len(disputes_tools) >= 10
    assert len(reporting_tools) >= 10

def test_keyword_search(temp_db):
    seed_tools(temp_db)
    results = temp_db.search_tools_keyword("invoice", limit=5)
    assert len(results) > 0
    assert any("invoice" in r.name.lower() or "invoice" in r.description.lower() for r in results)

    dispute_results = temp_db.search_tools_keyword("dispute", limit=5)
    assert len(dispute_results) > 0
    assert any("dispute" in r.name.lower() or "dispute" in r.description.lower() for r in dispute_results)

def test_execution_logging(temp_db):
    temp_db.log_execution(
        trace_id="trace_001",
        user_request="Send invoice for $50 to Bob",
        retrieved_tools=["create_draft_invoice", "send_invoice"],
        selected_tool="create_draft_invoice",
        arguments={"amount": 50, "recipient_email": "bob@example.com"},
        approval_status="AUTOMATIC",
        http_status=201,
        response={"invoice_id": "INV-123", "status": "DRAFT"},
        latency_ms=12.4
    )
    last_log = temp_db.get_last_execution()
    assert last_log is not None
    assert last_log["trace_id"] == "trace_001"
    assert last_log["selected_tool"] == "create_draft_invoice"
    assert last_log["http_status"] == 201

def test_execution_events_keep_multiple_steps_with_shared_trace(temp_db):
    common_trace = "trace_multi_step"
    first_id = temp_db.log_execution(
        trace_id=common_trace,
        user_request="Create and send an invoice",
        retrieved_tools=["create_draft_invoice", "send_invoice"],
        selected_tool="create_draft_invoice",
        arguments={"amount": 50},
        approval_status="AUTOMATIC",
        http_status=201,
        response={"invoice_id": "INV-001"},
        latency_ms=1.0,
    )
    second_id = temp_db.log_execution(
        trace_id=common_trace,
        user_request="Create and send an invoice",
        retrieved_tools=["create_draft_invoice", "send_invoice"],
        selected_tool="send_invoice",
        arguments={"invoice_id": "INV-001"},
        approval_status="AWAITING_APPROVAL",
        http_status=202,
        response={"status": "PREPARED"},
        latency_ms=1.0,
    )

    same_trace = [row for row in temp_db.get_recent_logs(limit=10) if row["trace_id"] == common_trace]
    assert first_id != second_id
    assert len(same_trace) == 2
    assert {row["execution_id"] for row in same_trace} == {first_id, second_id}

def test_old_execution_log_rows_remain_searchable(temp_db):
    with temp_db.get_connection() as conn:
        conn.execute(
            """INSERT INTO execution_logs
               (trace_id, timestamp, user_request, selected_tool, approval_status, http_status, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("trace_legacy", "2026-01-01T00:00:00+00:00", "Old request", "list_disputes", "AUTOMATIC", 200, 2.0),
        )

    rows = temp_db.get_recent_logs(limit=5)
    legacy = next(row for row in rows if row["trace_id"] == "trace_legacy")
    assert legacy["selected_tool"] == "list_disputes"
    assert legacy["execution_id"] is None
