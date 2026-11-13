# PayPal Customer Dispute & Claim Resolution Policy
*(Note: Mock/demo documentation for Datazoic assignment evaluation)*

## 1. Dispute Stages and Seller Response Windows
Customer disputes proceed through two distinct phases:
- **Phase 1: Inquiry**: The buyer and seller communicate directly within the Resolution Center to resolve issues informally.
- **Phase 2: Formal Claim**: Either party escalates the dispute to PayPal for investigation.
- **Mandatory Response Timeframe**: Merchants have strictly **10 calendar days** to respond to an open dispute with evidence or explanation. Failure to respond within 10 days results in an automatic default judgment and full refund in favor of the buyer.

## 2. Valid Evidence Types
When contesting a dispute, merchants must submit verifiable proof:
- **Merchandise Not Received**: Online tracking number from an integrated carrier showing "Delivered" to the buyer's shipping address.
- **Significantly Not As Described**: Clear contract terms, service specifications, signed delivery receipts, or photos of item condition prior to dispatch.
- **Unauthorized Transaction**: Proof of buyer identity verification, IP logs, or verified shipping address matching the PayPal transaction record.

## 3. Conceding Disputes
Merchants may choose to accept a claim at any time (`accept_dispute_claim`), which immediately closes the dispute, refunds the buyer in full, and avoids a dispute settlement fee.
