from datetime import date
from decimal import Decimal
from pathlib import Path

from src.imports import apply_mapping, parse_file, suggest_mapping
from src.reconciliation.common import row_id
from src.reconciliation.record_matching import match_client_records
from src.reconciliation.report import build_report
from tests.sample_data import COLD, HOT, row, sample_balances, sample_transactions

EXAMPLES = Path(__file__).parent.parent / "examples" / "client_files"
MARCH = dict(period_start=date(2024, 3, 1), period_end=date(2024, 3, 31))


def rec(record_id, direction, amount, day, file_type="ledger", tx_hash="", asset="ETH", fee="0", file_id="f"):
    return {"record_id": record_id, "file_id": file_id, "line": 2, "date": f"{day}T00:00:00",
            "asset": asset, "amount": amount, "chain_direction": direction, "fee": fee,
            "tx_hash": tx_hash, "file_type": file_type, "reference": "", "description": ""}


def example_records():
    ledger = parse_file("client_ledger.csv", (EXAMPLES / "client_ledger.csv").read_bytes())
    ledger_records = apply_mapping("ledger1", "ledger", ledger, {
        "date": "Date", "debit": "Debit", "credit": "Credit", "asset": "Currency",
        "tx_hash": "Tx Hash", "reference": "Entry", "description": "Description", "date_format": "mdy",
    }).records
    ex = parse_file("exchange_history.csv", (EXAMPLES / "exchange_history.csv").read_bytes())
    ex_records = apply_mapping("ex1", "exchange", ex, suggest_mapping("exchange", ex.headers)).records
    ar = parse_file("receivables.csv", (EXAMPLES / "receivables.csv").read_bytes())
    receivables = apply_mapping("ar1", "receivables", ar, suggest_mapping("receivables", ar.headers)).records
    return ledger_records + ex_records, receivables


def test_hash_match_reports_amount_difference():
    tx = row("0xv", HOT, "Out", "0.3", "2024-03-15", fee="0.0009")
    result = match_client_records([rec("r1", "Out", "0.31", "2024-03-15", tx_hash="0xV")], [tx], **MARCH)
    m = result.matches[0]
    assert (m.method, m.amount_ok, m.difference) == ("tx_hash", False, Decimal("-0.01"))


def test_amount_and_date_match_allows_fee_inclusive_ledger_amount():
    tx = row("0xm", HOT, "Out", "1.5", "2024-03-05", fee="0.0021")
    result = match_client_records([rec("r1", "Out", "1.5021", "2024-03-06")], [tx], **MARCH)
    assert result.matches[0].method == "amount_and_date" and result.matches[0].amount_ok


def test_direction_and_date_window_are_enforced():
    tx = row("0xa", HOT, "In", "1.0", "2024-03-05")
    wrong_dir = rec("r1", "Out", "1.0", "2024-03-05")
    too_far = rec("r2", "In", "1.0", "2024-03-15")
    result = match_client_records([wrong_dir, too_far], [tx], date_window_days=3, **MARCH)
    assert result.matches == []
    assert {u.record["record_id"] for u in result.unmatched_records} == {"r1", "r2"}
    assert result.chain_not_in_ledger == [tx]


def test_ambiguous_amounts_are_not_guessed():
    t1 = row("0x1", HOT, "In", "1.0", "2024-03-05")
    t2 = row("0x2", HOT, "In", "1.0", "2024-03-06")
    result = match_client_records([rec("r1", "In", "1.0", "2024-03-05")], [t1, t2], **MARCH)
    assert result.matches == []
    assert result.unmatched_records[0].reason.startswith("Ambiguous")


def test_exchange_withdrawal_with_fee_and_off_chain_lines():
    tx = row("0xw", COLD, "In", "0.999", "2024-03-29")
    withdrawal = rec("e1", "In", "1.0", "2024-03-29", file_type="exchange", fee="0.001")
    trade = rec("e2", "", "0.06", "2024-03-18", file_type="exchange")
    result = match_client_records([withdrawal, trade], [tx], **MARCH)
    assert result.matches[0].record_id == "e1"
    assert [r["record_id"] for r in result.off_chain_records] == ["e2"]
    assert result.chain_not_in_ledger == []  # only ledger files report missing chain rows


def test_ledger_wallet_scope_and_period_and_overrides():
    hot = row("0xh", HOT, "In", "1.0", "2024-03-05")
    cold = row("0xc", COLD, "In", "2.0", "2024-03-05")
    april = rec("r9", "In", "5", "2024-04-02", file_id="L")
    line = rec("r1", "In", "0.7", "2024-03-05", file_id="L")
    result = match_client_records([april, line], [hot, cold], file_wallets={"L": HOT},
                                  manual_matches=[("r1", row_id(hot))], **MARCH)
    assert result.outside_period == 1
    assert result.matches[0].method == "manual"
    assert result.chain_not_in_ledger == []  # cold wallet not covered by this ledger

    excluded = match_client_records([rec("r1", "In", "1.0", "2024-03-05")], [hot],
                                    excluded_record_ids={"r1"}, **MARCH)
    assert excluded.unmatched_records[0].reason == "Unmatched manually"


def test_example_client_files_end_to_end():
    records, receivables = example_records()
    report = build_report(
        sample_transactions(), [HOT, COLD], invoices=[], balance_inputs=sample_balances(),
        client_records=records, receivables=receivables, file_wallets={"ledger1": HOT},
        period_start="2024-03-01", period_end="2024-03-31",
    )
    s = report.summary()
    assert s["client_records_matched"] == 6  # 5 ledger lines + 1 exchange withdrawal
    assert s["client_records_amount_differences"] == 1  # contractor booked 0.31 vs 0.3 paid
    assert s["client_records_unmatched"] == 2  # subscription not on-chain, 1.0 ETH withdrawal
    assert s["client_records_off_chain"] == 1  # Buy
    assert s["client_records_outside_period"] == 1  # April line
    assert s["chain_rows_not_in_ledger"] == 1  # 0xpay2b missing from ledger
    assert s["invoice_statuses"] == {"paid": 1, "partially paid": 1, "unpaid": 1}
    assert s["unmatched_payments"] == 0  # the 0.05 ETH is explained by the exchange withdrawal
    assert {m.source for m in report.invoices.invoice_matches} == {"client file"}
