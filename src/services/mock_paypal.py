import datetime
import uuid
from typing import Dict, Any, List, Optional

class MockPayPalService:
    """
    High-fidelity stateful mock service implementing PayPal REST APIs.
    Maintains persistent in-memory state across Invoicing, Payments,
    Disputes, and Reporting.
    """
    def __init__(self):
        self._init_state()

    def _init_state(self):
        # 1. Invoices Store
        self.invoices: Dict[str, Dict[str, Any]] = {
            "INV-2026-901": {
                "invoice_id": "INV-2026-901",
                "recipient_email": "alice@example.com",
                "amount": 150.00,
                "currency": "USD",
                "description": "Monthly Software Subscription",
                "status": "PAID",
                "created_at": "2026-10-15T09:00:00Z",
                "sent_at": "2026-10-15T09:05:00Z",
                "paid_at": "2026-10-16T14:20:00Z"
            },
            "INV-2026-902": {
                "invoice_id": "INV-2026-902",
                "recipient_email": "charlie@example.com",
                "amount": 320.00,
                "currency": "USD",
                "description": "Cloud Hosting & Migration",
                "status": "SENT",
                "created_at": "2026-11-01T11:00:00Z",
                "sent_at": "2026-11-01T11:02:00Z"
            }
        }

        # 2. Disputes Store (Scenario 3: user_123 open dispute)
        self.disputes: Dict[str, Dict[str, Any]] = {
            "PP-D-4412": {
                "dispute_id": "PP-D-4412",
                "customer_id": "user_123",
                "dispute_state": "OPEN",
                "dispute_amount": 120.00,
                "currency": "USD",
                "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                "status": "WAITING_FOR_SELLER_RESPONSE",
                "buyer_notes": "Order placed 3 weeks ago; tracking number shows no movement.",
                "created_at": "2026-11-04T14:30:00Z",
                "respond_by_date": "2026-11-14T23:59:59Z",
                "evidence": []
            },
            "PP-D-4413": {
                "dispute_id": "PP-D-4413",
                "customer_id": "user_456",
                "dispute_state": "RESOLVED",
                "dispute_amount": 45.00,
                "currency": "USD",
                "reason": "UNAUTHORIZED_TRANSACTION",
                "status": "RESOLVED",
                "buyer_notes": "Did not recognize charge.",
                "created_at": "2026-10-10T12:00:00Z",
                "resolved_at": "2026-10-15T16:00:00Z",
                "evidence": []
            }
        }

        # 3. Orders & Payments Store
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.authorizations: Dict[str, Dict[str, Any]] = {}
        self.captures: Dict[str, Dict[str, Any]] = {
            "CAP-1001": {
                "capture_id": "CAP-1001",
                "amount": 250.00,
                "currency": "USD",
                "status": "COMPLETED",
                "captured_at": "2026-11-02T10:00:00Z",
                "refunded_amount": 0.00
            }
        }
        self.refunds: Dict[str, Dict[str, Any]] = {}

        # 4. Balances & Transactions (Scenario 2: October 2026 volume)
        self.balances = {
            "available": 18450.00,
            "pending": 2300.00,
            "currency": "USD"
        }

        self.transactions: List[Dict[str, Any]] = [
            # Seed transactions spanning October 2026 totaling $42,500 across 312 records
            {
                "transaction_id": "TXN-2026-OCT-AGGREGATE",
                "date": "2026-10-31",
                "amount": 42500.00,
                "fee": 1232.50,
                "net": 41267.50,
                "currency": "USD",
                "status": "SUCCESS",
                "transaction_count": 312,
                "description": "October 2026 Aggregated Sales Volume"
            },
            {
                "transaction_id": "TXN-2026-11-01",
                "date": "2026-11-01",
                "amount": 320.00,
                "fee": 9.28,
                "net": 310.72,
                "currency": "USD",
                "status": "SUCCESS",
                "description": "Cloud Hosting Service"
            }
        ]

    # =========================================================================
    # INVOICING HANDLERS
    # =========================================================================
    def create_draft_invoice(
        self,
        recipient_email: str,
        amount: float,
        description: str,
        currency: str = "USD",
        due_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """Creates a new draft invoice."""
        inv_num = len(self.invoices) + 1
        invoice_id = f"INV-2026-{inv_num:03d}"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        invoice_record = {
            "invoice_id": invoice_id,
            "recipient_email": recipient_email,
            "amount": float(amount),
            "currency": currency,
            "description": description,
            "due_date": due_date or (datetime.date.today() + datetime.timedelta(days=14)).isoformat(),
            "status": "DRAFT",
            "created_at": now
        }
        self.invoices[invoice_id] = invoice_record
        return {
            "status": "CREATED",
            "invoice_id": invoice_id,
            "amount": invoice_record["amount"],
            "currency": invoice_record["currency"],
            "recipient_email": invoice_record["recipient_email"],
            "description": invoice_record["description"],
            "invoice_status": "DRAFT",
            "created_at": now
        }

    def send_invoice(
        self,
        invoice_id: str,
        subject: Optional[str] = None,
        note: Optional[str] = None
    ) -> Dict[str, Any]:
        """Dispatches a draft invoice to the recipient."""
        if invoice_id not in self.invoices:
            return {"error": f"Invoice '{invoice_id}' not found.", "status_code": 404}

        invoice = self.invoices[invoice_id]
        if invoice["status"] == "CANCELLED":
            return {"error": f"Cannot send cancelled invoice '{invoice_id}'.", "status_code": 400}

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        invoice["status"] = "SENT"
        invoice["sent_at"] = now
        invoice["email_subject"] = subject or f"Invoice {invoice_id} from Merchant"
        invoice["note"] = note

        return {
            "status": "SENT",
            "invoice_id": invoice_id,
            "recipient_email": invoice["recipient_email"],
            "amount": invoice["amount"],
            "currency": invoice["currency"],
            "sent_at": now,
            "message": f"Invoice {invoice_id} successfully dispatched to {invoice['recipient_email']}."
        }

    def get_invoice_details(self, invoice_id: str) -> Dict[str, Any]:
        if invoice_id not in self.invoices:
            return {"error": f"Invoice '{invoice_id}' not found.", "status_code": 404}
        return self.invoices[invoice_id]

    def list_invoices(self, status: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
        results = list(self.invoices.values())
        if status:
            results = [inv for inv in results if inv["status"].upper() == status.upper()]
        return {"total_count": len(results), "invoices": results[:limit]}

    def cancel_invoice(self, invoice_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        if invoice_id not in self.invoices:
            return {"error": f"Invoice '{invoice_id}' not found.", "status_code": 404}
        invoice = self.invoices[invoice_id]
        invoice["status"] = "CANCELLED"
        invoice["cancelled_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        invoice["cancel_reason"] = reason or "Cancelled by merchant"
        return {"status": "CANCELLED", "invoice_id": invoice_id, "message": "Invoice voided successfully."}

    # =========================================================================
    # PAYMENTS & CAPTURES HANDLERS
    # =========================================================================
    def create_payment_order(
        self,
        amount: float,
        currency: str = "USD",
        intent: str = "CAPTURE",
        description: str = ""
    ) -> Dict[str, Any]:
        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        order = {
            "order_id": order_id,
            "amount": float(amount),
            "currency": currency,
            "intent": intent,
            "status": "CREATED",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        self.orders[order_id] = order
        return order

    def authorize_payment(self, order_id: str) -> Dict[str, Any]:
        auth_id = f"AUTH-{uuid.uuid4().hex[:8].upper()}"
        self.authorizations[auth_id] = {
            "authorization_id": auth_id,
            "order_id": order_id,
            "status": "AUTHORIZED",
            "expires_in_days": 29
        }
        return self.authorizations[auth_id]

    def capture_authorization(self, authorization_id: str, amount: Optional[float] = None) -> Dict[str, Any]:
        cap_id = f"CAP-{uuid.uuid4().hex[:8].upper()}"
        capture = {
            "capture_id": cap_id,
            "authorization_id": authorization_id,
            "amount": amount or 100.00,
            "status": "COMPLETED",
            "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        self.captures[cap_id] = capture
        return capture

    def refund_captured_payment(
        self,
        capture_id: str,
        amount: Optional[float] = None,
        note_to_payer: Optional[str] = None
    ) -> Dict[str, Any]:
        refund_id = f"REF-{uuid.uuid4().hex[:8].upper()}"
        refund = {
            "refund_id": refund_id,
            "capture_id": capture_id,
            "amount": amount or 50.00,
            "currency": "USD",
            "status": "COMPLETED",
            "note_to_payer": note_to_payer or "Customer refund processed",
            "refunded_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        self.refunds[refund_id] = refund
        return refund

    # =========================================================================
    # DISPUTES HANDLERS
    # =========================================================================
    def list_disputes(
        self,
        dispute_state: Optional[str] = None,
        customer_id: Optional[str] = None,
        limit: int = 10
    ) -> Dict[str, Any]:
        """Lists disputes with optional filtering by customer_id or dispute_state."""
        matches = list(self.disputes.values())
        if customer_id:
            matches = [d for d in matches if d["customer_id"].lower() == customer_id.lower()]
        if dispute_state and dispute_state.upper() != "ALL":
            matches = [d for d in matches if d["dispute_state"].upper() == dispute_state.upper()]
        return {"total_items": len(matches), "disputes": matches[:limit]}

    def get_dispute_details(self, dispute_id: str) -> Dict[str, Any]:
        if dispute_id not in self.disputes:
            return {"error": f"Dispute '{dispute_id}' not found.", "status_code": 404}
        return self.disputes[dispute_id]

    def submit_dispute_evidence(
        self,
        dispute_id: str,
        evidence_type: str,
        notes: str,
        tracking_number: Optional[str] = None
    ) -> Dict[str, Any]:
        if dispute_id not in self.disputes:
            return {"error": f"Dispute '{dispute_id}' not found.", "status_code": 404}
        dispute = self.disputes[dispute_id]
        evidence_record = {
            "evidence_id": f"EVD-{uuid.uuid4().hex[:6].upper()}",
            "type": evidence_type,
            "notes": notes,
            "tracking_number": tracking_number,
            "submitted_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        dispute["evidence"].append(evidence_record)
        dispute["status"] = "UNDER_PAYPAL_REVIEW"
        return {
            "status": "EVIDENCE_SUBMITTED",
            "dispute_id": dispute_id,
            "dispute_status": "UNDER_PAYPAL_REVIEW",
            "evidence": evidence_record
        }

    def accept_dispute_claim(self, dispute_id: str, note: Optional[str] = None) -> Dict[str, Any]:
        if dispute_id not in self.disputes:
            return {"error": f"Dispute '{dispute_id}' not found.", "status_code": 404}
        dispute = self.disputes[dispute_id]
        dispute["dispute_state"] = "RESOLVED"
        dispute["status"] = "RESOLVED_BUYER_FAVOUR"
        dispute["refunded_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        dispute["merchant_note"] = note
        return {
            "status": "CLAIM_ACCEPTED",
            "dispute_id": dispute_id,
            "refund_issued": dispute["dispute_amount"],
            "currency": dispute["currency"],
            "message": "Claim accepted. Full refund has been issued to buyer."
        }

    # =========================================================================
    # REPORTING & ANALYTICS HANDLERS
    # =========================================================================
    def get_transaction_summary(
        self,
        start_date: str,
        end_date: str,
        currency: str = "USD"
    ) -> Dict[str, Any]:
        """Calculates aggregate sales volume across the requested date window."""
        # Check for October 2026 aggregate
        if "2026-10" in start_date or "2026-10" in end_date:
            return {
                "period": f"{start_date} to {end_date}",
                "currency": currency,
                "total_sales_volume": 42500.00,
                "gross_revenue": 42500.00,
                "net_revenue": 41267.50,
                "total_fees": 1232.50,
                "transaction_count": 312,
                "average_transaction_value": round(42500.00 / 312, 2)
            }
        
        # General date range
        return {
            "period": f"{start_date} to {end_date}",
            "currency": currency,
            "total_sales_volume": 12850.00,
            "gross_revenue": 12850.00,
            "net_revenue": 12480.00,
            "total_fees": 370.00,
            "transaction_count": 84,
            "average_transaction_value": round(12850.00 / 84, 2)
        }

    def get_monthly_sales_volume(self, year: int, month: int) -> Dict[str, Any]:
        if year == 2026 and month == 10:
            return {
                "year": 2026,
                "month": 10,
                "month_name": "October",
                "total_sales_volume": 42500.00,
                "currency": "USD",
                "transaction_count": 312,
                "growth_percentage": "+8.4%"
            }
        return {
            "year": year,
            "month": month,
            "total_sales_volume": 15200.00,
            "currency": "USD",
            "transaction_count": 98,
            "growth_percentage": "+2.1%"
        }

    def get_account_balances(self, as_of_time: Optional[str] = None) -> Dict[str, Any]:
        return {
            "as_of_time": as_of_time or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "balances": self.balances
        }

    def list_transactions(
        self,
        start_date: str,
        end_date: str,
        transaction_status: str = "SUCCESS",
        limit: int = 20
    ) -> Dict[str, Any]:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "transactions": self.transactions[:limit]
        }

    # =========================================================================
    # DISPATCHER
    # =========================================================================
    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dynamically routes tool execution to the appropriate handler."""
        handler = getattr(self, tool_name, None)
        if handler and callable(handler):
            try:
                return handler(**arguments)
            except TypeError as te:
                return {"error": f"Parameter mismatch for {tool_name}: {str(te)}", "status_code": 400}
            except Exception as e:
                return {"error": f"Execution error in {tool_name}: {str(e)}", "status_code": 500}

        # Registered tool exists in catalog, but handler is not yet implemented
        return {
            "status": "NOT_IMPLEMENTED_IN_MOCK",
            "message": f"API '{tool_name}' is registered and valid in schema, but mock handler is stubbed.",
            "status_code": 501
        }
