import pytest

from src.orchestrator.llm_client import IntentDecision, LLMPlanningClient, ProposedAction, ProposedSubgoal


class FakeStructuredModel:
    def __init__(self, result):
        self.result = result
        self.messages = None
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, messages):
        self.messages = messages
        return self.result


def tool(name, category, properties, required):
    return {
        "tool_id": name,
        "name": name,
        "category": category,
        "endpoint": "/v1/test",
        "http_method": "POST",
        "description": f"Test operation {name}",
        "parameters_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
        "is_sensitive": False,
        "required_permissions": [],
        "trigger_phrases": [],
    }


def test_configured_model_classifies_intent_using_structured_output():
    fake = FakeStructuredModel({"intent": "RAG_DOCS", "clarification": None})
    client = LLMPlanningClient(model=fake)

    assert client.classify_intent("What is the dispute response timeframe?") == "RAG_DOCS"
    assert fake.schema is IntentDecision


def test_configured_model_identifies_next_subgoal_from_request_history_and_results():
    fake = FakeStructuredModel({"done": False, "subgoal": "Send invoice INV-101", "clarification": None})
    client = LLMPlanningClient(model=fake)

    subgoal = client.identify_next_subgoal(
        "Create and send an invoice to bob@example.com",
        execution_history=[{"tool_name": "create_draft_invoice", "workflow_step_id": "trace:step:1"}],
        scratchpad={"invoice_id": "INV-101"},
    )

    assert fake.schema is ProposedSubgoal
    assert subgoal.subgoal == "Send invoice INV-101"
    prompt = str(fake.messages)
    assert "Create and send an invoice" in prompt
    assert "create_draft_invoice" in prompt
    assert "INV-101" in prompt


def test_planner_receives_only_retrieved_candidates_and_returns_arguments():
    fake = FakeStructuredModel({
        "done": False,
        "tool_name": "create_draft_invoice",
        "arguments": {"recipient_email": "bob@example.com", "amount": 50, "description": "Web Design"},
        "rationale": "Create the draft before sending it.",
    })
    client = LLMPlanningClient(model=fake)
    candidates = [tool("create_draft_invoice", "invoicing", {
        "recipient_email": {"type": "string"},
        "amount": {"type": "number"},
        "description": {"type": "string"},
    }, ["recipient_email", "amount", "description"])]

    action = client.plan_next_action(
        "Send an invoice for $50 to bob@example.com for Web Design",
        candidates,
        current_subgoal="Create the draft invoice for Web Design",
    )

    assert fake.schema is ProposedAction
    assert "create_draft_invoice" in str(fake.messages)
    assert "refund_captured_payment" not in str(fake.messages)
    assert "Create the draft invoice for Web Design" in str(fake.messages)
    assert action.tool_name == "create_draft_invoice"
    assert action.arguments["amount"] == 50


def test_configured_planner_selects_invoice_api_when_required_values_are_missing():
    fake = FakeStructuredModel({
        "done": False,
        "tool_name": "create_draft_invoice",
        "arguments": {"recipient_email": "test@example.com"},
        "rationale": "The user requested an invoice draft and supplied the recipient.",
    })
    client = LLMPlanningClient(model=fake)
    candidates = [tool("create_draft_invoice", "invoicing", {
        "recipient_email": {"type": "string"},
        "amount": {"type": "number"},
        "description": {"type": "string"},
    }, ["recipient_email", "amount", "description"])]

    action = client.plan_next_action(
        "Create a draft invoice for test@example.com",
        candidates,
        current_subgoal="Create a draft invoice for test@example.com",
    )

    assert action.done is False
    assert action.tool_name == "create_draft_invoice"
    assert action.arguments == {"recipient_email": "test@example.com"}
    system_prompt = fake.messages[0][1]
    assert "If the correct API is available but required parameters are missing, still select that API" in system_prompt
    assert "never fabricate missing parameter values" in system_prompt
    assert "Do not invent customer searches or additional operations unless they are genuinely required" in system_prompt


