"""
ChainLedger Web UI

Flask application for the ChainLedger crypto accounting tool.
Provides endpoints for:
- Dashboard with stats
- Accounting exports
- Invoice creation and management
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flask import Flask, render_template, jsonify, request, send_file
from flask_cors import CORS

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.wallet import LedgerGenerator, validate_ethereum_address, validate_bitcoin_address, SUPPORTED_NETWORKS
from src.exports import ExportService, get_price_oracle
from src.invoices import InvoiceGenerator

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

        # Ensure directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.invoices_dir.mkdir(exist_ok=True)
        self.exports_dir.mkdir(exist_ok=True)

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
        return render_template("index.html", stats=stats, wallets=wallets)

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
                if blockchain == "ethereum" and not validate_ethereum_address(addr):
                    return jsonify({"success": False, "error": f"Invalid address: {addr}"}), 400
                if blockchain == "bitcoin" and not validate_bitcoin_address(addr):
                    return jsonify({"success": False, "error": f"Invalid address: {addr}"}), 400

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
    # Invoice Routes
    # -------------------------------------------------------------------------

    @app.route("/invoices")
    def invoices_page():
        invoices = metadata.get_invoices(limit=20)
        return render_template("invoices.html", invoices=invoices)

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
