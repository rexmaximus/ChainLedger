"""
Client Record Matching

Matches lines from client-supplied files (a ledger / GL export, or an exchange
history) against the on-chain rows fetched for the user's wallets.

Each file type is matched separately, because the same on-chain transaction can
legitimately appear in both a client ledger and an exchange history.

Rules, in order, for each record inside the reconciliation period:
1. Manual matches (from the override log).
2. Transaction hash: the record's hash equals a chain row's hash (case-insensitive).
   If the amounts differ beyond tolerance, the pair is still matched and the
   difference is reported.
3. Amount and date: same asset, the record's expected direction equals the
   chain row's direction, dates within `date_window_days`, and the amounts agree
   within tolerance. Fees are allowed for: a ledger line may include the network
   fee of an outgoing transaction, and an exchange withdrawal may be reported
   before its withdrawal fee. If more than one row could match (or a row could
   match more than one record), nothing is matched and the record is reported
   as ambiguous.

Exchange lines that are not deposits or withdrawals (trades, interest, ...) and
have no hash are not expected on-chain; they are counted, not reported as
exceptions.

For ledger files, chain rows with no ledger line are reported too ("on-chain
but not in the client ledger"). A ledger file can be tied to one wallet; then
only that wallet's rows are compared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from .common import row_id, to_decimal, tx_datetime

ABS_TOLERANCE = Decimal("0.00000001")


@dataclass
class RecordMatch:
    record_id: str
    row_id: str
    file_id: str
    file_type: str
    method: str  # "tx_hash", "amount_and_date" or "manual"
    asset: str
    record_amount: Decimal
    chain_amount: Decimal
    difference: Decimal  # chain - record
    record_date: str
    chain_date: str
    tx_hash: str
    amount_ok: bool


@dataclass
class UnmatchedRecord:
    record: dict
    reason: str


@dataclass
class RecordMatchResult:
    matches: list[RecordMatch] = field(default_factory=list)
    unmatched_records: list[UnmatchedRecord] = field(default_factory=list)
    off_chain_records: list[dict] = field(default_factory=list)
    outside_period: int = 0
    chain_not_in_ledger: list[dict] = field(default_factory=list)


def _record_date(rec: dict) -> datetime:
    return datetime.fromisoformat(rec["date"])


def _fits(rec: dict, tx: dict, tolerance_pct: Decimal) -> bool:
    amount = to_decimal(rec.get("amount"))
    chain = to_decimal(tx.get("amount_decimal"))
    tol = max(amount * tolerance_pct / Decimal(100), ABS_TOLERANCE)
    candidates = [chain]
    if tx.get("direction") == "Out":
        candidates.append(chain + to_decimal(tx.get("tx_fee_native")))  # ledger booked amount + fee
    rec_fee = to_decimal(rec.get("fee"))
    if rec_fee:
        candidates.append(chain + rec_fee)  # exchange reports gross, wallet receives net
    return any(abs(amount - c) <= tol for c in candidates)


def match_client_records(
    records: list[dict],
    transactions: list[dict],
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    date_window_days: int = 3,
    tolerance_pct: Decimal = Decimal("0.5"),
    manual_matches: Optional[list[tuple[str, str]]] = None,
    excluded_record_ids: Optional[set[str]] = None,
    file_wallets: Optional[dict[str, str]] = None,
) -> RecordMatchResult:
    """
    Match ledger/exchange records to chain rows.

    Args:
        records: Normalized records from src.imports.apply_mapping
                 (file_type "ledger" or "exchange")
        transactions: Chain row dicts (with wallet_address)
        period_start / period_end: Records outside this range are skipped
        date_window_days: Allowed date difference for amount-and-date matching
        tolerance_pct: Allowed amount difference, percent of the record amount
        manual_matches: (record_id, row_id) pairs forced by the user
        excluded_record_ids: Records the user has unmatched
        file_wallets: file_id -> wallet address the ledger file covers (optional)
    """
    excluded = set(excluded_record_ids or set())
    file_wallets = {k: v.lower() for k, v in (file_wallets or {}).items() if v}
    rows = {row_id(tx): tx for tx in transactions}
    result = RecordMatchResult()
    manual = list(manual_matches or [])

    in_period = []
    for rec in records:
        d = _record_date(rec).date()
        if (period_start and d < period_start) or (period_end and d > period_end):
            result.outside_period += 1
        else:
            in_period.append(rec)

    for file_type in ("ledger", "exchange"):
        group = [r for r in in_period if r.get("file_type") == file_type]
        if not group:
            continue
        used_rows: set[str] = set()
        done: set[str] = set()
        by_id = {r["record_id"]: r for r in group}

        def eligible_rows(rec: dict) -> dict:
            wallet = file_wallets.get(rec.get("file_id", ""))
            if not wallet:
                return rows
            return {rid: tx for rid, tx in rows.items() if (tx.get("wallet_address") or "").lower() == wallet}

        def record(rec: dict, rid: str, method: str) -> None:
            tx = rows[rid]
            rec_amount = to_decimal(rec.get("amount"))
            chain_amount = to_decimal(tx.get("amount_decimal"))
            result.matches.append(RecordMatch(
                record_id=rec["record_id"],
                row_id=rid,
                file_id=rec.get("file_id", ""),
                file_type=file_type,
                method=method,
                asset=rec.get("asset", ""),
                record_amount=rec_amount,
                chain_amount=chain_amount,
                difference=chain_amount - rec_amount,
                record_date=rec["date"][:10],
                chain_date=tx.get("block_date", ""),
                tx_hash=tx.get("tx_hash", ""),
                amount_ok=_fits(rec, tx, tolerance_pct),
            ))
            used_rows.add(rid)
            done.add(rec["record_id"])

        # 1. Manual
        for rec_id, rid in manual:
            if rec_id in by_id and rid in rows and rec_id not in done and rid not in used_rows:
                record(by_id[rec_id], rid, "manual")

        pending = [r for r in group if r["record_id"] not in done and r["record_id"] not in excluded]

        # 2. Transaction hash
        for rec in pending:
            h = (rec.get("tx_hash") or "").strip().lower()
            if not h:
                continue
            hits = [
                rid for rid, tx in eligible_rows(rec).items()
                if rid not in used_rows and (tx.get("tx_hash") or "").lower() == h
                and (not rec.get("chain_direction") or tx.get("direction") == rec["chain_direction"])
            ]
            if len(hits) == 1:
                record(rec, hits[0], "tx_hash")
            elif len(hits) > 1:
                fitting = [rid for rid in hits if _fits(rec, rows[rid], tolerance_pct)]
                if len(fitting) == 1:
                    record(rec, fitting[0], "tx_hash")

        # 3. Amount and date
        window = timedelta(days=date_window_days)
        proposals: dict[str, list[str]] = {}
        for rec in pending:
            if rec["record_id"] in done or not rec.get("chain_direction"):
                continue
            when = _record_date(rec)
            proposals[rec["record_id"]] = [
                rid for rid, tx in eligible_rows(rec).items()
                if rid not in used_rows
                and tx.get("asset", "").upper() == rec.get("asset", "").upper()
                and tx.get("direction") == rec["chain_direction"]
                and abs(tx_datetime(tx).date() - when.date()) <= window
                and _fits(rec, tx, tolerance_pct)
            ]
        claims: dict[str, int] = {}
        for cands in proposals.values():
            for rid in cands:
                claims[rid] = claims.get(rid, 0) + 1
        ambiguous: dict[str, str] = {}
        for rec_id, cands in sorted(proposals.items(), key=lambda kv: by_id[kv[0]]["date"]):
            if len(cands) == 1 and claims[cands[0]] == 1:
                record(by_id[rec_id], cands[0], "amount_and_date")
            elif len(cands) > 1:
                ambiguous[rec_id] = f"Ambiguous: {len(cands)} on-chain transactions fit amount and date"
            elif len(cands) == 1:
                ambiguous[rec_id] = "Ambiguous: the matching on-chain transaction also fits another line"

        # Leftovers
        for rec in group:
            rid = rec["record_id"]
            if rid in done:
                continue
            if rid in excluded:
                result.unmatched_records.append(UnmatchedRecord(rec, "Unmatched manually"))
            elif rid in ambiguous:
                result.unmatched_records.append(UnmatchedRecord(rec, ambiguous[rid]))
            elif file_type == "exchange" and not rec.get("chain_direction") and not rec.get("tx_hash"):
                result.off_chain_records.append(rec)
            else:
                result.unmatched_records.append(UnmatchedRecord(rec, "Not found on-chain"))

        if file_type == "ledger":
            ledger_files = {r.get("file_id") for r in group}
            wallets = {file_wallets[f] for f in ledger_files if f in file_wallets}
            covers_all = any(f not in file_wallets for f in ledger_files)
            for rid, tx in rows.items():
                if rid in used_rows:
                    continue
                if not covers_all and (tx.get("wallet_address") or "").lower() not in wallets:
                    continue
                result.chain_not_in_ledger.append(tx)

    return result
