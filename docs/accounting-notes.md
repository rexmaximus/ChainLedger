# Accounting Notes

This document explains the accounting logic in ChainLedger and why it works the way it does.

---

## Why Gross Revenue ≠ Net Inflow

A common mistake in crypto accounting is treating "total money received" as revenue. This is wrong.

**Example:**
- You receive 1 ETH payment from a client (income)
- You transfer 0.5 ETH from your Coinbase wallet to your Ledger (not income)
- You receive 0.5 ETH refund for a cancelled service (not income—or negative expense)

If you sum all inflows, you get 2 ETH. But your actual **income** is only 1 ETH.

ChainLedger separates these by classifying transactions:
- **INCOME** = money you earned (payments from clients)
- **TRANSFER** = money moving between your own wallets
- **EXPENSE** = money you spent

**GROSS REVENUE = sum of INCOME transactions only.**

---

## Why Income, Expenses, and Transfers are Separated

### Income
Money received in exchange for goods or services. This is taxable revenue in most jurisdictions.

### Expenses
Money paid out for business purposes (software subscriptions, contractor payments, etc.). These may be tax-deductible.

### Transfers
Money moving between wallets you control. These are not income or expenses—they're internal movements. Including them in revenue/expense totals would be incorrect.

**ChainLedger excludes TRANSFER transactions from all financial totals.**

---

## Why We Use Confirmation Timestamp for Fiat Valuation

When a transaction is confirmed on the blockchain, that's when it "happened" for accounting purposes. We fetch the historical price at that moment.

Why not use:
- **Today's price** — Irrelevant for tax purposes. If you received 1 ETH when it was worth $2,000, you earned $2,000 in income—even if ETH is now worth $4,000.
- **Initiation time** — The transaction isn't finalized until confirmed. Confirmation time is the legally relevant moment.

ChainLedger uses the **block date** (the date the transaction was confirmed on-chain) to look up historical prices.

---

## Why Transfers are Excluded from Totals

Consider this scenario:
1. Client pays you 1 ETH ($2,000)
2. You move that 1 ETH to cold storage
3. You move it back to hot wallet
4. You spend 0.5 ETH ($1,000) on a contractor

If you counted all inflows as income:
- Inflows: 1 + 1 = 2 ETH
- "Revenue": $4,000

But your actual income is $2,000, and your expenses are $1,000.

By excluding transfers:
- INCOME: 1 ETH ($2,000)
- EXPENSE: 0.5 ETH ($1,000)
- NET CASH FLOW: $1,000

This is the correct accounting.

---

## Classification Rules

### Default Rules
| Direction | Classification |
|-----------|---------------|
| Incoming | INCOME |
| Outgoing | EXPENSE |
| Both sender and receiver are your wallets | TRANSFER |
| Can't determine | UNKNOWN |

### Manual Overrides
Sometimes the default rules are wrong:
- A refund appears as incoming, but it's not income
- An internal transaction isn't detected as a transfer

ChainLedger allows manual overrides via the UI or by editing the classification overrides in the metadata file.

---

## Summary Calculations

| Metric | Formula |
|--------|---------|
| **GROSS REVENUE** | Sum of fiat values of all INCOME transactions |
| **TOTAL EXPENSES** | Sum of fiat values of all EXPENSE transactions |
| **NET CASH FLOW** | GROSS REVENUE - TOTAL EXPENSES |

TRANSFER and UNKNOWN transactions are excluded from all totals.

---

## Gas Fees

Gas fees are tracked separately. For EXPENSE transactions, the gas fee is an additional cost incurred. ChainLedger displays gas fees in the export but does not include them in the main expense total (you can add them manually if your accountant requires it).

---

## What This Is Not

This is **not** capital gains tracking. If you're holding crypto as an investment and selling at a profit, that's a different accounting problem (cost basis, FIFO/LIFO, etc.). ChainLedger is for tracking **income and expenses from being paid in crypto**.
