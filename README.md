NOTE: Implemented: wallet fetching, classification, exports, a reconciliation layer (client CSV/Excel files, own-wallet transfers and invoice payments matched to on-chain data, ETH, BTC and supported ERC-20 token balance checks) and freelancer invoicing. Not implemented yet: bank statement reconciliation, PDF import, direct QuickBooks/Xero connections, tokens outside the supported list.

# ChainLedger

Local-first crypto reconciliation and accounting, for accountants working with clients' crypto records and for crypto-paid freelancers.

---

## What is ChainLedger?

ChainLedger is a simple, local-only tool for checking crypto records against the blockchain. An accountant can upload the records a client provides (a ledger export, an exchange history, a receivables list) and reconcile them against the client's on-chain wallet activity. It also fetches and classifies wallet transactions for accountant-friendly exports, and includes invoicing tools for crypto-paid freelancers.

**No accounts. No cloud. Your data stays on your machine.**

---

## Who is this for?

- Accountants and bookkeepers reconciling a client's crypto wallet activity against the client's records
- Freelancers paid in ETH, BTC or supported Ethereum tokens such as USDC
- Independent contractors who need to track crypto income
- Anyone who wants clean transaction records without complex software

---

## What problems does it solve?

### 1. Transaction Fetching
Pull ETH and supported ERC-20 token transfers (USDC, USDT, DAI, WETH, WBTC, LINK, UNI, AAVE, MATIC) from an Ethereum wallet, or transactions from a Bitcoin wallet, with one click. No manual copy-pasting from block explorers. Other tokens are not fetched.

### 2. Proper Classification
Transactions are classified as **INCOME**, **EXPENSE**, **TRANSFER**, or **UNKNOWN**. This matters because:
- Your accountant needs to know what's revenue vs. internal movements
- Transfers between your own wallets are not income
- Outgoing payments are expenses, not negative revenue

### 3. Historical Fiat Values
Each transaction is enriched with the USD/CAD value at the time it occurred. This is what matters for tax reporting—not today's price.

### 4. Clean Exports
Generate CSV or Excel files with:
- Every transaction with date, amount, fiat value, and classification
- **GROSS REVENUE** (income only)
- **TOTAL EXPENSES** (expenses only)
- **NET CASH FLOW** (revenue minus expenses)

### 5. Professional Invoices
Create PDF invoices for your crypto payments with auto-generated invoice numbers and optional fiat equivalents.

### 6. Reconciliation
Check records against on-chain wallet activity:
- **Client files**: upload a client's ledger / GL export, exchange history or receivables list (CSV or Excel), map its columns, and match each line to on-chain transactions; lines not found on-chain, amount differences and on-chain transactions missing from the ledger are reported
- **Own-wallet transfers**: pair the outgoing and incoming sides of a move between your wallets, mark both as TRANSFER, and record the network fee as its own expense line
- **Invoice payments**: match incoming payments to invoices (uploaded receivables or ChainLedger invoices) as paid, partially paid, overpaid or unpaid; unmatched payments go on an exceptions list
- **Wallet balances**: opening balance + transactions − fees compared with the actual closing balance from the chain (ETH, BTC and supported ERC-20 tokens)
- **Report**: Excel and CSV with matched and unmatched items, differences, a note per exception, and a timestamped log of manual overrides

See [Reconciliation](docs/reconciliation.md) for the rules and what is not covered.

---

## What ChainLedger does NOT do

- **No tax filing**: This is not tax software. It produces records; you (or your accountant) file taxes.
- **No tax advice**: We don't tell you what's taxable. Consult a professional.
- **No capital gains**: This tracks income/expenses, not investment gains.
- **No compliance guarantees**: Tax rules vary by jurisdiction. This tool doesn't know your local laws.
- **No cloud sync**: By design. Your financial data never leaves your computer.

---

## Quick Start

### Prerequisites
- Python 3.10+
- pip

