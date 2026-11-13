import copy
import pytest

from src.config import settings
from src.orchestrator.graph import ScalableAgentOrchestrator
from src.orchestrator.llm_client import ProposedAction
from src.registry.db import ToolRegistryDB
from src.registry.retriever import ToolRetrievalResult


@pytest.fixture
def orchestrator():
    return ScalableAgentOrchestrator(db=ToolRegistryDB(settings.DATABASE_PATH))


class OneInvoiceCandidateRetriever:
    def __init__(self, candidate):
        self.candidate = candidate
        self.queries = []

    def retrieve_candidates(self, query, top_k=None):
        self.queries.append(query)
        return ToolRetrievalResult(query=query, tools=[self.candidate])


def test_missing_invoice_fields_can_be_supplied_once_without_reexecuting(orchestrator, monkeypatch):
    candidate = orchestrator.db.get_tool_by_name("create_draft_invoice")
    retriever = OneInvoiceCandidateRetriever(candidate)
    monkeypatch.setattr(orchestrator, "retriever", retriever)
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda _: "API_TOOL")
    dispatches = []
    original_dispatch = orchestrator.execution_engine.mock_service.execute_tool

    def track_dispatch(tool_name, arguments):
        dispatches.append((tool_name, copy.deepcopy(arguments)))
        return original_dispatch(tool_name, arguments)

    monkeypatch.setattr(orchestrator.execution_engine.mock_service, "execute_tool", track_dispatch)
    request = "Create a draft invoice for USD 50"

    pending = orchestrator.run(request)

    assert pending["pending_clarification"]["missing_parameters"] == ["recipient_email", "description"]
    assert "recipient's email address" in pending["final_response"]
    assert "No action was executed" in pending["final_response"]
    assert dispatches == []
    assert len(retriever.queries) == 1

    completed = orchestrator.run(
        pending["user_request"],
        state=pending,
        clarification_response="to bob@example.com with the description 'Consulting services'",
    )

    assert completed.get("pending_clarification") is None
    assert completed["original_user_request"] == request
    assert "User clarification:" in completed["user_request"]
    assert completed["execution_history"][-1]["tool_name"] == "create_draft_invoice"
    assert len(dispatches) == 1
    assert dispatches[0][1]["recipient_email"] == "bob@example.com"
    assert dispatches[0][1]["description"] == "Consulting services"

    replay = orchestrator.run(
        pending["user_request"],
        state=pending,
        clarification_response="to bob@example.com with the description 'Consulting services'",
    )
    assert "can no longer be resumed safely" in replay["final_response"]
    assert len(dispatches) == 1


def test_missing_arguments_after_the_single_clarification_attempt_stop(orchestrator, monkeypatch):
    candidate = orchestrator.db.get_tool_by_name("create_draft_invoice")
    monkeypatch.setattr(orchestrator, "retriever", OneInvoiceCandidateRetriever(candidate))
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda _: "API_TOOL")
    monkeypatch.setattr(
        orchestrator.planner,
        "plan_next_action",
        lambda **_: ProposedAction(
            done=False,
            tool_name="create_draft_invoice",
            arguments={"amount": 50, "currency": "USD"},
        ),
    )
    dispatches = []
    monkeypatch.setattr(orchestrator.execution_engine.mock_service, "execute_tool", lambda *args: dispatches.append(args))

    pending = orchestrator.run("Create a draft invoice")
    final = orchestrator.run(
        pending["user_request"],
        state=pending,
        clarification_response="bob@example.com, consulting, 50 USD",
    )

    assert final["clarification_attempts"] == 1
    assert final.get("pending_clarification") is None
    assert final["error_context"]
    assert dispatches == []


