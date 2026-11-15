from datetime import timedelta
from decimal import Decimal

from src.reconciliation.common import row_id
from src.reconciliation.transfer_matching import match_own_transfers
from tests.sample_data import COLD, HOT, VENDOR, row

OWN = [HOT, COLD]


def test_same_txid_pair_is_matched_and_fee_becomes_expense_line():
    out = row("0xabc", HOT, "Out", "1.5", "2024-03-05", fee="0.0021", from_address=HOT, to_address=COLD)
    inc = row("0xabc", COLD, "In", "1.5", "2024-03-05", from_address=HOT, to_address=COLD)
    result = match_own_transfers([out, inc], OWN)

    assert len(result.matches) == 1
    m = result.matches[0]
    assert m.method == "same_txid"
    assert (m.out_row_id, m.in_row_id) == (row_id(out), row_id(inc))
    assert all(tx["transaction_type"] == "TRANSFER" for tx in result.transactions)
    assert {tx["transfer_id"] for tx in result.transactions} == {m.transfer_id}
    assert len(result.fee_lines) == 1
    assert result.fee_lines[0].amount == Decimal("0.0021")
    assert result.fee_lines[0].asset == "ETH"
    assert result.unmatched_legs == []


def test_amount_minus_fee_within_time_window_matches_different_txids():
    out = row("0xout", HOT, "Out", "0.5", "2024-03-05", "09:00:00", fee="0.001")
    inc = row("0xin", COLD, "In", "0.499", "2024-03-05", "09:40:00")
    result = match_own_transfers([out, inc], OWN, time_window=timedelta(hours=1))
    assert len(result.matches) == 1
    assert result.matches[0].method == "amount_and_time"


def test_outside_time_window_or_wrong_asset_is_not_matched():
    out = row("0xout", HOT, "Out", "0.5", "2024-03-05", "09:00:00")
    late = row("0xin", COLD, "In", "0.5", "2024-03-05", "13:00:00")
    other_asset = row("0xin2", COLD, "In", "0.5", "2024-03-05", "09:10:00", asset="USDC")
    result = match_own_transfers([out, late, other_asset], OWN, time_window=timedelta(hours=2))
    assert result.matches == []


def test_incoming_before_outgoing_is_not_matched():
    out = row("0xout", HOT, "Out", "0.5", "2024-03-05", "09:00:00")
    inc = row("0xin", COLD, "In", "0.5", "2024-03-05", "08:59:00")
    assert match_own_transfers([out, inc], OWN).matches == []


def test_ambiguous_candidates_are_left_for_review():
    out = row("0xout", HOT, "Out", "0.5", "2024-03-05", "09:00:00")
    in1 = row("0xin1", COLD, "In", "0.5", "2024-03-05", "09:10:00")
    in2 = row("0xin2", COLD, "In", "0.5", "2024-03-05", "09:20:00")
    result = match_own_transfers([out, in1, in2], OWN)
    assert result.matches == []
    assert {leg.row_id for leg in result.unmatched_legs} == {row_id(out), row_id(in1), row_id(in2)}


def test_transfer_to_own_wallet_without_counterpart_is_unmatched_leg():
    out = row("0xabc", HOT, "Out", "1.0", "2024-03-05", from_address=HOT, to_address=COLD)
    vendor = row("0xdef", HOT, "Out", "0.3", "2024-03-06", from_address=HOT, to_address=VENDOR)
    result = match_own_transfers([out, vendor], OWN)
    assert [leg.row_id for leg in result.unmatched_legs] == [row_id(out)]
    assert result.unmatched_legs[0].side == "outgoing"


def test_manual_link_and_unlink_overrides():
    out = row("0xout", HOT, "Out", "0.5", "2024-03-05", "09:00:00")
    inc = row("0xin", COLD, "In", "0.45", "2024-03-09", "09:00:00")  # would never auto-match
    linked = match_own_transfers([out, inc], OWN, manual_links=[(row_id(out), row_id(inc))])
    assert linked.matches[0].method == "manual"

    a = row("0xabc", HOT, "Out", "1.0", "2024-03-05", from_address=HOT, to_address=COLD)
    b = row("0xabc", COLD, "In", "1.0", "2024-03-05", from_address=HOT, to_address=COLD)
    unlinked = match_own_transfers([a, b], OWN, excluded_row_ids={row_id(a)})
    assert unlinked.matches == []


def test_bitcoin_same_txid_between_own_wallets():
    w1 = "bc1qexamplewalletone0000000000000000000000"
    w2 = "bc1qexamplewallettwo0000000000000000000000"
    out = row("btctx", w1, "Out", "0.01", "2024-03-05", network="Bitcoin", asset="BTC",
              fee="0.00002", from_address=w1, to_address="Various")
    inc = row("btctx", w2, "In", "0.01", "2024-03-05", network="Bitcoin", asset="BTC",
              from_address="Various", to_address=w2)
    result = match_own_transfers([out, inc], [w1, w2])
    assert len(result.matches) == 1
    assert result.fee_lines[0].asset == "BTC"
