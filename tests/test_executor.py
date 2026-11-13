import pytest
import uuid
from src.config import settings
from src.registry.db import ToolRegistryDB
from src.services.mock_paypal import MockPayPalService
from src.execution.executor import SafeExecutionEngine
from src.execution.validator import ParameterValidator

@pytest.fixture
def engine():
    db = ToolRegistryDB(settings.DATABASE_PATH)
    mock_service = MockPayPalService()
    return SafeExecutionEngine(db, mock_service)


@pytest.mark.parametrize(
    "status_code,payload",
    [
        (400, {"status": "BAD_REQUEST", "message": "Invalid request", "status_code": 400}),
        (500, {"status": "UPSTREAM_FAILURE", "message": "Service failed", "status_code": 500}),
        (
            501,
            {
                "status": "NOT_IMPLEMENTED_IN_MOCK",
                "message": "This API is registered but has no mock handler.",
                "status_code": 501,
            },
        ),
    ],
)
def test_non_2xx_response_without_error_key_is_execution_failure(engine, monkeypatch, status_code, payload):
    monkeypatch.setattr(engine.mock_service, "execute_tool", lambda tool_name, arguments: payload)

    result = engine.execute("get_account_balances", {})

    assert result.status == "EXECUTION_ERROR"
    assert result.http_status == status_code
    assert result.output == payload
    assert payload["message"] in result.error_message
    if status_code == 501:
        assert "not implemented in the mock" in result.error_message.lower()


@pytest.mark.parametrize(
    "status_code,payload",
    [
        (200, {"available": 100, "status_code": 200}),
        (201, {"created": True, "status_code": 201}),
        (202, {"accepted": True, "status_code": 202}),
        (204, {"status_code": 204}),
    ],
)
def test_2xx_responses_remain_successful_and_preserve_payload(engine, monkeypatch, status_code, payload):
    monkeypatch.setattr(engine.mock_service, "execute_tool", lambda tool_name, arguments: payload)

    result = engine.execute("get_account_balances", {})

    assert result.status == "SUCCESS"
    assert result.http_status == status_code
    assert result.output == payload
    assert result.error_message is None

def test_scenario1_create_draft_invoice(engine):
    """Scenario 1: Creating draft invoice (safe action, auto-executed)."""
    res = engine.execute(
        tool_name="create_draft_invoice",
        arguments={
            "recipient_email": "bob@example.com",
            "amount": 50.0,
            "description": "Web Design services"
        },
        user_request="Send an invoice for $50 to bob@example.com for Web Design"
    )
    assert res.status == "SUCCESS"
    assert res.requires_approval is False
    assert res.output is not None
    assert "INV-2026-" in res.output["invoice_id"]
    assert res.output["amount"] == 50.0
    assert res.output["invoice_status"] == "DRAFT"

def test_scenario1_send_invoice_approval_gate(engine):
    """Scenario 1: Sending invoice is sensitive and requires approval."""
    # First create draft
    draft_res = engine.execute(
        tool_name="create_draft_invoice",
        arguments={"recipient_email": "bob@example.com", "amount": 50.0, "description": "Web Design"}
    )
    inv_id = draft_res.output["invoice_id"]

    # Attempt to send without confirmation -> should be PREPARED
    prep_res = engine.execute(
        tool_name="send_invoice",
        arguments={"invoice_id": inv_id},
        confirmed=False
    )
    assert prep_res.status == "PREPARED"
    assert prep_res.requires_approval is True
    assert prep_res.approval_prompt is not None

    # Now execute with confirmation -> should SUCCEED
    send_res = engine.execute(
        tool_name="send_invoice",
        arguments={"invoice_id": inv_id},
        confirmed=True
    )
    assert send_res.status == "SUCCESS"
    assert send_res.output["status"] == "SENT"
    assert "successfully dispatched" in send_res.output["message"]

