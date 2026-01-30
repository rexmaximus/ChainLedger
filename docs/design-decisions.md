# Design Decisions

This document explains the key architectural and product decisions in ChainLedger.

---

## Local-Only Storage

**Decision:** All data is stored locally in JSON files. No accounts, no cloud, no sync.

**Why:**
1. **Privacy** — Financial data is sensitive. Users shouldn't have to trust a third party.
2. **Simplicity** — No authentication, no database, no infrastructure to maintain.
3. **Portability** — Users own their data. They can back it up, move it, or delete it.
4. **Trust** — Open source + local storage = users can verify exactly what happens to their data.

**Trade-offs:**
- No multi-device sync
- Users are responsible for their own backups
- No collaboration features

For the target audience (solo freelancers), these trade-offs are acceptable.

---

## Accountant-Friendly Outputs

**Decision:** Prioritize clarity and explicitness over visual appeal.

**Why:**
- Accountants need to understand exactly what each number means
- Ambiguous labels cause questions and delays
- "Boring" spreadsheets are actually what professionals want

**Implementation:**
- Clear column headers (not abbreviated)
- Explicit labels: "GROSS REVENUE (USD)" not "Rev"
- Separate rows for USD and CAD values
- Transaction type column showing INCOME/EXPENSE/TRANSFER/UNKNOWN
- Direction column showing INCOMING/OUTGOING

---

## Separation of Concerns

**Decision:** Keep classification, fetching, export, and invoicing as separate modules.

```
wallet/          → Fetches raw transactions from blockchain
classification/  → Classifies transactions by type
exports/         → Generates reports from classified data
invoices/        → Generates PDF invoices (independent of export logic)
ui/              → Web interface that ties it all together
```

**Why:**
- Each module can be tested independently
- Logic changes in one area don't break others
- Easier to understand and maintain
- Could be used as a library (import just the parts you need)

---

## Flask for UI

**Decision:** Use Flask for the web interface.

**Why:**
- Simple and lightweight
- No build step required
- Works well for local-only applications
- Python ecosystem (same language as the core logic)

**Alternatives considered:**
- **CLI only** — Less accessible for non-technical users
- **Electron/Desktop app** — Much more complex to build and distribute
- **Django** — Overkill for this use case

---

## JSON for Persistence

**Decision:** Use a single JSON file for metadata storage.

**Why:**
- No database to install or configure
- Human-readable (users can inspect/edit if needed)
- Simple backup (copy one file)
- Sufficient for the data volume (invoices, export records, settings)

**Trade-offs:**
- Not suitable for thousands of records (fine for typical freelancer usage)
- No concurrent access handling (not needed for single-user local app)

---

## Historical Price Lookup

**Decision:** Use CoinGecko API for historical prices.

**Why:**
- Free tier is sufficient for typical usage
- Covers all major tokens
- Provides historical data by date

**Implementation details:**
- Prices are cached in memory to reduce API calls
- Rate limiting (1.5s between requests) to stay within free tier
- Fallback: stablecoins default to $1.00 if lookup fails

---

## No Authentication

**Decision:** No login, no accounts.

**Why:**
- Local-only means no need for authentication
- Reduces complexity dramatically
- No password management, no security vulnerabilities from auth bugs
- Users run it on their own machine—they're already authenticated by their OS

---

## No Real-Time Features

**Decision:** No live price feeds, no auto-refresh, no notifications.

**Why:**
- This is an accounting tool, not a trading tool
- Historical accuracy matters more than real-time data
- Simpler implementation, fewer things to break

---

## Manual Override Support

**Decision:** Allow users to manually override transaction classifications.

**Why:**
- Auto-classification can't be 100% accurate
- Some transactions are ambiguous (refunds, grants, etc.)
- Users know their transactions better than any algorithm

**Implementation:**
- Overrides stored in metadata by transaction hash
- Applied during export generation
- Can be managed via UI or by editing the metadata file directly

---

## Explicit Limitations

**Decision:** Clearly document what the tool does NOT do.

**Why:**
- Prevents misuse (e.g., treating this as tax filing software)
- Sets correct expectations
- Legal protection (we're not providing tax advice)

**See:** [limitations.md](limitations.md)

---

## PDF Invoices Without QR Codes

**Decision:** Removed QR code feature from invoices.

**Why:**
- Payment URIs vary by token and wallet
- QR code scanning is unreliable across different wallet apps
- Adds complexity (qrcode library dependency)
- Most crypto payments are copy-paste anyway

The wallet address is displayed clearly in the PDF. Users can copy it.
