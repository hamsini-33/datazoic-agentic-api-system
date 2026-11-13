from collections import deque

import pytest
from pydantic import ValidationError

from src.config import settings
from src.orchestrator.graph import ScalableAgentOrchestrator
from src.orchestrator.llm_client import LLMPlanningClient, ProposedAction
from src.registry.db import ToolRegistryDB
from src.registry.retriever import ToolRetrievalResult


class FakeStructuredModel:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.schema = None
        self.calls = []

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, messages):
        self.calls.append((self.schema, messages))
        result = self.responses.popleft()
        if isinstance(result, Exception):
            raise result
        return result


class StubRetriever:
    def __init__(self, candidate):
        self.candidate = candidate
        self.queries = []

    def retrieve_candidates(self, query, top_k=None):
        self.queries.append((query, top_k))
        return ToolRetrievalResult(query=query, tools=[self.candidate])


def provider_orchestrator(monkeypatch, candidate_name, responses):
    orchestrator = ScalableAgentOrchestrator(db=ToolRegistryDB(settings.DATABASE_PATH))
    candidate = orchestrator.db.get_tool_by_name(candidate_name)
    retriever = StubRetriever(candidate)
    monkeypatch.setattr(orchestrator, "retriever", retriever)
    model = FakeStructuredModel(responses)
    orchestrator.planner = LLMPlanningClient(model=model)
    dispatches = []

    def dispatch(tool_name, arguments):
        dispatches.append((tool_name, dict(arguments)))
        return {"status": "CREATED", "invoice_id": "INV-TEST-1", "status_code": 201}

    monkeypatch.setattr(orchestrator.execution_engine.mock_service, "execute_tool", dispatch)
    return orchestrator, model, retriever, dispatches


def initial_provider_responses(subgoal, action, final_subgoal=None):
    responses = [
        {"intent": "API_TOOL"},
        {"done": False, "subgoal": subgoal},
        action,
    ]
    if final_subgoal is not None:
        responses.append({"done": True})
    return responses


def test_provider_valid_invoice_arguments_are_schema_conditioned_and_executed(monkeypatch):
    action = {
        "done": False,
        "tool_name": "create_draft_invoice",
        "arguments": {
            "recipient_email": "bob@example.com",
            "amount": 50,
            "currency": "EUR",
            "description": "Consulting",
        },
    }
    orchestrator, model, retriever, dispatches = provider_orchestrator(
        monkeypatch,
        "create_draft_invoice",
        initial_provider_responses("Create invoice for EUR 50", action, final_subgoal=True),
    )

    state = orchestrator.run("Create an invoice for EUR 50 to bob@example.com")

    assert state["is_complete"] is True
    assert dispatches == [("create_draft_invoice", action["arguments"])]
    assert len(retriever.queries) == 1
    planner_messages = str(model.calls[2][1])
    assert "parameters" in planner_messages
    assert "recipient_email" in planner_messages
    assert "currency" in planner_messages


@pytest.mark.parametrize(
    "arguments,user_text,expected_status",
    [
        ({"amount": 50, "currency": "USD", "description": "Design"}, "Create an invoice", "clarification"),
        ({"recipient_email": "bob..smith@example.com", "amount": 50, "description": "Design"}, "Create an invoice", "validation"),
        ({"recipient_email": "bob@example.com", "amount": 50, "currency": "CAD", "description": "Design"}, "Create invoice for CAD 50", "validation"),
        ({"recipient_email": "bob@example.com", "amount": 50, "description": "Design", "unexpected": "value"}, "Create an invoice", "validation"),
    ],
)
def test_rejected_provider_invoice_arguments_never_reach_execution(monkeypatch, arguments, user_text, expected_status):
    action = {"done": False, "tool_name": "create_draft_invoice", "arguments": arguments}
    orchestrator, _, _, dispatches = provider_orchestrator(
        monkeypatch,
        "create_draft_invoice",
        initial_provider_responses("Create invoice", action),
    )

    state = orchestrator.run(user_text)

    assert dispatches == []
    if expected_status == "clarification":
        assert state.get("pending_clarification")
        assert "recipient's email address" in state["final_response"]
    else:
        assert state["error_context"]
        assert state.get("pending_clarification") is None


def test_provider_malformed_structured_output_is_rejected():
    client = LLMPlanningClient(model=FakeStructuredModel([{"intent": "NOT_AN_INTENT"}]))

    with pytest.raises(ValidationError):
        client.classify_intent("Create an invoice")


def test_provider_action_missing_structured_fields_is_rejected():
    candidate = ToolRegistryDB(settings.DATABASE_PATH).get_tool_by_name("create_draft_invoice")
    client = LLMPlanningClient(model=FakeStructuredModel([{"tool_name": "create_draft_invoice"}]))

    with pytest.raises(ValidationError):
        client.plan_next_action("Create an invoice", [candidate.model_dump()])


def test_provider_error_does_not_fall_back_to_mock_or_execute(monkeypatch):
    orchestrator, _, _, dispatches = provider_orchestrator(
        monkeypatch,
        "create_draft_invoice",
        [RuntimeError("provider unavailable")],
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        orchestrator.run("Create an invoice")

    assert dispatches == []


def test_provider_sensitive_action_keeps_existing_approval_boundary(monkeypatch):
    action = {
        "done": False,
        "tool_name": "send_invoice",
        "arguments": {"invoice_id": "INV-2026-900"},
    }
    orchestrator, model, _, dispatches = provider_orchestrator(
        monkeypatch,
        "send_invoice",
        initial_provider_responses("Send invoice INV-2026-900", action, final_subgoal=True),
    )

    pending = orchestrator.run("Send invoice INV-2026-900")
    assert pending["pending_approval"]["tool_name"] == "send_invoice"
    assert dispatches == []
    call_count_before_approval = len(model.calls)

    completed = orchestrator.run(
        pending["user_request"],
        state=pending,
        approval_decision=True,
    )

    assert dispatches == [("send_invoice", {"invoice_id": "INV-2026-900"})]
    assert len(model.calls) == call_count_before_approval + 1  # only next-subgoal identification, after dispatch
    assert completed.get("pending_approval") is None
