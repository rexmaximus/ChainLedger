"""
Reconciliation Service

Fetches data for the user's wallets and runs the reconciliation report.

The fetched transactions and chain balances are cached in
data/reconciliation/last_run.json so overrides and notes can be applied and the
report regenerated without fetching from the chain again.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from src.classification import classify_transactions

from .common import fmt_amount
from .report import ReconciliationReport, build_report, export_report_csv, export_report_xlsx

logger = logging.getLogger(__name__)


def fetch_inputs(
    network: str,
    wallet_addresses: list[str],
    api_key: Optional[str],
    period_start: date,
    period_end: date,
) -> dict:
    """
    Fetch transactions and opening/closing balances from the chain.

    Balances the API cannot provide (e.g. historical Bitcoin balances) are left
    as None with the reason in the *_source field.
    """
    from src.wallet import SUPPORTED_NETWORKS, HistoricalBalanceUnavailable, LedgerGenerator

    start_dt = datetime.combine(period_start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(period_end, time.max, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    transactions: list[dict] = []
    balances: list[dict] = []
    for address in wallet_addresses:
        generator = LedgerGenerator(network=network, api_key=api_key)
        try:
            rows = generator.generate_ledger(address, from_date=start_dt, to_date=end_dt)
            for row in rows:
                tx = asdict(row)
                tx["wallet_address"] = address
                transactions.append(tx)

            spec = {
                "wallet_address": address,
                "network": SUPPORTED_NETWORKS[network]["name"],
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            }
            # Opening = balance just before the period starts
            try:
                opening = generator.get_balance(address, start_dt - timedelta(seconds=1))
                spec["opening_balance"] = fmt_amount(opening)
                spec["opening_source"] = "chain API"
            except HistoricalBalanceUnavailable as e:
                spec["opening_balance"] = None
                spec["opening_source"] = f"unavailable: {e}"
            # Closing = balance at the end of the period (current if the period is not over)
            try:
                closing = generator.get_balance(address, None if end_dt >= now else end_dt)
                spec["actual_closing"] = fmt_amount(closing)
                spec["actual_source"] = "chain API (current balance)" if end_dt >= now else "chain API"
            except HistoricalBalanceUnavailable as e:
                spec["actual_closing"] = None
                spec["actual_source"] = f"unavailable: {e}"
            balances.append(spec)
        finally:
            generator.close()

    return {
        "fetched_at": now.isoformat(),
        "network": network,
        "wallet_addresses": wallet_addresses,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "transactions": transactions,
        "balances": balances,
    }


def add_fee_fiat_values(report: ReconciliationReport) -> None:
    """Value network fee lines in USD/CAD with CoinGecko historical prices."""
    from src.exports import get_price_oracle

    oracle = get_price_oracle()
    for line in report.transfers.fee_lines:
        try:
            prices = oracle.get_historical_price(
                line.asset, datetime.strptime(line.block_date, "%Y-%m-%d"), ["usd", "cad"]
            )
            line.usd_value = float(line.amount) * prices.get("usd", 0)
            line.cad_value = float(line.amount) * prices.get("cad", 0)
        except Exception as e:
            logger.warning(f"Failed to value fee {line.tx_hash[:16]}: {e}")


class ReconciliationService:
    """Runs reconciliation and writes report files and the cache."""

    def __init__(self, data_dir: Path, exports_dir: Path):
        self.cache_dir = Path(data_dir) / "reconciliation"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.cache_dir / "last_run.json"
        self.exports_dir = Path(exports_dir)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def save_cache(self, inputs: dict, settings: dict) -> None:
        with open(self.cache_path, "w") as f:
            json.dump({"inputs": inputs, "settings": settings}, f, indent=2)

    def load_cache(self) -> Optional[dict]:
        if not self.cache_path.exists():
            return None
        try:
            with open(self.cache_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def run(
        self,
        inputs: dict,
        own_wallets: list[str],
        invoices: list[dict],
        state: dict,
        classification_overrides: Optional[dict[str, str]] = None,
        time_window_hours: float = 2,
        invoice_tolerance_pct: Decimal = Decimal("0.5"),
        include_prices: bool = False,
    ) -> tuple[ReconciliationReport, dict]:
        """Classify, reconcile and export. Returns the report and file names."""
        classified = classify_transactions(
            inputs["transactions"], own_wallets, classification_overrides or {}
        )
        report = build_report(
            transactions=classified,
            own_wallets=own_wallets,
            invoices=invoices,
            balance_inputs=inputs["balances"],
            state=state,
            time_window_hours=time_window_hours,
            invoice_tolerance_pct=invoice_tolerance_pct,
        )
        if include_prices:
            add_fee_fiat_values(report)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        files = {
            "csv": f"chainledger_reconciliation_{stamp}.csv",
            "xlsx": f"chainledger_reconciliation_{stamp}.xlsx",
        }
        export_report_csv(report, self.exports_dir / files["csv"])
        export_report_xlsx(report, self.exports_dir / files["xlsx"])
        return report, files
