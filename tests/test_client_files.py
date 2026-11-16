import io
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from src.imports import ImportErrorWithRows, apply_mapping, parse_file, suggest_mapping

EXAMPLES = Path(__file__).parent.parent / "examples" / "client_files"

LEDGER_MAPPING = {
    "date": "Date", "debit": "Debit", "credit": "Credit", "asset": "Currency",
    "tx_hash": "Tx Hash", "reference": "Entry", "description": "Description", "date_format": "mdy",
}


def load(name):
    return parse_file(name, (EXAMPLES / name).read_bytes())


def test_csv_headers_and_suggested_mapping():
    parsed = load("client_ledger.csv")
    assert parsed.headers[:3] == ["Date", "Entry", "Account"]
    assert parsed.rows[0]["_line"] == 2
    m = suggest_mapping("ledger", parsed.headers)
    assert m["date"] == "Date" and m["debit"] == "Debit" and m["credit"] == "Credit"
    assert m["tx_hash"] == "Tx Hash" and m["asset"] == "Currency"


def test_ledger_debit_credit_become_directions():
    result = apply_mapping("f1", "ledger", load("client_ledger.csv"), LEDGER_MAPPING)
    assert result.errors == []
    first, second = result.records[:2]
    assert (first["chain_direction"], first["amount"], first["tx_hash"]) == ("In", "2.0", "0xpay1")
    assert (second["chain_direction"], second["amount"]) == ("Out", "1.5021")
    assert first["date"] == "2024-03-04T00:00:00"
    assert first["record_id"] == "f1:2"


def test_exchange_types_map_to_expected_chain_direction():
    parsed = load("exchange_history.csv")
    mapping = suggest_mapping("exchange", parsed.headers)
    assert mapping["type"] == "Type" and mapping["tx_hash"] == "TxID" and mapping["fee"] == "Fee"
    result = apply_mapping("f2", "exchange", parsed, mapping)
    assert [r["chain_direction"] for r in result.records] == ["", "In", "In"]
    assert result.records[2]["fee"] == "0.001"


def test_receivables_become_invoices():
    parsed = load("receivables.csv")
    mapping = suggest_mapping("receivables", parsed.headers)
    assert mapping["invoice_number"] == "Invoice #"
    assert mapping["payer_wallet"] == "Customer Wallet"
    result = apply_mapping("f3", "receivables", parsed, mapping)
    inv = result.records[0]
    assert (inv["invoice_number"], inv["crypto_amount"], inv["token_type"]) == ("INV-C-101", "2.0", "ETH")
    assert inv["source"] == "client file" and inv["id"] == "file:f3:2"


def test_row_errors_are_reported_by_line_and_good_rows_kept():
    content = b"Date,Amount\n2024-03-01,1.5\nnot a date,2\n2024-03-03,abc\n,\n"
    parsed = parse_file("x.csv", content)
    result = apply_mapping("f", "ledger", parsed, {"date": "Date", "amount": "Amount", "default_asset": "eth"})
    assert len(result.records) == 1 and result.records[0]["asset"] == "ETH"
    assert [e.split(":")[0] for e in result.errors] == ["line 3", "line 4"]
    assert result.skipped == 1


def test_incomplete_mapping_is_refused():
    parsed = parse_file("x.csv", b"Date,Amount\n2024-03-01,1\n")
    result = apply_mapping("f", "ledger", parsed, {"date": "Date"})
    assert result.records == []
    assert "Mapping incomplete" in result.errors[0]


def test_xlsx_first_sheet_and_dates():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([])  # blank first row is skipped
    ws.append(["Date", "Amount", "Asset"])
    import datetime
    ws.append([datetime.datetime(2024, 3, 2, 10, 30), -0.25, "BTC"])
    buf = io.BytesIO()
    wb.save(buf)
    parsed = parse_file("ledger.xlsx", buf.getvalue())
    result = apply_mapping("f", "ledger", parsed, {"date": "Date", "amount": "Amount", "asset": "Asset"})
    rec = result.records[0]
    assert (rec["date"], rec["chain_direction"], Decimal(rec["amount"])) == ("2024-03-02T10:30:00", "Out", Decimal("0.25"))


def test_unsupported_file_type():
    with pytest.raises(ImportErrorWithRows):
        parse_file("statement.pdf", b"%PDF-1.4")
