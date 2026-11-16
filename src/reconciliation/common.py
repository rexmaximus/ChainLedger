"""
Shared helpers for the reconciliation module.

Transactions are handled as plain dicts with the same fields as
`src.wallet.LedgerRow`, plus:
- wallet_address: the user's wallet the row was fetched for
- transaction_type: classification (INCOME/EXPENSE/TRANSFER/UNKNOWN)

All amounts are compared as Decimal, never float.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

NATIVE_ASSET = {"Ethereum": "ETH", "Bitcoin": "BTC"}


def to_decimal(value) -> Decimal:
    """Convert a string/number to Decimal. Empty or invalid values become 0."""
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def row_id(tx: dict) -> str:
    """
    Stable identifier for one side of a transaction.

    The same tx hash can appear several times (an outgoing row in one of your
    wallets and an incoming row in another, or an ETH row and a token row), so
    the id includes direction, wallet and asset.
    """
    return ":".join([
        tx.get("network", ""),
        tx.get("tx_hash", ""),
        tx.get("direction", ""),
        (tx.get("wallet_address") or "").lower(),
        tx.get("asset", ""),
    ])


def tx_datetime(tx: dict) -> datetime:
    """Block timestamp of a row as an aware UTC datetime."""
    stamp = f"{tx.get('block_date', '1970-01-01')} {tx.get('block_time') or '00:00:00'}"
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def parse_date(value) -> date:
    """Parse an ISO date or datetime string (or pass through a date)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:19]).date()


def fmt_amount(value: Decimal) -> str:
    """Plain decimal string without exponent notation or trailing zeros."""
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text


def utc_now_iso() -> str:
    """Current UTC time as an ISO 8601 string, used for audit timestamps."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
