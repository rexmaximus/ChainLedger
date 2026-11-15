"""
Wallet Balance Reconciliation

For one wallet, one native asset and one period:

    expected closing = opening balance
                       + incoming amounts
                       - outgoing amounts
                       - network fees paid
    difference       = actual closing - expected closing

The opening and actual closing balances come from the chain API where the API
supports it, or are entered by the user (and logged as a manual override).

A non-zero difference means the fetched transactions do not explain the
balance movement. Common causes: token transfers or contract calls the fetcher
does not return, fees that could not be looked up, failed transactions that
still paid gas, or a wrong manual balance.

Only the network's native asset (ETH or BTC) is reconciled. Outgoing amounts
are assumed to exclude the fee, which the fee column records separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from .common import NATIVE_ASSET, to_decimal, tx_datetime


@dataclass
class BalanceReconciliation:
    wallet_address: str
    network: str
    asset: str
    period_start: str  # opening balance is taken at the start of this date (UTC)
    period_end: str  # closing balance is taken at the end of this date (UTC)
    opening_balance: Optional[Decimal]
    opening_source: str
    inflows: Decimal
    outflows: Decimal
    fees: Decimal
    transaction_count: int
    expected_closing: Optional[Decimal]
    actual_closing: Optional[Decimal]
    actual_source: str
    difference: Optional[Decimal]

    @property
    def reconciled(self) -> bool:
        return self.difference is not None and self.difference == 0


def reconcile_balance(
    wallet_address: str,
    network: str,
    transactions: list[dict],
    period_start: date,
    period_end: date,
    opening_balance: Optional[Decimal],
    actual_closing: Optional[Decimal],
    opening_source: str = "",
    actual_source: str = "",
) -> BalanceReconciliation:
    """
    Reconcile a wallet's native-asset balance over a period.

    Args:
        wallet_address: The wallet being reconciled
        network: "Ethereum" or "Bitcoin"
        transactions: Rows for this wallet (other wallets' rows are ignored)
        period_start / period_end: Inclusive date range (UTC)
        opening_balance: Balance at the start of period_start, or None if unknown
        actual_closing: Balance at the end of period_end, or None if unknown
        opening_source / actual_source: Where each balance came from

    Returns:
        BalanceReconciliation; expected closing and difference are None when an
        input balance is missing.
    """
    asset = NATIVE_ASSET.get(network, "")
    wallet = wallet_address.lower()
    inflows = outflows = fees = Decimal(0)
    count = 0

    for tx in transactions:
        if (tx.get("wallet_address") or "").lower() != wallet:
            continue
        if tx.get("network") != network:
            continue
        if not period_start <= tx_datetime(tx).date() <= period_end:
            continue
        if (tx.get("tx_status") or "Success") != "Success":
            continue
        counted = False
        if tx.get("asset") == asset:
            amount = to_decimal(tx.get("amount_decimal"))
            if tx.get("direction") == "In":
                inflows += amount
                counted = True
            elif tx.get("direction") == "Out":
                outflows += amount
                counted = True
        if tx.get("direction") == "Out":
            fee = to_decimal(tx.get("tx_fee_native"))
            if fee:
                fees += fee
                counted = True
        count += counted

    expected = None
    if opening_balance is not None:
        expected = opening_balance + inflows - outflows - fees
    difference = None
    if expected is not None and actual_closing is not None:
        difference = actual_closing - expected

    return BalanceReconciliation(
        wallet_address=wallet_address,
        network=network,
        asset=asset,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        opening_balance=opening_balance,
        opening_source=opening_source,
        inflows=inflows,
        outflows=outflows,
        fees=fees,
        transaction_count=count,
        expected_closing=expected,
        actual_closing=actual_closing,
        actual_source=actual_source,
        difference=difference,
    )
