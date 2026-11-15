from decimal import Decimal

from src.reconciliation.common import row_id
from src.reconciliation.invoice_matching import (
    OVERPAID, PAID, PARTIALLY_PAID, UNPAID, match_invoices_to_payments,
)
from tests.sample_data import CLIENT_A, CLIENT_B, HOT, row


def invoice(inv_id, amount, created="2024-03-01T09:00:00", payer="", token="ETH", status="unpaid"):
    return {"id": inv_id, "invoice_number": inv_id.upper(), "created_at": created,
            "crypto_amount": amount, "token_type": token, "recipient_wallet": payer,
            "sender_wallet": HOT, "status": status}


def by_id(result):
    return {m.invoice_id: m for m in result.invoice_matches}


def test_exact_payment_within_tolerance_is_paid():
    pay = row("0xp", HOT, "In", "0.999", "2024-03-02", from_address=CLIENT_A)
    result = match_invoices_to_payments([invoice("i1", 1.0)], [pay], tolerance_pct=Decimal("0.5"))
    m = by_id(result)["i1"]
    assert m.status == PAID
    assert m.payment_row_ids == [row_id(pay)]
    assert result.unmatched_payments == []


def test_payment_before_invoice_date_or_other_asset_is_not_matched():
    early = row("0xp1", HOT, "In", "1.0", "2024-02-28", from_address=CLIENT_A)
    usdc = row("0xp2", HOT, "In", "1.0", "2024-03-02", asset="USDC", from_address=CLIENT_A)
    result = match_invoices_to_payments([invoice("i1", 1.0)], [early, usdc])
    assert by_id(result)["i1"].status == UNPAID
    assert len(result.unmatched_payments) == 2


def test_payer_mismatch_is_not_matched():
    pay = row("0xp", HOT, "In", "1.0", "2024-03-02", from_address=CLIENT_B)
    result = match_invoices_to_payments([invoice("i1", 1.0, payer=CLIENT_A)], [pay])
    assert by_id(result)["i1"].status == UNPAID


def test_partial_payments_from_verified_payer():
    p1 = row("0xp1", HOT, "In", "0.4", "2024-03-02", from_address=CLIENT_B)
    p2 = row("0xp2", HOT, "In", "0.2", "2024-03-03", from_address=CLIENT_B)
    result = match_invoices_to_payments([invoice("i1", 1.0, payer=CLIENT_B)], [p1, p2])
    m = by_id(result)["i1"]
    assert m.status == PARTIALLY_PAID
    assert m.amount_received == Decimal("0.6")
    assert m.difference == Decimal("-0.4")
    assert m.payer_verified


def test_instalments_adding_up_are_paid():
    p1 = row("0xp1", HOT, "In", "0.5", "2024-03-02", from_address=CLIENT_B)
    p2 = row("0xp2", HOT, "In", "0.5", "2024-03-03", from_address=CLIENT_B)
    result = match_invoices_to_payments([invoice("i1", 1.0, payer=CLIENT_B)], [p1, p2])
    assert by_id(result)["i1"].status == PAID


def test_overpayment_from_verified_payer():
    pay = row("0xp", HOT, "In", "1.2", "2024-03-02", from_address=CLIENT_A)
    result = match_invoices_to_payments([invoice("i1", 1.0, payer=CLIENT_A)], [pay])
    m = by_id(result)["i1"]
    assert m.status == OVERPAID
    assert m.difference == Decimal("0.2")


def test_unverified_payer_only_matches_exact_amounts():
    pay = row("0xp", HOT, "In", "1.2", "2024-03-02", from_address=CLIENT_A)
    result = match_invoices_to_payments([invoice("i1", 1.0)], [pay])
    assert by_id(result)["i1"].status == UNPAID
    assert [u.row_id for u in result.unmatched_payments] == [row_id(pay)]


def test_oldest_invoice_gets_payment_first_and_cancelled_is_skipped():
    pay = row("0xp", HOT, "In", "1.0", "2024-03-10", from_address=CLIENT_A)
    invs = [
        invoice("new", 1.0, created="2024-03-05T09:00:00"),
        invoice("old", 1.0, created="2024-03-01T09:00:00"),
        invoice("cxl", 1.0, created="2024-02-01T09:00:00", status="cancelled"),
    ]
    result = by_id(match_invoices_to_payments(invs, [pay]))
    assert result["old"].status == PAID
    assert result["new"].status == UNPAID
    assert "cxl" not in result


def test_manual_match_and_unmatch_overrides():
    pay = row("0xp", HOT, "In", "0.7", "2024-03-02", from_address=CLIENT_A)
    manual = match_invoices_to_payments([invoice("i1", 1.0)], [pay], manual_matches=[("i1", row_id(pay))])
    m = by_id(manual)["i1"]
    assert (m.method, m.status) == ("manual", PARTIALLY_PAID)

    exact = row("0xq", HOT, "In", "1.0", "2024-03-02", from_address=CLIENT_A)
    excluded = match_invoices_to_payments([invoice("i1", 1.0)], [exact], excluded_row_ids={row_id(exact)})
    assert by_id(excluded)["i1"].status == UNPAID
    assert excluded.unmatched_payments[0].reason == "Unmatched manually"
