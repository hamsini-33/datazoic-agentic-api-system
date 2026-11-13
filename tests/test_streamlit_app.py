from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "streamlit_app.py"


TRACE = [{
    "step_number": 1,
    "subgoal": "Create invoice for [REDACTED_EMAIL]",
    "retrieval_query": "Create invoice for [REDACTED_EMAIL]",
    "candidate_source": "fresh retrieval for this step",
    "retrieved_candidates": [{"name": "create_draft_invoice", "score": 0.91}],
    "planner_candidates": ["create_draft_invoice"],
    "selected_api": "create_draft_invoice",
    "parameters": {"recipient_email": "[REDACTED]", "amount": 50.0},
    "prior_results": {},
    "execution_status": "SUCCESS",
    "http_status": 201,
    "output": {"invoice_id": "INV-101", "recipient_email": "[REDACTED]"},
}]


class FakeRag:
    def __init__(self):
        self.calls = []

    def run(self, query):
        self.calls.append(query)
        if isinstance(self.result, Exception):
            raise self.result
        if self.result is not None:
            return self.result
        return {
            "found": True,
            "answer": "Use the document process.",
            "answer_mode": "extractive_offline",
            "evidence_sufficient": True,
            "sources": [{"file": "guide.md", "section": "Invoices", "relevance": 0.9}],
        }

    result = None


class FakeSystemSearch:
    def __init__(self):
        self.calls = []

    def run(self, query, search_type="auto"):
        self.calls.append((query, search_type))
        if isinstance(self.result, Exception):
            raise self.result
        if self.result is not None:
            return self.result
        return {
            "search_type": "capabilities",
            "query": query,
            "found": True,
            "total_matching_tools": 1,
            "tools": [{"name": "list_disputes", "category": "disputes", "endpoint": "GET /v1/disputes"}],
        }

    result = None


class FakeOrchestrator:
    def __init__(self, run_results=None):
        self.run_results = list(run_results or [])
        self.run_calls = []
        self.rag_tool = FakeRag()
        self.system_search_tool = FakeSystemSearch()

    def run(self, request, state=None, approval_decision=None, clarification_response=None):
        self.run_calls.append({
            "request": request,
            "state": state,
            "decision": approval_decision,
            "clarification_response": clarification_response,
        })
        result = self.run_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def completed_state(response="Invoice created.", trace=None):
    return {
        "final_response": response,
        "is_complete": True,
        "planning_trace": TRACE if trace is None else trace,
        "execution_history": [],
    }


def pending_state(approval_id="approval-1"):
    return {
        "final_response": "APPROVAL REQUIRED",
        "is_complete": False,
        "planning_trace": TRACE,
        "pending_approval": {
            "approval_id": approval_id,
            "tool_name": "send_invoice",
            "arguments": {"invoice_id": "INV-101"},
            "workflow_step_id": "trace-1:step:2",
            "prompt": "Confirm sending invoice INV-101?",
        },
    }


def clarification_state():
    return {
        "user_request": "Create an invoice",
        "original_user_request": "Create an invoice",
        "final_response": "I need the recipient's email address. Please provide it. No action was executed.",
        "is_complete": True,
        "needs_clarification": True,
        "pending_clarification": {
            "clarification_id": "clarification-1",
            "tool_name": "create_draft_invoice",
            "missing_parameters": ["recipient_email"],
            "prompt": "I need the recipient's email address. Please provide it. No action was executed.",
        },
    }


def app_with(orchestrator):
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.session_state["orchestrator"] = orchestrator
    app.run()
    assert not app.exception
    return app


def test_successful_chat_renders_response_execution_status_and_trace():
    backend = FakeOrchestrator([completed_state("Mock invoice created.")])
    app = app_with(backend)

    app.chat_input[0].set_value("Create a draft invoice").run()

    assert len(backend.run_calls) == 1
    assert backend.run_calls[0]["request"] == "Create a draft invoice"
    output = "\n".join(str(element.value) for element in app.markdown)
    output += "\n" + "\n".join(str(element.value) for element in app.caption)
    assert "Mock invoice created." in output
    assert "Execution status: Completed" in output
    assert "create_draft_invoice" in output
    assert "Fresh retrieval" in output
    assert "INV-101" in "\n".join(str(element.value) for element in app.json)


def test_same_orchestrator_instance_survives_streamlit_reruns():
    backend = FakeOrchestrator([completed_state()])
    app = app_with(backend)
    stored = app.session_state["orchestrator"]

    app.radio[0].set_value("System Search").run()
    app.radio[0].set_value("Agent Chat").run()

    assert app.session_state["orchestrator"] is stored is backend


def test_each_chat_submission_is_independent_not_previous_transcript_memory():
    backend = FakeOrchestrator([completed_state("First response"), completed_state("Second response")])
    app = app_with(backend)

    app.chat_input[0].set_value("First independent request").run()
    app.chat_input[0].set_value("Second independent request").run()

    assert [call["request"] for call in backend.run_calls] == ["First independent request", "Second independent request"]
    assert all(call["state"] is None for call in backend.run_calls)


def test_chat_followup_resumes_only_the_pending_clarification():
    clarification = clarification_state()
    backend = FakeOrchestrator([clarification, completed_state("Invoice created.")])
    app = app_with(backend)

    app.chat_input[0].set_value("Create an invoice").run()
    assert "Clarification needed" in "\n".join(str(item.value) for item in app.caption)
    app.chat_input[0].set_value("Recipient is bob@example.com").run()

    assert backend.run_calls[0]["state"] is None
    assert backend.run_calls[1]["state"] is clarification
    assert backend.run_calls[1]["request"] == "Create an invoice"
    assert backend.run_calls[1]["clarification_response"] == "Recipient is bob@example.com"


