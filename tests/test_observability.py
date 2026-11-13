from io import StringIO

from rich.console import Console

from main import build_argument_parser, display_verbose_trace
from src.execution.executor import ExecutionResult
from src.orchestrator.graph import ScalableAgentOrchestrator
from src.orchestrator.llm_client import ProposedAction, ProposedSubgoal
from src.orchestrator.trace import redact_text, redact_trace_value
from src.registry.db import ToolRegistryDB
from src.registry.retriever import ToolRetrievalResult
from src.registry.seed_data import seed_tools


def test_verbose_trace_is_disabled_by_default():
    assert build_argument_parser().parse_args([]).verbose is False


def test_trace_redacts_customer_values_credentials_and_contact_information():
    safe = redact_trace_value({
        "invoice_id": "INV-101",
        "recipient_email": "bob@example.com",
        "customer_id": "user_123",
        "access_token": "secret-token-value",
        "amount": 50.0,
    })

    assert safe["invoice_id"] == "INV-101"
    assert safe["recipient_email"] == "[REDACTED]"
    assert safe["customer_id"] == "[REDACTED]"
    assert safe["access_token"] == "[REDACTED]"
    assert safe["amount"] == 50.0
    assert "bob@example.com" not in redact_text("Send to bob@example.com")
    assert "secret-token-value" not in redact_text("access_token=secret-token-value")
    assert "Bearer customer-token" not in redact_text("Authorization: Bearer customer-token")
    assert "Alice Smith" not in redact_text("Send invoice to Alice Smith")


def test_phone_redaction_preserves_iso_dates_and_times():
    values = [
        "2026-11-12T14:57:32+00:00",
        "2026-11-12T14:57:32Z",
        "2026-11-12",
        "14:57:32+00:00",
        "14:57:32",
    ]

    for value in values:
        redacted = redact_text(f"created_at={value}")
        assert value in redacted
        assert "[REDACTED_PHONE]" not in redacted

    phone = redact_text("Call +1 (415) 555-2671 for support")
    assert "[REDACTED_PHONE]" in phone
    assert "+1 (415) 555-2671" not in phone


def test_multistep_trace_records_fresh_candidates_and_masked_state(tmp_path, monkeypatch):
    db = ToolRegistryDB(str(tmp_path / "trace_test.db"))
    seed_tools(db)
    create_tool = db.get_tool_by_name("create_draft_invoice")
    send_tool = db.get_tool_by_name("send_invoice")
    queries = []
    planner_calls = []
    subgoals = ["Create an invoice", "Send invoice INV-101 to bob@example.com"]

    class Retriever:
        def retrieve_candidates(self, query, top_k=None):
            queries.append((query, top_k))
            tool = create_tool if len(queries) == 1 else send_tool
            return ToolRetrievalResult(query=query, tools=[tool], scores={tool.tool_id: 0.91 - (len(queries) - 1) * 0.1})

    class Executor:
        def execute(self, **kwargs):
            if kwargs["tool_name"] == "create_draft_invoice":
                return ExecutionResult(
                    status="SUCCESS", tool_name=kwargs["tool_name"], http_status=201,
                    execution_id="exec_1", output={
                        "invoice_id": "INV-101", "recipient_email": "bob@example.com", "amount": 50.0,
                    },
                )
            return ExecutionResult(
                status="SUCCESS", tool_name=kwargs["tool_name"], http_status=200,
                execution_id="exec_2", output={"invoice_id": "INV-101", "status": "SENT"},
            )

    orchestrator = ScalableAgentOrchestrator(db=db, retriever=Retriever(), execution_engine=Executor())
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda _: "API_TOOL")

    def identify_next_subgoal(user_request, execution_history, scratchpad):
        index = len(execution_history)
        return ProposedSubgoal(done=index >= len(subgoals), subgoal=subgoals[index] if index < len(subgoals) else None)

    def plan_next_action(**kwargs):
        planner_calls.append(kwargs)
        index = len(kwargs["execution_history"])
        tool_name = "create_draft_invoice" if index == 0 else "send_invoice"
        args = (
            {"recipient_email": "bob@example.com", "amount": 50, "description": "Design"}
            if index == 0 else {"invoice_id": kwargs["scratchpad"]["invoice_id"]}
        )
        return ProposedAction(done=False, tool_name=tool_name, arguments=args)

    monkeypatch.setattr(orchestrator.planner, "identify_next_subgoal", identify_next_subgoal)
    monkeypatch.setattr(orchestrator.planner, "plan_next_action", plan_next_action)

    request = "Create an invoice for $50 to bob@example.com and send it"
    state = orchestrator.run(request)

    assert [query for query, _ in queries] == subgoals
    assert [call["current_subgoal"] for call in planner_calls] == subgoals
    assert [candidate["name"] for candidate in planner_calls[0]["candidate_tools"]] == ["create_draft_invoice"]
    assert [candidate["name"] for candidate in planner_calls[1]["candidate_tools"]] == ["send_invoice"]
    assert len(state["planning_trace"]) == 2

    first, second = state["planning_trace"]
    assert first["candidate_source"] == "fresh retrieval for this step"
    assert first["retrieved_candidates"] == [{"name": "create_draft_invoice", "score": 0.91}]
    assert first["planner_candidates"] == ["create_draft_invoice"]
    assert first["execution_status"] == "SUCCESS"
    assert first["http_status"] == 201
    assert first["output"]["invoice_id"] == "INV-101"
    assert first["output"]["recipient_email"] == "[REDACTED]"
    assert first["parameters"]["recipient_email"] == "[REDACTED]"
    assert second["prior_results"]["invoice_id"] == "INV-101"
    assert second["prior_results"]["recipient_email"] == "[REDACTED]"
    assert second["selected_api"] == "send_invoice"
    assert second["execution_status"] == "SUCCESS"

    stream = StringIO()
    test_console = Console(file=stream, force_terminal=False, width=180)
    display_verbose_trace(request, state, test_console)
    rendered = stream.getvalue()
    assert "Original Request" in rendered
    assert "Fresh retrieval" in rendered
    assert "create_draft_invoice" in rendered and "send_invoice" in rendered
    assert "INV-101" in rendered
    assert "bob@example.com" not in rendered
    assert "REDACTED" in rendered

