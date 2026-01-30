# Limitations

ChainLedger is a simple accounting tool. This document explicitly states what it does **not** do.

---

## No Tax Advice

ChainLedger does not provide tax advice. It generates transaction records and calculates totals, but:

- It does not tell you what is taxable
- It does not tell you what is deductible
- It does not interpret tax laws
- It does not know your jurisdiction's rules

**You are responsible for understanding your tax obligations.** Consult a tax professional.

---

## No Capital Gains Calculation

ChainLedger tracks **income and expenses from being paid in crypto**. It does not track:

- Cost basis for investments
- Capital gains or losses from selling crypto
- FIFO, LIFO, or specific identification methods
- Holding periods for long-term vs. short-term gains

If you're trading or investing in crypto (not just receiving it as payment), you need different software.

---

## No Jurisdiction-Specific Logic

Tax rules vary dramatically by country, state, and even city. ChainLedger does not:

- Know where you live
- Apply local tax rates
- Handle jurisdiction-specific reporting requirements
- Generate government-specific forms (1099, T4, etc.)

The exports are raw data. You or your accountant must interpret them according to your local laws.

---

## No Tax Filing

ChainLedger does not file taxes. It produces records that you can use when filing, but:

- It does not connect to tax authorities
- It does not generate official tax forms
- It does not submit anything on your behalf

---

## No Compliance Guarantees

We make no guarantees that ChainLedger outputs are compliant with any tax authority's requirements. The tool is provided "as is."

If you're audited, ChainLedger exports may be helpful as supporting documentation, but they are not official records.

---

## No Multi-User Support

ChainLedger is designed for a single user on a single machine. It does not support:

- Multiple user accounts
- Role-based access
- Shared access to data
- Concurrent editing

---

## No Cloud Sync

All data is stored locally. There is no:

- Cloud backup
- Multi-device sync
- Remote access

You are responsible for backing up your data.

---

## No Real-Time Data

ChainLedger fetches data on demand. It does not provide:

- Live price feeds
- Real-time transaction notifications
- Automatic refresh

---

## No DeFi Protocol Support

ChainLedger tracks simple transfers. It does not understand:

- Liquidity pool deposits/withdrawals
- Staking rewards
- Yield farming
- NFT transactions
- Complex smart contract interactions

These may appear as generic transactions but won't be correctly classified or valued.

---

## No Historical Data Editing

Once a transaction is fetched and enriched with prices, those prices are based on the lookup at that time. If CoinGecko's historical data changes later, previously generated exports won't reflect that.

---

## Limited Token Support

ChainLedger supports common tokens (ETH, BTC, USDC, USDT, DAI, and about 20 others). Obscure tokens may:

- Not have price data available
- Be treated as having zero value
- Require manual valuation

---

## No Mobile App

ChainLedger runs as a local web application. There is no:

- iOS app
- Android app
- Mobile-optimized interface

It's designed for desktop use.

---

## Summary

ChainLedger is a **record-keeping tool**, not a tax solution. It helps you organize your crypto transactions into a format that's useful for accounting. Everything else—tax interpretation, filing, compliance—is your responsibility.

When in doubt, consult a professional.
