import copy
import json
import uuid
import pytest
from src.config import settings
from src.registry.db import ToolRegistryDB
from src.orchestrator.graph import ScalableAgentOrchestrator
from src.orchestrator.llm_client import ProposedAction
from src.registry.retriever import ToolRetrievalResult
from src.execution.executor import ExecutionResult

@pytest.fixture(scope="module")
def orchestrator():
    db = ToolRegistryDB(settings.DATABASE_PATH)
    return ScalableAgentOrchestrator(db=db)

def test_scenario1_invoice_multi_step_and_approval(orchestrator, monkeypatch):
    """
    Scenario 1: 'Send an invoice for $50 to bob@example.com for Web Design'
    Must demonstrate:
    1. Intent classification & tool discovery
    2. Step 1: create_draft_invoice (draft created)
    3. Step 2: send_invoice sensitive action halted at approval gate
    4. Step 3: Confirmation dispatches invoice to bob@example.com
    """
    req = "Send an invoice for $50 to bob@example.com for Web Design services"

    # Step A: Run without prior confirmation -> halts at approval gate
    state_turn1 = orchestrator.run(req)

    assert state_turn1["intent"] == "API_TOOL"
    assert "pending_approval" in state_turn1
    approval = state_turn1["pending_approval"]
    assert approval["tool_name"] == "send_invoice"
    assert approval["approval_id"].startswith("approval_")
    assert approval["workflow_step_id"] == f"{state_turn1['trace_id']}:step:2"
    assert approval["trace_id"] == state_turn1["trace_id"]
    assert approval["prepared_execution_id"].startswith("exec_")
    assert "invoice_id" in state_turn1["scratchpad"]
    inv_id = state_turn1["scratchpad"]["invoice_id"]
    assert "INV-2026-" in inv_id
    assert "APPROVAL REQUIRED" in state_turn1["final_response"]

    # The natural-language extraction and first draft result must be carried
    # forward as the exact ID in the separately approved send proposal.
    draft_step = state_turn1["execution_history"][0]
    assert draft_step["tool_name"] == "create_draft_invoice"
    assert draft_step["arguments"] == {
        "recipient_email": "bob@example.com",
        "amount": 50.0,
        "currency": "USD",
        "description": "Web Design services",
    }
    assert draft_step["output"]["invoice_id"] == inv_id
    assert approval["arguments"] == {"invoice_id": inv_id}

    # Approval resumes the exact action. No classification or planning may
    # happen before dispatch; planning resumes only after execution.
    event_order = []
    original_dispatch = orchestrator.execution_engine.mock_service.execute_tool

    def track_dispatch(tool_name, arguments):
        event_order.append(("execute", tool_name, copy.deepcopy(arguments)))
        return original_dispatch(tool_name, arguments)

    def planner_must_only_run_after_dispatch(**kwargs):
        event_order.append(("plan", None, None))
        assert event_order[0][0] == "execute"
        return ProposedAction(done=True)

    monkeypatch.setattr(orchestrator.execution_engine.mock_service, "execute_tool", track_dispatch)
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda *_: pytest.fail("approval must bypass classification"))
    monkeypatch.setattr(orchestrator.planner, "plan_next_action", planner_must_only_run_after_dispatch)

    state_turn2 = orchestrator.run(req, state=state_turn1, approval_decision=True)

    assert state_turn2["is_complete"] is True
    assert "successfully sent to bob@example.com" in state_turn2["final_response"]
    assert inv_id in state_turn2["final_response"]
    assert event_order[0] == ("execute", approval["tool_name"], approval["arguments"])
    assert [event[1] for event in event_order if event[0] == "execute"] == ["send_invoice"]
    assert state_turn2.get("pending_approval") is None
    assert state_turn2.get("approval_decision") is None

    trace_events = [event for event in orchestrator.db.get_recent_logs(limit=1000) if event["trace_id"] == approval["trace_id"]]
    approval_event = next(event for event in trace_events if event["approval_status"] == "APPROVAL_APPROVED")
    approved_payload = json.loads(approval_event["response"])
    assert approved_payload["workflow_step_id"] == approval["workflow_step_id"]
    assert approved_payload["prepared_execution_id"] == approval["prepared_execution_id"]
    execution_event = next(
        event for event in trace_events
        if event["approval_status"] == "APPROVED" and event["selected_tool"] == approval["tool_name"]
    )
    assert json.loads(execution_event["arguments"]) == approval["arguments"]


