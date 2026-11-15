import csv
import io
from datetime import date
from decimal import Decimal

import openpyxl

from src.reconciliation import overrides as ov
from src.reconciliation.balance import reconcile_balance
from src.reconciliation.common import row_id
from src.reconciliation.report import build_report, export_report_csv, export_report_xlsx
from tests.sample_data import COLD, HOT, row, sample_balances, sample_invoices, sample_transactions


def test_balance_formula_and_difference():
    txs = [
        row("a", HOT, "In", "2.0", "2024-03-04"),
        row("b", HOT, "Out", "0.5", "2024-03-05", fee="0.01"),
        row("c", HOT, "In", "9.0", "2024-04-02"),  # outside the period
        row("d", COLD, "In", "9.0", "2024-03-02"),  # other wallet
    ]
    b = reconcile_balance(HOT, "Ethereum", txs, date(2024, 3, 1), date(2024, 3, 31),
                          opening_balance=Decimal("1"), actual_closing=Decimal("2.48"))
    assert (b.inflows, b.outflows, b.fees) == (Decimal("2.0"), Decimal("0.5"), Decimal("0.01"))
    assert b.expected_closing == Decimal("2.49")
    assert b.difference == Decimal("-0.01")
    assert not b.reconciled


def test_balance_without_opening_has_no_difference():
    b = reconcile_balance(HOT, "Ethereum", [], date(2024, 3, 1), date(2024, 3, 31),
                          opening_balance=None, actual_closing=Decimal("1"))
    assert b.expected_closing is None and b.difference is None


def test_overrides_are_logged_with_timestamps_and_kept_after_removal():
    state = ov.empty_state()
    o = ov.add_override(state, "unmatch_payment", {"row_id": "x"}, note="refund, not income")
    ov.set_note(state, "payment:x", "asked client")
    assert ov.remove_override(state, o["id"], note="was wrong")
    assert [e["action"] for e in state["log"]] == ["add unmatch_payment", "note", "remove unmatch_payment"]
    assert all(e["timestamp"].endswith("+00:00") for e in state["log"])
    assert state["overrides"][0]["active"] is False
    assert ov.unmatched_payment_rows(state) == set()


def test_full_report_on_sample_data():
    report = build_report(sample_transactions(), [HOT, COLD], sample_invoices(), sample_balances())
    s = report.summary()
    assert s["matched_transfers"] == 1
    assert s["network_fee_lines"] == 1
    assert s["invoice_statuses"] == {"paid": 1, "partially paid": 1, "unpaid": 1}
    assert s["unmatched_payments"] == 1  # 0xmystery
    hot, cold = report.balances
    assert hot.expected_closing == Decimal("1.847")
    assert hot.difference == Decimal("-0.0005")
    assert cold.reconciled
    categories = sorted(e.category for e in report.exceptions)
    assert categories == [
        "Balance difference",
        "Incoming payment not matched to an invoice",
        "Invoice partially paid",
        "Invoice unpaid",
    ]


def test_manual_override_and_note_flow_through_report():
    txs = sample_transactions()
    mystery = row_id(next(t for t in txs if t["tx_hash"] == "0xmystery"))
    state = ov.empty_state()
    ov.add_override(state, "match_invoice", {"invoice_id": "inv-3", "row_id": mystery}, note="client paid in part")
    ov.add_override(state, "closing_balance", {"wallet_address": HOT, "date": "2024-03-31", "amount": "1.847"},
                    note="balance per explorer")
    ov.set_note(state, "invoice:inv-3", "chasing remaining 0.70 ETH")
    report = build_report(txs, [HOT, COLD], sample_invoices(), sample_balances(), state=state)

    inv3 = next(m for m in report.invoices.invoice_matches if m.invoice_id == "inv-3")
    assert (inv3.method, inv3.status) == ("manual", "partially paid")
    assert report.invoices.unmatched_payments == []
    assert report.balances[0].reconciled
    assert "manual" in report.balances[0].actual_source
    exc = next(e for e in report.exceptions if e.exception_id == "invoice:inv-3")
    assert exc.note == "chasing remaining 0.70 ETH"
    assert len(report.override_log) == 3


def test_exports_contain_all_sections():
    report = build_report(sample_transactions(), [HOT, COLD], sample_invoices(), sample_balances())
    text = export_report_csv(report)
    rows = list(csv.reader(io.StringIO(text)))
    titles = [r[0] for r in rows if len(r) == 1 and r[0].isupper()]
    for title in ["SUMMARY", "MATCHED TRANSFERS", "UNMATCHED TRANSFER LEGS", "NETWORK FEES",
                  "INVOICE MATCHING", "UNMATCHED PAYMENTS", "BALANCE RECONCILIATION",
                  "EXCEPTIONS", "OVERRIDE LOG"]:
        assert title in titles

    wb = openpyxl.load_workbook(io.BytesIO(export_report_xlsx(report)))
    assert "Exceptions" in wb.sheetnames and "Override Log" in wb.sheetnames
    assert wb["Exceptions"].cell(row=1, column=8).value == "Note"
