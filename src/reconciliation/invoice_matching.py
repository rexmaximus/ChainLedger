"""
Invoice-to-Payment Matching

Matches incoming payments to invoices created with ChainLedger's invoice module.

A payment is eligible for an invoice when:
- the asset equals the invoice token
- it was confirmed on or after the invoice date
- if the invoice records the client's wallet and the payment's sender is known,
  they are the same address
- if the invoice records your receiving wallet, the payment arrived there

Invoices are processed oldest first:
1. Manual matches (from the override log) are applied first.
2. One eligible payment within the tolerance of the invoice amount -> paid.
3. Only when the payer is verified (the invoice has a client wallet and the
   payment came from it):
   - several payments that add up to the amount within tolerance -> paid
   - payments that add up to less -> partially paid
   - a single payment above the amount plus tolerance -> overpaid
4. Otherwise -> unpaid.

Step 3 needs a verified payer because without one, any larger or smaller
incoming amount of the same token could be assigned to the wrong invoice.
Bitcoin rows from the current fetcher carry no sender address, so Bitcoin
invoices can only be matched by exact amount (step 2) or manually.

Incoming payments not assigned to any invoice are returned as exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .common import parse_date, row_id, to_decimal, tx_datetime

PAID = "paid"
PARTIALLY_PAID = "partially paid"
OVERPAID = "overpaid"
UNPAID = "unpaid"

UNKNOWN_SENDERS = {"", "various"}


@dataclass
class InvoiceMatch:
    """Matching outcome for one invoice."""

    invoice_id: str
    invoice_number: str
    invoice_date: str
    asset: str
    invoice_amount: Decimal
    amount_received: Decimal
    difference: Decimal  # received - invoiced
    status: str
    recorded_status: str  # status stored on the invoice record
    payment_row_ids: list[str] = field(default_factory=list)
    payment_tx_hashes: list[str] = field(default_factory=list)
    payer_verified: bool = False
    method: str = ""  # "exact_amount", "payer_accumulated", "manual" or ""
    source: str = "ChainLedger invoice"  # or "client file"
    customer: str = ""


@dataclass
class UnmatchedPayment:
    row_id: str
    tx: dict
    reason: str


@dataclass
class InvoiceMatchResult:
    invoice_matches: list[InvoiceMatch] = field(default_factory=list)
    unmatched_payments: list[UnmatchedPayment] = field(default_factory=list)


def _sender(tx: dict) -> str:
    sender = (tx.get("from_address") or "").lower()
    return "" if sender in UNKNOWN_SENDERS else sender


def match_invoices_to_payments(
    invoices: list[dict],
    payments: list[dict],
    tolerance_pct: Decimal = Decimal("0.5"),
    manual_matches: Optional[list[tuple[str, str]]] = None,
    excluded_row_ids: Optional[set[str]] = None,
) -> InvoiceMatchResult:
    """
    Match invoices to incoming payments.

    Args:
        invoices: Invoice dicts as stored in metadata.json (id, invoice_number,
                  created_at, crypto_amount, token_type, recipient_wallet,
                  sender_wallet, status)
        payments: Incoming row dicts that are not own-wallet transfers
        tolerance_pct: Allowed difference as a percentage of the invoice amount
        manual_matches: (invoice_id, payment_row_id) pairs forced by the user
        excluded_row_ids: Payments the user has unmatched; never auto-assigned

    Returns:
        InvoiceMatchResult
    """
    excluded = set(excluded_row_ids or set())
    pay_rows = {row_id(p): p for p in payments if p.get("direction") == "In"}
    assigned: dict[str, str] = {}  # payment row id -> invoice id

    open_invoices = [
        i for i in invoices if (i.get("status") or "").lower() not in ("cancelled", "void", "voided")
    ]
    open_invoices.sort(key=lambda i: i.get("created_at", ""))

    manual_by_invoice: dict[str, list[str]] = {}
    for inv_id, pay_id in manual_matches or []:
        if pay_id in pay_rows and pay_id not in assigned:
            manual_by_invoice.setdefault(inv_id, []).append(pay_id)
            assigned[pay_id] = inv_id

    result = InvoiceMatchResult()

    for inv in open_invoices:
        inv_id = inv.get("id", "")
        amount = to_decimal(inv.get("crypto_amount"))
        asset = (inv.get("token_type") or "").upper()
        inv_date = parse_date(inv.get("created_at"))
        tolerance = amount * tolerance_pct / Decimal(100)
        payer = (inv.get("recipient_wallet") or "").strip().lower()
        receiving = (inv.get("sender_wallet") or "").strip().lower()

        def eligible(pid: str, tx: dict) -> bool:
            if pid in assigned or pid in excluded:
                return False
            if (tx.get("asset") or "").upper() != asset:
                return False
            if tx_datetime(tx).date() < inv_date:
                return False
            sender = _sender(tx)
            if payer and sender and sender != payer:
                return False
            wallet = (tx.get("wallet_address") or tx.get("to_address") or "").lower()
            members = {a.lower() for a in (tx.get("wallet_addresses") or "").split(",") if a}
            if receiving and wallet and wallet != receiving and receiving not in members:
                return False
            return True

        chosen: list[str] = []
        method = ""
        payer_verified = False

        if inv_id in manual_by_invoice:
            chosen = manual_by_invoice[inv_id]
            method = "manual"
        else:
            candidates = sorted(
                (pid for pid, tx in pay_rows.items() if eligible(pid, tx)),
                key=lambda pid: tx_datetime(pay_rows[pid]),
            )
            exact = [
                pid for pid in candidates
                if abs(to_decimal(pay_rows[pid].get("amount_decimal")) - amount) <= tolerance
            ]
            if exact:
                chosen = [exact[0]]
                method = "exact_amount"
                payer_verified = bool(payer) and _sender(pay_rows[exact[0]]) == payer
            elif payer:
                from_payer = [pid for pid in candidates if _sender(pay_rows[pid]) == payer]
                total = Decimal(0)
                for pid in from_payer:
                    value = to_decimal(pay_rows[pid].get("amount_decimal"))
                    if not chosen and value > amount + tolerance:
                        chosen = [pid]  # single overpayment
                        total = value
                        break
                    if total + value > amount + tolerance:
                        break
                    chosen.append(pid)
                    total += value
                    if total >= amount - tolerance:
                        break
                if chosen:
                    method = "payer_accumulated"
                    payer_verified = True

        received = sum((to_decimal(pay_rows[p].get("amount_decimal")) for p in chosen), Decimal(0))
        if not chosen:
            status = UNPAID
        elif received > amount + tolerance:
            status = OVERPAID
        elif received < amount - tolerance:
            status = PARTIALLY_PAID
        else:
            status = PAID

        for pid in chosen:
            assigned[pid] = inv_id

        result.invoice_matches.append(InvoiceMatch(
            invoice_id=inv_id,
            invoice_number=inv.get("invoice_number", ""),
            invoice_date=inv_date.isoformat(),
            asset=asset,
            invoice_amount=amount,
            amount_received=received,
            difference=received - amount,
            status=status,
            recorded_status=inv.get("status", ""),
            payment_row_ids=list(chosen),
            payment_tx_hashes=[pay_rows[p].get("tx_hash", "") for p in chosen],
            payer_verified=payer_verified,
            method=method,
            source=inv.get("source") or "ChainLedger invoice",
            customer=inv.get("recipient_name", ""),
        ))

    for pid, tx in sorted(pay_rows.items(), key=lambda kv: tx_datetime(kv[1])):
        if pid in assigned:
            continue
        reason = "Unmatched manually" if pid in excluded else "No invoice matches this payment"
        result.unmatched_payments.append(UnmatchedPayment(row_id=pid, tx=tx, reason=reason))

    return result