def test_rejecting_send_for_created_invoice_never_dispatches_send(orchestrator, monkeypatch):
    request = "Send an invoice for $50 to reject@example.com for Regression coverage"
    dispatches = []
    draft_results = []
    original_dispatch = orchestrator.execution_engine.mock_service.execute_tool

    def track_dispatch(tool_name, arguments):
        dispatches.append((tool_name, copy.deepcopy(arguments)))
        result = original_dispatch(tool_name, arguments)
        if tool_name == "create_draft_invoice":
            draft_results.append(result)
        return result

    monkeypatch.setattr(orchestrator.execution_engine.mock_service, "execute_tool", track_dispatch)

    pending = orchestrator.run(request)
    invoice_id = pending["scratchpad"]["invoice_id"]
    assert pending["pending_approval"]["tool_name"] == "send_invoice"
    assert pending["pending_approval"]["arguments"] == {"invoice_id": invoice_id}
    assert dispatches == [
        (
            "create_draft_invoice",
            {
                "recipient_email": "reject@example.com",
                "amount": 50.0,
                "currency": "USD",
                "description": "Regression coverage",
            },
        )
    ]

    rejected = orchestrator.run(
        pending["user_request"],
        state=pending,
        approval_decision=False,
    )

    assert rejected.get("pending_approval") is None
    assert "was rejected and was not executed" in rejected["final_response"]
    assert len(dispatches) == 1  # Draft exists; send was never dispatched.
    assert dispatches[0][0] == "create_draft_invoice"
    assert draft_results[0]["invoice_id"] == invoice_id


def _prepare_sensitive_action(orchestrator, monkeypatch, actions, request):
    tools = [orchestrator.db.get_tool_by_name(name) for name, _ in actions]

    class StubRetriever:
        def retrieve_candidates(self, query, top_k=None):
            return ToolRetrievalResult(query=query, tools=tools)

    def plan_next_action(**kwargs):
        step_index = len(kwargs.get("execution_history", []))
        if step_index >= len(actions):
            return ProposedAction(done=True)
        tool_name, arguments = actions[step_index]
        return ProposedAction(done=False, tool_name=tool_name, arguments=copy.deepcopy(arguments))

    monkeypatch.setattr(orchestrator, "retriever", StubRetriever())
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda _: "API_TOOL")
    monkeypatch.setattr(orchestrator.planner, "plan_next_action", plan_next_action)
    return orchestrator.run(request)