def test_planner_rejects_tool_not_in_retrieved_candidates():
    fake = FakeStructuredModel({
        "done": False,
        "tool_name": "refund_captured_payment",
        "arguments": {"capture_id": "CAP-1001"},
    })
    client = LLMPlanningClient(model=fake)
    candidates = [tool("list_disputes", "disputes", {"dispute_state": {"type": "string"}}, [])]

    action = client.plan_next_action("Check open disputes", candidates)

    assert action.done is True
    assert action.tool_name is None
    assert "retrieved API candidates" in action.clarification


def test_offline_planner_replans_with_generated_id_and_stops_after_send():
    client = LLMPlanningClient()
    client.provider = "mock"
    candidates = [
        tool("create_draft_invoice", "invoicing", {
            "recipient_email": {"type": "string"}, "amount": {"type": "number"},
            "description": {"type": "string"}, "currency": {"type": "string"},
        }, ["recipient_email", "amount", "description"]),
        tool("send_invoice", "invoicing", {"invoice_id": {"type": "string"}}, ["invoice_id"]),
    ]
    request = "Send an invoice for $50 to bob@example.com for Web Design services"

    first = client.plan_next_action(request, candidates)
    second = client.plan_next_action(
        request,
        candidates,
        scratchpad={"invoice_id": "INV-2026-003"},
        execution_history=[{"tool_name": "create_draft_invoice"}],
    )
    finished = client.plan_next_action(
        request,
        candidates,
        scratchpad={"invoice_id": "INV-2026-003"},
        execution_history=[{"tool_name": "create_draft_invoice"}, {"tool_name": "send_invoice"}],
    )

    assert first.tool_name == "create_draft_invoice"
    assert first.arguments["recipient_email"] == "bob@example.com"
    assert second.tool_name == "send_invoice"
    assert second.arguments["invoice_id"] == "INV-2026-003"
    assert finished.done is True


@pytest.mark.parametrize(
    ("user_text", "expected_currency"),
    [
        ("Create invoice for USD 150", "USD"),
        ("Create invoice for $150", "USD"),
        ("Create invoice for 150 USD", "USD"),
        ("Create invoice for 150 dollars", "USD"),
        ("Create invoice for EUR 150", "EUR"),
    ],
)
def test_offline_invoice_amount_parser_extracts_supported_amount_formats(user_text, expected_currency):
    arguments = LLMPlanningClient._extract_invoice_arguments(user_text, {})

    assert arguments["amount"] == 150.0
    assert arguments["currency"] == expected_currency


@pytest.mark.parametrize("user_text", ["Create invoice without an amount", "Create invoice for USD", "Create invoice for USD 1.2345"])
def test_offline_invoice_amount_parser_does_not_guess_invalid_or_absent_amount(user_text):
    arguments = LLMPlanningClient._extract_invoice_arguments(user_text, {})

    assert "amount" not in arguments


@pytest.mark.parametrize(
    "request_text",
    [
        "Create an invoice to bob@example.com",
        "Create an invoice for customer bob@example.com",
        "Create an invoice with recipient: bob@example.com",
    ],
)
def test_offline_invoice_parser_extracts_standard_recipient_email(request_text):
    arguments = LLMPlanningClient._extract_invoice_arguments(request_text, {})

    assert arguments["recipient_email"] == "bob@example.com"


def test_offline_invoice_parser_preserves_plus_tag_recipient_email():
    arguments = LLMPlanningClient._extract_invoice_arguments(
        "Create an invoice to bob+invoice@example.com",
        {},
    )

    assert arguments["recipient_email"] == "bob+invoice@example.com"


@pytest.mark.parametrize(
    "request_text",
    [
        "Create an invoice to bob..smith@example.com",
        "Create an invoice to bob@example",
        "Create an invoice to @example.com",
        "Create an invoice to bob@example.com or alice@example.com",
    ],
)
def test_offline_invoice_parser_omits_malformed_or_ambiguous_recipient(request_text):
    arguments = LLMPlanningClient._extract_invoice_arguments(request_text, {})

    assert "recipient_email" not in arguments


@pytest.mark.parametrize(
    ("currency", "expected_currency"),
    [("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP"), ("INR", "INR")],
)
def test_offline_invoice_parser_extracts_supported_currency_prefix(currency, expected_currency):
    arguments = LLMPlanningClient._extract_invoice_arguments(f"Create invoice for {currency.lower()} 150", {})

    assert arguments["amount"] == 150.0
    assert arguments["currency"] == expected_currency


