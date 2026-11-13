"""Streamlit interface for the existing Datazoic agent and auxiliary tools."""

from typing import Any, Dict, Optional

import streamlit as st

from src.config import settings
from src.orchestrator.graph import ScalableAgentOrchestrator
from src.orchestrator.trace import redact_text, redact_trace_value
from src.registry.db import ToolRegistryDB


def create_orchestrator() -> ScalableAgentOrchestrator:
    """Create the existing backend once for the current browser session."""
    db = ToolRegistryDB(settings.DATABASE_PATH)
    return ScalableAgentOrchestrator(db=db)


def initialize_session(session_state, factory=create_orchestrator) -> Optional[str]:
    """Initialize session-owned UI state and retain one orchestrator per session."""
    defaults = {
        "chat_messages": [],
        "pending_request": None,
        "pending_agent_state": None,
        "last_agent_state": None,
        "rag_result": None,
        "system_search_result": None,
        "ui_error": None,
    }
    for key, value in defaults.items():
        if key not in session_state:
            session_state[key] = value

    if "orchestrator" not in session_state:
        try:
            session_state["orchestrator"] = factory()
        except Exception:
            return "The agent could not be initialized. Check the Python environment and local database/vector-store configuration."
    return None


def _session_value(session_state, key: str, default=None):
    try:
        return session_state[key]
    except KeyError:
        return default


def _trace_from_state(agent_state: Optional[Dict[str, Any]]) -> list:
    if not agent_state:
        return []
    return redact_trace_value(agent_state.get("planning_trace", []))


def _execution_status(agent_state: Dict[str, Any]) -> str:
    if agent_state.get("pending_approval"):
        return "Awaiting explicit approval"
    if agent_state.get("pending_clarification"):
        return "Clarification needed"
    if str(agent_state.get("final_response", "")).startswith("Cancelled:"):
        return "Cancelled by user"
    if agent_state.get("error_context"):
        return "Failed"
    if agent_state.get("needs_clarification"):
        return "Clarification needed"
    trace = agent_state.get("planning_trace", [])
    if any(step.get("execution_status") in {"FAILED", "VALIDATION_ERROR", "NO_CANDIDATES"} for step in trace):
        return "Failed"
    return "Completed"


def _assistant_message(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role": "assistant",
        "content": state.get("final_response") or "The request completed without a response message.",
        "status": _execution_status(state),
        "trace": _trace_from_state(state),
    }


def _save_pending(session_state, request: Optional[str], state: Optional[Dict[str, Any]]) -> None:
    if state and state.get("pending_approval"):
        session_state["pending_request"] = state.get("user_request", request)
        # Keep the exact returned AgentState; approval must not be reconstructed.
        session_state["pending_agent_state"] = state
    else:
        session_state["pending_request"] = None
        session_state["pending_agent_state"] = None


def submit_chat_request(session_state, request: str) -> None:
    """Run a new request or answer the one bounded pending clarification."""
    orchestrator = session_state["orchestrator"]
    session_state["ui_error"] = None
    session_state["chat_messages"].append({"role": "user", "content": request})
    try:
        previous_state = _session_value(session_state, "last_agent_state")
        pending_clarification = previous_state.get("pending_clarification") if previous_state else None
        if pending_clarification:
            state = orchestrator.run(
                previous_state.get("user_request", ""),
                state=previous_state,
                clarification_response=request,
            )
        else:
            state = orchestrator.run(request)
    except Exception:
        session_state["ui_error"] = "The agent request failed. No successful result was returned. You can submit the request again."
        return

    session_state["last_agent_state"] = state
    _save_pending(session_state, request, state)
    session_state["chat_messages"].append(_assistant_message(state))


def resolve_pending_approval(session_state, decision: bool) -> None:
    """Resume the saved pending action through the existing approval interface."""
    orchestrator = session_state["orchestrator"]
    request = _session_value(session_state, "pending_request")
    pending_state = _session_value(session_state, "pending_agent_state")
    if not request or not pending_state:
        session_state["ui_error"] = "The pending approval state is unavailable. Start the request again; no action was authorized."
        _save_pending(session_state, None, None)
        return

    session_state["ui_error"] = None
    try:
        # Pass the exact stored state and explicit decision; never rebuild action data.
        state = orchestrator.run(request, state=pending_state, approval_decision=decision)
    except Exception:
        # The approval token is single use. Do not offer a retry against possibly consumed state.
        _save_pending(session_state, None, None)
        session_state["ui_error"] = "Approval resolution failed. The saved approval was not retried; start the request again if needed."
        return

    session_state["last_agent_state"] = state
    _save_pending(session_state, request, state)
    if session_state["chat_messages"] and session_state["chat_messages"][-1]["role"] == "assistant":
        session_state["chat_messages"][-1] = _assistant_message(state)
    else:
        session_state["chat_messages"].append(_assistant_message(state))


