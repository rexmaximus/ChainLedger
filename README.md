NOTE: Wallet fetching, classification, exports, invoices and a first reconciliation layer (own-wallet transfer matching, invoice-to-payment matching, native ETH/BTC balance checks) are implemented. Exchange CSV import and bank or QuickBooks/Xero ledger import are not.

# ChainLedger

Local-first crypto accounting for freelancers and independent contractors.

---

## What is ChainLedger?

ChainLedger is a simple, local-only tool that helps crypto-paid freelancers generate clean accounting records. It fetches your wallet transactions, classifies them, and produces accountant-friendly exports.

**No accounts. No cloud. Your data stays on your machine.**

---

## Who is this for?

- Freelancers paid in cryptocurrency (ETH, BTC, USDC, etc.)
- Independent contractors who need to track crypto income
- Anyone who wants clean transaction records without complex software

---

## What problems does it solve?

### 1. Transaction Fetching
Pull all transactions from your Ethereum or Bitcoin wallet with one click. No manual copy-pasting from block explorers.

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
Check your on-chain records against themselves and against your ChainLedger invoices:
- **Own-wallet transfers**: pair the outgoing and incoming sides of a move between your wallets, mark both as TRANSFER, and record the network fee as its own expense line
- **Invoice payments**: match incoming payments to invoices as paid, partially paid, overpaid or unpaid; unmatched payments go on an exceptions list
- **Wallet balances**: opening balance + transactions − fees compared with the actual closing balance from the chain (native ETH/BTC only)
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
| **Wallet Fetching** | Pull transactions from Ethereum (via Alchemy) or Bitcoin (via Blockstream) |
| **Classification** | Auto-classify as INCOME/EXPENSE/TRANSFER with manual override support |
| **Fiat Valuation** | Historical USD/CAD values via CoinGecko |
| **Excel/CSV Export** | Accountant-ready spreadsheets with summaries |
| **PDF Invoices** | Professional invoices with auto-numbering |
| **Own-Wallet Transfer Matching** | Pairs outgoing/incoming rows between your wallets (same txid, or amount and time window); fee recorded as a separate expense line |
| **Invoice-to-Payment Matching** | Paid / partially paid / overpaid / unpaid per invoice; unmatched payments listed as exceptions |
| **Balance Reconciliation** | Expected vs actual closing balance per wallet for native ETH (historical, via Alchemy) and BTC (current balance, or a manually entered one) |
| **Reconciliation Report** | Excel/CSV report with exception notes and a timestamped override log |
| **Local Storage** | All data stored in local JSON files |

Not implemented yet: exchange CSV import, bank statement import, QuickBooks/Xero ledger import, ERC-20 token balance reconciliation.

---

## Supported Tokens

ETH, BTC, USDC, USDT, DAI, WETH, WBTC, MATIC, LINK, UNI, AAVE, CRV, MKR, COMP, SNX, YFI, SUSHI, 1INCH, ENS, LDO, RPL, FRAX, LUSD

---

## Project Structure

```
ChainLedger/
├── src/
│   ├── wallet/           # Transaction fetching
│   ├── classification/   # Transaction classification logic
│   ├── exports/          # CSV/Excel report generation
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
- [Reconciliation](docs/reconciliation.md): How transfers, invoice payments and balances are reconciled
- [Limitations](docs/limitations.md): What this tool explicitly does not handle

---

## License

MIT
