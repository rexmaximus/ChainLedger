"""
Reconciliation Report

Runs the three reconciliation steps over already-fetched data and exports the
result as CSV (one file, sections in order) or Excel (one sheet per section).

Sections:
- Summary
- Matched Transfers / Unmatched Transfer Legs / Network Fees
- Client Records Matched / Client Records Unmatched / On-Chain Not In Ledger
- Invoice Matching / Unmatched Payments
- Balance Reconciliation
- Exceptions (every item needing review, with its free-text note)
- Override Log (every manual change, with UTC timestamp)

This module does no network calls; see service.py for fetching.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

from . import overrides as ov
from .balance import BalanceReconciliation, reconcile_balance
from .common import fmt_amount, parse_date, row_id, to_decimal, utc_now_iso
from .invoice_matching import PAID, InvoiceMatchResult, match_invoices_to_payments
from .record_matching import RecordMatchResult, match_client_records
from .transfer_matching import TransferMatchResult, match_own_transfers


@dataclass
class ExceptionItem:
    exception_id: str
    category: str
    reference: str
    date: str
    asset: str
    amount: str
    detail: str
    note: str = ""


@dataclass
class ReconciliationReport:
    generated_at: str
    settings: dict
    transfers: TransferMatchResult
    invoices: InvoiceMatchResult
    balances: list[BalanceReconciliation]
    exceptions: list[ExceptionItem] = field(default_factory=list)
    override_log: list[dict] = field(default_factory=list)
    active_overrides: list[dict] = field(default_factory=list)
    records: RecordMatchResult = field(default_factory=RecordMatchResult)

    def summary(self) -> dict:
        statuses: dict[str, int] = {}
        for m in self.invoices.invoice_matches:
            statuses[m.status] = statuses.get(m.status, 0) + 1
        return {
            "matched_transfers": len(self.transfers.matches),
            "unmatched_transfer_legs": len(self.transfers.unmatched_legs),
            "network_fee_lines": len(self.transfers.fee_lines),
            "invoices": len(self.invoices.invoice_matches),
            "invoice_statuses": statuses,
            "unmatched_payments": len(self.invoices.unmatched_payments),
            "client_records_matched": len(self.records.matches),
            "client_records_amount_differences": sum(1 for m in self.records.matches if not m.amount_ok),
            "client_records_unmatched": len(self.records.unmatched_records),
            "client_records_off_chain": len(self.records.off_chain_records),
            "client_records_outside_period": self.records.outside_period,
            "chain_rows_not_in_ledger": len(self.records.chain_not_in_ledger),
            "balances_checked": len(self.balances),
            "balances_reconciled": sum(1 for b in self.balances if b.reconciled),
            "exceptions": len(self.exceptions),
            "manual_overrides_active": len(self.active_overrides),
        }

    def to_dict(self) -> dict:
        """JSON-safe representation (Decimals become strings)."""
        def clean(value):
            if isinstance(value, Decimal):
                return fmt_amount(value)
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value

        return clean({
            "generated_at": self.generated_at,
            "settings": self.settings,
            "summary": self.summary(),
            "transfers": [asdict(m) for m in self.transfers.matches],
            "fee_lines": [asdict(f) for f in self.transfers.fee_lines],
            "unmatched_transfer_legs": [
                {"row_id": u.row_id, "side": u.side, "reason": u.reason, **_tx_brief(u.tx)}
                for u in self.transfers.unmatched_legs
            ],
            "invoices": [asdict(m) for m in self.invoices.invoice_matches],
            "unmatched_payments": [
                {"row_id": u.row_id, "reason": u.reason, **_tx_brief(u.tx)}
                for u in self.invoices.unmatched_payments
            ],
            "balances": [{**asdict(b), "reconciled": b.reconciled} for b in self.balances],
            "record_matches": [asdict(m) for m in self.records.matches],
            "unmatched_records": [{**u.record, "reason": u.reason} for u in self.records.unmatched_records],
            "off_chain_records": self.records.off_chain_records,
            "chain_not_in_ledger": [{"row_id": row_id(tx), **_tx_brief(tx)} for tx in self.records.chain_not_in_ledger],
            "exceptions": [asdict(e) for e in self.exceptions],
            "override_log": self.override_log,
            "active_overrides": self.active_overrides,
        })


def _tx_brief(tx: dict) -> dict:
    return {
        "tx_hash": tx.get("tx_hash", ""),
        "date": f"{tx.get('block_date', '')} {tx.get('block_time', '')}".strip(),
        "wallet_address": tx.get("wallet_address", ""),
        "from_address": tx.get("from_address", ""),
        "to_address": tx.get("to_address", ""),
        "direction": tx.get("direction", ""),
        "network": tx.get("network", ""),
        "asset": tx.get("asset", ""),
        "amount": tx.get("amount_decimal", ""),
        "fee": tx.get("tx_fee_native", ""),
        "transaction_type": tx.get("transaction_type", ""),
    }


# =============================================================================
# Run
# =============================================================================

def build_report(
    transactions: list[dict],
    own_wallets: list[str],
    invoices: list[dict],
    balance_inputs: list[dict],
    state: Optional[dict] = None,
    time_window_hours: float = 2,
    invoice_tolerance_pct: Decimal = Decimal("0.5"),
    amount_tolerance: Decimal = Decimal("0.00000001"),
    client_records: Optional[list[dict]] = None,
    receivables: Optional[list[dict]] = None,
    file_wallets: Optional[dict[str, str]] = None,
    period_start=None,
    period_end=None,
    record_tolerance_pct: Decimal = Decimal("0.5"),
    date_window_days: int = 3,
) -> ReconciliationReport:
    """
    Run transfer matching, invoice matching and balance reconciliation.

    Args:
        transactions: Classified row dicts for the user's wallets (with wallet_address)
        own_wallets: Self-custodial wallet addresses
        invoices: Invoice dicts from metadata.json
        balance_inputs: One dict per wallet: wallet_address, network,
            period_start, period_end, opening_balance, opening_source,
            actual_closing, actual_source (balances may be None)
        state: Reconciliation state (overrides, notes, log)
        time_window_hours: Window for amount-and-time transfer matching
        invoice_tolerance_pct: Invoice amount tolerance, percent of invoice amount
        amount_tolerance: Absolute tolerance for transfer amounts
        client_records: Ledger/exchange records from uploaded client files
        receivables: Invoice dicts from uploaded receivables files
        file_wallets: file_id -> wallet a ledger file covers
        period_start / period_end: Reconciliation period (client records outside it are skipped)
        record_tolerance_pct: Amount tolerance for client records, percent
        date_window_days: Date tolerance for client records
    """
    state = state if state is not None else ov.empty_state()

    transfers = match_own_transfers(
        transactions,
        own_wallets,
        time_window=timedelta(hours=float(time_window_hours)),
        amount_tolerance=amount_tolerance,
        manual_links=ov.manual_transfer_links(state),
        excluded_row_ids=ov.unlinked_transfer_rows(state),
    )

    record_result = match_client_records(
        client_records or [],
        transfers.transactions,
        period_start=parse_date(period_start) if period_start else None,
        period_end=parse_date(period_end) if period_end else None,
        date_window_days=int(date_window_days),
        tolerance_pct=Decimal(str(record_tolerance_pct)),
        manual_matches=ov.manual_record_matches(state),
        excluded_record_ids=ov.unmatched_record_ids(state),
        file_wallets=file_wallets,
    )

    # Incoming rows explained by an exchange withdrawal are the client's own funds, not customer payments
    exchange_rows = {m.row_id for m in record_result.matches if m.file_type == "exchange"}

    payments = [
        tx for tx in transfers.transactions
        if tx.get("direction") == "In" and tx.get("transaction_type") != "TRANSFER"
        and row_id(tx) not in exchange_rows
    ]
    invoice_result = match_invoices_to_payments(
        list(invoices) + list(receivables or []),
        payments,
        tolerance_pct=invoice_tolerance_pct,
        manual_matches=ov.manual_invoice_matches(state),
        excluded_row_ids=ov.unmatched_payment_rows(state),
    )

    balances = []
    for spec in balance_inputs:
        start, end = parse_date(spec["period_start"]), parse_date(spec["period_end"])
        opening, opening_src = spec.get("opening_balance"), spec.get("opening_source", "")
        closing, closing_src = spec.get("actual_closing"), spec.get("actual_source", "")
        manual_open = ov.manual_balance(state, "opening", spec["wallet_address"], start.isoformat())
        manual_close = ov.manual_balance(state, "closing", spec["wallet_address"], end.isoformat())
        if manual_open is not None:
            opening, opening_src = manual_open, "manual entry (see override log)"
        if manual_close is not None:
            closing, closing_src = manual_close, "manual entry (see override log)"
        balances.append(reconcile_balance(
            wallet_address=spec["wallet_address"],
            network=spec["network"],
            transactions=transfers.transactions,
            period_start=start,
            period_end=end,
            opening_balance=None if opening is None else to_decimal(opening),
            actual_closing=None if closing is None else to_decimal(closing),
            opening_source=opening_src,
            actual_source=closing_src,
        ))

    report = ReconciliationReport(
        generated_at=utc_now_iso(),
        settings={
            "own_wallets": list(own_wallets),
            "time_window_hours": float(time_window_hours),
            "invoice_tolerance_pct": str(invoice_tolerance_pct),
            "amount_tolerance": str(amount_tolerance),
            "client_record_tolerance_pct": str(record_tolerance_pct),
            "client_record_date_window_days": int(date_window_days),
        },
        transfers=transfers,
        invoices=invoice_result,
        balances=balances,
        override_log=list(state.get("log", [])),
        active_overrides=[o for o in state.get("overrides", []) if o.get("active")],
        records=record_result,
    )
    report.exceptions = _collect_exceptions(report, state)
    return report


def _collect_exceptions(report: ReconciliationReport, state: dict) -> list[ExceptionItem]:
    items: list[ExceptionItem] = []

    def add(eid, category, reference, date, asset, amount, detail):
        items.append(ExceptionItem(eid, category, reference, date, asset, amount, detail,
                                   ov.note_for(state, eid)))

    for leg in report.transfers.unmatched_legs:
        tx = leg.tx
        add(f"transfer:{leg.row_id}", f"Unmatched transfer leg ({leg.side})", tx.get("tx_hash", ""),
            tx.get("block_date", ""), tx.get("asset", ""), tx.get("amount_decimal", ""),
            f"{leg.reason}. Wallet {tx.get('wallet_address', '')}")

    for m in report.transfers.matches:
        if m.method == "amount_and_time":
            add(f"transfer_review:{m.out_row_id}", "Transfer matched by amount and time (review)",
                f"{m.out_tx_hash} -> {m.in_tx_hash}", m.out_datetime[:10], m.asset,
                fmt_amount(m.amount_sent),
                "Different txids; confirm both sides are your own wallets or unlink")

    for p in report.invoices.unmatched_payments:
        tx = p.tx
        add(f"payment:{p.row_id}", "Incoming payment not matched to an invoice", tx.get("tx_hash", ""),
            tx.get("block_date", ""), tx.get("asset", ""), tx.get("amount_decimal", ""),
            f"{p.reason}. From {tx.get('from_address', '')} to wallet {tx.get('wallet_address', '')}")

    for m in report.invoices.invoice_matches:
        if m.status != PAID:
            add(f"invoice:{m.invoice_id}", f"Invoice {m.status}", m.invoice_number, m.invoice_date,
                m.asset, fmt_amount(m.invoice_amount),
                f"Received {fmt_amount(m.amount_received)}, difference {fmt_amount(m.difference)}")

    for u in report.records.unmatched_records:
        rec = u.record
        add(f"client_record:{rec['record_id']}", f"Client {rec.get('file_type', '')} line not matched on-chain",
            rec.get("tx_hash") or rec.get("reference") or f"line {rec.get('line')}", rec["date"][:10],
            rec.get("asset", ""), rec.get("amount", ""),
            f"{u.reason}. File {rec.get('file_name', rec.get('file_id', ''))}, line {rec.get('line')}"
            + (f": {rec['description']}" if rec.get("description") else ""))

    for m in report.records.matches:
        if not m.amount_ok:
            add(f"client_amount:{m.record_id}", "Client record amount differs from chain", m.tx_hash,
                m.record_date, m.asset, fmt_amount(m.difference),
                f"Client {fmt_amount(m.record_amount)} vs chain {fmt_amount(m.chain_amount)}")

    for tx in report.records.chain_not_in_ledger:
        add(f"not_in_ledger:{row_id(tx)}", "On-chain transaction missing from client ledger",
            tx.get("tx_hash", ""), tx.get("block_date", ""), tx.get("asset", ""), tx.get("amount_decimal", ""),
            f"{'Received in' if tx.get('direction') == 'In' else 'Sent from'} wallet {tx.get('wallet_address', '')}")

    for b in report.balances:
        eid = f"balance:{b.network}:{b.wallet_address.lower()}:{b.period_start}:{b.period_end}"
        if b.difference is None:
            missing = [n for n, v in (("opening", b.opening_balance), ("closing", b.actual_closing)) if v is None]
            add(eid, "Balance not reconciled (balance unavailable)", b.wallet_address, b.period_end,
                b.asset, "", f"Missing {' and '.join(missing)} balance; enter it manually")
        elif b.difference != 0:
            add(eid, "Balance difference", b.wallet_address, b.period_end, b.asset,
                fmt_amount(b.difference),
                f"Actual {fmt_amount(b.actual_closing)} vs expected {fmt_amount(b.expected_closing)}")
    return items


# =============================================================================
# Export
# =============================================================================

def _sections(report: ReconciliationReport) -> list[tuple[str, list[str], list[list]]]:
    """(title, header, rows) for each report section, shared by CSV and Excel."""
    a = fmt_amount
    s = report.summary()
    summary_rows = [
        ["Generated at (UTC)", report.generated_at],
        ["Own wallets", ", ".join(report.settings["own_wallets"])],
        ["Transfer time window (hours)", report.settings["time_window_hours"]],
        ["Invoice amount tolerance (%)", report.settings["invoice_tolerance_pct"]],
        ["Matched transfers", s["matched_transfers"]],
        ["Unmatched transfer legs", s["unmatched_transfer_legs"]],
        ["Network fee expense lines", s["network_fee_lines"]],
        ["Invoices checked", s["invoices"]],
    ]
    summary_rows += [[f"Invoices {k}", v] for k, v in sorted(s["invoice_statuses"].items())]
    summary_rows += [
        ["Unmatched incoming payments", s["unmatched_payments"]],
        ["Client records matched to chain", s["client_records_matched"]],
        ["Client records matched with amount differences", s["client_records_amount_differences"]],
        ["Client records not found on-chain", s["client_records_unmatched"]],
        ["Client exchange lines not expected on-chain (trades etc.)", s["client_records_off_chain"]],
        ["Client records outside the period (skipped)", s["client_records_outside_period"]],
        ["On-chain transactions missing from client ledger", s["chain_rows_not_in_ledger"]],
        ["Wallet balances checked", s["balances_checked"]],
        ["Wallet balances reconciled (difference = 0)", s["balances_reconciled"]],
        ["Exceptions", s["exceptions"]],
        ["Active manual overrides", s["manual_overrides_active"]],
    ]

    transfers = [[
        m.transfer_id, m.method, m.network, m.asset, m.out_wallet, m.out_tx_hash, m.out_datetime,
        a(m.amount_sent), m.in_wallet, m.in_tx_hash, m.in_datetime, a(m.amount_received),
        a(m.fee), m.fee_asset,
    ] for m in report.transfers.matches]

    unmatched_legs = [[
        u.side, u.tx.get("block_date", ""), u.tx.get("wallet_address", ""), u.tx.get("tx_hash", ""),
        u.tx.get("from_address", ""), u.tx.get("to_address", ""), u.tx.get("asset", ""),
        u.tx.get("amount_decimal", ""), u.reason,
    ] for u in report.transfers.unmatched_legs]

    fees = [[
        f.transfer_id, f.block_date, f.block_time, f.wallet_address, f.tx_hash, f.network, f.asset,
        a(f.amount), "" if f.usd_value is None else f"{f.usd_value:.2f}",
        "" if f.cad_value is None else f"{f.cad_value:.2f}", "EXPENSE", f.description,
    ] for f in report.transfers.fee_lines]

    invoices = [[
        m.source, m.invoice_number, m.customer, m.invoice_date, m.asset, a(m.invoice_amount), a(m.amount_received),
        a(m.difference), m.status, m.recorded_status, m.method,
        "yes" if m.payer_verified else "no", "; ".join(m.payment_tx_hashes),
    ] for m in report.invoices.invoice_matches]

    payments = [[
        u.tx.get("block_date", ""), u.tx.get("wallet_address", ""), u.tx.get("tx_hash", ""),
        u.tx.get("from_address", ""), u.tx.get("asset", ""), u.tx.get("amount_decimal", ""), u.reason,
    ] for u in report.invoices.unmatched_payments]

    def opt(value):
        return "" if value is None else a(value)

    balances = [[
        b.wallet_address, b.network, b.asset, b.period_start, b.period_end,
        opt(b.opening_balance), b.opening_source, a(b.inflows), a(b.outflows), a(b.fees),
        opt(b.expected_closing), opt(b.actual_closing), b.actual_source, opt(b.difference),
        "yes" if b.reconciled else "no",
    ] for b in report.balances]

    record_matches = [[
        m.file_type, m.record_id, m.method, m.asset, m.record_date, a(m.record_amount), m.chain_date,
        a(m.chain_amount), a(m.difference), "yes" if m.amount_ok else "no", m.tx_hash,
    ] for m in report.records.matches]

    unmatched_records = [[
        u.record.get("file_type", ""), u.record.get("file_name", u.record.get("file_id", "")),
        u.record.get("line", ""), u.record["date"][:10], u.record.get("asset", ""), u.record.get("amount", ""),
        u.record.get("chain_direction", ""), u.record.get("tx_hash", ""), u.record.get("reference", ""),
        u.record.get("description", ""), u.reason,
    ] for u in report.records.unmatched_records]

    not_in_ledger = [[
        tx.get("block_date", ""), tx.get("wallet_address", ""), tx.get("tx_hash", ""), tx.get("direction", ""),
        tx.get("asset", ""), tx.get("amount_decimal", ""), tx.get("tx_fee_native", ""),
    ] for tx in report.records.chain_not_in_ledger]

    exceptions = [[
        e.exception_id, e.category, e.reference, e.date, e.asset, e.amount, e.detail, e.note,
    ] for e in report.exceptions]

    log = [[
        entry.get("timestamp", ""), entry.get("action", ""), entry.get("override_id", ""),
        entry.get("details", ""), entry.get("note", ""),
    ] for entry in report.override_log]

    return [
        ("Summary", ["Item", "Value"], summary_rows),
        ("Matched Transfers", [
            "Transfer ID", "Match Method", "Network", "Asset", "From Wallet", "Outgoing Tx Hash",
            "Sent At (UTC)", "Amount Sent", "To Wallet", "Incoming Tx Hash", "Received At (UTC)",
            "Amount Received", "Network Fee", "Fee Asset",
        ], transfers),
        ("Unmatched Transfer Legs", [
            "Side", "Date", "Wallet", "Tx Hash", "From Address", "To Address", "Asset", "Amount", "Reason",
        ], unmatched_legs),
        ("Network Fees", [
            "Transfer ID", "Date", "Time", "Wallet", "Tx Hash", "Network", "Asset", "Fee Amount",
            "Fee (USD)", "Fee (CAD)", "Transaction Type", "Description",
        ], fees),
        ("Invoice Matching", [
            "Source", "Invoice Number", "Customer", "Invoice Date", "Asset", "Invoice Amount", "Amount Received",
            "Difference", "Matched Status", "Status In Invoice List", "Match Method",
            "Payer Verified", "Payment Tx Hashes",
        ], invoices),
        ("Unmatched Payments", [
            "Date", "Wallet", "Tx Hash", "From Address", "Asset", "Amount", "Reason",
        ], payments),
        ("Client Records Matched", [
            "File Type", "Record ID", "Match Method", "Asset", "Client Date", "Client Amount", "Chain Date",
            "Chain Amount", "Difference (Chain - Client)", "Amount Within Tolerance", "Tx Hash",
        ], record_matches),
        ("Client Records Unmatched", [
            "File Type", "File", "Line", "Date", "Asset", "Amount", "Expected Direction", "Tx Hash",
            "Reference", "Description", "Reason",
        ], unmatched_records),
        ("On-Chain Not In Ledger", [
            "Date", "Wallet", "Tx Hash", "Direction", "Asset", "Amount", "Fee",
        ], not_in_ledger),
        ("Balance Reconciliation", [
            "Wallet", "Network", "Asset", "Period Start", "Period End", "Opening Balance",
            "Opening Source", "Inflows", "Outflows", "Network Fees", "Expected Closing",
            "Actual Closing", "Actual Source", "Difference (Actual - Expected)", "Reconciled",
        ], balances),
        ("Exceptions", [
            "Exception ID", "Category", "Reference", "Date", "Asset", "Amount", "Detail", "Note",
        ], exceptions),
        ("Override Log", ["Timestamp (UTC)", "Action", "Override ID", "Details", "Note"], log),
    ]


def export_report_csv(report: ReconciliationReport, output_path: Optional[Path] = None) -> str:
    """Export the report as one CSV with titled sections. Returns the CSV text."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["CHAINLEDGER RECONCILIATION REPORT"])
    for title, header, rows in _sections(report):
        writer.writerow([])
        writer.writerow([title.upper()])
        writer.writerow(header)
        if rows:
            writer.writerows(rows)
        else:
            writer.writerow(["(none)"])
    content = output.getvalue()
    if output_path:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            f.write(content)
    return content


