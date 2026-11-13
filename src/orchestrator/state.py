from typing import List, Dict, Any, Optional, TypedDict

class AgentState(TypedDict, total=False):
    user_request: str
    original_user_request: str
    trace_id: str
    intent: str  # "API_TOOL", "RAG_DOCS", "SYSTEM_SEARCH", "CLARIFICATION"
    subgoals: List[str]
    current_subgoal_idx: int
    current_subgoal: str
    subgoal_complete: bool
    candidate_query: str
    candidate_tools: List[Dict[str, Any]]
    planning_trace: List[Dict[str, Any]]
    scratchpad: Dict[str, Any]
    planned_action: Optional[Dict[str, Any]]  # {"tool_name": str, "arguments": dict}
    execution_history: List[Dict[str, Any]]
    pending_approval: Optional[Dict[str, Any]]
    pending_clarification: Optional[Dict[str, Any]]
    clarification_attempts: int
    clarification_response: str
    clarification_resume_tool: Optional[str]
    approval_decision: Optional[bool]
    error_context: Optional[str]
    needs_clarification: bool
    confirmed: bool
    final_response: str
    is_complete: bool
