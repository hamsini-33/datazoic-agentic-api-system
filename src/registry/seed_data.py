from typing import List
from src.registry.models import ToolMetadata, ToolCategory, HTTPMethod
from src.registry.db import ToolRegistryDB

PAYPAL_TOOLS: List[ToolMetadata] = [
    # =========================================================================
    # INVOICING APIS (15 Tools)
    # =========================================================================
    ToolMetadata(
        tool_id="inv_create_draft",
        name="create_draft_invoice",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices",
        http_method=HTTPMethod.POST,
        description="Creates a draft invoice with recipient details, line items, currency, and total amount. Draft invoices are not sent until explicitly dispatched.",
        parameters_schema={
            "type": "object",
            "properties": {
                "recipient_email": {"type": "string", "description": "Email address of the invoice recipient"},
                "amount": {"type": "number", "description": "Total invoice balance due in currency units"},
                "currency": {"type": "string", "default": "USD", "description": "3-letter ISO-4217 currency code (e.g. USD, EUR)"},
                "description": {"type": "string", "description": "Description of items, services, or work performed"},
                "due_date": {"type": "string", "description": "Payment due date in ISO format YYYY-MM-DD"}
            },
            "required": ["recipient_email", "amount", "description"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["create draft invoice", "draft an invoice for", "new invoice to", "generate invoice"]
    ),
    ToolMetadata(
        tool_id="inv_send",
        name="send_invoice",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices/{invoice_id}/send",
        http_method=HTTPMethod.POST,
        description="Sends a created draft invoice to the recipient via email and notifies their PayPal account. Irreversible action.",
        parameters_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The unique PayPal identifier of the invoice to dispatch (e.g., INV2-XXXX)"},
                "subject": {"type": "string", "description": "Custom subject line for the invoice dispatch email"},
                "note": {"type": "string", "description": "Personalized message or instructions for the recipient"}
            },
            "required": ["invoice_id"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["send invoice", "dispatch invoice to customer", "email the invoice", "send bill"]
    ),
    ToolMetadata(
        tool_id="inv_get_details",
        name="get_invoice_details",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices/{invoice_id}",
        http_method=HTTPMethod.GET,
        description="Retrieves complete information for an invoice including status, line items, payments made, and audit timestamps.",
        parameters_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The unique PayPal invoice ID"}
            },
            "required": ["invoice_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["get invoice details", "check invoice status", "lookup invoice", "view invoice"]
    ),
    ToolMetadata(
        tool_id="inv_update",
        name="update_invoice",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices/{invoice_id}",
        http_method=HTTPMethod.PUT,
        description="Updates an existing draft invoice's items, amount, description, or recipient information.",
        parameters_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The unique PayPal invoice ID to update"},
                "amount": {"type": "number", "description": "Updated invoice amount"},
                "description": {"type": "string", "description": "Updated description"}
            },
            "required": ["invoice_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["update invoice", "modify draft invoice", "edit invoice amount"]
    ),
    ToolMetadata(
        tool_id="inv_cancel",
        name="cancel_invoice",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices/{invoice_id}/cancel",
        http_method=HTTPMethod.POST,
        description="Cancels a sent invoice and notifies the recipient that the invoice is void.",
        parameters_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The ID of the invoice to cancel"},
                "reason": {"type": "string", "description": "Reason for cancellation"}
            },
            "required": ["invoice_id"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["cancel invoice", "void invoice", "revoke sent invoice"]
    ),
    ToolMetadata(
        tool_id="inv_delete_draft",
        name="delete_draft_invoice",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices/{invoice_id}",
        http_method=HTTPMethod.DELETE,
        description="Permanently deletes a draft invoice. Sent invoices cannot be deleted; they must be cancelled.",
        parameters_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The ID of the draft invoice to delete"}
            },
            "required": ["invoice_id"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["delete draft invoice", "remove draft invoice", "discard invoice"]
    ),
    ToolMetadata(
        tool_id="inv_list",
        name="list_invoices",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices",
        http_method=HTTPMethod.GET,
        description="Lists all merchant invoices with optional filtering by status (DRAFT, SENT, PAID, CANCELLED) and page size.",
        parameters_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: DRAFT, SENT, PAID, CANCELLED"},
                "limit": {"type": "integer", "default": 20, "description": "Maximum number of records to return"}
            }
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["list invoices", "show my invoices", "find all invoices", "get invoice list"]
    ),
    ToolMetadata(
        tool_id="inv_record_payment",
        name="record_external_payment",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices/{invoice_id}/payments",
        http_method=HTTPMethod.POST,
        description="Records an offline or external payment (e.g. cash, wire transfer, check) against a sent invoice.",
        parameters_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The invoice ID receiving payment"},
                "payment_method": {"type": "string", "description": "Payment method: CASH, BANK_TRANSFER, CHECK, OTHER"},
                "amount": {"type": "number", "description": "Payment amount recorded"}
            },
            "required": ["invoice_id", "payment_method", "amount"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["record invoice payment", "mark invoice as paid offline", "log payment for invoice"]
    ),
    ToolMetadata(
        tool_id="inv_send_reminder",
        name="send_invoice_reminder",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices/{invoice_id}/remind",
        http_method=HTTPMethod.POST,
        description="Sends a payment reminder notification to the recipient of an unpaid or overdue invoice.",
        parameters_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The invoice ID to remind recipient about"},
                "note": {"type": "string", "description": "Optional reminder note"}
            },
            "required": ["invoice_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["send invoice reminder", "remind client to pay", "follow up on invoice"]
    ),
    ToolMetadata(
        tool_id="inv_generate_qr",
        name="generate_invoice_qr",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices/{invoice_id}/generate-qr-code",
        http_method=HTTPMethod.GET,
        description="Generates a base64-encoded QR code image URL allowing mobile buyers to scan and pay an invoice.",
        parameters_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The invoice ID"}
            },
            "required": ["invoice_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["get invoice qr code", "generate qr to pay invoice", "qr code for invoice"]
    ),
    ToolMetadata(
        tool_id="inv_search",
        name="search_invoices",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/search-invoices",
        http_method=HTTPMethod.POST,
        description="Searches invoices by complex criteria including recipient email, date range, amount bounds, and keywords.",
        parameters_schema={
            "type": "object",
            "properties": {
                "recipient_email": {"type": "string", "description": "Filter by recipient email address"},
                "start_date": {"type": "string", "description": "Creation start date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "Creation end date (YYYY-MM-DD)"}
            }
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["search invoices", "filter invoices by customer", "find invoice for email"]
    ),
    ToolMetadata(
        tool_id="inv_create_template",
        name="create_invoice_template",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/templates",
        http_method=HTTPMethod.POST,
        description="Creates a reusable invoice template for recurring billing formats, default line items, and terms.",
        parameters_schema={
            "type": "object",
            "properties": {
                "template_name": {"type": "string", "description": "Descriptive name for the template"},
                "default_currency": {"type": "string", "default": "USD", "description": "Default currency code"},
                "standard_terms": {"type": "string", "description": "Default payment terms and conditions"}
            },
            "required": ["template_name"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["create invoice template", "save billing template", "new invoice format"]
    ),
    ToolMetadata(
        tool_id="inv_list_templates",
        name="list_invoice_templates",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/templates",
        http_method=HTTPMethod.GET,
        description="Retrieves all saved invoice templates configured on the merchant account.",
        parameters_schema={"type": "object", "properties": {}},
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["list invoice templates", "view billing templates", "get invoice formats"]
    ),
    ToolMetadata(
        tool_id="inv_get_template",
        name="get_invoice_template",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/templates/{template_id}",
        http_method=HTTPMethod.GET,
        description="Fetches full configuration details for a specific invoice template ID.",
        parameters_schema={
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "The template identifier"}
            },
            "required": ["template_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["get invoice template", "view template details", "inspect template"]
    ),
    ToolMetadata(
        tool_id="inv_refund",
        name="refund_invoice",
        category=ToolCategory.INVOICING,
        endpoint="/v2/invoicing/invoices/{invoice_id}/refunds",
        http_method=HTTPMethod.POST,
        description="Issues a full or partial refund for a paid PayPal invoice directly back to the payer's account.",
        parameters_schema={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The invoice ID to refund"},
                "amount": {"type": "number", "description": "Refund amount (omit for full refund)"},
                "reason": {"type": "string", "description": "Reason for the refund"}
            },
            "required": ["invoice_id"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/invoicing"],
        trigger_phrases=["refund invoice", "issue refund for invoice", "reimburse invoice payment"]
    ),

    # =========================================================================
    # PAYMENTS & ORDERS & CAPTURES APIS (16 Tools)
    # =========================================================================
    ToolMetadata(
        tool_id="pay_create_order",
        name="create_payment_order",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/checkout/orders",
        http_method=HTTPMethod.POST,
        description="Creates an online payment order intent (CAPTURE or AUTHORIZE) with currency and items to initiate checkout.",
        parameters_schema={
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["CAPTURE", "AUTHORIZE"], "default": "CAPTURE"},
                "amount": {"type": "number", "description": "Transaction total amount"},
                "currency": {"type": "string", "default": "USD"},
                "description": {"type": "string", "description": "Order description"}
            },
            "required": ["amount"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["create payment order", "start checkout order", "initiate payment"]
    ),
    ToolMetadata(
        tool_id="pay_authorize",
        name="authorize_payment",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/checkout/orders/{order_id}/authorize",
        http_method=HTTPMethod.POST,
        description="Authorizes payment funds for an order without immediate capture, holding buyer funds for up to 29 days.",
        parameters_schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "PayPal order ID to authorize"}
            },
            "required": ["order_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["authorize payment", "hold funds for order", "place authorization"]
    ),
    ToolMetadata(
        tool_id="pay_capture",
        name="capture_authorization",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/payments/authorizations/{authorization_id}/capture",
        http_method=HTTPMethod.POST,
        description="Captures previously authorized payment funds, moving money into the merchant balance. Sensitive financial action.",
        parameters_schema={
            "type": "object",
            "properties": {
                "authorization_id": {"type": "string", "description": "The authorization ID to capture"},
                "amount": {"type": "number", "description": "Amount to capture (omit to capture full authorized amount)"}
            },
            "required": ["authorization_id"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["capture payment", "capture authorization", "claim authorized funds"]
    ),
    ToolMetadata(
        tool_id="pay_refund_capture",
        name="refund_captured_payment",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/payments/captures/{capture_id}/refund",
        http_method=HTTPMethod.POST,
        description="Issues a full or partial refund for a captured payment transaction. Irreversible financial action.",
        parameters_schema={
            "type": "object",
            "properties": {
                "capture_id": {"type": "string", "description": "The capture transaction ID to refund"},
                "amount": {"type": "number", "description": "Refund amount"},
                "note_to_payer": {"type": "string", "description": "Explanation note for the buyer"}
            },
            "required": ["capture_id"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["refund payment", "issue refund for capture", "return money to buyer", "reverse payment"]
    ),
    ToolMetadata(
        tool_id="pay_get_details",
        name="get_payment_details",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/checkout/orders/{order_id}",
        http_method=HTTPMethod.GET,
        description="Retrieves current order status, payment source details, buyer information, and amount breakdown.",
        parameters_schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "The PayPal order ID"}
            },
            "required": ["order_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["get payment details", "lookup order status", "check order payment", "view payment"]
    ),
    ToolMetadata(
        tool_id="pay_list_orders",
        name="list_payment_orders",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/checkout/orders",
        http_method=HTTPMethod.GET,
        description="Lists checkout orders created by the merchant within a given timeframe.",
        parameters_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: CREATED, SAVED, APPROVED, VOIDED, COMPLETED"},
                "limit": {"type": "integer", "default": 20}
            }
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["list payment orders", "find orders", "show checkout orders"]
    ),
    ToolMetadata(
        tool_id="pay_void_auth",
        name="void_authorization",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/payments/authorizations/{authorization_id}/void",
        http_method=HTTPMethod.POST,
        description="Voids an open authorization, immediately releasing reserved funds back to the buyer.",
        parameters_schema={
            "type": "object",
            "properties": {
                "authorization_id": {"type": "string", "description": "The authorization ID to cancel"}
            },
            "required": ["authorization_id"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["void authorization", "cancel hold on funds", "release authorization"]
    ),
    ToolMetadata(
        tool_id="pay_reauthorize",
        name="reauthorize_payment",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/payments/authorizations/{authorization_id}/reauthorize",
        http_method=HTTPMethod.POST,
        description="Reauthorizes a payment authorization before expiration, extending the hold window for an additional 30 days.",
        parameters_schema={
            "type": "object",
            "properties": {
                "authorization_id": {"type": "string", "description": "The authorization ID to reauthorize"}
            },
            "required": ["authorization_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["reauthorize payment", "extend authorization hold"]
    ),
    ToolMetadata(
        tool_id="pay_get_capture",
        name="get_capture_details",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/payments/captures/{capture_id}",
        http_method=HTTPMethod.GET,
        description="Retrieves details of a captured payment, including transaction fees, net amount, and settlement status.",
        parameters_schema={
            "type": "object",
            "properties": {
                "capture_id": {"type": "string", "description": "The capture transaction ID"}
            },
            "required": ["capture_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["get capture details", "view capture", "inspect capture fees"]
    ),
    ToolMetadata(
        tool_id="pay_list_captures",
        name="list_captures",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/payments/captures",
        http_method=HTTPMethod.GET,
        description="Lists all captured payments for the account with status and pagination.",
        parameters_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20}
            }
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["list captures", "show captured payments", "find all captures"]
    ),
    ToolMetadata(
        tool_id="pay_get_refund",
        name="get_refund_details",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/payments/refunds/{refund_id}",
        http_method=HTTPMethod.GET,
        description="Retrieves the status, amount, and timestamp of a previously issued refund.",
        parameters_schema={
            "type": "object",
            "properties": {
                "refund_id": {"type": "string", "description": "The refund transaction ID"}
            },
            "required": ["refund_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["get refund details", "lookup refund status", "view refund"]
    ),
    ToolMetadata(
        tool_id="pay_create_payout",
        name="create_payout_batch",
        category=ToolCategory.PAYMENTS,
        endpoint="/v1/payments/payouts",
        http_method=HTTPMethod.POST,
        description="Dispatches direct batch payouts to multiple vendor or affiliate PayPal accounts simultaneously.",
        parameters_schema={
            "type": "object",
            "properties": {
                "recipient_email": {"type": "string", "description": "Recipient PayPal account email"},
                "amount": {"type": "number", "description": "Payout amount"},
                "currency": {"type": "string", "default": "USD"},
                "note": {"type": "string", "description": "Note for recipient"}
            },
            "required": ["recipient_email", "amount"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/payouts"],
        trigger_phrases=["send payout", "pay affiliate", "disburse funds", "transfer money to vendor"]
    ),
    ToolMetadata(
        tool_id="pay_get_payout_status",
        name="get_payout_batch_status",
        category=ToolCategory.PAYMENTS,
        endpoint="/v1/payments/payouts/{payout_batch_id}",
        http_method=HTTPMethod.GET,
        description="Retrieves processing status and item-by-item breakdown of a batch payout request.",
        parameters_schema={
            "type": "object",
            "properties": {
                "payout_batch_id": {"type": "string", "description": "The payout batch ID"}
            },
            "required": ["payout_batch_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payouts"],
        trigger_phrases=["check payout status", "view payout batch", "payout progress"]
    ),
    ToolMetadata(
        tool_id="pay_get_payout_item",
        name="get_payout_item_status",
        category=ToolCategory.PAYMENTS,
        endpoint="/v1/payments/payouts-item/{payout_item_id}",
        http_method=HTTPMethod.GET,
        description="Retrieves detailed transaction status for an individual payout item inside a batch.",
        parameters_schema={
            "type": "object",
            "properties": {
                "payout_item_id": {"type": "string", "description": "The unique payout item ID"}
            },
            "required": ["payout_item_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payouts"],
        trigger_phrases=["get payout item status", "inspect payout line item"]
    ),
    ToolMetadata(
        tool_id="pay_cancel_unclaimed",
        name="cancel_unclaimed_payout",
        category=ToolCategory.PAYMENTS,
        endpoint="/v1/payments/payouts-item/{payout_item_id}/cancel",
        http_method=HTTPMethod.POST,
        description="Cancels an unclaimed payout item that was sent to an unregistered recipient, returning funds.",
        parameters_schema={
            "type": "object",
            "properties": {
                "payout_item_id": {"type": "string", "description": "The unclaimed payout item ID"}
            },
            "required": ["payout_item_id"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/payouts"],
        trigger_phrases=["cancel unclaimed payout", "revoke unclaimed transfer"]
    ),
    ToolMetadata(
        tool_id="pay_verify_credentials",
        name="verify_payment_credentials",
        category=ToolCategory.PAYMENTS,
        endpoint="/v2/payments/verify-credentials",
        http_method=HTTPMethod.POST,
        description="Validates merchant payment gateway credentials and active billing agreements.",
        parameters_schema={"type": "object", "properties": {}},
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/payments"],
        trigger_phrases=["verify credentials", "check payment gateway status"]
    ),

    # =========================================================================
    # CUSTOMER DISPUTES & CLAIMS APIS (10 Tools)
    # =========================================================================
    ToolMetadata(
        tool_id="disp_list",
        name="list_disputes",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/disputes",
        http_method=HTTPMethod.GET,
        description="Lists customer dispute claims filed against the merchant, filterable by dispute status, state, or customer ID.",
        parameters_schema={
            "type": "object",
            "properties": {
                "dispute_state": {"type": "string", "enum": ["REQUIRED_ACTION", "REQUIRED_OTHER_PARTY_ACTION", "UNDER_PAYPAL_REVIEW", "RESOLVED", "OPEN"], "default": "OPEN"},
                "customer_id": {"type": "string", "description": "Filter disputes by customer/buyer ID (e.g. user_123)"},
                "limit": {"type": "integer", "default": 10}
            }
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/disputes"],
        trigger_phrases=["list disputes", "is there a dispute open", "check disputes from user", "show customer disputes", "find open claims"]
    ),
    ToolMetadata(
        tool_id="disp_get_details",
        name="get_dispute_details",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/disputes/{dispute_id}",
        http_method=HTTPMethod.GET,
        description="Fetches full dispute details, reason code, claim amount, buyer notes, and merchant evidence deadlines.",
        parameters_schema={
            "type": "object",
            "properties": {
                "dispute_id": {"type": "string", "description": "The unique PayPal dispute ID (e.g., PP-D-XXXX)"}
            },
            "required": ["dispute_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/disputes"],
        trigger_phrases=["get dispute details", "lookup dispute", "inspect dispute claim", "view dispute information"]
    ),
    ToolMetadata(
        tool_id="disp_submit_evidence",
        name="submit_dispute_evidence",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/disputes/{dispute_id}/provide-evidence",
        http_method=HTTPMethod.POST,
        description="Submits merchant evidence (proof of delivery, tracking number, refund receipt) to contest a customer dispute.",
        parameters_schema={
            "type": "object",
            "properties": {
                "dispute_id": {"type": "string", "description": "The dispute ID"},
                "evidence_type": {"type": "string", "description": "PROOF_OF_DELIVERY, PROOF_OF_REFUND, CUSTOM_NOTES"},
                "tracking_number": {"type": "string", "description": "Package shipment tracking number"},
                "notes": {"type": "string", "description": "Explanation notes for PayPal dispute reviewer"}
            },
            "required": ["dispute_id", "evidence_type", "notes"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/disputes"],
        trigger_phrases=["submit dispute evidence", "contest dispute", "upload proof of delivery", "respond to dispute"]
    ),
    ToolMetadata(
        tool_id="disp_accept_claim",
        name="accept_dispute_claim",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/disputes/{dispute_id}/accept-claim",
        http_method=HTTPMethod.POST,
        description="Accepts buyer dispute claim, closing the dispute and immediately refunding buyer in full. Irreversible.",
        parameters_schema={
            "type": "object",
            "properties": {
                "dispute_id": {"type": "string", "description": "The dispute ID to accept and refund"},
                "note": {"type": "string", "description": "Merchant note"}
            },
            "required": ["dispute_id"]
        },
        is_sensitive=True,
        required_permissions=["https://uri.paypal.com/services/disputes"],
        trigger_phrases=["accept dispute claim", "concede dispute", "refund buyer to close dispute", "settle dispute"]
    ),
    ToolMetadata(
        tool_id="disp_appeal",
        name="appeal_dispute",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/disputes/{dispute_id}/appeal",
        http_method=HTTPMethod.POST,
        description="Appeals a resolved dispute decided against merchant, providing new evidence for secondary review.",
        parameters_schema={
            "type": "object",
            "properties": {
                "dispute_id": {"type": "string", "description": "The closed dispute ID to appeal"},
                "appeal_reason": {"type": "string", "description": "Explanation of why the decision was incorrect"}
            },
            "required": ["dispute_id", "appeal_reason"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/disputes"],
        trigger_phrases=["appeal dispute", "contest closed dispute", "appeal lost claim"]
    ),
    ToolMetadata(
        tool_id="disp_escalate",
        name="escalate_dispute",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/disputes/{dispute_id}/escalate",
        http_method=HTTPMethod.POST,
        description="Escalates an inquiry to an official PayPal claim requiring formal resolution and mediation.",
        parameters_schema={
            "type": "object",
            "properties": {
                "dispute_id": {"type": "string", "description": "The dispute ID to escalate"},
                "note": {"type": "string", "description": "Reason for escalation"}
            },
            "required": ["dispute_id"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/disputes"],
        trigger_phrases=["escalate dispute to paypal", "escalate inquiry to claim"]
    ),
    ToolMetadata(
        tool_id="disp_supporting_info",
        name="provide_supporting_info",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/disputes/{dispute_id}/provide-supporting-info",
        http_method=HTTPMethod.POST,
        description="Supplies requested clarification or supplementary documents to PayPal dispute investigators.",
        parameters_schema={
            "type": "object",
            "properties": {
                "dispute_id": {"type": "string", "description": "The dispute ID"},
                "notes": {"type": "string", "description": "Supporting details"}
            },
            "required": ["dispute_id", "notes"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/disputes"],
        trigger_phrases=["provide supporting info for dispute", "clarify dispute details"]
    ),
    ToolMetadata(
        tool_id="disp_ack_return",
        name="acknowledge_return_item",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/disputes/{dispute_id}/acknowledge-return-item",
        http_method=HTTPMethod.POST,
        description="Acknowledges receipt of returned merchandise from a buyer during a dispute resolution process.",
        parameters_schema={
            "type": "object",
            "properties": {
                "dispute_id": {"type": "string", "description": "The dispute ID"},
                "item_condition": {"type": "string", "enum": ["GOOD", "DAMAGED", "WRONG_ITEM"]}
            },
            "required": ["dispute_id", "item_condition"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/disputes"],
        trigger_phrases=["acknowledge return package", "confirm returned item received"]
    ),
    ToolMetadata(
        tool_id="disp_send_msg",
        name="send_dispute_message",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/disputes/{dispute_id}/send-message",
        http_method=HTTPMethod.POST,
        description="Sends a direct communicative message to the buyer within the PayPal dispute resolution center.",
        parameters_schema={
            "type": "object",
            "properties": {
                "dispute_id": {"type": "string", "description": "The dispute ID"},
                "message": {"type": "string", "description": "Message text"}
            },
            "required": ["dispute_id", "message"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/disputes"],
        trigger_phrases=["send message to buyer in dispute", "message customer about claim"]
    ),
    ToolMetadata(
        tool_id="disp_list_reasons",
        name="list_dispute_reasons",
        category=ToolCategory.DISPUTES,
        endpoint="/v1/customer/disputes/reasons",
        http_method=HTTPMethod.GET,
        description="Lists all valid PayPal dispute reason codes and categories (e.g. MERCHANDISE_NOT_RECEIVED, UNAUTHORIZED).",
        parameters_schema={"type": "object", "properties": {}},
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/disputes"],
        trigger_phrases=["list dispute reasons", "get dispute reason codes"]
    ),

    # =========================================================================
    # REPORTING & TRANSACTIONS APIS (10 Tools)
    # =========================================================================
    ToolMetadata(
        tool_id="rep_summary",
        name="get_transaction_summary",
        category=ToolCategory.REPORTING,
        endpoint="/v1/reporting/transactions/summary",
        http_method=HTTPMethod.GET,
        description="Retrieves aggregate financial metrics including total sales volume, gross revenue, net revenue, and transaction counts across a date range.",
        parameters_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date in ISO format YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date in ISO format YYYY-MM-DD"},
                "currency": {"type": "string", "default": "USD", "description": "3-letter currency code"}
            },
            "required": ["start_date", "end_date"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/reporting"],
        trigger_phrases=["what was my total sales volume", "sales volume last month", "gross revenue summary", "total sales report"]
    ),
    ToolMetadata(
        tool_id="rep_list_txns",
        name="list_transactions",
        category=ToolCategory.REPORTING,
        endpoint="/v1/reporting/transactions",
        http_method=HTTPMethod.GET,
        description="Lists individual account transaction records with timestamps, payment statuses, gross amounts, and payer details.",
        parameters_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
                "transaction_status": {"type": "string", "description": "SUCCESS, DENIED, PENDING, REVERSED"},
                "limit": {"type": "integer", "default": 20}
            },
            "required": ["start_date", "end_date"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/reporting"],
        trigger_phrases=["list transactions", "show transaction history", "find transactions between dates"]
    ),
    ToolMetadata(
        tool_id="rep_balances",
        name="get_account_balances",
        category=ToolCategory.REPORTING,
        endpoint="/v1/reporting/balances",
        http_method=HTTPMethod.GET,
        description="Retrieves primary and foreign currency wallet balances currently available in the merchant PayPal account.",
        parameters_schema={
            "type": "object",
            "properties": {
                "as_of_time": {"type": "string", "description": "Optional ISO timestamp for historical balance"}
            }
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/reporting"],
        trigger_phrases=["get account balance", "what is my balance", "check paypal wallet balance", "available funds"]
    ),
    ToolMetadata(
        tool_id="rep_settlement",
        name="get_settlement_report",
        category=ToolCategory.REPORTING,
        endpoint="/v1/reporting/settlements",
        http_method=HTTPMethod.GET,
        description="Retrieves bank settlement transfer details, batch payout summaries, and deposit dates.",
        parameters_schema={
            "type": "object",
            "properties": {
                "settlement_date": {"type": "string", "description": "Date of settlement YYYY-MM-DD"}
            },
            "required": ["settlement_date"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/reporting"],
        trigger_phrases=["get settlement report", "bank deposit report", "settlement transfer status"]
    ),
    ToolMetadata(
        tool_id="rep_financial_stmt",
        name="generate_financial_statement",
        category=ToolCategory.REPORTING,
        endpoint="/v1/reporting/financial-statements",
        http_method=HTTPMethod.POST,
        description="Generates an official downloadable accounting financial statement covering fees, charges, and payouts.",
        parameters_schema={
            "type": "object",
            "properties": {
                "month": {"type": "integer", "description": "Month number 1-12"},
                "year": {"type": "integer", "description": "Year YYYY"}
            },
            "required": ["month", "year"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/reporting"],
        trigger_phrases=["generate financial statement", "monthly accounting report", "download statement"]
    ),
    ToolMetadata(
        tool_id="rep_search_date",
        name="search_transactions_by_date",
        category=ToolCategory.REPORTING,
        endpoint="/v1/reporting/search-transactions",
        http_method=HTTPMethod.POST,
        description="Searches specific transaction records matching payer names, transaction IDs, or exact amounts.",
        parameters_schema={
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "Specific transaction reference ID"},
                "payer_email": {"type": "string", "description": "Payer email address"}
            }
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/reporting"],
        trigger_phrases=["search transactions", "find transaction by id", "lookup transaction by email"]
    ),
    ToolMetadata(
        tool_id="rep_dispute_report",
        name="get_dispute_report",
        category=ToolCategory.REPORTING,
        endpoint="/v1/reporting/dispute-reports",
        http_method=HTTPMethod.GET,
        description="Retrieves monthly chargeback and dispute statistics, calculating dispute-to-transaction ratio.",
        parameters_schema={
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Year YYYY"}
            },
            "required": ["year"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/reporting"],
        trigger_phrases=["get dispute report", "dispute ratio metrics", "chargeback rate report"]
    ),
    ToolMetadata(
        tool_id="rep_fee_statements",
        name="list_fee_statements",
        category=ToolCategory.REPORTING,
        endpoint="/v1/reporting/fee-statements",
        http_method=HTTPMethod.GET,
        description="Lists itemized PayPal merchant processing fees, currency conversion charges, and tax withholdings.",
        parameters_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"}
            },
            "required": ["start_date", "end_date"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/reporting"],
        trigger_phrases=["list fee statements", "show merchant processing fees", "view paypal fees"]
    ),
    ToolMetadata(
        tool_id="rep_monthly_sales",
        name="get_monthly_sales_volume",
        category=ToolCategory.REPORTING,
        endpoint="/v1/reporting/sales-volume/monthly",
        http_method=HTTPMethod.GET,
        description="Convenience reporting endpoint retrieving sales volume and growth percentage for a designated month and year.",
        parameters_schema={
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Year (e.g., 2026)"},
                "month": {"type": "integer", "description": "Month (1-12)"}
            },
            "required": ["year", "month"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/reporting"],
        trigger_phrases=["monthly sales volume", "sales volume last month", "how much did i sell in month"]
    ),
    ToolMetadata(
        tool_id="rep_currency_conv",
        name="get_currency_conversions",
        category=ToolCategory.REPORTING,
        endpoint="/v1/reporting/currency-conversions",
        http_method=HTTPMethod.GET,
        description="Retrieves official PayPal conversion exchange rates applied to multi-currency cross-border transactions.",
        parameters_schema={
            "type": "object",
            "properties": {
                "from_currency": {"type": "string", "default": "USD"},
                "to_currency": {"type": "string", "default": "EUR"}
            },
            "required": ["from_currency", "to_currency"]
        },
        is_sensitive=False,
        required_permissions=["https://uri.paypal.com/services/reporting"],
        trigger_phrases=["currency conversion rates", "check exchange rates", "forex conversion report"]
    )
]

def seed_tools(db: ToolRegistryDB) -> int:
    """Seeds the SQLite database with all 51 PayPal API tool specifications."""
    db.register_tools_bulk(PAYPAL_TOOLS)
    return len(PAYPAL_TOOLS)