def test_rejection_is_audited_and_never_executes(orchestrator, monkeypatch):
    request = "Send the pending invoice"
    state = _prepare_sensitive_action(
        orchestrator, monkeypatch, [("send_invoice", {"invoice_id": "INV-2026-902"})], request
    )
    approval = state["pending_approval"]
    calls = []
    monkeypatch.setattr(
        orchestrator.execution_engine.mock_service,
        "execute_tool",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    rejected = orchestrator.run(request, state=state, approval_decision=False)

    assert calls == []
    assert rejected.get("pending_approval") is None
    assert "was rejected and was not executed" in rejected["final_response"]
    events = [event for event in orchestrator.db.get_recent_logs(limit=1000) if event["trace_id"] == approval["trace_id"]]
    rejection = next(event for event in events if event["approval_status"] == "APPROVAL_REJECTED")
    payload = json.loads(rejection["response"])
    assert rejection["selected_tool"] == approval["tool_name"]
    assert json.loads(rejection["arguments"]) == approval["arguments"]
    assert payload["workflow_step_id"] == approval["workflow_step_id"]
    assert payload["prepared_execution_id"] == approval["prepared_execution_id"]


@pytest.mark.parametrize("alteration", ["approval_id", "tool_name", "arguments", "trace_id", "workflow_step_id"])
def test_altered_pending_approval_is_rejected_and_not_executed(orchestrator, monkeypatch, alteration):
    request = "Send the pending invoice"
    state = _prepare_sensitive_action(
        orchestrator, monkeypatch, [("send_invoice", {"invoice_id": "INV-2026-902"})], request
    )
    original = state["pending_approval"]
    tampered = copy.deepcopy(state)
    if alteration == "approval_id":
        tampered["pending_approval"]["approval_id"] = "approval_forged"
    elif alteration == "tool_name":
        tampered["pending_approval"]["tool_name"] = "accept_dispute_claim"
    elif alteration == "arguments":
        tampered["pending_approval"]["arguments"] = {"invoice_id": "INV-2026-901"}
    elif alteration == "trace_id":
        tampered["trace_id"] = "trace_forged"
        tampered["pending_approval"]["trace_id"] = "trace_forged"
    elif alteration == "workflow_step_id":
        tampered["pending_approval"]["workflow_step_id"] += ":altered"

    calls = []
    monkeypatch.setattr(
        orchestrator.execution_engine.mock_service,
        "execute_tool",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    result = orchestrator.run(request, state=tampered, approval_decision=True)

    assert calls == []
    assert "did not match the prepared action" in result["final_response"]
    events = [event for event in orchestrator.db.get_recent_logs(limit=1000) if event["trace_id"] == original["trace_id"]]
    rejection = next(event for event in events if event["approval_status"] == "APPROVAL_REJECTED")
    assert json.loads(rejection["response"])["prepared_execution_id"] == original["prepared_execution_id"]


def test_consumed_approval_cannot_be_replayed(orchestrator, monkeypatch):
    request = "Send the pending invoice"
    state = _prepare_sensitive_action(
        orchestrator, monkeypatch, [("send_invoice", {"invoice_id": "INV-2026-902"})], request
    )
    calls = []
    original_dispatch = orchestrator.execution_engine.mock_service.execute_tool

    def track_dispatch(tool_name, arguments):
        calls.append((tool_name, copy.deepcopy(arguments)))
        return original_dispatch(tool_name, arguments)

    monkeypatch.setattr(orchestrator.execution_engine.mock_service, "execute_tool", track_dispatch)
    approved = orchestrator.run(request, state=state, approval_decision=True)
    replayed = orchestrator.run(request, state=state, approval_decision=True)

    assert len(calls) == 1
    assert calls[0] == ("send_invoice", state["pending_approval"]["arguments"])
    assert "already been used" in replayed["final_response"]


def test_execution_failure_after_approval_does_not_restore_token(orchestrator, monkeypatch):
    request = "Send the pending invoice"
    state = _prepare_sensitive_action(
        orchestrator, monkeypatch, [("send_invoice", {"invoice_id": "INV-2026-902"})], request
    )
    calls = []

    def fail_after_approval(**kwargs):
        calls.append(kwargs)
        return ExecutionResult(
            status="EXECUTION_ERROR",
            tool_name=kwargs["tool_name"],
            error_message="forced post-approval failure",
            http_status=500,
        )

    monkeypatch.setattr(orchestrator.execution_engine, "execute", fail_after_approval)
    failed = orchestrator.run(request, state=state, approval_decision=True)
    replayed = orchestrator.run(request, state=state, approval_decision=True)

    assert len(calls) == 1
    assert "forced post-approval failure" in failed["final_response"]
    assert failed.get("pending_approval") is None
    assert "already been used" in replayed["final_response"]


def test_each_subsequent_sensitive_action_requires_fresh_approval(orchestrator, monkeypatch):
    request = "Send an invoice and accept the open dispute"
    first = _prepare_sensitive_action(
        orchestrator,
        monkeypatch,
        [
            ("send_invoice", {"invoice_id": "INV-2026-902"}),
            ("accept_dispute_claim", {"dispute_id": "PP-D-4412"}),
        ],
        request,
    )
    first_approval = first["pending_approval"]

    second = orchestrator.run(request, state=first, approval_decision=True)

    second_approval = second["pending_approval"]
    assert second_approval["tool_name"] == "accept_dispute_claim"
    assert second_approval["workflow_step_id"] == f"{first_approval['trace_id']}:step:2"
    assert second_approval["approval_id"] != first_approval["approval_id"]
    assert second_approval["prepared_execution_id"] != first_approval["prepared_execution_id"]
    assert second["approval_decision"] is None

def test_scenario2_sales_volume_reporting(orchestrator):
    """
    Scenario 2: 'What was my total sales volume last month?'
    Must demonstrate date normalization, retrieval of reporting tools,
    execution, and clear natural-language synthesis.
    """
    req = "What was my total sales volume last month?"
    state = orchestrator.run(req)

    assert state["intent"] == "API_TOOL"
    assert state["is_complete"] is True
    assert "$42,500.00" in state["final_response"]
    assert "312" in state["final_response"]

def test_scenario3_dispute_status_lookup(orchestrator):
    """
    Scenario 3: 'Is there a dispute open from user_123?'
    Must demonstrate customer_id extraction, dispute listing,
    and structured response reporting claim details.
    """
    req = "Is there a dispute open from user_123?"
    state = orchestrator.run(req)

    assert state["intent"] == "API_TOOL"
    assert state["is_complete"] is True
    assert "PP-D-4412" in state["final_response"]
    assert "$120.00" in state["final_response"]
    assert "user_123" in state["final_response"]

def test_scenario4_rag_knowledge_inquiry(orchestrator):
    """
    Scenario 4: Documentation inquiry routed to dedicated RAG tool.
    'What is the dispute response timeframe?'
    """
    req = "What is the dispute response timeframe according to PayPal policy?"
    state = orchestrator.run(req)

    assert state["intent"] == "RAG_DOCS"
    assert state["is_complete"] is True
    assert "10 calendar days" in state["final_response"]
    assert "dispute_policy.md" in state["final_response"]

def test_scenario5_system_search_capabilities(orchestrator):
    """
    Scenario 5: Introspecting system capabilities.
    'What tools are available for managing invoices?'
    """
    req = "What tools are available for managing invoices?"
    state = orchestrator.run(req)

    assert state["intent"] == "SYSTEM_SEARCH"
    assert state["is_complete"] is True
    assert "create_draft_invoice" in state["final_response"]
    assert "send_invoice" in state["final_response"]

def test_scenario6_system_search_audit_history(orchestrator):
    """
    Scenario 6: Querying past execution history and audit logs.
    'What was the status of my last request?'
    """
    req = "What was the status of my last request?"
    state = orchestrator.run(req)

    assert state["intent"] == "SYSTEM_SEARCH"
    assert state["is_complete"] is True
    assert "Latest Execution" in state["final_response"]
    assert "Status: 200" in state["final_response"]


def test_failed_step_preserves_error_and_stops_workflow(orchestrator, monkeypatch):
    draft_tool = orchestrator.db.get_tool_by_name("create_draft_invoice")
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda request: "API_TOOL")
    monkeypatch.setattr(
        orchestrator.planner,
        "plan_next_action",
        lambda **kwargs: ProposedAction(
            done=False,
            tool_name="create_draft_invoice",
            arguments={"recipient_email": "bob@example.com", "amount": 50, "description": "Design"},
        ),
    )

    class StubRetriever:
        def retrieve_candidates(self, query, top_k=None):
            return ToolRetrievalResult(query=query, tools=[draft_tool])

    class FailingExecutor:
        def __init__(self):
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            return ExecutionResult(
                status="EXECUTION_ERROR",
                tool_name=kwargs["tool_name"],
                error_message="forced mock failure",
            )

    executor = FailingExecutor()
    monkeypatch.setattr(orchestrator, "retriever", StubRetriever())
    monkeypatch.setattr(orchestrator, "execution_engine", executor)

    state = orchestrator.run("Send an invoice for $50 to bob@example.com for Design")

    assert len(executor.calls) == 1
    assert state["is_complete"] is True
    assert state["error_context"] == "forced mock failure"
    assert "Execution failed: forced mock failure" in state["final_response"]


def test_metadata_only_api_501_stops_workflow_as_failure(orchestrator, monkeypatch):
    tool = orchestrator.db.get_tool_by_name("list_dispute_reasons")
    request = "List registered dispute reason codes"
    trace_id = f"trace_metadata_only_{uuid.uuid4().hex}"
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda request: "API_TOOL")
    monkeypatch.setattr(
        orchestrator.planner,
        "plan_next_action",
        lambda **kwargs: ProposedAction(done=False, tool_name="list_dispute_reasons", arguments={}),
    )

    class StubRetriever:
        def retrieve_candidates(self, query, top_k=None):
            return ToolRetrievalResult(query=query, tools=[tool])

    monkeypatch.setattr(orchestrator, "retriever", StubRetriever())
    state = orchestrator.run(
        request,
        state={"user_request": request, "trace_id": trace_id},
    )

    assert state["error_context"]
    assert "not implemented" in state["error_context"].lower()
    assert "failed" in state["final_response"].lower()
    assert "successfully" not in state["final_response"].lower()

    matching_events = [
        event for event in orchestrator.db.get_recent_logs(limit=1000)
        if event["trace_id"] == trace_id
    ]
    assert len(matching_events) == 1
    event = matching_events[0]
    assert event["selected_tool"] == "list_dispute_reasons"
    assert event["http_status"] == 501
    assert json.loads(event["response"])["status"] == "NOT_IMPLEMENTED_IN_MOCK"


