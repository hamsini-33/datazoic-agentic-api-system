import copy

import pytest

from src.config import settings
from src.execution.executor import ExecutionResult
from src.orchestrator.graph import ScalableAgentOrchestrator
from src.orchestrator.llm_client import ProposedAction, ProposedSubgoal
from src.registry.db import ToolRegistryDB
from src.registry.retriever import ToolRetrievalResult


@pytest.fixture
def orchestrator():
    return ScalableAgentOrchestrator(db=ToolRegistryDB(settings.DATABASE_PATH))


@pytest.mark.parametrize("subgoals", [["alpha", "beta"], ["alpha", "beta", "gamma"]])
def test_each_step_retrieves_fresh_candidates_and_preserves_previous_outputs(orchestrator, monkeypatch, subgoals):
    tools = {
        name: orchestrator.db.get_tool_by_name(name)
        for name in ("create_draft_invoice", "list_disputes", "get_account_balances")
    }
    names = list(tools)
    selected = names[:len(subgoals)]
    request = "Complete alpha, beta, and gamma in order"
    queries = []
    planner_inputs = []
    execution_calls = []

    def identify_next_subgoal(user_request, execution_history, scratchpad):
        assert user_request == request
        index = len(execution_history)
        if index >= len(subgoals):
            return ProposedSubgoal(done=True)
        return ProposedSubgoal(done=False, subgoal=subgoals[index])

    def retrieve_candidates(query, top_k=None):
        queries.append((query, top_k))
        index = subgoals.index(query)
        return ToolRetrievalResult(query=query, tools=[tools[selected[index]]])

    def plan_next_action(**kwargs):
        index = len(kwargs["execution_history"])
        planner_inputs.append(copy.deepcopy(kwargs))
        assert kwargs["user_request"] == request
        assert kwargs["current_subgoal"] == subgoals[index]
        assert [candidate["name"] for candidate in kwargs["candidate_tools"]] == [selected[index]]
        for previous in range(index):
            assert kwargs["scratchpad"][f"step_{previous}_id"] == f"ID-{previous}"
        return ProposedAction(done=False, tool_name=selected[index], arguments={})

    class SuccessfulExecutor:
        def execute(self, **kwargs):
            execution_calls.append(kwargs)
            index = len(execution_calls) - 1
            return ExecutionResult(
                status="SUCCESS",
                tool_name=kwargs["tool_name"],
                output={f"step_{index}_id": f"ID-{index}", "step_output": f"result-{index}"},
                execution_id=f"exec-{index}",
                latency_ms=1.0,
            )

    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda _: "API_TOOL")
    monkeypatch.setattr(orchestrator.planner, "identify_next_subgoal", identify_next_subgoal)
    monkeypatch.setattr(orchestrator.planner, "plan_next_action", plan_next_action)
    monkeypatch.setattr(orchestrator, "retriever", type("Retriever", (), {"retrieve_candidates": staticmethod(retrieve_candidates)})())
    monkeypatch.setattr(orchestrator, "execution_engine", SuccessfulExecutor())

    state = orchestrator.run(request)

    assert [query for query, _ in queries] == subgoals
    assert all(top_k == settings.TOP_K_TOOLS for _, top_k in queries)
    assert [call["tool_name"] for call in execution_calls] == selected
    assert [entry["tool_name"] for entry in state["execution_history"]] == selected
    assert state["scratchpad"]["step_output"] == f"result-{len(subgoals) - 1}"
    assert state["scratchpad"]["step_0_id"] == "ID-0"
    assert state["is_complete"] is True


def test_empty_step_retrieval_stops_before_planning_or_dispatch(orchestrator, monkeypatch):
    queries = []
    dispatched = []
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda _: "API_TOOL")
    monkeypatch.setattr(orchestrator.planner, "identify_next_subgoal", lambda **_: ProposedSubgoal(done=False, subgoal="unsupported operation"))

    class EmptyRetriever:
        def retrieve_candidates(self, query, top_k=None):
            queries.append(query)
            return ToolRetrievalResult(
                query=query,
                tools=[],
                clarification_needed=True,
                clarification_message="No relevant API candidate was found for unsupported operation.",
            )

    class ExecutorMustNotRun:
        def execute(self, **kwargs):
            dispatched.append(kwargs)
            raise AssertionError("No action can execute without retrieved candidates")

    monkeypatch.setattr(orchestrator, "retriever", EmptyRetriever())
    monkeypatch.setattr(orchestrator, "execution_engine", ExecutorMustNotRun())
    monkeypatch.setattr(orchestrator.planner, "plan_next_action", lambda **_: pytest.fail("planner must not run without candidates"))

    state = orchestrator.run("Do an unsupported operation")

    assert queries == ["unsupported operation"]
    assert dispatched == []
    assert "No relevant API candidate" in state["final_response"]
    assert state["needs_clarification"] is True
    assert state["is_complete"] is True


def test_failed_intermediate_execution_does_not_retrieve_or_run_later_steps(orchestrator, monkeypatch):
    candidate = orchestrator.db.get_tool_by_name("create_draft_invoice")
    queries = []
    calls = []
    monkeypatch.setattr(orchestrator.planner, "classify_intent", lambda _: "API_TOOL")
    monkeypatch.setattr(orchestrator.planner, "identify_next_subgoal", lambda **_: ProposedSubgoal(done=False, subgoal="first operation"))
    monkeypatch.setattr(
        orchestrator.planner,
        "plan_next_action",
        lambda **_: ProposedAction(done=False, tool_name=candidate.name, arguments={}),
    )

    class OneCandidateRetriever:
        def retrieve_candidates(self, query, top_k=None):
            queries.append(query)
            return ToolRetrievalResult(query=query, tools=[candidate])

    class FailingExecutor:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return ExecutionResult(status="EXECUTION_ERROR", tool_name=kwargs["tool_name"], error_message="step failed")

    monkeypatch.setattr(orchestrator, "retriever", OneCandidateRetriever())
    monkeypatch.setattr(orchestrator, "execution_engine", FailingExecutor())

    state = orchestrator.run("Do the first operation then a second")

    assert queries == ["first operation"]
    assert len(calls) == 1
    assert state["error_context"] == "step failed"
    assert "Execution failed" in state["final_response"]

