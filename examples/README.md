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

## client_files/ and sample_reconciliation_client_files.csv / .xlsx

Fictional files of the kind a client might hand an accountant, built to line up with the same sample wallet data:
- `client_ledger.csv`: a GL export for the hot wallet (debit/credit columns, MM/DD/YYYY dates)
- `exchange_history.csv`: an exchange history with a trade and two withdrawals
- `receivables.csv`: the client's invoice list

Upload them on the Client Files page to try the mapping step. The sample report shows what reconciliation finds:
- 6 lines matched on-chain (by tx hash, or by amount and date, including a ledger line that books the gas fee with the transfer)
- 1 amount difference (contractor booked at 0.31 ETH, 0.30 ETH paid on-chain), with a note
- 2 lines not found on-chain (a subscription and a 1.0 ETH withdrawal)
- 1 on-chain receipt missing from the ledger (Client B's second instalment)
- 1 trade not expected on-chain, 1 April line skipped as outside the period
- Receivables: 1 paid, 1 partially paid, 1 unpaid

## sample_invoice.pdf

To generate a sample invoice:

1. Run ChainLedger: `python src/ui/app.py`
2. Go to Invoices → Create Invoice
3. Fill in sample data and click Create

The PDF will be saved to `data/invoices/`.

---

**Note:** These samples use fictional data. No real wallet addresses or transactions are included.