### Installation

```bash
git clone https://github.com/rexmaximus/ChainLedger.git
cd ChainLedger
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

Ethereum needs an Alchemy API key, entered in the form on the Accounting or Reconciliation page. Bitcoin uses Blockstream's public API and needs no key.

### Run

```bash
python src/ui/app.py
```

Open **http://127.0.0.1:5000** in your browser.

### Tests

```bash
pip install pytest
python -m pytest
```

---

## Features

| Feature | Description |
|---------|-------------|
| **Wallet Fetching** | Pull ETH and supported ERC-20 transfers from Ethereum (via Alchemy), or transactions from Bitcoin (via Blockstream) |
| **Classification** | Auto-classify as INCOME/EXPENSE/TRANSFER with manual override support |
| **Fiat Valuation** | Historical USD/CAD values via CoinGecko |
| **Excel/CSV Export** | Accountant-ready spreadsheets with summaries |
| **PDF Invoices** (Freelancer Tools) | Professional invoices with auto-numbering |
| **Client File Import** | Upload CSV/Excel ledger exports, exchange histories and receivables lists, with column mapping and per-row error reporting (no PDF) |
| **Client Record Matching** | Matches client file lines to on-chain transactions by tx hash, or amount and date; reports unmatched lines, amount differences and on-chain transactions missing from the ledger |
| **Own-Wallet Transfer Matching** | Pairs outgoing/incoming rows between your wallets (same txid, or amount and time window); fee recorded as a separate expense line |
| **Invoice-to-Payment Matching** | Paid / partially paid / overpaid / unpaid per invoice (uploaded receivables or ChainLedger invoices); unmatched payments listed as exceptions |
| **Balance Reconciliation** | Expected vs actual closing balance per wallet and asset: ETH and supported ERC-20 tokens (historical, via Alchemy) and BTC (current balance, or a manually entered one) |
| **Reconciliation Report** | Excel/CSV report with exception notes and a timestamped override log |
| **Local Storage** | All data stored in local JSON files |

Not implemented yet: bank statement reconciliation, PDF import, direct QuickBooks/Xero connections or preset formats (their CSV exports can be uploaded through column mapping), presets for specific exchanges, tokens outside the supported list, other chains.

---

## Supported Assets

| Where | Assets |
|-------|--------|
| Fetched from the blockchain | ETH and the ERC-20 tokens USDC, USDT, DAI, WETH, WBTC, LINK, UNI, AAVE, MATIC on Ethereum mainnet (identified by contract address, so look-alike and spam tokens are ignored), and BTC. Other tokens are not fetched. |
| Historical USD/CAD prices (CoinGecko) | ETH, BTC, USDC, USDT, DAI, WETH, WBTC, MATIC, LINK, UNI, AAVE. Any other symbol is valued at $1.00. |
| Invoices and client files | Any asset symbol, but only ETH and BTC lines can be matched to fetched on-chain transactions. |

---

## Project Structure

```
ChainLedger/
├── src/
│   ├── wallet/           # Transaction fetching
│   ├── classification/   # Transaction classification logic
│   ├── exports/          # CSV/Excel report generation
│   ├── imports/          # Client CSV/Excel file import and column mapping
│   ├── invoices/         # PDF invoice generation
│   ├── reconciliation/   # Transfer, invoice and balance reconciliation
│   └── ui/               # Flask web interface
├── tests/                # Unit tests (pytest)
├── docs/                 # Documentation
├── examples/             # Sample outputs
└── data/                 # Local storage (created on first run)
```

---

## Documentation

- [Accounting Notes](docs/accounting-notes.md): Why the accounting logic works the way it does
- [Design Decisions](docs/design-decisions.md): Key architectural choices
- [Reconciliation](docs/reconciliation.md): How client files, transfers, invoice payments and balances are reconciled
- [Limitations](docs/limitations.md): What this tool explicitly does not handle

---

## License

MIT