def render_execution_trace(trace: list) -> None:
    """Render the existing redacted planning_trace without regenerating it."""
    if not trace:
        return
    st.markdown("#### API retrieval and execution trace")
    for step in trace:
        number = step.get("step_number", "?")
        subgoal = redact_text(str(step.get("subgoal", "API step")))
        with st.expander(f"Step {number}: {subgoal}", expanded=False):
            st.caption(f"Fresh retrieval: {step.get('candidate_source', 'current step candidates')}")
            st.write("**Retrieval query**")
            st.code(redact_text(str(step.get("retrieval_query", ""))), language=None)
            st.write("**Retrieved candidates and relevance**")
            candidates = step.get("retrieved_candidates", [])
            if candidates:
                st.dataframe(
                    [{"API": item.get("name"), "Relevance": item.get("score")} for item in candidates],
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.write("No candidates retrieved.")
            st.write("**Candidates passed to planner**")
            planner_candidates = step.get("planner_candidates", [])
            st.write(", ".join(planner_candidates) if planner_candidates else "None")
            st.write(f"**Selected API:** {step.get('selected_api') or 'None'}")
            http_status = step.get("http_status")
            status = step.get("execution_status", "Unknown")
            st.write(f"**Execution:** {status}" + (f" · HTTP {http_status}" if http_status is not None else ""))
            for label, field in (
                ("Parameters (redacted)", "parameters"),
                ("Prior outputs available to this step", "prior_results"),
                ("Step result", "output"),
            ):
                value = redact_trace_value(step.get(field))
                if value not in (None, {}, []):
                    st.write(f"**{label}**")
                    st.json(value)
            if step.get("error"):
                st.error(redact_text(str(step["error"])))


def render_chat(orchestrator) -> None:
    session_state = st.session_state
    st.subheader("Agent Chat")
    st.caption("Each submitted message is an independent request. The transcript is for display and is not sent as planner memory.")

    for index, message in enumerate(session_state["chat_messages"]):
        with st.chat_message(message["role"]):
            st.markdown(redact_text(message["content"]))
            if message["role"] == "assistant":
                st.caption(f"Execution status: {message.get('status', 'Completed')}")
                if message.get("trace"):
                    render_execution_trace(message["trace"])

    pending_state = _session_value(session_state, "pending_agent_state")
    pending = pending_state.get("pending_approval") if pending_state else None
    last_state = _session_value(session_state, "last_agent_state")
    clarification = last_state.get("pending_clarification") if last_state else None
    if pending:
        st.warning("This operation requires your explicit approval. The prepared action will be submitted exactly as shown.")
        st.markdown(f"**Operation:** `{pending.get('tool_name', 'unknown')}`")
        st.markdown(f"**Workflow step:** `{pending.get('workflow_step_id', 'unknown')}`")
        st.markdown("**Prepared parameters (sensitive values redacted):**")
        st.json(redact_trace_value(pending.get("arguments", {})))
        st.info(redact_text(pending.get("prompt", "Review the prepared action before deciding.")))
        approval_id = pending.get("approval_id", "pending")
        approve_col, reject_col = st.columns(2)
        with approve_col:
            if st.button("Approve this action", key=f"approve_{approval_id}", type="primary"):
                resolve_pending_approval(session_state, True)
                st.rerun()
        with reject_col:
            if st.button("Reject this action", key=f"reject_{approval_id}"):
                resolve_pending_approval(session_state, False)
                st.rerun()

    if _session_value(session_state, "ui_error"):
        st.error(session_state["ui_error"])

    if clarification and not pending:
        st.info(clarification.get("prompt", "Please provide the missing required information."))
    input_prompt = "Provide the requested details" if clarification and not pending else "Ask the agent to discover or run a mock PayPal API"
    request = st.chat_input(input_prompt, disabled=bool(pending), key="agent_chat_input")
    if request:
        submit_chat_request(session_state, request)
        st.rerun()


def render_rag(orchestrator) -> None:
    st.subheader("Knowledge RAG")
    st.caption("Search the separate knowledge-document collection. This does not run PayPal API operations.")
    with st.form("knowledge_rag_form"):
        query = st.text_input("Question about the local knowledge documents", key="rag_query")
        submitted = st.form_submit_button("Search documents")
    if submitted and query.strip():
        st.session_state["rag_result"] = None
        try:
            st.session_state["rag_result"] = orchestrator.rag_tool.run(query.strip())
            st.session_state["ui_error"] = None
        except Exception:
            st.session_state["ui_error"] = "Knowledge RAG failed. Check the local document index and configured answer provider."
    result = _session_value(st.session_state, "rag_result")
    if result:
        safe_result = redact_trace_value(result)
        st.markdown("#### Answer")
        st.write(safe_result.get("answer") or safe_result.get("answer_context", "No answer was returned."))
        st.caption(f"Answer mode: {safe_result.get('answer_mode', 'unknown')} · Evidence sufficient: {safe_result.get('evidence_sufficient', False)}")
        sources = safe_result.get("sources", [])
        if sources:
            st.markdown("#### Sources")
            st.dataframe(sources, hide_index=True, use_container_width=True)
        elif not safe_result.get("found", False):
            st.info(safe_result.get("message", "No matching documents were found."))
    if _session_value(st.session_state, "ui_error"):
        st.error(st.session_state["ui_error"])


def render_system_search(orchestrator) -> None:
    st.subheader("System Search")
    st.caption("Search registered capabilities or SQLite execution history. This does not invoke API discovery or execution.")
    with st.form("system_search_form"):
        search_type = st.selectbox("Search", ["auto", "capabilities", "history"], key="system_search_type")
        query = st.text_input("System search query", key="system_search_query")
        submitted = st.form_submit_button("Search system")
    if submitted and query.strip():
        st.session_state["system_search_result"] = None
        try:
            st.session_state["system_search_result"] = orchestrator.system_search_tool.run(
                query.strip(), search_type=search_type,
            )
            st.session_state["ui_error"] = None
        except Exception:
            st.session_state["ui_error"] = "System Search failed while querying the registry or execution history."
    result = _session_value(st.session_state, "system_search_result")
    if result:
        safe_result = redact_trace_value(result)
        if safe_result.get("search_type") == "capabilities":
            st.write(safe_result.get("message", "Capability search completed."))
            if safe_result.get("tools"):
                st.dataframe(safe_result["tools"], hide_index=True, use_container_width=True)
            elif not safe_result.get("total_matching_tools"):
                st.info("No matching capabilities were found.")
        else:
            st.write(safe_result.get("message", "Execution history search completed."))
            records = safe_result.get("recent_history") or safe_result.get("logs") or []
            if records:
                st.dataframe(records, hide_index=True, use_container_width=True)
            elif not safe_result.get("found", False):
                st.info("No matching execution records were found.")
    if _session_value(st.session_state, "ui_error"):
        st.error(st.session_state["ui_error"])


def render_app() -> None:
    st.set_page_config(page_title="Datazoic Agent", page_icon="🤖", layout="wide")
    st.title("Datazoic Agentic API Discovery & Execution")
    st.caption("LangGraph orchestration · semantic API discovery · mock PayPal execution · separate Knowledge RAG and System Search")

    initialization_error = initialize_session(st.session_state)
    if initialization_error:
        st.error(initialization_error)
        st.stop()

    orchestrator = st.session_state["orchestrator"]
    st.sidebar.markdown("### Runtime")
    st.sidebar.write(f"Planner provider: `{settings.LLM_PROVIDER}`")
    st.sidebar.write("PayPal execution: **Mock only**")
    st.sidebar.caption("Pending approvals live only in this browser session. If it is lost or refreshed, submit the request again; no action is authorized by a lost approval.")
    mode = st.sidebar.radio("Workspace", ["Agent Chat", "Knowledge RAG", "System Search"], key="workspace_mode")

    if _session_value(st.session_state, "pending_agent_state") and mode != "Agent Chat":
        st.warning("An API action is awaiting approval. Return to Agent Chat to approve or reject it.")
    if mode == "Agent Chat":
        render_chat(orchestrator)
    elif mode == "Knowledge RAG":
        render_rag(orchestrator)
    else:
        render_system_search(orchestrator)


if __name__ == "__main__":
    render_app()
