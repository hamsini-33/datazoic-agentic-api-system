# PayPal Invoicing Developer & Merchant Guide
*(Note: Mock/demo documentation for Datazoic assignment evaluation)*

## 1. Invoice Lifecycle Overview
A PayPal invoice progresses through deterministic statuses:
- **DRAFT**: Created but not yet dispatched. Merchants can modify line items, amounts, or delete the draft without buyer notification.
- **SENT**: Dispatched to the buyer via email and PayPal account notification. The invoice is locked against direct edits.
- **PAID**: The buyer has completed payment through PayPal, or the merchant has recorded an offline payment (cash/wire).
- **CANCELLED**: The merchant has voided the sent invoice. The buyer is notified that payment is no longer required.

## 2. Best Practices for Sending Invoices
- Always provide a clear, itemized description of goods or services.
- Due dates default to 14 days from dispatch if not explicitly set.
- Merchants can issue automated payment reminders up to 3 times before an invoice is marked overdue.
- When an invoice is paid, funds are immediately credited to the merchant's available PayPal balance minus standard processing fees.
