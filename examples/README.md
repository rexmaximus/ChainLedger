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

## sample_invoice.pdf

To generate a sample invoice:

1. Run ChainLedger: `python src/ui/app.py`
2. Go to Invoices → Create Invoice
3. Fill in sample data and click Create

The PDF will be saved to `data/invoices/`.

---

**Note:** These samples use fictional data. No real wallet addresses or transactions are included.
