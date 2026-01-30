"""
Transaction Classification

Classifies blockchain transactions into accounting categories:
- INCOME: Money received (payments from clients)
- EXPENSE: Money spent (payments to others)
- TRANSFER: Money moved between your own wallets
- UNKNOWN: Cannot determine classification

Classification Rules:
1. Manual overrides take priority (keyed by transaction hash)
2. If both sender and receiver are in the wallet list → TRANSFER
3. Incoming direction → INCOME
4. Outgoing direction → EXPENSE
5. Otherwise → UNKNOWN
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TransactionType(str, Enum):
    """Transaction classification types."""

    INCOME = "INCOME"
    EXPENSE = "EXPENSE"
    TRANSFER = "TRANSFER"
    UNKNOWN = "UNKNOWN"


@dataclass
class ClassifiedTransaction:
    """A transaction with its classification."""

    tx_hash: str
    direction: str  # "In" or "Out"
    from_address: str
    to_address: str
    transaction_type: TransactionType
    override_applied: bool = False


def classify_transaction(
    tx_hash: str,
    direction: str,
    from_address: str,
    to_address: str,
    wallet_addresses: set[str],
    override: Optional[str] = None,
) -> ClassifiedTransaction:
    """
    Classify a single transaction.

    Args:
        tx_hash: Transaction hash
        direction: "In" or "Out"
        from_address: Sender address
        to_address: Receiver address
        wallet_addresses: Set of wallet addresses owned by user (lowercase)
        override: Manual override classification (if any)

    Returns:
        ClassifiedTransaction with the determined type
    """
    # Manual override takes priority
    if override:
        override_upper = override.upper()
        if override_upper in [t.value for t in TransactionType]:
            return ClassifiedTransaction(
                tx_hash=tx_hash,
                direction=direction,
                from_address=from_address,
                to_address=to_address,
                transaction_type=TransactionType(override_upper),
                override_applied=True,
            )

    # Normalize addresses for comparison
    from_lower = from_address.lower() if from_address else ""
    to_lower = to_address.lower() if to_address else ""

    # Check if this is a transfer between user's own wallets
    from_is_user = from_lower in wallet_addresses
    to_is_user = to_lower in wallet_addresses

    if from_is_user and to_is_user:
        tx_type = TransactionType.TRANSFER
    elif direction == "In":
        tx_type = TransactionType.INCOME
    elif direction == "Out":
        tx_type = TransactionType.EXPENSE
    else:
        tx_type = TransactionType.UNKNOWN

    return ClassifiedTransaction(
        tx_hash=tx_hash,
        direction=direction,
        from_address=from_address,
        to_address=to_address,
        transaction_type=tx_type,
        override_applied=False,
    )


def classify_transactions(
    transactions: list[dict],
    wallet_addresses: list[str],
    overrides: Optional[dict[str, str]] = None,
) -> list[dict]:
    """
    Classify a list of transactions.

    Args:
        transactions: List of transaction dicts (must have tx_hash, direction,
                      from_address, to_address fields)
        wallet_addresses: List of wallet addresses owned by user
        overrides: Dict mapping tx_hash -> classification override

    Returns:
        Same transactions with 'transaction_type' field added
    """
    wallet_set = {addr.lower() for addr in wallet_addresses}
    overrides = overrides or {}

    results = []
    for tx in transactions:
        classified = classify_transaction(
            tx_hash=tx.get("tx_hash", ""),
            direction=tx.get("direction", ""),
            from_address=tx.get("from_address", ""),
            to_address=tx.get("to_address", ""),
            wallet_addresses=wallet_set,
            override=overrides.get(tx.get("tx_hash", "")),
        )

        # Add classification to transaction
        tx_copy = dict(tx)
        tx_copy["transaction_type"] = classified.transaction_type.value
        tx_copy["classification_overridden"] = classified.override_applied
        results.append(tx_copy)

    return results


def apply_overrides(
    transactions: list[dict],
    overrides: dict[str, str],
) -> list[dict]:
    """
    Apply manual classification overrides to already-classified transactions.

    This is useful when re-processing exports with updated overrides.

    Args:
        transactions: List of transaction dicts with 'transaction_type' field
        overrides: Dict mapping tx_hash -> new classification

    Returns:
        Transactions with overrides applied
    """
    results = []
    for tx in transactions:
        tx_copy = dict(tx)
        tx_hash = tx.get("tx_hash", "")

        if tx_hash in overrides:
            override = overrides[tx_hash].upper()
            if override in [t.value for t in TransactionType]:
                tx_copy["transaction_type"] = override
                tx_copy["classification_overridden"] = True

        results.append(tx_copy)

    return results


# =============================================================================
# Classification Statistics
# =============================================================================

def get_classification_stats(transactions: list[dict]) -> dict:
    """
    Get statistics about transaction classifications.

    Returns:
        Dict with counts for each classification type
    """
    stats = {
        "total": len(transactions),
        "income_count": 0,
        "expense_count": 0,
        "transfer_count": 0,
        "unknown_count": 0,
        "override_count": 0,
    }

    for tx in transactions:
        tx_type = tx.get("transaction_type", "UNKNOWN")
        if tx_type == "INCOME":
            stats["income_count"] += 1
        elif tx_type == "EXPENSE":
            stats["expense_count"] += 1
        elif tx_type == "TRANSFER":
            stats["transfer_count"] += 1
        else:
            stats["unknown_count"] += 1

        if tx.get("classification_overridden"):
            stats["override_count"] += 1

    return stats
