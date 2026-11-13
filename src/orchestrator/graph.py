import copy
import json
import uuid
from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END
from src.config import settings
from src.registry.db import ToolRegistryDB
from src.registry.retriever import SemanticToolRetriever
from src.execution.executor import SafeExecutionEngine
from src.auxiliary.rag_tool import RAGPipelineTool
from src.auxiliary.system_search_tool import SystemSearchTool
from src.binder.dynamic_binder import JustInTimeToolBinder
from src.orchestrator.state import AgentState
from src.orchestrator.llm_client import LLMPlanningClient
from src.orchestrator.trace import redact_trace_value, redact_text

class ScalableAgentOrchestrator:
    def __init__(
        self,
        db: Optional[ToolRegistryDB] = None,
        retriever: Optional[SemanticToolRetriever] = None,
        execution_engine: Optional[SafeExecutionEngine] = None,
        rag_tool: Optional[RAGPipelineTool] = None,
        system_search_tool: Optional[SystemSearchTool] = None
    ):
        self.db = db or ToolRegistryDB(settings.DATABASE_PATH)
        self.retriever = retriever or SemanticToolRetriever(self.db)
        self.execution_engine = execution_engine or SafeExecutionEngine(self.db)
        self.rag_tool = rag_tool or RAGPipelineTool()
        self.system_search_tool = system_search_tool or SystemSearchTool(self.db)
        self.planner = LLMPlanningClient()
        # In-process authoritative copies prevent modified graph state from
        # changing the action a user approved. Tokens are consumed before use.
        self._pending_approvals: Dict[str, Dict[str, Any]] = {}
        self._pending_clarifications: Dict[str, Dict[str, Any]] = {}

        self.app = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(AgentState)

        # Register Graph Nodes
        graph.add_node("route_start", self._route_start_node)
        graph.add_node("classify_intent", self._classify_intent_node)
        graph.add_node("identify_next_subgoal", self._identify_next_subgoal_node)
        graph.add_node("discover_tools", self._discover_tools_node)
        graph.add_node("plan_and_execute", self._plan_and_execute_node)
        graph.add_node("handle_rag", self._rag_node)
        graph.add_node("handle_system_search", self._system_search_node)
        graph.add_node("clarification", self._clarification_node)
        graph.add_node("synthesize", self._synthesis_node)
        graph.add_node("resolve_approval", self._resolve_approval_node)
        graph.add_node("resume_clarification", self._resume_clarification_node)

        # Resume an explicit approval decision without classifying or planning.
        graph.set_entry_point("route_start")
        graph.add_conditional_edges(
            "route_start",
            self._route_start,
            {
                "CLASSIFY": "classify_intent",
                "WAIT": "synthesize",
                "RESOLVE_APPROVAL": "resolve_approval",
                "RESUME_CLARIFICATION": "resume_clarification",
            }
        )
        graph.add_edge("resume_clarification", "discover_tools")

        # Intent Conditional Routing
        graph.add_conditional_edges(
            "classify_intent",
            self._route_intent,
            {
                "RAG_DOCS": "handle_rag",
                "SYSTEM_SEARCH": "handle_system_search",
                "CLARIFICATION": "clarification",
                "API_TOOL": "identify_next_subgoal"
            }
        )

        graph.add_conditional_edges(
            "identify_next_subgoal",
            lambda s: "DONE" if s.get("subgoal_complete") else "RETRIEVE",
            {"DONE": "synthesize", "RETRIEVE": "discover_tools"},
        )

        # Routing from Tool Discovery
        graph.add_conditional_edges(
            "discover_tools",
            lambda s: "synthesize" if s.get("needs_clarification") else "plan_and_execute",
            {
                "synthesize": "synthesize",
                "plan_and_execute": "plan_and_execute"
            }
        )

        # Execution Loop / Chaining Router
        graph.add_conditional_edges(
            "plan_and_execute",
            self._route_execution,
            {
                "LOOP_NEXT_STEP": "identify_next_subgoal",
                "APPROVAL_HALT": "synthesize",
                "DONE": "synthesize"
            }
        )

        graph.add_conditional_edges(
            "resolve_approval",
            self._route_execution,
            {
                "LOOP_NEXT_STEP": "identify_next_subgoal",
                "APPROVAL_HALT": "synthesize",
                "DONE": "synthesize"
            }
        )

        # Terminal Transitions
        graph.add_edge("handle_rag", "synthesize")
        graph.add_edge("handle_system_search", "synthesize")
        graph.add_edge("clarification", END)
        graph.add_edge("synthesize", END)

        return graph.compile()

    def _route_start_node(self, state: AgentState) -> Dict[str, Any]:
        return {}

    @staticmethod
    def _route_start(state: AgentState) -> str:
        if state.get("clarification_resume_tool"):
            return "RESUME_CLARIFICATION"
        if state.get("pending_approval"):
            return "RESOLVE_APPROVAL" if state.get("approval_decision") is not None else "WAIT"
        if state.get("approval_decision") is not None:
            return "RESOLVE_APPROVAL"
        return "CLASSIFY"

    def _resume_clarification_node(self, state: AgentState) -> Dict[str, Any]:
        """Retry only the action that previously failed required-field validation."""
        subgoal = state.get("current_subgoal") or state["user_request"]
        response = state.get("clarification_response", "")
        planning_trace = copy.deepcopy(state.get("planning_trace", []))
        if planning_trace:
            planning_trace[-1]["clarification_attempt"] = state.get("clarification_attempts", 1)
            planning_trace[-1]["clarification_response"] = redact_text(response)
            planning_trace[-1]["execution_status"] = "RETRIEVAL_PENDING"
        return {
            "candidate_query": f"{subgoal}. Additional user details: {response}",
            "candidate_tools": [],
            "planning_trace": planning_trace,
            "needs_clarification": False,
            "error_context": None,
            "is_complete": False,
        }

    # =========================================================================
    # NODE IMPLEMENTATIONS
    # =========================================================================
    def _classify_intent_node(self, state: AgentState) -> Dict[str, Any]:
        user_req = state["user_request"]
        trace = state.get("trace_id") or f"trace_{uuid.uuid4().hex[:8]}"
        intent = self.planner.classify_intent(user_req)
        return {
            "intent": intent,
            "trace_id": trace,
            "scratchpad": state.get("scratchpad") or {},
            "execution_history": state.get("execution_history") or []
        }

    def _route_intent(self, state: AgentState) -> str:
        return state.get("intent", "API_TOOL")

    def _identify_next_subgoal_node(self, state: AgentState) -> Dict[str, Any]:
        proposal = self.planner.identify_next_subgoal(
            user_request=state["user_request"],
            execution_history=state.get("execution_history", []),
            scratchpad=state.get("scratchpad", {}),
        )
        if proposal.done:
            return {
                "current_subgoal": "",
                "candidate_query": "",
                "candidate_tools": [],
                "subgoal_complete": True,
                "needs_clarification": bool(proposal.clarification),
                "final_response": proposal.clarification or state.get("final_response"),
                "is_complete": True,
            }
        query = proposal.subgoal.strip()
        planning_trace = copy.deepcopy(state.get("planning_trace", []))
        planning_trace.append({
            "step_number": len(state.get("execution_history", [])) + 1,
            "subgoal": redact_text(query),
            "retrieval_query": redact_text(query),
            "candidate_source": "fresh retrieval for this step",
            "retrieved_candidates": [],
            "planner_candidates": [],
            "selected_api": None,
            "parameters": {},
            "prior_results": redact_trace_value(state.get("scratchpad", {})),
            "execution_status": "RETRIEVAL_PENDING",
            "http_status": None,
            "output": None,
        })
        return {
            "current_subgoal": query,
            "candidate_query": query,
            "candidate_tools": [],
            "planning_trace": planning_trace,
            "subgoal_complete": False,
            "needs_clarification": False,
        }

    def _discover_tools_node(self, state: AgentState) -> Dict[str, Any]:
        query = state.get("candidate_query") or state.get("current_subgoal") or state["user_request"]
        retrieval_res = self.retriever.retrieve_candidates(query, top_k=settings.TOP_K_TOOLS)
        planning_trace = copy.deepcopy(state.get("planning_trace", []))
        if planning_trace:
            trace_step = planning_trace[-1]
            trace_step["retrieval_query"] = redact_text(query)
            trace_step["retrieved_candidates"] = [
                {
                    "name": tool.name,
                    "score": retrieval_res.scores.get(tool.tool_id),
                }
                for tool in retrieval_res.tools
            ]
            trace_step["planner_candidates"] = [tool.name for tool in retrieval_res.tools]

        if retrieval_res.clarification_needed or not retrieval_res.tools:
            if planning_trace:
                planning_trace[-1]["execution_status"] = "NO_CANDIDATES"
            return {
                "candidate_tools": [],
                "planning_trace": planning_trace,
                "needs_clarification": True,
                "is_complete": True,
                "final_response": retrieval_res.clarification_message or f"I couldn't find a relevant API for the next step: {query}. Please clarify the request."
            }

        candidate_tools_dict = [t.model_dump() for t in retrieval_res.tools]
        return {
            "candidate_tools": candidate_tools_dict,
            "planning_trace": planning_trace,
            "needs_clarification": False,
        }

    def _plan_and_execute_node(self, state: AgentState) -> Dict[str, Any]:
        scratchpad = dict(state.get("scratchpad", {}))
        history = list(state.get("execution_history", []))

        if len(history) >= 8:
            return {
                "error_context": "The workflow reached the maximum of 8 API steps.",
                "is_complete": True,
                "final_response": "I stopped this workflow after 8 API steps to avoid an unbounded execution loop."
            }

        proposal = self.planner.plan_next_action(
            user_request=state["user_request"],
            current_subgoal=state.get("current_subgoal") or state["user_request"],
            candidate_tools=state.get("candidate_tools", []),
            scratchpad=scratchpad,
            execution_history=history,
        )
        if proposal.done:
            planning_trace = self._update_trace_entry(state, len(history) + 1, {
                "execution_status": "PLANNER_DONE",
                "planner_note": redact_text(proposal.clarification or "No API action selected."),
            })
            return {
                "is_complete": True,
                "final_response": proposal.clarification or "Request completed.",
                "planned_action": None,
                "needs_clarification": bool(proposal.clarification),
                "planning_trace": planning_trace,
            }

        current_subgoal = proposal.tool_name
        args = proposal.arguments

        expected_resume_tool = state.get("clarification_resume_tool")
        if expected_resume_tool and current_subgoal != expected_resume_tool:
            planning_trace = self._update_trace_entry(state, len(history) + 1, {
                "selected_api": current_subgoal,
                "execution_status": "REJECTED_CLARIFICATION_ACTION_MISMATCH",
            })
            return {
                "planning_trace": planning_trace,
                "error_context": "The clarification retry selected a different API than the incomplete action.",
                "is_complete": True,
                "final_response": "I couldn't safely resume the incomplete action. Please start the request again.",
            }

        # Enforce candidate membership again at the orchestration boundary.
        candidate_names = {t.get("name") for t in state.get("candidate_tools", [])}
        if current_subgoal not in candidate_names:
            planning_trace = self._update_trace_entry(state, len(history) + 1, {
                "selected_api": current_subgoal,
                "parameters": redact_trace_value(args),
                "execution_status": "REJECTED_OUTSIDE_CANDIDATES",
            })
            return {
                "planning_trace": planning_trace,
                "error_context": "Planner selected a tool outside the retrieved candidate set.",
                "is_complete": True,
                "final_response": "I stopped because the proposed API was not among the retrieved candidates. Please clarify the request."
            }

        # A new action always starts unapproved. Approval is consumed only by
        # the dedicated resolver for the exact prepared action.
        state_with_selection = dict(state)
        state_with_selection["clarification_resume_tool"] = None
        state_with_selection["planning_trace"] = self._update_trace_entry(state, len(history) + 1, {
            "selected_api": current_subgoal,
            "parameters": redact_trace_value(args),
            "execution_status": "EXECUTION_PENDING",
        })
        exec_res = self.execution_engine.execute(
            tool_name=current_subgoal,
            arguments=args,
            user_request=state["user_request"],
            confirmed=False,
            trace_id=state.get("trace_id")
        )

        step_id = f"{state.get('trace_id')}:step:{len(history) + 1}"
        return self._execution_result_update(state_with_selection, current_subgoal, args, exec_res, step_id)

    @staticmethod
    def _update_trace_entry(state: AgentState, step_number: int, updates: Dict[str, Any]) -> List[Dict[str, Any]]:
        trace = copy.deepcopy(state.get("planning_trace", []))
        for item in trace:
            if item.get("step_number") == step_number:
                item.update(updates)
                break
        return trace

    def _execution_result_update(
        self,
        state: AgentState,
        tool_name: str,
        arguments: Dict[str, Any],
        exec_res: Any,
        workflow_step_id: str,
    ) -> Dict[str, Any]:
        scratchpad = dict(state.get("scratchpad", {}))
        history = list(state.get("execution_history", []))
        trace_updates: Dict[str, Any] = {
            "http_status": getattr(exec_res, "http_status", None),
            "output": redact_trace_value(getattr(exec_res, "output", None)),
        }

        if exec_res.status == "PREPARED":
            trace_updates["execution_status"] = "AWAITING_APPROVAL"
            approval_id = f"approval_{uuid.uuid4().hex}"
            pending = {
                "approval_id": approval_id,
                "tool_name": tool_name,
                "arguments": copy.deepcopy(arguments),
                "workflow_step_id": workflow_step_id,
                "trace_id": state.get("trace_id"),
                "prepared_execution_id": exec_res.execution_id,
                "prompt": exec_res.approval_prompt,
            }
            authoritative = copy.deepcopy(pending)
            authoritative["user_request"] = state["user_request"]
            self._pending_approvals[approval_id] = authoritative
            return {
                "planning_trace": self._update_trace_entry(state, len(history) + 1, trace_updates),
                "pending_approval": pending,
                "approval_decision": None,
                "clarification_resume_tool": None,
                "planned_action": {"tool_name": tool_name, "arguments": copy.deepcopy(arguments)},
                "is_complete": False,
            }

        if exec_res.status == "VALIDATION_ERROR":
            trace_updates.update({
                "execution_status": "VALIDATION_ERROR",
                "error": redact_text(exec_res.error_message or "Argument validation failed."),
            })
            missing_required = [
                error.split("'", 2)[1]
                for error in getattr(exec_res, "validation_errors", [])
                if error.startswith("Missing required parameter: '") and "'" in error
            ]
            clarification_attempts = int(state.get("clarification_attempts", 0))
            if missing_required and clarification_attempts < 1:
                labels = {
                    "recipient_email": "the recipient's email address",
                    "amount": "the invoice amount",
                    "description": "a description of the goods or services",
                }
                needed = [labels.get(name, f"'{name}'") for name in missing_required]
                if len(needed) == 1:
                    needed_text = needed[0]
                else:
                    needed_text = ", ".join(needed[:-1]) + " and " + needed[-1]
                prompt = f"I need {needed_text} before I can safely continue. Please provide it. No action was executed."
                pending_clarification = {
                    "clarification_id": f"clarification_{uuid.uuid4().hex}",
                    "tool_name": tool_name,
                    "missing_parameters": missing_required,
                    "prompt": prompt,
                }
                return {
                    "planning_trace": self._update_trace_entry(state, len(history) + 1, trace_updates),
                    "pending_approval": None,
                    "approval_decision": None,
                    "clarification_resume_tool": None,
                    "pending_clarification": pending_clarification,
                    "needs_clarification": True,
                    "error_context": None,
                    "is_complete": True,
                    "final_response": prompt,
                }
            return {
                "planning_trace": self._update_trace_entry(state, len(history) + 1, trace_updates),
                "pending_approval": None,
                "approval_decision": None,
                "clarification_resume_tool": None,
                "error_context": exec_res.error_message,
                "is_complete": True,
                "final_response": f"Operation halted due to invalid arguments: {exec_res.error_message}"
            }

        if exec_res.status == "SUCCESS":
            trace_updates["execution_status"] = "SUCCESS"
            if exec_res.output:
                scratchpad.update(exec_res.output)

            history.append({
                "tool_name": tool_name,
                "subgoal": tool_name,
                "arguments": copy.deepcopy(arguments),
                "output": exec_res.output,
                "latency_ms": exec_res.latency_ms,
                "execution_id": exec_res.execution_id,
                "trace_id": state.get("trace_id"),
                "workflow_step_id": workflow_step_id,
            })

            return {
                "planning_trace": self._update_trace_entry(state, len(history), trace_updates),
                "scratchpad": scratchpad,
                "execution_history": history,
                "pending_approval": None,
                "approval_decision": None,
                "clarification_resume_tool": None,
                "error_context": None,
                "planned_action": {"tool_name": tool_name, "arguments": copy.deepcopy(arguments)},
                "is_complete": False,
            }

        trace_updates.update({
            "execution_status": "FAILED",
            "error": redact_text(exec_res.error_message or "Execution failed."),
        })
        return {
            "planning_trace": self._update_trace_entry(state, len(history) + 1, trace_updates),
            "pending_approval": None,
            "approval_decision": None,
            "clarification_resume_tool": None,
            "error_context": exec_res.error_message,
            "is_complete": True,
            "final_response": f"Execution failed: {exec_res.error_message}"
        }

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def _approval_state_matches(self, state: AgentState, authoritative: Dict[str, Any]) -> bool:
        pending = state.get("pending_approval") or {}
        history = state.get("execution_history", [])
        expected_step_id = f"{authoritative['trace_id']}:step:{len(history) + 1}"
        fields_match = (
            bool(authoritative.get("prepared_execution_id"))
            and pending.get("approval_id") == authoritative["approval_id"]
            and pending.get("tool_name") == authoritative["tool_name"]
            and pending.get("trace_id") == authoritative["trace_id"]
            and state.get("trace_id") == authoritative["trace_id"]
            and pending.get("workflow_step_id") == authoritative["workflow_step_id"] == expected_step_id
            and pending.get("prepared_execution_id") == authoritative["prepared_execution_id"]
            and state.get("user_request") == authoritative["user_request"]
        )
        try:
            arguments_match = self._canonical_json(pending.get("arguments")) == self._canonical_json(authoritative["arguments"])
        except (TypeError, ValueError):
            arguments_match = False
        return fields_match and arguments_match

    def _record_approval_decision(
        self,
        action: Dict[str, Any],
        decision: str,
        reason: Optional[str] = None,
        attempted_action: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.execution_engine.record_approval_decision(
            decision=decision,
            approval_id=action.get("approval_id", "unknown"),
            tool_name=action.get("tool_name", "unknown"),
            arguments=copy.deepcopy(action.get("arguments") or {}),
            trace_id=action.get("trace_id") or "trace_unknown",
            workflow_step_id=action.get("workflow_step_id", "unknown"),
            prepared_execution_id=action.get("prepared_execution_id"),
            user_request=action.get("user_request", ""),
            reason=reason,
            attempted_action=attempted_action,
        )

    def _resolve_approval_node(self, state: AgentState) -> Dict[str, Any]:
        pending = state.get("pending_approval") or {}
        decision = state.get("approval_decision")
        claimed_id = pending.get("approval_id")

        # Consume a recognized token before validating or executing. Any later
        # failure therefore requires a newly prepared action and approval.
        authoritative = self._pending_approvals.pop(claimed_id, None) if claimed_id else None
        if authoritative is None and pending.get("prepared_execution_id"):
            # If only the approval ID was altered, identify and consume the
            # original token by its other server-issued workflow references.
            matching_token = next((
                token for token, action in self._pending_approvals.items()
                if action.get("prepared_execution_id") == pending.get("prepared_execution_id")
                and action.get("trace_id") == state.get("trace_id")
                and action.get("workflow_step_id") == pending.get("workflow_step_id")
            ), None)
            if matching_token:
                authoritative = self._pending_approvals.pop(matching_token)
        if authoritative is None:
            rejected_action = {
                **pending,
                "user_request": state.get("user_request", ""),
                "trace_id": pending.get("trace_id") or state.get("trace_id"),
            }
            self._record_approval_decision(
                rejected_action,
                "REJECTED",
                reason="Approval ID is missing, unknown, or already consumed.",
            )
            message = "Approval could not be validated or has already been used. The action was not executed."
            return {
                "pending_approval": None,
                "approval_decision": None,
                "error_context": message,
                "final_response": message,
                "is_complete": True,
            }

        if not self._approval_state_matches(state, authoritative) or type(decision) is not bool:
            attempted = {
                "approval_id": claimed_id,
                "tool_name": pending.get("tool_name"),
                "arguments": pending.get("arguments"),
                "trace_id": state.get("trace_id"),
                "workflow_step_id": pending.get("workflow_step_id"),
                "prepared_execution_id": pending.get("prepared_execution_id"),
            }
            self._record_approval_decision(
                authoritative,
                "REJECTED",
                reason="Approval state did not match the prepared action.",
                attempted_action=attempted,
            )
            message = "Approval state did not match the prepared action. The action was not executed; please start again."
            return {
                "pending_approval": None,
                "approval_decision": None,
                "error_context": message,
                "final_response": message,
                "is_complete": True,
            }

        if decision is False:
            self._record_approval_decision(
                authoritative,
                "REJECTED",
                reason="The user rejected the prepared action.",
            )
            message = f"Cancelled: `{authoritative['tool_name']}` was rejected and was not executed."
            return {
                "pending_approval": None,
                "approval_decision": None,
                "error_context": message,
                "final_response": message,
                "is_complete": True,
            }

        # Record the decision separately, then execute only the authoritative
        # action saved when approval was requested.
        self._record_approval_decision(authoritative, "APPROVED")
        exec_res = self.execution_engine.execute(
            tool_name=authoritative["tool_name"],
            arguments=copy.deepcopy(authoritative["arguments"]),
            user_request=authoritative["user_request"],
            confirmed=True,
            trace_id=authoritative["trace_id"],
        )
        state_for_result = dict(state)
        state_for_result["pending_approval"] = None
        state_for_result["approval_decision"] = None
        return self._execution_result_update(
            state_for_result,
            authoritative["tool_name"],
            authoritative["arguments"],
            exec_res,
            authoritative["workflow_step_id"],
        )

    def _route_execution(self, state: AgentState) -> str:
        if state.get("pending_approval"):
            return "APPROVAL_HALT"
        if not state.get("is_complete", False):
            return "LOOP_NEXT_STEP"
        return "DONE"

    def _rag_node(self, state: AgentState) -> Dict[str, Any]:
        user_req = state["user_request"]
        res = self.rag_tool.run(user_req)
        return {
            "scratchpad": {"rag_result": res},
            "is_complete": True
        }

    def _system_search_node(self, state: AgentState) -> Dict[str, Any]:
        user_req = state["user_request"]
        res = self.system_search_tool.run(user_req)
        return {
            "scratchpad": {"system_search_result": res},
            "is_complete": True
        }

    def _clarification_node(self, state: AgentState) -> Dict[str, Any]:
        return {
            "final_response": state.get("final_response") or "I could not confidently understand your request. Please specify if you want to manage invoices, payments, or check disputes.",
            "is_complete": True
        }

    def _synthesis_node(self, state: AgentState) -> Dict[str, Any]:
        # 1. Check for Pending Human Approval
        if state.get("pending_approval"):
            approval = state["pending_approval"]
            return {
                "final_response": f"{approval['prompt']}",
                "is_complete": False
            }

        # Preserve validation/execution/step-limit failures instead of replacing
        # the diagnostic with a generic success response.
        if state.get("error_context"):
            return {
                "final_response": state.get("final_response") or state["error_context"],
                "is_complete": True,
            }
        if state.get("needs_clarification"):
            return {
                "final_response": state.get("final_response") or "Could you clarify your request?",
                "is_complete": True,
            }

        # 2. Check for RAG Result
        if "rag_result" in state.get("scratchpad", {}):
            rag = state["scratchpad"]["rag_result"]
            sources_str = ", ".join(
                f"{s['file']} ({s['section']})" if s.get("section") else s["file"]
                for s in rag.get("sources", [])
            )
            answer = rag.get("answer") or rag.get("answer_context", "No answer was generated.")
            citation = f"\n\nSources: {sources_str}" if sources_str else ""
            return {
                "final_response": f"{answer}{citation}",
                "is_complete": True
            }

        # 3. Check for System Search Result
        if "system_search_result" in state.get("scratchpad", {}):
            sys_res = state["scratchpad"]["system_search_result"]
            if sys_res["search_type"] == "capabilities":
                names = [f"`{t['name']}`" for t in sys_res["tools"]]
                return {
                    "final_response": f"Found {sys_res['total_matching_tools']} registered capabilities:\n" + ", ".join(names),
                    "is_complete": True
                }
            else:
                records = sys_res.get("recent_history") or sys_res.get("logs") or []
                last_exec = sys_res.get("latest_execution") or (records[0] if records else None)
                if not last_exec:
                    return {
                        "final_response": sys_res.get("message") or "No matching execution records were found.",
                        "is_complete": True,
                    }

                query = sys_res.get("query", "").lower()
                if "recent" in query or "history" in query:
                    if not records:
                        records = [last_exec]
                    lines = [
                        f"{record.get('timestamp', 'Unknown time')} | Trace: {record.get('trace_id', 'unknown')} | "
                        f"Tool: `{record.get('selected_tool') or 'N/A'}` | Status: {record.get('http_status', 'unknown')}"
                        for record in records
                    ]
                    return {
                        "final_response": "Recent execution records:\n" + "\n".join(lines),
                        "is_complete": True,
                    }

                return {
                    "final_response": f"Latest Execution (Trace: {last_exec.get('trace_id', 'unknown')}):\n"
                    f"Tool: `{last_exec.get('selected_tool') or 'N/A'}`\n"
                    f"Status: {last_exec.get('http_status', 'unknown')}\n"
                    f"Latency: {last_exec.get('latency_ms', 0)}ms",
                    "is_complete": True,
                }

        # 4. Synthesize API Execution Results
        scratch = state.get("scratchpad", {})
        user_req = state["user_request"].lower()

        # Scenario 1 Synthesis
        if "invoice_id" in scratch:
            inv_id = scratch["invoice_id"]
            amount = scratch.get("amount", 50.0)
            recipient = scratch.get("recipient_email", "the recipient")
            status = scratch.get("status", "DRAFT")
            if status == "SENT":
                return {
                    "final_response": f"Invoice `{inv_id}` for ${amount:.2f} has been created and successfully sent to {recipient}.",
                    "is_complete": True
                }
            return {
                "final_response": f"Invoice `{inv_id}` for ${amount:.2f} has been created in DRAFT status for {recipient}.",
                "is_complete": True
            }

        # Scenario 2 Synthesis
        if "total_sales_volume" in scratch:
            vol = scratch["total_sales_volume"]
            tx_count = scratch.get("transaction_count", 0)
            curr = scratch.get("currency", "USD")
            period = scratch.get("period", "the requested period")
            return {
                "final_response": f"Your total sales volume for {period} was ${vol:,.2f} {curr} across {tx_count} transactions.",
                "is_complete": True
            }

        # Scenario 3 Synthesis
        if "disputes" in scratch:
            disputes = scratch["disputes"]
            if disputes:
                d = disputes[0]
                return {
                    "final_response": f"Yes, there is {len(disputes)} open dispute for {d['customer_id']}: Dispute `{d['dispute_id']}` for ${d['dispute_amount']:.2f} {d['currency']} (Reason: {d['reason']}, Status: {d['status']}).",
                    "is_complete": True
                }
            return {
                "final_response": "No open disputes found for that user.",
                "is_complete": True
            }

        return {
            "final_response": "Request processed successfully.",
            "is_complete": True
        }

    # =========================================================================
    # PUBLIC RUN METHOD
    # =========================================================================
    def run(
        self,
        user_request: str,
        state: Optional[AgentState] = None,
        approval_decision: Optional[bool] = None,
        clarification_response: Optional[str] = None,
    ) -> AgentState:
        """Run a request or resolve the exact pending approval in graph state."""
        if clarification_response is not None:
            if not clarification_response.strip():
                return {
                    **(copy.deepcopy(state) if state else {}),
                    "final_response": "Please provide the requested information before continuing.",
                    "is_complete": False,
                }
            clarification = (state or {}).get("pending_clarification") or {}
            clarification_id = clarification.get("clarification_id")
            authoritative = self._pending_clarifications.get(clarification_id) if clarification_id else None
            if authoritative is None or state != authoritative:
                self._pending_clarifications.pop(clarification_id, None)
                return {
                    **(copy.deepcopy(state) if state else {}),
                    "pending_clarification": None,
                    "needs_clarification": False,
                    "error_context": "Clarification state is missing, altered, or already used.",
                    "final_response": "This clarification can no longer be resumed safely. Please start the request again.",
                    "is_complete": True,
                }
            self._pending_clarifications.pop(clarification_id, None)
            init_state = copy.deepcopy(authoritative)
            original = init_state.get("original_user_request") or init_state["user_request"]
            init_state["user_request"] = f"{original}\nUser clarification: {clarification_response.strip()}"
            init_state["original_user_request"] = original
            init_state["pending_clarification"] = None
            init_state["clarification_attempts"] = int(init_state.get("clarification_attempts", 0)) + 1
            init_state["clarification_response"] = clarification_response.strip()
            init_state["clarification_resume_tool"] = clarification.get("tool_name")
            init_state["needs_clarification"] = False
            init_state["error_context"] = None
        elif state:
            init_state = copy.deepcopy(state)
            if not state.get("pending_approval") and not state.get("pending_clarification"):
                init_state["user_request"] = user_request
            init_state.setdefault("original_user_request", init_state.get("user_request", user_request))
        else:
            init_state = {
                "user_request": user_request,
                "original_user_request": user_request,
                "trace_id": f"trace_{uuid.uuid4().hex[:8]}"
            }
        init_state["approval_decision"] = approval_decision
        # Approval is scoped to the resolver call and never remains a workflow-wide flag.
        init_state["confirmed"] = False

        final_state = self.app.invoke(init_state)
        pending_clarification = final_state.get("pending_clarification")
        if pending_clarification:
            self._pending_clarifications[pending_clarification["clarification_id"]] = copy.deepcopy(final_state)
        return final_state