@pytest.mark.parametrize("decision,button", [(True, "Approve this action"), (False, "Reject this action")])
def test_approval_forwards_exact_saved_request_state_and_decision(decision, button):
    pending = pending_state()
    resolution = completed_state("Approved action handled.") if decision else completed_state("Cancelled: `send_invoice` was rejected and was not executed.")
    if not decision:
        resolution["error_context"] = "Cancelled by user"
    backend = FakeOrchestrator([pending, resolution])
    app = app_with(backend)
    app.chat_input[0].set_value("Send the invoice").run()
    saved_state = app.session_state["pending_agent_state"]
    saved_request = app.session_state["pending_request"]

    app.button(key=f"{'approve' if decision else 'reject'}_approval-1").click().run()

    assert backend.run_calls[1]["request"] == saved_request == "Send the invoice"
    assert backend.run_calls[1]["state"] is saved_state is pending
    assert backend.run_calls[1]["decision"] is decision
    assert app.session_state["pending_agent_state"] is None
    expected_status = "Completed" if decision else "Cancelled by user"
    assert app.session_state["chat_messages"][-1]["status"] == expected_status


def test_subsequent_sensitive_action_retains_its_fresh_pending_approval():
    backend = FakeOrchestrator([
        pending_state("approval-1"),
        pending_state("approval-2"),
        completed_state("Both approved operations handled."),
    ])
    app = app_with(backend)
    app.chat_input[0].set_value("Perform two sensitive actions").run()
    first_state = app.session_state["pending_agent_state"]

    app.button(key="approve_approval-1").click().run()

    second_state = app.session_state["pending_agent_state"]
    assert second_state is not first_state
    assert second_state["pending_approval"]["approval_id"] == "approval-2"
    assert len(backend.run_calls) == 2
    assert backend.run_calls[1]["decision"] is True
    assert app.button(key="approve_approval-2")


def test_rag_calls_only_the_existing_rag_tool():
    backend = FakeOrchestrator()
    app = app_with(backend)
    app.radio[0].set_value("Knowledge RAG").run()
    app.text_input(key="rag_query").set_value("How do invoices work?")
    app.button(key="FormSubmitter:knowledge_rag_form-Search documents").click().run()

    assert backend.run_calls == []
    assert backend.rag_tool.calls == ["How do invoices work?"]
    output = "\n".join(str(element.value) for element in app.markdown)
    assert "Use the document process." in output


def test_system_search_calls_only_the_existing_system_search_tool():
    backend = FakeOrchestrator()
    app = app_with(backend)
    app.radio[0].set_value("System Search").run()
    app.selectbox(key="system_search_type").set_value("capabilities")
    app.text_input(key="system_search_query").set_value("invoice capabilities")
    app.button(key="FormSubmitter:system_search_form-Search system").click().run()

    assert backend.run_calls == []
    assert backend.system_search_tool.calls == [("invoice capabilities", "capabilities")]
    output = "\n".join(str(element.value) for element in app.dataframe)
    assert "list_disputes" in output


def test_chat_errors_are_displayed_without_success_message():
    backend = FakeOrchestrator([RuntimeError("provider unavailable")])
    app = app_with(backend)

    app.chat_input[0].set_value("Run a request").run()

    errors = "\n".join(str(element.value) for element in app.error)
    assert "request failed" in errors.lower()
    assert "successfully" not in errors.lower()
    assert not any("successfully" in str(element.value).lower() for element in app.success)


def test_lost_pending_state_fails_closed_and_requests_a_new_submission():
    backend = FakeOrchestrator()
    app = app_with(backend)
    app.session_state["pending_request"] = None
    app.session_state["pending_agent_state"] = None

    from streamlit_app import resolve_pending_approval
    resolve_pending_approval(app.session_state, True)

    assert backend.run_calls == []
    assert "start the request again" in app.session_state["ui_error"].lower()


@pytest.mark.parametrize(
    "mode,tool_attr,form_key,form_submit,error_text",
    [
        ("Knowledge RAG", "rag_tool", "rag_query", "FormSubmitter:knowledge_rag_form-Search documents", "Knowledge RAG failed"),
        ("System Search", "system_search_tool", "system_search_query", "FormSubmitter:system_search_form-Search system", "System Search failed"),
    ],
)
def test_auxiliary_errors_are_shown_without_api_execution(mode, tool_attr, form_key, form_submit, error_text):
    backend = FakeOrchestrator()
    getattr(backend, tool_attr).result = RuntimeError("local auxiliary failure")
    app = app_with(backend)
    app.radio[0].set_value(mode).run()
    app.text_input(key=form_key).set_value("test query")
    app.button(key=form_submit).click().run()

    assert backend.run_calls == []
    assert any(error_text in str(element.value) for element in app.error)


def test_ui_trace_redacts_sensitive_values():
    trace = [dict(TRACE[0], output={"invoice_id": "INV-101", "recipient_email": "alice@example.com"})]
    backend = FakeOrchestrator([completed_state("Done", trace=trace)])
    app = app_with(backend)
    app.chat_input[0].set_value("Create invoice for alice@example.com").run()

    rendered = "\n".join(str(element.value) for element in list(app.markdown) + list(app.code))
    rendered += "\n" + "\n".join(str(element.value) for element in app.json)
    assert "alice@example.com" not in rendered
    assert "[REDACTED]" in rendered or "[REDACTED_EMAIL]" in rendered
