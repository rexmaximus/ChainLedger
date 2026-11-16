# Reconciliation

This document explains what the Reconciliation page does, how each check works, and what it does not cover.

Code: `src/reconciliation/`. Tests: `tests/`. Sample output: `examples/sample_reconciliation.csv` and `.xlsx`.

---

## What it reconciles

ChainLedger reconciles **on-chain wallet data against records supplied by a client, against invoices, and against itself**:

| Check | Compares | Output |
|-------|----------|--------|
| Client files | Lines in an uploaded ledger / GL export or exchange history vs on-chain transactions | Matched lines, lines not found on-chain, amount differences, on-chain transactions missing from the ledger |
| Own-wallet transfers | Outgoing rows in one of your wallets vs incoming rows in another | Matched pairs, network fee lines, unmatched legs |
| Invoice payments | Uploaded receivables and invoices created in ChainLedger vs incoming payments | Paid / partially paid / overpaid / unpaid, unmatched payments |
| Wallet balances | Opening balance + transactions − fees vs the actual closing balance | Difference per wallet |

It does **not yet** reconcile bank statements, read PDFs, or connect to QuickBooks or Xero. See [Not covered yet](#not-covered-yet).

---

## 0. Client files

On the **Client Files** page, upload a `.csv` or `.xlsx` file (max 10 MB) and say what it is:

| File type | Each line is… | Expected on-chain as |
|-----------|---------------|----------------------|
| Client ledger / GL export | an entry in the client's crypto wallet account | Debit / positive = into a wallet; credit / negative = out of a wallet |
| Exchange transaction history | a line from the client's exchange account | Withdrawal = into a wallet; deposit = out of a wallet; other types (trades, interest) are not expected on-chain |
| Receivables (invoice list) | an invoice the client issued | Matched to incoming payments like ChainLedger invoices (section 2) |

The first non-empty row must be the headers (Excel: first sheet only). ChainLedger suggests a column for each field from the header names; you confirm or change it, pick the date format (ISO, MM/DD/YYYY or DD/MM/YYYY), and give a default asset if the file has no currency column. Saving the mapping reads every row and reports each unreadable row by line number. A file with at least one usable row is marked **ready** and is included in every reconciliation run. Uploads, mapping changes and removals are written to the override log.

A ledger file can be tied to one wallet. Then only that wallet's on-chain transactions are compared with it.

Matching, per file type (ledger and exchange lines are matched separately, since one on-chain transaction can appear in both):

1. Manual matches are applied first.
2. **Transaction hash**: the line's hash equals an on-chain transaction's hash. If the amounts differ beyond tolerance, the line is still matched and the difference is reported as an exception.
3. **Amount and date**: same asset, the expected direction, dates within the window (default 3 days), and amounts within tolerance (default 0.5%). A ledger amount may include the network fee of an outgoing transaction; an exchange withdrawal may be reported before its withdrawal fee. When more than one transaction could match, nothing is matched and the line is reported as ambiguous.

Lines outside the run's period are skipped and counted. Exceptions:

- a ledger or exchange line not found on-chain
- a matched line whose amount differs from the chain
- for ledger files: an on-chain transaction with no ledger line

An incoming transaction matched to an exchange withdrawal is the client's own funds, so it is not treated as a customer payment in invoice matching.

---

## 1. Own-wallet transfer matching

Wallets treated as yours: the addresses you enter on the page, plus every saved wallet marked *self-custodial* on the same blockchain.

Rules, in order:

1. **Manual links** you have made are applied first.
2. **Same txid.** An outgoing row and an incoming row with the same transaction hash, in two different wallets of yours. On a single chain, a direct transfer between your wallets always has one txid, so this is the normal case.
3. **Amount and time.** Different txids, same network and asset, the incoming row confirmed within the time window after the outgoing row (default 2 hours), and the amount received equals the amount sent, or the amount sent minus the recorded network fee. If more than one row could match, nothing is matched and the rows are listed as ambiguous. Every amount-and-time match is also listed as an exception for review, because it is a heuristic.

For each matched pair:

- both rows are marked `TRANSFER` and share a transfer ID (`TR-0001`, …)
- the network fee on the outgoing row becomes a separate **expense line** (`Network Fees` sheet)

Rows that look like transfers between your wallets (the existing classifier marked them `TRANSFER`, or the counterparty address is one of your wallets) but have no match are listed as **unmatched transfer legs**. Typical cause: one of your wallets was not included in the run.

The match results only appear in the reconciliation report. To carry confirmed matches (same txid or manual) into the Accounting export, click **Use matched transfers in Accounting export**. This saves them as `TRANSFER` classification overrides and records the action in the override log.

---

## 2. Invoice-to-payment matching

Inputs: invoices from uploaded receivables files and invoices created in ChainLedger (`data/metadata.json`); cancelled or void invoices are skipped. Payments are incoming rows not matched as own-wallet transfers or exchange withdrawals.

A payment is **eligible** for an invoice when:

- the asset equals the invoice token
- it was confirmed on or after the invoice date
- if the invoice has a client wallet and the payment's sender is known, they are the same address
- if the invoice has your receiving wallet, the payment arrived in that wallet

Invoices are processed oldest first:

1. Manual matches are applied first.
2. One eligible payment within the tolerance (default 0.5% of the invoice amount) → **paid**.
3. Only if the payer is verified (the invoice records the client wallet and the payments came from it):
   - several payments that add up to the amount within tolerance → **paid**
   - payments that add up to less → **partially paid**
   - one payment above the amount plus tolerance → **overpaid**
4. Otherwise → **unpaid**.

Step 3 requires a verified payer so an unrelated incoming payment is not assigned to the wrong invoice. Bitcoin rows from the current fetcher do not record sender addresses, so Bitcoin invoices are matched only by amount (step 2) or manually.

Every incoming payment not assigned to an invoice is listed as an exception. Unpaid, partially paid and overpaid invoices are exceptions too.

The report shows the matched status next to the status stored on the invoice. Reconciliation does not change the stored invoice status.

---

## 3. Wallet balance reconciliation

For each wallet and the chosen period (start and end dates, UTC):

```
expected closing = opening balance + incoming − outgoing − network fees paid
difference       = actual closing − expected closing
```

Only the network's native asset is reconciled (ETH or BTC).

| Balance | Ethereum | Bitcoin |
|---------|----------|---------|
| Opening (start of period) | `eth_getBalance` at the last block before the period starts (Alchemy) | Not available from Blockstream; enter it manually |
| Actual closing (end of period) | `eth_getBalance` at the last block of the end date | Current balance if the period ends today or later; otherwise enter it manually |

A manual balance is entered with **Enter opening** / **Enter closing** and requires a source note. It is logged like any other override.

A non-zero difference means the fetched transactions do not explain the balance change. Common causes:

- token transfers or contract calls that the fetcher does not return (it fetches ETH transfers only, not ERC-20 transfers)
- fees that could not be looked up
- failed transactions that still paid gas
- a wrong manual balance

---

## 4. Report, exceptions, notes and overrides

Each run writes an Excel file (one sheet per section) and a CSV file (the same sections in order) to `data/exports/`:

`Summary`, `Matched Transfers`, `Unmatched Transfer Legs`, `Network Fees`, `Invoice Matching`, `Unmatched Payments`, `Client Records Matched`, `Client Records Unmatched`, `On-Chain Not In Ledger`, `Balance Reconciliation`, `Exceptions`, `Override Log`.

**Exceptions** list every item needing review, each with a stable ID and a free-text **note** you can edit on the page.

**Manual overrides** available on the page:

| Override | Effect |
|----------|--------|
| Link transfer | Pair an outgoing and an incoming row as one transfer |
| Unlink transfer | Stop a row from being auto-matched as a transfer |
| Match to invoice | Assign a payment to an invoice |
| Unmatch payment | Stop a payment from being auto-assigned |
| Match client record | Match a client file line to an on-chain transaction |
| Unmatch client record | Stop a client file line from being auto-matched |
| Opening / closing balance | Use a manually entered balance |

Every override needs a reason. Adding or removing an override, editing a note, and applying transfers to the Accounting export are each written to the **override log** with a UTC timestamp. Removed overrides are kept (marked inactive) so the history is complete. All of this is stored in `data/metadata.json` under `reconciliation`.

The last fetch is cached in `data/reconciliation/last_run.json`, so **Re-run last fetch** (and every override or note) regenerates the report without calling the chain APIs again.

---

## Fetcher changes made for reconciliation

- **Ethereum:** the fee on outgoing external transactions is now looked up from the transaction receipt (`gasUsed × effectiveGasPrice`). It used to be recorded as 0. Transfer results are now paginated.
- **Bitcoin:** the full confirmed history is fetched (previously only the first page of 25). For outgoing transactions the amount now excludes the miner fee. The fee was previously counted inside the amount and again in the fee column.

---

## Not covered yet

- Bank statement reconciliation (bank lines are fiat; nothing matches them to on-chain data yet)
- PDF files of any kind
- Direct QuickBooks / Xero connections, preset formats for them or for specific exchanges, or posting entries to a ledger (their CSV exports can be uploaded with column mapping)
- Fiat amounts in client files (lines are compared in crypto units)
- ERC-20 token balances (only native ETH/BTC balances are reconciled)
- Fiat (USD/CAD) balance reconciliation; balances are compared in crypto units
- Assigning one Bitcoin fee when several of your wallets fund the same transaction (each wallet's row shows the whole fee)
