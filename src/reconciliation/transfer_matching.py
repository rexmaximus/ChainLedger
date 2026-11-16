"""
Own-Wallet Transfer Matching

Pairs an outgoing row in one of your wallets with the incoming row in another of
your wallets, so internal movements are not reported as an expense on one side
and income on the other.

Matching rules, applied in this order:
1. Manual links (from the override log) are applied first, as given.
2. Same txid: an outgoing row and an incoming row on the same network share a
   transaction hash and asset, and the wallets differ. This is the normal case on-chain.
3. Amount and time: same network and asset, different txids, the incoming row
   is confirmed within `time_window` after the outgoing row, and the amount
   received equals the amount sent, or the amount sent minus the recorded
   network fee, within `amount_tolerance`.
   If an outgoing row has more than one possible incoming row (or the other way
   round), nothing is matched and both rows are reported as ambiguous.

For every matched pair:
- both rows are marked TRANSFER and share a transfer_id
- the network fee on the outgoing row (if any) becomes its own expense line

Rows that look like own-wallet transfers (classified TRANSFER, or the
counterparty address is one of your wallets) but have no match are returned as
unmatched legs for review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from .common import NATIVE_ASSET, row_id, to_decimal, tx_datetime


@dataclass
class TransferMatch:
    """A matched pair of rows representing one own-wallet transfer."""

    transfer_id: str
    method: str  # "same_txid", "amount_and_time" or "manual"
    network: str
    asset: str
    out_row_id: str
    in_row_id: str
    out_wallet: str
    in_wallet: str
    out_tx_hash: str
    in_tx_hash: str
    out_datetime: str
    in_datetime: str
    amount_sent: Decimal
    amount_received: Decimal
    fee: Decimal
    fee_asset: str


@dataclass
class FeeLine:
    """Network fee paid on an own-wallet transfer, reported as an expense line."""

    transfer_id: str
    tx_hash: str
    wallet_address: str
    block_date: str
    block_time: str
    network: str
    asset: str
    amount: Decimal
    description: str = "Network fee on own-wallet transfer"
    usd_value: Optional[float] = None
    cad_value: Optional[float] = None


@dataclass
class UnmatchedLeg:
    """A row that looks like an own-wallet transfer but has no matching row."""

    row_id: str
    side: str  # "outgoing" or "incoming"
    reason: str
    tx: dict


@dataclass
class TransferMatchResult:
    matches: list[TransferMatch] = field(default_factory=list)
    fee_lines: list[FeeLine] = field(default_factory=list)
    unmatched_legs: list[UnmatchedLeg] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)  # annotated copies


def _counterparty(tx: dict) -> str:
    if tx.get("direction") == "Out":
        return (tx.get("to_address") or "").lower()
    return (tx.get("from_address") or "").lower()


def _amount_fits(sent: Decimal, fee: Decimal, received: Decimal, tolerance: Decimal) -> bool:
    if abs(received - sent) <= tolerance:
        return True
    return fee > 0 and abs(received - (sent - fee)) <= tolerance


def match_own_transfers(
    transactions: list[dict],
    own_wallets: set[str] | list[str],
    time_window: timedelta = timedelta(hours=2),
    amount_tolerance: Decimal = Decimal("0.00000001"),
    manual_links: Optional[list[tuple[str, str]]] = None,
    excluded_row_ids: Optional[set[str]] = None,
) -> TransferMatchResult:
    """
    Match outgoing and incoming rows between the user's own wallets.

    Args:
        transactions: Row dicts (LedgerRow fields + wallet_address + transaction_type)
        own_wallets: Addresses the user controls (case-insensitive)
        time_window: Maximum delay between the outgoing and incoming confirmation
                     for amount-and-time matching
        amount_tolerance: Allowed difference in crypto units when comparing amounts
        manual_links: (out_row_id, in_row_id) pairs forced by the user
        excluded_row_ids: Rows the user has unlinked; never auto-matched

    Returns:
        TransferMatchResult with matches, fee lines, unmatched legs and annotated rows
    """
    own = {w.lower() for w in own_wallets}
    excluded = set(excluded_row_ids or set())
    rows = {row_id(tx): dict(tx) for tx in transactions}

    def is_own_row(tx: dict) -> bool:
        return (tx.get("wallet_address") or "").lower() in own

    outgoing = {rid: tx for rid, tx in rows.items() if tx.get("direction") == "Out" and is_own_row(tx)}
    incoming = {rid: tx for rid, tx in rows.items() if tx.get("direction") == "In" and is_own_row(tx)}

    result = TransferMatchResult()
    used: set[str] = set()
    ambiguous: dict[str, str] = {}

    def record(out_id: str, in_id: str, method: str) -> None:
        out_tx, in_tx = rows[out_id], rows[in_id]
        transfer_id = f"TR-{len(result.matches) + 1:04d}"
        fee = to_decimal(out_tx.get("tx_fee_native"))
        fee_asset = NATIVE_ASSET.get(out_tx.get("network", ""), out_tx.get("asset", ""))
        result.matches.append(TransferMatch(
            transfer_id=transfer_id,
            method=method,
            network=out_tx.get("network", ""),
            asset=out_tx.get("asset", ""),
            out_row_id=out_id,
            in_row_id=in_id,
            out_wallet=out_tx.get("wallet_address", ""),
            in_wallet=in_tx.get("wallet_address", ""),
            out_tx_hash=out_tx.get("tx_hash", ""),
            in_tx_hash=in_tx.get("tx_hash", ""),
            out_datetime=tx_datetime(out_tx).isoformat(),
            in_datetime=tx_datetime(in_tx).isoformat(),
            amount_sent=to_decimal(out_tx.get("amount_decimal")),
            amount_received=to_decimal(in_tx.get("amount_decimal")),
            fee=fee,
            fee_asset=fee_asset,
        ))
        if fee > 0:
            result.fee_lines.append(FeeLine(
                transfer_id=transfer_id,
                tx_hash=out_tx.get("tx_hash", ""),
                wallet_address=out_tx.get("wallet_address", ""),
                block_date=out_tx.get("block_date", ""),
                block_time=out_tx.get("block_time", ""),
                network=out_tx.get("network", ""),
                asset=fee_asset,
                amount=fee,
            ))
        for rid in (out_id, in_id):
            rows[rid]["transaction_type"] = "TRANSFER"
            rows[rid]["transfer_id"] = transfer_id
            rows[rid]["transfer_match_method"] = method
        used.update((out_id, in_id))

    # 1. Manual links
    for out_id, in_id in manual_links or []:
        if out_id in rows and in_id in rows and out_id not in used and in_id not in used:
            record(out_id, in_id, "manual")

    def available(rid: str) -> bool:
        return rid not in used and rid not in excluded

    # 2. Same txid
    by_hash: dict[tuple[str, str, str], list[str]] = {}
    for rid, tx in incoming.items():
        by_hash.setdefault((tx.get("network", ""), tx.get("tx_hash", ""), tx.get("asset", "")), []).append(rid)

    for out_id, out_tx in sorted(outgoing.items(), key=lambda kv: tx_datetime(kv[1])):
        if not available(out_id):
            continue
        key = (out_tx.get("network", ""), out_tx.get("tx_hash", ""), out_tx.get("asset", ""))
        candidates = [
            rid for rid in by_hash.get(key, [])
            if available(rid)
            and (rows[rid].get("wallet_address") or "").lower()
            != (out_tx.get("wallet_address") or "").lower()
        ]
        if len(candidates) == 1:
            record(out_id, candidates[0], "same_txid")
        elif len(candidates) > 1:
            # One tx paying several of your wallets: the amounts must decide.
            sent = to_decimal(out_tx.get("amount_decimal"))
            exact = [
                rid for rid in candidates
                if abs(to_decimal(rows[rid].get("amount_decimal")) - sent) <= amount_tolerance
            ]
            if len(exact) == 1:
                record(out_id, exact[0], "same_txid")
            else:
                ambiguous[out_id] = "Same txid credits several of your wallets"

    # 3. Amount and time
    def candidates_for(out_id: str) -> list[str]:
        out_tx = rows[out_id]
        out_time = tx_datetime(out_tx)
        sent = to_decimal(out_tx.get("amount_decimal"))
        fee = to_decimal(out_tx.get("tx_fee_native"))
        if out_tx.get("asset") != NATIVE_ASSET.get(out_tx.get("network", "")):
            fee = Decimal(0)  # the fee is not in the transferred asset
        found = []
        for in_id, in_tx in incoming.items():
            if not available(in_id):
                continue
            if in_tx.get("tx_hash") == out_tx.get("tx_hash"):
                continue
            if (in_tx.get("wallet_address") or "").lower() == (out_tx.get("wallet_address") or "").lower():
                continue
            if in_tx.get("network") != out_tx.get("network") or in_tx.get("asset") != out_tx.get("asset"):
                continue
            delay = tx_datetime(in_tx) - out_time
            if delay < timedelta(0) or delay > time_window:
                continue
            if _amount_fits(sent, fee, to_decimal(in_tx.get("amount_decimal")), amount_tolerance):
                found.append(in_id)
        return found

    pending = [rid for rid in outgoing if available(rid) and rid not in ambiguous]
    proposals = {out_id: candidates_for(out_id) for out_id in pending}
    claimed: dict[str, list[str]] = {}
    for out_id, cands in proposals.items():
        for in_id in cands:
            claimed.setdefault(in_id, []).append(out_id)

    for out_id in sorted(proposals, key=lambda rid: tx_datetime(rows[rid])):
        cands = proposals[out_id]
        if len(cands) == 1 and len(claimed[cands[0]]) == 1:
            record(out_id, cands[0], "amount_and_time")
        elif len(cands) > 1:
            ambiguous[out_id] = "Several incoming rows fit amount and time window"
            for in_id in cands:
                ambiguous.setdefault(in_id, "Several outgoing rows fit amount and time window")
        elif len(cands) == 1:
            ambiguous[out_id] = "Incoming row also fits another outgoing row"
            ambiguous.setdefault(cands[0], "Several outgoing rows fit amount and time window")

    # Unmatched legs that look like own-wallet transfers
    for rid, tx in rows.items():
        if rid in used or not is_own_row(tx):
            continue
        side = "outgoing" if tx.get("direction") == "Out" else "incoming"
        if rid in ambiguous:
            reason = ambiguous[rid]
        elif rid in excluded and tx.get("transaction_type") == "TRANSFER":
            reason = "Unlinked manually"
        elif tx.get("transaction_type") == "TRANSFER" or _counterparty(tx) in own:
            reason = "No matching row found in your other wallets"
        else:
            continue
        result.unmatched_legs.append(UnmatchedLeg(row_id=rid, side=side, reason=reason, tx=tx))

    result.transactions = sorted(rows.values(), key=tx_datetime)
    return result