def test_clarification_resume_preserves_sensitive_action_approval(orchestrator, monkeypatch):
    candidate = orchestrator.db.get_tool_by_name("send_invoice")
    monkeypatch.setattr(orchestrator, "retriever", OneInvoiceCandidateRetriever(candidate))
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda _: "API_TOOL")
    proposals = [
        ProposedAction(done=False, tool_name="send_invoice", arguments={}),
        ProposedAction(done=False, tool_name="send_invoice", arguments={"invoice_id": "INV-2026-902"}),
    ]
    monkeypatch.setattr(orchestrator.planner, "plan_next_action", lambda **_: proposals.pop(0))
    dispatches = []
    monkeypatch.setattr(
        orchestrator.execution_engine.mock_service,
        "execute_tool",
        lambda tool_name, arguments: dispatches.append((tool_name, copy.deepcopy(arguments)))
        or {"status": "SENT", "status_code": 200},
    )

    pending = orchestrator.run("Send invoice INV-2026-902")
    assert pending.get("pending_clarification")
    assert dispatches == []

    approval = orchestrator.run(
        pending["user_request"],
        state=pending,
        clarification_response="The invoice id is INV-2026-902",
    )
    assert approval["pending_approval"]["tool_name"] == "send_invoice"
    assert dispatches == []

    completed = orchestrator.run(
        approval["user_request"],
        state=approval,
        approval_decision=True,
    )
    assert dispatches == [("send_invoice", {"invoice_id": "INV-2026-902"})]
    assert completed.get("pending_approval") is None


@pytest.mark.parametrize("approval_decision", [True, False], ids=["approved", "rejected"])
def test_invoice_clarification_continues_to_send_approval_once(
    orchestrator, monkeypatch, approval_decision
):
    dispatches = []
    original_dispatch = orchestrator.execution_engine.mock_service.execute_tool

    def track_dispatch(tool_name, arguments):
        result = original_dispatch(tool_name, copy.deepcopy(arguments))
        dispatches.append((tool_name, copy.deepcopy(arguments), copy.deepcopy(result)))
        return result

    monkeypatch.setattr(orchestrator.execution_engine.mock_service, "execute_tool", track_dispatch)
    request = "Send an invoice for USD 50"

    clarification = orchestrator.run(request)

    assert clarification.get("pending_clarification")
    assert clarification["original_user_request"] == request
    assert dispatches == []

    pending_approval = orchestrator.run(
        clarification["user_request"],
        state=clarification,
        clarification_response="to workflow@example.com with the description 'End-to-end test'",
    )

    assert pending_approval.get("pending_clarification") is None
    assert pending_approval["original_user_request"] == request
    assert pending_approval["execution_history"][-1]["tool_name"] == "create_draft_invoice"
    draft_calls = [call for call in dispatches if call[0] == "create_draft_invoice"]
    assert len(draft_calls) == 1
    assert draft_calls[0][1] == {
        "recipient_email": "workflow@example.com",
        "amount": 50.0,
        "currency": "USD",
        "description": "End-to-end test",
    }
    invoice_id = draft_calls[0][2]["invoice_id"]
    assert pending_approval["scratchpad"]["invoice_id"] == invoice_id
    assert pending_approval["pending_approval"]["tool_name"] == "send_invoice"
    assert pending_approval["pending_approval"]["arguments"] == {"invoice_id": invoice_id}
    assert [call[0] for call in dispatches] == ["create_draft_invoice"]

    completed = orchestrator.run(
        pending_approval["user_request"],
        state=pending_approval,
        approval_decision=approval_decision,
    )

    assert completed.get("pending_approval") is None
    assert len([call for call in dispatches if call[0] == "create_draft_invoice"]) == 1
    send_calls = [call for call in dispatches if call[0] == "send_invoice"]
    if approval_decision:
        assert len(send_calls) == 1
        assert send_calls[0][1] == {"invoice_id": invoice_id}
        assert send_calls[0][2]["status"] == "SENT"
        assert "successfully sent" in completed["final_response"]
    else:
        assert send_calls == []
        assert "was rejected and was not executed" in completed["final_response"]
