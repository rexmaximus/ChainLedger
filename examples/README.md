# Sample Outputs

This directory contains example outputs from ChainLedger.

## sample_export.csv

A sample accounting export showing:
- 4 transactions (2 income, 1 expense, 1 transfer)
- Transaction classification (INCOME, EXPENSE, TRANSFER)
- USD and CAD fiat values
- Summary section with:
  - GROSS REVENUE (income only)
  - TOTAL EXPENSES (expense only)
  - NET CASH FLOW (revenue - expenses)
  - Gas fees (tracked separately)

Note how the TRANSFER transaction is excluded from revenue/expense totals.

## sample_reconciliation.csv / sample_reconciliation.xlsx

A sample reconciliation report built from the fictional data in `tests/sample_data.py` (a hot and a cold Ethereum wallet, three invoices, one month). It shows:
- 1 own-wallet transfer matched by txid, with its network fee as a separate expense line
- Invoice statuses: 1 paid, 1 partially paid (two instalments from the client's wallet), 1 unpaid
- 1 incoming payment with no invoice (exception)
- Balance check: the cold wallet reconciles; the hot wallet shows a −0.0005 ETH difference (exception)
- An exception note and its entry in the override log

## sample_invoice.pdf

To generate a sample invoice:

1. Run ChainLedger: `python src/ui/app.py`
2. Go to Invoices → Create Invoice
3. Fill in sample data and click Create

The PDF will be saved to `data/invoices/`.

---

**Note:** These samples use fictional data. No real wallet addresses or transactions are included.