@pytest.mark.parametrize(
    ("currency", "expected_currency"),
    [("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP"), ("INR", "INR")],
)
def test_offline_invoice_parser_extracts_supported_currency_suffix(currency, expected_currency):
    arguments = LLMPlanningClient._extract_invoice_arguments(f"Create invoice for 150 {currency.lower()}", {})

    assert arguments["amount"] == 150.0
    assert arguments["currency"] == expected_currency


def test_offline_invoice_parser_defaults_currency_to_usd_when_not_specified():
    arguments = LLMPlanningClient._extract_invoice_arguments("Create a draft invoice", {})

    assert arguments["currency"] == "USD"


def test_offline_invoice_parser_leaves_unsupported_currency_unresolved():
    arguments = LLMPlanningClient._extract_invoice_arguments("Create invoice for CAD 150", {})

    assert arguments["amount"] == 150.0
    assert "currency" not in arguments


def test_offline_invoice_parser_does_not_default_after_labeled_unsupported_currency():
    arguments = LLMPlanningClient._extract_invoice_arguments("Create an invoice with currency: CAD", {})

    assert "currency" not in arguments


def test_offline_invoice_parser_leaves_conflicting_currencies_unresolved():
    arguments = LLMPlanningClient._extract_invoice_arguments(
        "Create invoice for USD 150 and EUR 200",
        {},
    )

    assert "currency" not in arguments


def test_offline_planner_requests_clarification_for_unsupported_invoice_currency():
    client = LLMPlanningClient()
    candidates = [tool("create_draft_invoice", "invoicing", {
        "recipient_email": {"type": "string"},
        "amount": {"type": "number"},
        "description": {"type": "string"},
        "currency": {"type": "string"},
    }, ["recipient_email", "amount", "description"])]

    action = client.plan_next_action("Create invoice for CAD 150", candidates)

    assert action.done is True
    assert "supported invoice currency" in action.clarification


def test_offline_planner_requests_clarification_for_conflicting_invoice_currencies():
    client = LLMPlanningClient()
    candidates = [tool("create_draft_invoice", "invoicing", {
        "recipient_email": {"type": "string"},
        "amount": {"type": "number"},
        "description": {"type": "string"},
        "currency": {"type": "string"},
    }, ["recipient_email", "amount", "description"])]

    action = client.plan_next_action("Create invoice for USD 150 and EUR 200", candidates)

    assert action.done is True
    assert "supported invoice currency" in action.clarification


@pytest.mark.parametrize("quote", ["'", '"'])
def test_offline_invoice_parser_extracts_quoted_description_with_recipient_and_amount(quote):
    request_text = (
        f"Create a draft invoice for customer test@example.com for USD 150 "
        f"with the description {quote}Consulting services{quote}."
    )

    arguments = LLMPlanningClient._extract_invoice_arguments(request_text, {})

    assert arguments["recipient_email"] == "test@example.com"
    assert arguments["amount"] == 150.0
    assert arguments["description"] == "Consulting services"


def test_offline_invoice_parser_preserves_legacy_for_description_format():
    arguments = LLMPlanningClient._extract_invoice_arguments(
        "Send an invoice for $50 to bob@example.com for Web Design services.",
        {},
    )

    assert arguments["description"] == "Web Design services"


@pytest.mark.parametrize(
    "request_text",
    [
        "Create an invoice for customer Alice Smith",
        "Create an invoice for bob@example.com",
        "Create an invoice for USD 150",
        "Create an invoice for customer test@example.com for USD 150",
    ],
)
def test_offline_invoice_parser_does_not_invent_description_from_recipient_or_amount(request_text):
    arguments = LLMPlanningClient._extract_invoice_arguments(request_text, {})

    assert "description" not in arguments


def test_offline_planner_clarifies_unsupported_or_ambiguous_request():
    client = LLMPlanningClient()
    candidates = [tool("list_disputes", "disputes", {"customer_id": {"type": "string"}}, [])]

    action = client.plan_next_action("Please take care of it", candidates)

    assert action.done is True
    assert action.clarification