def test_approval_and_execution_events_share_trace_but_have_distinct_ids(engine):
    trace = f"trace_approval_events_{uuid.uuid4().hex[:8]}"
    draft = engine.execute(
        tool_name="create_draft_invoice",
        arguments={"recipient_email": "events@example.com", "amount": 25.0, "description": "Audit test"},
        trace_id=trace,
    )
    invoice_id = draft.output["invoice_id"]
    prepared = engine.execute(
        tool_name="send_invoice",
        arguments={"invoice_id": invoice_id},
        trace_id=trace,
        confirmed=False,
    )
    sent = engine.execute(
        tool_name="send_invoice",
        arguments={"invoice_id": invoice_id},
        trace_id=trace,
        confirmed=True,
    )

    events = [row for row in engine.db.get_recent_logs(limit=20) if row["trace_id"] == trace]
    assert prepared.status == "PREPARED"
    assert sent.status == "SUCCESS"
    assert len(events) == 3
    assert len({row["execution_id"] for row in events}) == 3
    assert {row["approval_status"] for row in events} == {"AUTOMATIC", "AWAITING_APPROVAL", "APPROVED"}

def test_scenario2_sales_volume_reporting(engine):
    """Scenario 2: Total sales volume last month (October 2026)."""
    res = engine.execute(
        tool_name="get_transaction_summary",
        arguments={"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )
    assert res.status == "SUCCESS"
    assert res.output["total_sales_volume"] == 42500.00
    assert res.output["transaction_count"] == 312
    assert res.output["currency"] == "USD"

def test_scenario3_user_dispute_lookup(engine):
    """Scenario 3: Checking if user_123 has an open dispute."""
    res = engine.execute(
        tool_name="list_disputes",
        arguments={"customer_id": "user_123", "dispute_state": "OPEN"}
    )
    assert res.status == "SUCCESS"
    assert res.output["total_items"] == 1
    dispute = res.output["disputes"][0]
    assert dispute["dispute_id"] == "PP-D-4412"
    assert dispute["customer_id"] == "user_123"
    assert dispute["dispute_amount"] == 120.00
    assert dispute["dispute_state"] == "OPEN"

def test_validation_missing_required_parameter(engine):
    """Validation test: Omitting required 'amount' in create_draft_invoice."""
    res = engine.execute(
        tool_name="create_draft_invoice",
        arguments={"recipient_email": "bob@example.com", "description": "Web Design"}
    )
    assert res.status == "VALIDATION_ERROR"
    assert "Missing required parameter: 'amount'" in res.error_message

def test_validation_invalid_type(engine):
    """Validation test: Passing malformed amount."""
    res = engine.execute(
        tool_name="create_draft_invoice",
        arguments={"recipient_email": "bob@example.com", "amount": "invalid_number", "description": "Web Design"}
    )
    assert res.status == "VALIDATION_ERROR"
    assert "must be of type number" in res.error_message


@pytest.mark.parametrize("recipient", ["not-an-email", "bob..smith@example.com", "bob@example"])
def test_invoice_recipient_email_format_is_validated_before_dispatch(engine, monkeypatch, recipient):
    dispatch_calls = []
    monkeypatch.setattr(engine.mock_service, "execute_tool", lambda *args, **kwargs: dispatch_calls.append(args))

    result = engine.execute(
        "create_draft_invoice",
        {"recipient_email": recipient, "amount": 10, "description": "Design"},
    )

    assert result.status == "VALIDATION_ERROR"
    assert "recipient_email" in result.error_message
    assert dispatch_calls == []


@pytest.mark.parametrize("currency", ["CAD", "JPY", "", None])
def test_invoice_currency_must_be_supported_before_dispatch(engine, monkeypatch, currency):
    dispatch_calls = []
    monkeypatch.setattr(engine.mock_service, "execute_tool", lambda *args, **kwargs: dispatch_calls.append(args))
    arguments = {"recipient_email": "valid@example.com", "amount": 10, "description": "Design", "currency": currency}

    result = engine.execute("create_draft_invoice", arguments)

    assert result.status == "VALIDATION_ERROR"
    assert "currency" in result.error_message
    assert dispatch_calls == []


@pytest.mark.parametrize("currency", ["usd", "EUR", "gbp", "INR"])
def test_invoice_currency_accepts_supported_values_and_normalizes_case(engine, currency):
    result = engine.execute(
        "create_draft_invoice",
        {"recipient_email": "valid@example.com", "amount": 10, "description": "Design", "currency": currency},
    )

    assert result.status == "SUCCESS"
    assert result.output["currency"] == currency.upper()


@pytest.mark.parametrize(
    "user_request",
    [
        "Create an invoice for CAD 10",
        "Create an invoice for USD 10 and EUR 20",
        "Create an invoice in EUR for 10",
    ],
)
def test_invoice_request_currency_evidence_cannot_be_ignored(engine, monkeypatch, user_request):
    dispatch_calls = []
    monkeypatch.setattr(engine.mock_service, "execute_tool", lambda *args, **kwargs: dispatch_calls.append(args))

    result = engine.execute(
        "create_draft_invoice",
        {"recipient_email": "valid@example.com", "amount": 10, "description": "Design", "currency": "USD"},
        user_request=user_request,
    )

    assert result.status == "VALIDATION_ERROR"
    assert "currency" in result.error_message.lower()
    assert dispatch_calls == []


@pytest.mark.parametrize("schema_type", ["integer", "number"])
@pytest.mark.parametrize("value", [True, False])
def test_numeric_schema_rejects_boolean_values(schema_type, value):
    result = ParameterValidator.validate(
        {"type": "object", "properties": {"amount": {"type": schema_type}}},
        {"amount": value},
    )

    assert result.is_valid is False
    assert any(f"must be of type {schema_type}, received bool" in error for error in result.errors)


@pytest.mark.parametrize("value", [True, False])
def test_boolean_schema_accepts_boolean_values(value):
    result = ParameterValidator.validate(
        {"type": "object", "properties": {"enabled": {"type": "boolean"}}},
        {"enabled": value},
    )

    assert result.is_valid is True
    assert result.validated_arguments == {"enabled": value}


@pytest.mark.parametrize(
    "schema_type,value",
    [("integer", 3), ("number", 3), ("number", 3.5)],
)
def test_numeric_schemas_continue_to_accept_valid_numeric_values(schema_type, value):
    result = ParameterValidator.validate(
        {"type": "object", "properties": {"amount": {"type": schema_type}}},
        {"amount": value},
    )

    assert result.is_valid is True
    assert result.validated_arguments == {"amount": value}


def test_unsupported_argument_is_rejected_before_dispatch(engine, monkeypatch):
    dispatch_calls = []
    monkeypatch.setattr(
        engine.mock_service,
        "execute_tool",
        lambda *args, **kwargs: dispatch_calls.append((args, kwargs)),
    )

    result = engine.execute(
        "create_draft_invoice",
        {
            "recipient_email": "valid@example.com",
            "amount": 25,
            "description": "Valid fields",
            "unexpected": "must not be forwarded",
        },
    )

    assert result.status == "VALIDATION_ERROR"
    assert "Unsupported argument: 'unexpected'" in result.error_message
    assert "recipient_email" in result.error_message
    assert result.output is None
    assert dispatch_calls == []


def test_empty_properties_rejects_every_supplied_argument(engine, monkeypatch):
    dispatch_calls = []
    monkeypatch.setattr(
        engine.mock_service,
        "execute_tool",
        lambda *args, **kwargs: dispatch_calls.append((args, kwargs)),
    )

    result = engine.execute("list_dispute_reasons", {"unexpected": "value"})

    assert result.status == "VALIDATION_ERROR"
    assert "Unsupported argument: 'unexpected'" in result.error_message
    assert "Allowed arguments: [none]" in result.error_message
    assert dispatch_calls == []


def test_enum_validation_rejects_invalid_value_before_dispatch(engine, monkeypatch):
    dispatch_calls = []
    monkeypatch.setattr(
        engine.mock_service,
        "execute_tool",
        lambda *args, **kwargs: dispatch_calls.append((args, kwargs)),
    )

    result = engine.execute("create_payment_order", {"amount": 25, "intent": "TRANSFER"})

    assert result.status == "VALIDATION_ERROR"
    assert "Invalid value for 'intent'" in result.error_message
    assert dispatch_calls == []


def test_valid_enum_and_schema_without_properties_remain_supported(engine):
    enum_result = engine.execute("create_payment_order", {"amount": 25, "intent": "AUTHORIZE"})
    schema_without_properties = ParameterValidator.validate(
        {"type": "object"}, {"legacy_argument": "preserved"}
    )

    assert enum_result.status == "SUCCESS"
    assert schema_without_properties.is_valid is True
    assert schema_without_properties.validated_arguments == {"legacy_argument": "preserved"}

def test_audit_logging_persistence(engine):
    """Verify that every execution step is recorded in SQLite execution_logs."""
    engine.execute(
        tool_name="get_account_balances",
        arguments={},
        user_request="What is my account balance?"
    )
    last_log = engine.db.get_last_execution()
    assert last_log is not None
    assert last_log["selected_tool"] == "get_account_balances"
    assert last_log["user_request"] == "What is my account balance?"
    assert last_log["http_status"] == 200
