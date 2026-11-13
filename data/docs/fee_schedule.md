# PayPal Merchant Processing Fee Schedule
*(Note: Mock/demo documentation for Datazoic assignment evaluation)*

## 1. Commercial Transaction Fees
- **Domestic Commercial Payments**: Standard rate of **2.9% + $0.30 USD** per completed transaction.
- **International Commercial Transactions**: Standard rate of **4.4% + fixed fee** based on currency received.
- **Invoicing Payments**: Same as standard commercial rate (2.9% + $0.30 USD). No extra charge for invoice creation or dispatch.

## 2. Dispute and Chargeback Fees
- **Standard Dispute Fee**: $15.00 USD per dispute filed against the merchant, waived if the merchant and buyer resolve the issue during the Inquiry stage or if the merchant concedes via `accept_dispute_claim`.
- **High-Volume Dispute Fee**: $30.00 USD if the merchant's monthly dispute-to-sales ratio exceeds 1.5%.

## 3. Payout and Transfer Fees
- **Standard Bank Transfers**: Free (takes 1-3 business days).
- **Instant Transfers**: 1.5% fee (minimum $0.25 USD, maximum $15.00 USD).
- **Batch Payouts API**: $0.25 USD per individual domestic payout line item.
