import time
import uuid
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from src.config import settings
from src.registry.db import ToolRegistryDB
from src.execution.validator import ParameterValidator
from src.services.mock_paypal import MockPayPalService

class ExecutionResult(BaseModel):
    status: str = Field(..., description="'SUCCESS', 'PREPARED', 'VALIDATION_ERROR', 'EXECUTION_ERROR'")
    tool_name: str
    requires_approval: bool = False
    approval_prompt: Optional[str] = None
    output: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    http_status: Optional[int] = None
    execution_id: Optional[str] = None
    trace_id: Optional[str] = None
    validation_errors: List[str] = Field(default_factory=list)

class SafeExecutionEngine:
    def __init__(self, db: ToolRegistryDB, mock_service: Optional[MockPayPalService] = None):
        self.db = db
        self.mock_service = mock_service or MockPayPalService()

    def record_approval_decision(
        self,
        *,
        decision: str,
        approval_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        trace_id: str,
        workflow_step_id: str,
        prepared_execution_id: Optional[str],
        user_request: str,
        reason: Optional[str] = None,
        attempted_action: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Append an approval decision linked to its prepared workflow step."""
        approved = decision == "APPROVED"
        execution_id = f"exec_{uuid.uuid4().hex}"
        response: Dict[str, Any] = {
            "approval_id": approval_id,
            "decision": decision,
            "workflow_step_id": workflow_step_id,
            "prepared_execution_id": prepared_execution_id,
        }
        if reason:
            response["reason"] = reason
        if attempted_action is not None:
            response["attempted_action"] = attempted_action

        return self.db.log_execution(
            trace_id=trace_id,
            user_request=user_request,
            retrieved_tools=[tool_name],
            selected_tool=tool_name,
            arguments=arguments,
            approval_status=f"APPROVAL_{decision}",
            http_status=200 if approved else 403,
            response=response,
            latency_ms=0.0,
            execution_id=execution_id,
        )

    def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user_request: str = "",
        confirmed: bool = False,
        trace_id: Optional[str] = None
    ) -> ExecutionResult:
        """
        Executes a proposed tool safely and deterministically:
        1. Validates arguments against Pydantic/JSON Schema.
        2. Checks sensitivity flags for Human-in-the-Loop approval.
        3. Dispatches to mock PayPal service.
        4. Logs full execution trace to the SQLite audit log.
        """
        trace = trace_id or f"trace_{uuid.uuid4().hex[:8]}"
        execution_id = f"exec_{uuid.uuid4().hex}"

        # 1. Fetch canonical API schema from SQLite
        tool_meta = self.db.get_tool_by_name(tool_name)
        if not tool_meta:
            err = f"API Tool '{tool_name}' is not registered in the tool database."
            self.db.log_execution(
                trace_id=trace, execution_id=execution_id, user_request=user_request,
                retrieved_tools=[tool_name], selected_tool=tool_name, arguments=arguments,
                approval_status="NOT_REGISTERED", http_status=404,
                response={"error": err}, latency_ms=0.0,
            )
            return ExecutionResult(status="EXECUTION_ERROR", tool_name=tool_name, error_message=err,
                                   http_status=404, execution_id=execution_id, trace_id=trace)

        # 2. Strict Parameter Validation
        val_result = ParameterValidator.validate(
            tool_meta.parameters_schema,
            arguments,
            tool_name=tool_name,
            user_request=user_request,
        )
        if not val_result.is_valid:
            err_msg = "Validation failed: " + "; ".join(val_result.errors)
            self.db.log_execution(
                trace_id=trace,
                user_request=user_request,
                retrieved_tools=[tool_name],
                selected_tool=tool_name,
                arguments=arguments,
                approval_status="VALIDATION_FAILED",
                http_status=400,
                response={"error": err_msg},
                latency_ms=0.5,
                execution_id=execution_id,
            )
            return ExecutionResult(
                status="VALIDATION_ERROR",
                tool_name=tool_name,
                error_message=err_msg,
                http_status=400,
                execution_id=execution_id,
                trace_id=trace,
                validation_errors=val_result.errors,
            )

        validated_args = val_result.validated_arguments

        # 3. Sensitivity & Human-in-the-Loop Approval Gate
        if tool_meta.is_sensitive and not confirmed and settings.ENABLE_APPROVAL_GATES:
            prompt = (
                f"APPROVAL REQUIRED: '{tool_name}' is a sensitive financial operation.\n"
                f"Parameters: {validated_args}\n"
                f"Do you authorize this transaction? [Yes/No]"
            )
            self.db.log_execution(
                trace_id=trace,
                user_request=user_request,
                retrieved_tools=[tool_name],
                selected_tool=tool_name,
                arguments=validated_args,
                approval_status="AWAITING_APPROVAL",
                http_status=202,
                response={"status": "PREPARED", "prompt": prompt},
                latency_ms=1.0,
                execution_id=execution_id,
            )
            return ExecutionResult(
                status="PREPARED",
                tool_name=tool_name,
                requires_approval=True,
                approval_prompt=prompt,
                output=validated_args,
                http_status=202,
                execution_id=execution_id,
                trace_id=trace,
            )

        # 4. Deterministic Dispatch to Mock Backend
        start_time = time.perf_counter()
        # No automatic retry is performed: the current adapter is synchronous and
        # mock-backed, while several catalog operations are non-idempotent. A
        # future remote adapter must classify transient failures and carry an
        # idempotency key before enabling retries.
        response = self.mock_service.execute_tool(tool_name, validated_args)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        http_code = int(response.get("status_code", 200))
        successful_http_status = 200 <= http_code < 300
        execution_failed = not successful_http_status or "error" in response
        error_message = None
        if execution_failed:
            if response.get("status") == "NOT_IMPLEMENTED_IN_MOCK":
                detail = response.get("message") or "No mock handler is registered for this API."
                error_message = f"{tool_name} is not implemented in the mock: {detail}"
            else:
                error_message = str(
                    response.get("error")
                    or response.get("message")
                    or response.get("status")
                    or f"{tool_name} returned unsuccessful HTTP status {http_code}."
                )

        # 5. Audit Logging to SQLite
        self.db.log_execution(
            trace_id=trace,
            user_request=user_request,
            retrieved_tools=[tool_name],
            selected_tool=tool_name,
            arguments=validated_args,
            approval_status="APPROVED" if tool_meta.is_sensitive else "AUTOMATIC",
            http_status=http_code,
            response=response,
            latency_ms=elapsed_ms,
            execution_id=execution_id,
        )

        if execution_failed:
            return ExecutionResult(
                status="EXECUTION_ERROR",
                tool_name=tool_name,
                output=response,
                error_message=error_message,
                latency_ms=elapsed_ms,
                http_status=http_code,
                execution_id=execution_id,
                trace_id=trace,
            )

        return ExecutionResult(
            status="SUCCESS",
            tool_name=tool_name,
            output=response,
            latency_ms=elapsed_ms,
            http_status=http_code,
            execution_id=execution_id,
            trace_id=trace,
        )