def export_report_xlsx(report: ReconciliationReport, output_path: Optional[Path] = None) -> bytes:
    """Export the report as an Excel workbook, one sheet per section."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    header_fill = PatternFill(start_color="1a1a2e", end_color="1a1a2e", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    flag_fill = PatternFill(start_color="fce4ec", end_color="fce4ec", fill_type="solid")

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, header, rows in _sections(report):
        ws = wb.create_sheet(title=title[:31])
        for col, name in enumerate(header, 1):
            cell = ws.cell(row=1, column=col, value=name)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", wrap_text=True)
        for r, row in enumerate(rows, 2):
            for c, value in enumerate(row, 1):
                ws.cell(row=r, column=c, value=value)
            if title == "Balance Reconciliation" and row[-1] == "no":
                for c in range(1, len(row) + 1):
                    ws.cell(row=r, column=c).fill = flag_fill
        for col in range(1, len(header) + 1):
            longest = max([len(str(header[col - 1]))] + [len(str(row[col - 1])) for row in rows[:200]])
            ws.column_dimensions[get_column_letter(col)].width = min(max(12, longest + 2), 60)
        ws.freeze_panes = "A2"

    buffer = io.BytesIO()
    wb.save(buffer)
    data = buffer.getvalue()
    if output_path:
        with open(output_path, "wb") as f:
            f.write(data)
    return data
