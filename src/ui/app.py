"""
ChainLedger Web UI

Flask application for the ChainLedger crypto accounting tool.
Provides endpoints for:
- Dashboard with stats
- Accounting exports
- Invoice creation and management
- Reconciliation (transfer matching, invoice matching, balance checks)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from flask import Flask, render_template, jsonify, request, send_file
from flask_cors import CORS

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.wallet import LedgerGenerator, validate_ethereum_address, validate_bitcoin_wallet, parse_extended_key, InvalidExtendedKey, SUPPORTED_NETWORKS
from src.exports import ExportService, get_price_oracle
from src.invoices import InvoiceGenerator
from src.imports import (
    DATE_FORMATS, FILE_TYPES, MAPPING_FIELDS, ImportErrorWithRows, apply_mapping, parse_file, suggest_mapping,
)
from src.reconciliation import overrides as recon_overrides
from src.reconciliation.service import ReconciliationService, fetch_inputs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class Invoice:
    """Invoice record."""
    id: str
    invoice_number: str
    created_at: str
    sender_name: str
    sender_email: str
    sender_wallet: str
    recipient_name: str
    recipient_email: str
    recipient_wallet: Optional[str] = None
    crypto_amount: float = 0.0
    token_type: str = "ETH"
    usd_value: Optional[float] = None
    cad_value: Optional[float] = None
    work_description: str = ""
    status: str = "unpaid"
    paid_at: Optional[str] = None
    paid_tx_hash: Optional[str] = None
    pdf_filename: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Export:
    """Export record."""
    id: str
    created_at: str
    wallet_addresses: list[str]
    blockchain: str
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    transaction_count: int = 0
    gross_revenue_usd: Optional[float] = None
    gross_revenue_cad: Optional[float] = None
    total_expenses_usd: Optional[float] = None
    total_expenses_cad: Optional[float] = None
    net_cash_flow_usd: Optional[float] = None
    net_cash_flow_cad: Optional[float] = None
    filename: str = ""
    format: str = "csv"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SenderProfile:
    """Saved sender profile."""
    name: str
    email: str
    wallet: str
    business_name: str = ""
    address_line1: str = ""
    address_line2: str = ""
    tax_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# =============================================================================
# Metadata Manager
# =============================================================================

class MetadataManager:
    """Manages local JSON storage for invoices, exports, and settings."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.metadata_path = data_dir / "metadata.json"
        self.invoices_dir = data_dir / "invoices"
        self.exports_dir = data_dir / "exports"
        self.client_files_dir = data_dir / "client_files"

        # Ensure directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.invoices_dir.mkdir(exist_ok=True)
        self.exports_dir.mkdir(exist_ok=True)
        self.client_files_dir.mkdir(exist_ok=True)

        self._data = self._load()

    def _load(self) -> dict:
        if self.metadata_path.exists():
            try:
                with open(self.metadata_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        return {
            "invoices": [],
            "exports": [],
            "sender_profile": None,
            "settings": {
                "default_currency": "USD",
                "invoice_prefix": "INV",
                "next_invoice_number": 1,
            },
            "wallets": [],
            "classification_overrides": {},
        }

    def _save(self) -> None:
        with open(self.metadata_path, "w") as f:
            json.dump(self._data, f, indent=2)

    # Invoice methods
    def get_next_invoice_number(self) -> str:
        prefix = self._data["settings"]["invoice_prefix"]
        number = self._data["settings"]["next_invoice_number"]
        self._data["settings"]["next_invoice_number"] = number + 1
        self._save()
        return f"{prefix}-{number:05d}"

    def add_invoice(self, invoice: Invoice) -> None:
        self._data["invoices"].append(invoice.to_dict())
        self._save()

    def get_invoices(self, limit: int = 50, status: Optional[str] = None) -> list[Invoice]:
        invoices = [Invoice(**d) for d in self._data["invoices"]]
        if status:
            invoices = [i for i in invoices if i.status == status]
        invoices.sort(key=lambda i: i.created_at, reverse=True)
        return invoices[:limit]

    def get_invoice(self, invoice_id: str) -> Optional[Invoice]:
        for d in self._data["invoices"]:
            if d["id"] == invoice_id:
                return Invoice(**d)
        return None

    def update_invoice_status(self, invoice_id: str, status: str, tx_hash: Optional[str] = None) -> bool:
        for d in self._data["invoices"]:
            if d["id"] == invoice_id:
                d["status"] = status
                if status == "paid":
                    d["paid_at"] = datetime.utcnow().isoformat()
                    if tx_hash:
                        d["paid_tx_hash"] = tx_hash
                self._save()
                return True
        return False

    def delete_invoice(self, invoice_id: str) -> Optional[str]:
        for i, d in enumerate(self._data["invoices"]):
            if d["id"] == invoice_id:
                pdf_filename = d.get("pdf_filename", "")
                del self._data["invoices"][i]
                self._save()
                return pdf_filename
        return None

    # Export methods
    def add_export(self, export: Export) -> None:
        self._data["exports"].append(export.to_dict())
        self._save()

    def get_exports(self, limit: int = 50) -> list[Export]:
        exports = [Export(**d) for d in self._data["exports"]]
        exports.sort(key=lambda e: e.created_at, reverse=True)
        return exports[:limit]

    # Sender profile
    def get_sender_profile(self) -> Optional[SenderProfile]:
        if self._data["sender_profile"]:
            return SenderProfile(**self._data["sender_profile"])
        return None

    def save_sender_profile(self, profile: SenderProfile) -> None:
        self._data["sender_profile"] = profile.to_dict()
        self._save()

    # Wallets
    def get_saved_wallets(self) -> list[dict]:
        return self._data.get("wallets", [])

    def save_wallet(self, address: str, name: str, blockchain: str, wallet_type: str = "self-custodial") -> None:
        """
        Save a wallet address.

        Args:
            address: The wallet address
            name: Human-readable name
            blockchain: Network (ethereum, bitcoin)
            wallet_type: Either "self-custodial" (you control keys) or "exchange" (custodial)
        """
        wallets = self._data.get("wallets", [])
        for w in wallets:
            if w["address"].lower() == address.lower():
                w["name"] = name
                w["blockchain"] = blockchain
                w["wallet_type"] = wallet_type
                self._save()
                return
        wallets.append({
            "address": address,
            "name": name,
            "blockchain": blockchain,
            "wallet_type": wallet_type,
            "added_at": datetime.utcnow().isoformat(),
        })
        self._data["wallets"] = wallets
        self._save()

    def get_self_custodial_addresses(self, blockchain: Optional[str] = None) -> set[str]:
        """
        Get all self-custodial wallet addresses for transfer detection.

        Only self-custodial wallets are used for automatic transfer detection
        because you control the private keys and thus truly own those addresses.
        Exchange/custodial wallets cannot be auto-detected as transfers.

        Args:
            blockchain: Optional filter by blockchain

        Returns:
            Set of lowercase wallet addresses
        """
        wallets = self._data.get("wallets", [])
        addresses = set()
        for w in wallets:
            # Default to self-custodial for backwards compatibility
            wtype = w.get("wallet_type", "self-custodial")
            if wtype == "self-custodial":
                if blockchain is None or w.get("blockchain", "").lower() == blockchain.lower():
                    addresses.add(w["address"].lower())
        return addresses

    def remove_wallet(self, address: str) -> bool:
        wallets = self._data.get("wallets", [])
        original_len = len(wallets)
        wallets = [w for w in wallets if w["address"].lower() != address.lower()]
        if len(wallets) < original_len:
            self._data["wallets"] = wallets
            self._save()
            return True
        return False

    # Classification overrides
    def get_classification_overrides(self) -> dict[str, str]:
        return self._data.get("classification_overrides", {})

    def set_classification_override(self, tx_hash: str, tx_type: str) -> None:
        if "classification_overrides" not in self._data:
            self._data["classification_overrides"] = {}
        self._data["classification_overrides"][tx_hash] = tx_type.upper()
        self._save()

    def remove_classification_override(self, tx_hash: str) -> bool:
        overrides = self._data.get("classification_overrides", {})
        if tx_hash in overrides:
            del overrides[tx_hash]
            self._save()
            return True
        return False

    # Reconciliation state (overrides, exception notes, override log)
    def get_reconciliation_state(self) -> dict:
        state = self._data.setdefault("reconciliation", recon_overrides.empty_state())
        for key, default in recon_overrides.empty_state().items():
            state.setdefault(key, default)
        return state

    def save_reconciliation_state(self) -> None:
        self._save()

    def set_last_reconciliation(self, info: dict) -> None:
        self.get_reconciliation_state()["last_run"] = info
        self._save()

    # Client files (uploaded CSV/Excel from a client)
    def get_client_files(self) -> list[dict]:
        return list(self._data.setdefault("client_files", []))

    def get_client_file(self, file_id: str) -> Optional[dict]:
        return next((f for f in self._data.setdefault("client_files", []) if f["id"] == file_id), None)

    def add_client_file(self, record: dict) -> None:
        self._data.setdefault("client_files", []).append(record)
        self._save()

    def update_client_file(self, file_id: str, **changes) -> Optional[dict]:
        record = self.get_client_file(file_id)
        if record is not None:
            record.update(changes)
            self._save()
        return record

    def delete_client_file(self, file_id: str) -> Optional[dict]:
        files = self._data.setdefault("client_files", [])
        record = self.get_client_file(file_id)
        if record is not None:
            files.remove(record)
            self._save()
        return record

    def get_invoice_dicts(self) -> list[dict]:
        return [dict(d) for d in self._data.get("invoices", [])]

    # Dashboard stats
    def get_dashboard_stats(self) -> dict:
        invoices = self.get_invoices(limit=1000)
        exports = self.get_exports(limit=1000)
        unpaid = [i for i in invoices if i.status == "unpaid"]
        paid = [i for i in invoices if i.status == "paid"]

        return {
            "total_invoices": len(invoices),
            "unpaid_invoices": len(unpaid),
            "paid_invoices": len(paid),
            "total_unpaid_usd": sum(i.usd_value or 0 for i in unpaid),
            "total_paid_usd": sum(i.usd_value or 0 for i in paid),
            "total_exports": len(exports),
            "recent_invoices": [i.to_dict() for i in invoices[:5]],
            "recent_exports": [e.to_dict() for e in exports[:5]],
        }


# =============================================================================
# Flask App Factory
# =============================================================================

def _wallet_error(blockchain: str, address: str) -> Optional[str]:
    """Why `address` is not usable on `blockchain`, or None if it is."""
    if blockchain == "ethereum":
        return None if validate_ethereum_address(address) else f"Invalid Ethereum address: {address}"
    if blockchain == "bitcoin":
        if address[:4].lower() in {"xpub", "ypub", "zpub", "tpub", "upub", "vpub", "xprv", "yprv", "zprv"}:
            try:
                parse_extended_key(address)
                return None
            except (InvalidExtendedKey, ValueError) as e:
                return f"Invalid extended public key: {e}"
        if validate_bitcoin_wallet(address):
            return None
        return f"Invalid Bitcoin address or extended public key (xpub/ypub/zpub): {address}"
    return None


def create_app(data_dir: Optional[Path] = None) -> Flask:
    """Create and configure the Flask application."""

    if data_dir is None:
        data_dir = Path(__file__).parent.parent.parent / "data"

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    CORS(app)

    # Initialize metadata manager
    metadata = MetadataManager(data_dir)
    app.config["metadata_manager"] = metadata
    app.config["invoices_dir"] = str(metadata.invoices_dir)
    app.config["exports_dir"] = str(metadata.exports_dir)

    # -------------------------------------------------------------------------
    # Dashboard Routes
    # -------------------------------------------------------------------------

    @app.route("/")
    def dashboard():
        stats = metadata.get_dashboard_stats()
        wallets = metadata.get_saved_wallets()
        client_files = metadata.get_client_files()
        recon = {
            "last_run": metadata.get_reconciliation_state().get("last_run"),
            "client_file_count": len(client_files),
            "client_files_ready": sum(1 for f in client_files if f.get("status") == "ready"),
        }
        return render_template("index.html", stats=stats, wallets=wallets, recon=recon)

    @app.route("/api/stats")
    def get_stats():
        return jsonify(metadata.get_dashboard_stats())

    @app.route("/api/wallets", methods=["GET"])
    def list_wallets():
        return jsonify(metadata.get_saved_wallets())

    @app.route("/api/wallets", methods=["POST"])
    def add_wallet():
        data = request.get_json()
        address = data.get("address", "").strip()
        name = data.get("name", "").strip()
        blockchain = data.get("blockchain", "ethereum").lower()
        wallet_type = data.get("wallet_type", "self-custodial").lower()

        if not address or not name:
            return jsonify({"error": "Address and name required"}), 400

        if wallet_type not in ("self-custodial", "exchange"):
            wallet_type = "self-custodial"

        error = _wallet_error(blockchain, address)
        if error:
            return jsonify({"error": error}), 400

        metadata.save_wallet(address, name, blockchain, wallet_type)
        return jsonify({"success": True})

    @app.route("/api/wallets/<address>", methods=["DELETE"])
    def delete_wallet(address: str):
        if metadata.remove_wallet(address):
            return jsonify({"success": True})
        return jsonify({"error": "Wallet not found"}), 404

    # -------------------------------------------------------------------------
    # Accounting Routes
    # -------------------------------------------------------------------------

    @app.route("/accounting")
    def accounting_page():
        wallets = metadata.get_saved_wallets()
        return render_template("accounting.html", wallets=wallets, networks=SUPPORTED_NETWORKS)

    @app.route("/accounting/api/generate", methods=["POST"])
    def generate_export():
        try:
            data = request.get_json()

            wallet_input = data.get("wallet_addresses", "").strip()
            if not wallet_input:
                return jsonify({"success": False, "error": "Wallet address required"}), 400

            wallet_addresses = [w.strip() for w in wallet_input.split(",") if w.strip()]
            blockchain = data.get("blockchain", "ethereum").lower()
            api_key = data.get("api_key", "").strip() or None

            # Validate
            if SUPPORTED_NETWORKS.get(blockchain, {}).get("requires_api_key") and not api_key:
                return jsonify({"success": False, "error": "API key required"}), 400

            for addr in wallet_addresses:
                error = _wallet_error(blockchain, addr)
                if error:
                    return jsonify({"success": False, "error": error}), 400

            # Date range
            from_date = None
            to_date = None
            if data.get("time_scope") == "range":
                if data.get("from_date"):
                    from_date = datetime.fromisoformat(data["from_date"]).replace(tzinfo=timezone.utc)
                if data.get("to_date"):
                    to_date = datetime.fromisoformat(data["to_date"]).replace(tzinfo=timezone.utc)

            output_format = data.get("output_format", "csv").lower()
            include_prices = data.get("include_prices", True)

            # Generate export
            export_service = ExportService(output_dir=metadata.exports_dir)
            overrides = metadata.get_classification_overrides()

            # Get ALL self-custodial wallet addresses for transfer detection
            # This allows automatic detection of transfers between any of your wallets
            all_self_custodial = metadata.get_self_custodial_addresses(blockchain)
            # Also include the queried addresses (in case they're not saved yet)
            classification_addresses = all_self_custodial | {a.lower() for a in wallet_addresses}

            result = export_service.generate_export(
                wallet_addresses=wallet_addresses,
                blockchain=blockchain,
                api_key=api_key,
                from_date=from_date,
                to_date=to_date,
                output_format=output_format,
                include_prices=include_prices,
                classification_overrides=overrides,
                classification_addresses=list(classification_addresses),
            )

            if result.get("transaction_count", 0) > 0:
                export_record = Export(
                    id=str(uuid.uuid4()),
                    created_at=datetime.utcnow().isoformat(),
                    wallet_addresses=wallet_addresses,
                    blockchain=blockchain,
                    from_date=from_date.isoformat() if from_date else None,
                    to_date=to_date.isoformat() if to_date else None,
                    transaction_count=result.get("transaction_count", 0),
                    gross_revenue_usd=result.get("totals", {}).get("gross_revenue_usd"),
                    gross_revenue_cad=result.get("totals", {}).get("gross_revenue_cad"),
                    total_expenses_usd=result.get("totals", {}).get("total_expenses_usd"),
                    total_expenses_cad=result.get("totals", {}).get("total_expenses_cad"),
                    net_cash_flow_usd=result.get("totals", {}).get("net_cash_flow_usd"),
                    net_cash_flow_cad=result.get("totals", {}).get("net_cash_flow_cad"),
                    filename=result.get("filename", ""),
                    format=output_format,
                )
                metadata.add_export(export_record)

            return jsonify({
                "success": True,
                "filename": result.get("filename"),
                "download_url": f"/accounting/api/download/{result.get('filename')}",
                "transaction_count": result.get("transaction_count", 0),
                "totals": result.get("totals"),
            })

        except Exception as e:
            logger.exception("Export failed")
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/accounting/api/download/<filename>")
    def download_export(filename: str):
        if "/" in filename or "\\" in filename or ".." in filename:
            return jsonify({"error": "Invalid filename"}), 400

        filepath = os.path.join(metadata.exports_dir, filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "File not found"}), 404

        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if filename.endswith(".xlsx") else "text/csv"
        return send_file(filepath, mimetype=mimetype, as_attachment=True, download_name=filename)

    @app.route("/accounting/api/exports")
    def list_exports():
        return jsonify([e.to_dict() for e in metadata.get_exports(limit=20)])

    @app.route("/accounting/api/overrides")
    def list_overrides():
        return jsonify(metadata.get_classification_overrides())

    @app.route("/accounting/api/overrides/<tx_hash>", methods=["PUT"])
    def set_override(tx_hash: str):
        data = request.get_json()
        tx_type = data.get("transaction_type", "").upper()
        if tx_type not in ("INCOME", "EXPENSE", "TRANSFER", "UNKNOWN"):
            return jsonify({"error": "Invalid type"}), 400
        metadata.set_classification_override(tx_hash, tx_type)
        return jsonify({"success": True})

    @app.route("/accounting/api/overrides/<tx_hash>", methods=["DELETE"])
    def remove_override(tx_hash: str):
        if metadata.remove_classification_override(tx_hash):
            return jsonify({"success": True})
        return jsonify({"error": "Not found"}), 404

    # -------------------------------------------------------------------------
    # Reconciliation Routes
    # -------------------------------------------------------------------------

    recon_service = ReconciliationService(data_dir=data_dir, exports_dir=metadata.exports_dir)

    def _load_client_records() -> tuple[list[dict], list[dict], dict[str, str]]:
        """Parse every mapped client file into records (ledger/exchange) and receivables."""
        records, receivables, wallets = [], [], {}
        for f in metadata.get_client_files():
            if f.get("status") != "ready":
                continue
            path = metadata.client_files_dir / f["stored_name"]
            if not path.exists():
                continue
            parsed = parse_file(f["stored_name"], path.read_bytes())
            mapped = apply_mapping(f["id"], f["file_type"], parsed, f.get("mapping", {}))
            for rec in mapped.records:
                rec["file_name"] = f["original_name"]
            if f["file_type"] == "receivables":
                receivables.extend(mapped.records)
            else:
                records.extend(mapped.records)
            if f.get("wallet_address"):
                wallets[f["id"]] = f["wallet_address"]
        return records, receivables, wallets

    def _run_reconciliation(inputs: dict, settings: dict) -> dict:
        blockchain = inputs["network"]
        own_wallets = sorted(
            metadata.get_self_custodial_addresses(blockchain)
            | {a.lower() for a in inputs["wallet_addresses"]}
        )
        client_records, receivables, file_wallets = _load_client_records()
        report, files = recon_service.run(
            inputs=inputs,
            own_wallets=own_wallets,
            invoices=metadata.get_invoice_dicts(),
            state=metadata.get_reconciliation_state(),
            classification_overrides=metadata.get_classification_overrides(),
            time_window_hours=float(settings.get("time_window_hours", 2)),
            invoice_tolerance_pct=Decimal(str(settings.get("invoice_tolerance_pct", "0.5"))),
            include_prices=bool(settings.get("include_prices", False)),
            client_records=client_records,
            receivables=receivables,
            file_wallets=file_wallets,
            record_tolerance_pct=Decimal(str(settings.get("record_tolerance_pct", "0.5"))),
            date_window_days=int(settings.get("date_window_days", 3)),
        )
        result = report.to_dict()
        result["success"] = True
        result["fetched_at"] = inputs.get("fetched_at")
        result["period_start"] = inputs.get("period_start")
        result["period_end"] = inputs.get("period_end")
        result["network"] = blockchain
        result["download_csv"] = f"/accounting/api/download/{files['csv']}"
        result["download_xlsx"] = f"/accounting/api/download/{files['xlsx']}"
        result["open_invoices"] = [
            {"id": i["id"], "invoice_number": i.get("invoice_number", ""),
             "token_type": i.get("token_type", ""), "crypto_amount": i.get("crypto_amount")}
            for i in metadata.get_invoice_dicts() if i.get("status") != "cancelled"
        ] + [
            {"id": r["id"], "invoice_number": f"{r['invoice_number']} (client file)",
             "token_type": r["token_type"], "crypto_amount": r["crypto_amount"]}
            for r in receivables
        ]
        result["client_files_used"] = [
            {"id": f["id"], "name": f["original_name"], "file_type": f["file_type"]}
            for f in metadata.get_client_files() if f.get("status") == "ready"
        ]
        metadata.set_last_reconciliation({
            "generated_at": result["generated_at"],
            "network": blockchain,
            "period_start": result["period_start"],
            "period_end": result["period_end"],
            "summary": result["summary"],
            "download_csv": result["download_csv"],
            "download_xlsx": result["download_xlsx"],
        })
        return result

    @app.route("/reconciliation")
    def reconciliation_page():
        wallets = metadata.get_saved_wallets()
        return render_template("reconciliation.html", wallets=wallets, networks=SUPPORTED_NETWORKS)

    @app.route("/reconciliation/api/run", methods=["POST"])
    def run_reconciliation():
        try:
            data = request.get_json()
            blockchain = data.get("blockchain", "ethereum").lower()
            if blockchain not in SUPPORTED_NETWORKS:
                return jsonify({"success": False, "error": "Unsupported blockchain"}), 400
            api_key = (data.get("api_key") or "").strip() or None
            if SUPPORTED_NETWORKS[blockchain]["requires_api_key"] and not api_key:
                return jsonify({"success": False, "error": "API key required"}), 400

            wallet_addresses = [w.strip() for w in data.get("wallet_addresses", "").split(",") if w.strip()]
            if not wallet_addresses:
                return jsonify({"success": False, "error": "At least one wallet address required"}), 400
            for addr in wallet_addresses:
                error = _wallet_error(blockchain, addr)
                if error:
                    return jsonify({"success": False, "error": error}), 400

            try:
                period_start = date.fromisoformat(data.get("period_start", ""))
                period_end = date.fromisoformat(data.get("period_end", ""))
            except ValueError:
                return jsonify({"success": False, "error": "Period start and end dates required"}), 400
            if period_end < period_start:
                return jsonify({"success": False, "error": "Period end is before period start"}), 400

            settings = {
                "time_window_hours": float(data.get("time_window_hours") or 2),
                "invoice_tolerance_pct": str(Decimal(str(data.get("invoice_tolerance_pct") or "0.5"))),
                "include_prices": bool(data.get("include_prices", False)),
                "record_tolerance_pct": str(Decimal(str(data.get("record_tolerance_pct") or "0.5"))),
                "date_window_days": int(data.get("date_window_days") or 3),
            }
            inputs = fetch_inputs(blockchain, wallet_addresses, api_key, period_start, period_end)
            recon_service.save_cache(inputs, settings)
            return jsonify(_run_reconciliation(inputs, settings))

        except (ValueError, InvalidOperation) as e:
            return jsonify({"success": False, "error": f"Invalid input: {e}"}), 400
        except Exception as e:
            logger.exception("Reconciliation failed")
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/reconciliation/api/rerun", methods=["POST"])
    def rerun_reconciliation():
        """Re-run on the cached fetch, e.g. after an override or note."""
        cache = recon_service.load_cache()
        if not cache:
            return jsonify({"success": False, "error": "No previous run. Run a reconciliation first."}), 400
        try:
            return jsonify(_run_reconciliation(cache["inputs"], cache["settings"]))
        except Exception as e:
            logger.exception("Reconciliation re-run failed")
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/reconciliation/api/overrides", methods=["POST"])
    def add_reconciliation_override():
        data = request.get_json()
        note = (data.get("note") or "").strip()
        if not note:
            return jsonify({"error": "A reason is required for every manual override"}), 400
        try:
            override = recon_overrides.add_override(
                metadata.get_reconciliation_state(), data.get("type", ""), data.get("fields", {}), note
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        metadata.save_reconciliation_state()
        return jsonify({"success": True, "override": override})

    @app.route("/reconciliation/api/overrides/<override_id>", methods=["DELETE"])
    def remove_reconciliation_override(override_id: str):
        data = request.get_json(silent=True) or {}
        if recon_overrides.remove_override(
            metadata.get_reconciliation_state(), override_id, (data.get("note") or "").strip()
        ):
            metadata.save_reconciliation_state()
            return jsonify({"success": True})
        return jsonify({"error": "Override not found"}), 404

    @app.route("/reconciliation/api/notes", methods=["PUT"])
    def set_exception_note():
        data = request.get_json()
        exception_id = (data.get("exception_id") or "").strip()
        if not exception_id:
            return jsonify({"error": "exception_id required"}), 400
        recon_overrides.set_note(metadata.get_reconciliation_state(), exception_id, data.get("note", ""))
        metadata.save_reconciliation_state()
        return jsonify({"success": True})

    @app.route("/reconciliation/api/apply-transfers", methods=["POST"])
    def apply_transfer_classifications():
        """Save matched transfers as TRANSFER classification overrides for the Accounting export."""
        cache = recon_service.load_cache()
        if not cache:
            return jsonify({"success": False, "error": "No previous run"}), 400
        result = _run_reconciliation(cache["inputs"], cache["settings"])
        # Amount-and-time matches are suggestions; only confirmed pairs are applied.
        hashes = sorted({
            h for t in result["transfers"] if t["method"] in ("same_txid", "manual")
            for h in (t["out_tx_hash"], t["in_tx_hash"])
        })
        for tx_hash in hashes:
            metadata.set_classification_override(tx_hash, "TRANSFER")
        if hashes:
            recon_overrides.log_event(
                metadata.get_reconciliation_state(),
                "apply transfer classifications",
                f"{len(hashes)} tx hash(es) set to TRANSFER: {', '.join(hashes)}",
            )
            metadata.save_reconciliation_state()
        return jsonify({"success": True, "count": len(hashes)})

    # -------------------------------------------------------------------------
    # Client File Routes (CSV/Excel supplied by a client)
    # -------------------------------------------------------------------------

    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB uploads

    def _client_file_view(record: dict, parsed=None) -> dict:
        view = {k: v for k, v in record.items() if k != "stored_name"}
        if parsed is not None:
            view["headers"] = parsed.headers
            view["preview"] = [{h: r.get(h, "") for h in parsed.headers} for r in parsed.rows[:5]]
        view["fields"] = {
            name: {"label": label, "required": required}
            for name, (label, required) in MAPPING_FIELDS[record["file_type"]].items()
        }
        return view

    def _read_client_file(record: dict):
        path = metadata.client_files_dir / record["stored_name"]
        return parse_file(record["stored_name"], path.read_bytes())

    @app.route("/client-files")
    def client_files_page():
        return render_template(
            "client_files.html",
            files=metadata.get_client_files(),
            file_types=FILE_TYPES,
            date_formats=DATE_FORMATS,
            wallets=metadata.get_saved_wallets(),
        )

    @app.route("/client-files/api/list")
    def list_client_files():
        return jsonify([_client_file_view(f) for f in metadata.get_client_files()])

    @app.route("/client-files/api/upload", methods=["POST"])
    def upload_client_file():
        upload = request.files.get("file")
        file_type = request.form.get("file_type", "")
        if not upload or not upload.filename:
            return jsonify({"error": "Choose a file to upload"}), 400
        if file_type not in FILE_TYPES:
            return jsonify({"error": "Choose what kind of file this is"}), 400
        original = os.path.basename(upload.filename.replace("\\", "/"))
        ext = os.path.splitext(original)[1].lower()
        if ext not in (".csv", ".xlsx", ".txt"):
            return jsonify({"error": "Only .csv and .xlsx files are supported (PDF is not)"}), 400
        content = upload.read()
        try:
            parsed = parse_file(original, content)
        except ImportErrorWithRows as e:
            return jsonify({"error": str(e)}), 400

        file_id = uuid.uuid4().hex[:10]
        stored_name = f"{file_id}{ext}"
        (metadata.client_files_dir / stored_name).write_bytes(content)
        record = {
            "id": file_id,
            "original_name": original,
            "stored_name": stored_name,
            "file_type": file_type,
            "uploaded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "row_count": len(parsed.rows),
            "mapping": suggest_mapping(file_type, parsed.headers),
            "wallet_address": (request.form.get("wallet_address") or "").strip(),
            "status": "needs mapping",
            "record_count": 0,
            "error_count": 0,
        }
        metadata.add_client_file(record)
        recon_overrides.log_event(
            metadata.get_reconciliation_state(), "upload client file",
            f"{original} as {FILE_TYPES[file_type]} ({len(parsed.rows)} rows), id {file_id}",
        )
        metadata.save_reconciliation_state()
        return jsonify(_client_file_view(record, parsed))

    @app.route("/client-files/api/<file_id>")
    def get_client_file(file_id: str):
        record = metadata.get_client_file(file_id)
        if record is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(_client_file_view(record, _read_client_file(record)))

    @app.route("/client-files/api/<file_id>/mapping", methods=["PUT"])
    def save_client_file_mapping(file_id: str):
        record = metadata.get_client_file(file_id)
        if record is None:
            return jsonify({"error": "Not found"}), 404
        data = request.get_json() or {}
        mapping = {k: str(v).strip() for k, v in (data.get("mapping") or {}).items() if str(v).strip()}
        parsed = _read_client_file(record)
        result = apply_mapping(file_id, record["file_type"], parsed, mapping)
        status = "ready" if result.records and not result.errors else (
            "ready (with row errors)" if result.records else "needs mapping")
        # Rows that fail to parse are reported, but a file with some usable rows can still be used.
        usable = bool(result.records)
        metadata.update_client_file(
            file_id,
            mapping=mapping,
            wallet_address=(data.get("wallet_address") or "").strip(),
            status="ready" if usable else "needs mapping",
            record_count=len(result.records),
            error_count=len(result.errors),
        )
        recon_overrides.log_event(
            metadata.get_reconciliation_state(), "map client file",
            f"{record['original_name']}: {', '.join(f'{k}={v}' for k, v in mapping.items())}; "
            f"{len(result.records)} records, {len(result.errors)} row errors",
        )
        metadata.save_reconciliation_state()
        return jsonify({
            "success": usable,
            "status": status,
            "record_count": len(result.records),
            "errors": result.errors[:50],
            "error_count": len(result.errors),
            "preview": result.records[:5],
        })

    @app.route("/client-files/api/<file_id>", methods=["DELETE"])
    def delete_client_file(file_id: str):
        record = metadata.delete_client_file(file_id)
        if record is None:
            return jsonify({"error": "Not found"}), 404
        path = metadata.client_files_dir / record["stored_name"]
        if path.exists():
            path.unlink()
        recon_overrides.log_event(
            metadata.get_reconciliation_state(), "delete client file",
            f"{record['original_name']} (id {file_id})",
        )
        metadata.save_reconciliation_state()
        return jsonify({"success": True})

    # -------------------------------------------------------------------------
    # Invoice Routes
    # -------------------------------------------------------------------------

    @app.route("/invoices")
    def invoices_page():
        invoices = metadata.get_invoices(limit=20)
        stats = metadata.get_dashboard_stats()
        return render_template("invoices.html", invoices=invoices, stats=stats)

    @app.route("/invoices/create")
    def create_invoice_page():
        sender_profile = metadata.get_sender_profile()
        return render_template("create_invoice.html", sender_profile=sender_profile)

    @app.route("/invoices/api/create", methods=["POST"])
    def create_invoice():
        try:
            data = request.get_json()

            required = ["sender_name", "sender_email", "sender_wallet", "recipient_name", "recipient_email", "crypto_amount", "token_type"]
            for field in required:
                if not data.get(field):
                    return jsonify({"success": False, "error": f"{field} required"}), 400

            invoice_number = data.get("invoice_number") or metadata.get_next_invoice_number()
            token_type = data.get("token_type", "ETH").upper()
            crypto_amount = float(data.get("crypto_amount", 0))

            usd_value = None
            cad_value = None
            if data.get("include_fiat_value", True):
                try:
                    oracle = get_price_oracle()
                    prices = oracle.get_current_price(token_type, ["usd", "cad"])
                    usd_value = crypto_amount * prices.get("usd", 0)
                    cad_value = crypto_amount * prices.get("cad", 0)
                except:
                    pass

            generator = InvoiceGenerator(output_dir=metadata.invoices_dir)
            invoice_date = datetime.utcnow()

            pdf_bytes, filename = generator.generate_invoice(
                invoice_number=invoice_number,
                invoice_date=invoice_date,
                sender_name=data.get("sender_name"),
                sender_email=data.get("sender_email"),
                sender_wallet=data.get("sender_wallet"),
                sender_business=data.get("sender_business", ""),
                sender_address=data.get("sender_address", ""),
                sender_tax_id=data.get("sender_tax_id", ""),
                recipient_name=data.get("recipient_name"),
                recipient_email=data.get("recipient_email"),
                recipient_wallet=data.get("recipient_wallet"),
                crypto_amount=crypto_amount,
                token_type=token_type,
                usd_value=usd_value,
                cad_value=cad_value,
                work_description=data.get("work_description", ""),
                notes=data.get("notes", ""),
            )

            invoice_record = Invoice(
                id=str(uuid.uuid4()),
                invoice_number=invoice_number,
                created_at=invoice_date.isoformat(),
                sender_name=data.get("sender_name"),
                sender_email=data.get("sender_email"),
                sender_wallet=data.get("sender_wallet"),
                recipient_name=data.get("recipient_name"),
                recipient_email=data.get("recipient_email"),
                recipient_wallet=data.get("recipient_wallet"),
                crypto_amount=crypto_amount,
                token_type=token_type,
                usd_value=usd_value,
                cad_value=cad_value,
                work_description=data.get("work_description", ""),
                pdf_filename=filename,
                notes=data.get("notes", ""),
            )
            metadata.add_invoice(invoice_record)

            if data.get("save_profile"):
                profile = SenderProfile(
                    name=data.get("sender_name"),
                    email=data.get("sender_email"),
                    wallet=data.get("sender_wallet"),
                    business_name=data.get("sender_business", ""),
                    address_line1=data.get("sender_address", ""),
                    tax_id=data.get("sender_tax_id", ""),
                )
                metadata.save_sender_profile(profile)

            return jsonify({
                "success": True,
                "invoice_number": invoice_number,
                "download_url": f"/invoices/api/download/{filename}",
                "usd_value": usd_value,
                "cad_value": cad_value,
            })

        except Exception as e:
            logger.exception("Invoice creation failed")
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/invoices/api/download/<filename>")
    def download_invoice(filename: str):
        if "/" in filename or "\\" in filename or ".." in filename:
            return jsonify({"error": "Invalid filename"}), 400

        filepath = os.path.join(metadata.invoices_dir, filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "File not found"}), 404

        return send_file(filepath, mimetype="application/pdf", as_attachment=True, download_name=filename)

    @app.route("/invoices/api/list")
    def list_invoices():
        status = request.args.get("status")
        return jsonify([i.to_dict() for i in metadata.get_invoices(limit=50, status=status)])

    @app.route("/invoices/api/<invoice_id>/status", methods=["PUT"])
    def update_invoice_status(invoice_id: str):
        data = request.get_json()
        status = data.get("status", "").lower()
        if status not in ("paid", "unpaid", "cancelled"):
            return jsonify({"error": "Invalid status"}), 400

        if metadata.update_invoice_status(invoice_id, status, data.get("tx_hash")):
            return jsonify({"success": True})
        return jsonify({"error": "Not found"}), 404

    @app.route("/invoices/api/<invoice_id>", methods=["DELETE"])
    def delete_invoice(invoice_id: str):
        pdf_filename = metadata.delete_invoice(invoice_id)
        if pdf_filename is None:
            return jsonify({"error": "Not found"}), 404

        if pdf_filename:
            pdf_path = os.path.join(metadata.invoices_dir, pdf_filename)
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except:
                    pass

        return jsonify({"success": True})

    @app.route("/invoices/api/current-price")
    def get_current_price():
        token = request.args.get("token", "ETH").upper()
        try:
            oracle = get_price_oracle()
            prices = oracle.get_current_price(token, ["usd", "cad"])
            return jsonify({"token": token, "usd": prices.get("usd", 0), "cad": prices.get("cad", 0)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Health check
    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    logger.info("ChainLedger initialized")
    return app


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ChainLedger - Crypto Accounting Tool")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    print(r"""
   _____ _           _       _                _
  / ____| |         (_)     | |              | |
 | |    | |__   __ _ _ _ __ | |     ___  __ _| | __ _  ___ _ __
 | |    | '_ \ / _` | | '_ \| |    / _ \/ _` | |/ _` |/ _ \ '__|
 | |____| | | | (_| | | | | | |___|  __/ (_| | | (_| |  __/ |
  \_____|_| |_|\__,_|_|_| |_|______\___|\__,_|_|\__,_|\___|_|

    """)
    print(f"   Running at: http://{args.host}:{args.port}")
    print()
    print("   Press Ctrl+C to stop")
    print()

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)