def test_empty_system_search_history_never_synthesizes_success(orchestrator, monkeypatch):
    request = "What was the status of my last request?"
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda _: "SYSTEM_SEARCH")
    monkeypatch.setattr(
        orchestrator.system_search_tool,
        "run",
        lambda query: {
            "search_type": "history",
            "query": query,
            "found": False,
            "latest_execution": None,
            "recent_history": [],
            "message": "No matching execution records were found.",
        },
    )

    result = orchestrator.run(request)

    assert "No matching execution records were found" in result["final_response"]
    assert "Request processed successfully" not in result["final_response"]


def test_ambiguous_request_keeps_clarification_and_never_executes(orchestrator, monkeypatch):
    candidate = orchestrator.db.get_tool_by_name("list_disputes")
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda request: "API_TOOL")
    monkeypatch.setattr(
        orchestrator.planner,
        "plan_next_action",
        lambda **kwargs: ProposedAction(
            done=True,
            clarification="What would you like me to do? Please specify an invoice, payment, or dispute task.",
        ),
    )

    class StubRetriever:
        def retrieve_candidates(self, query, top_k=None):
            return ToolRetrievalResult(query=query, tools=[candidate])

    class ExecutorMustNotRun:
        def execute(self, **kwargs):
            raise AssertionError("Ambiguous requests must be clarified before execution")

    monkeypatch.setattr(orchestrator, "retriever", StubRetriever())
    monkeypatch.setattr(orchestrator, "execution_engine", ExecutorMustNotRun())

    state = orchestrator.run("Please take care of it")

    assert state["needs_clarification"] is True
    assert state["final_response"].startswith("What would you like me to do?")
