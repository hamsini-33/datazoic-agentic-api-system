import main


def test_sensitive_operation_approval_has_no_default_and_requires_yes(monkeypatch):
    captured = {}

    def fake_ask(*args, **kwargs):
        captured.update(kwargs)
        return "yes"

    monkeypatch.setattr(main.Prompt, "ask", fake_ask)

    assert main.ask_for_sensitive_approval() is True
    assert "default" not in captured
    assert captured["choices"] == ["yes", "no"]


def test_sensitive_operation_rejection_does_not_approve(monkeypatch):
    monkeypatch.setattr(main.Prompt, "ask", lambda *args, **kwargs: "no")

    assert main.ask_for_sensitive_approval() is False


def test_cli_forwards_approval_and_rejection_to_orchestrator():
    class StubOrchestrator:
        def __init__(self):
            self.calls = []

        def run(self, user_request, state=None, approval_decision=None):
            self.calls.append((user_request, state, approval_decision))
            return {"approval_decision": approval_decision}

    orchestrator = StubOrchestrator()
    pending_state = {"pending_approval": {"approval_id": "approval_test"}}

    approved = main.submit_approval_decision(orchestrator, "request", pending_state, True)
    rejected = main.submit_approval_decision(orchestrator, "request", pending_state, False)

    assert approved["approval_decision"] is True
    assert rejected["approval_decision"] is False
    assert orchestrator.calls == [
        ("request", pending_state, True),
        ("request", pending_state, False),
    ]


def test_cli_forwards_clarification_response_and_saved_state():
    class StubOrchestrator:
        def __init__(self):
            self.call = None

        def run(self, user_request, state=None, approval_decision=None, clarification_response=None):
            self.call = (user_request, state, approval_decision, clarification_response)
            return {"final_response": "Resumed"}

    orchestrator = StubOrchestrator()
    pending_state = {"pending_clarification": {"clarification_id": "clarification_test"}}

    result = main.submit_clarification_response(
        orchestrator,
        "Create invoice",
        pending_state,
        "Recipient is bob@example.com",
    )

    assert result["final_response"] == "Resumed"
    assert orchestrator.call == (
        "Create invoice",
        pending_state,
        None,
        "Recipient is bob@example.com",
    )
