"""
Wallet Transaction Fetching

Fetches blockchain transactions from Ethereum (via Alchemy) and Bitcoin (via Blockstream).
Produces standardized LedgerRow records for downstream processing.
"""

from __future__ import annotations

import re
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class LedgerRow:
    """A single transaction record from the blockchain."""

    tx_hash: str
    block_number: int
    block_date: str  # YYYY-MM-DD
    block_time: str  # HH:MM:SS
    from_address: str
    to_address: str
    direction: str  # "In" or "Out"
    network: str  # "Ethereum" or "Bitcoin"
    asset: str  # Token symbol (ETH, BTC, USDC, etc.)
    amount_raw: str  # Raw amount in smallest unit
    amount_decimal: str  # Human-readable amount
    tx_fee_native: str  # Gas/transaction fee in native token
    tx_status: str  # "Success" or "Failed"
    category: str  # "Incoming Transfer", "Outgoing Transfer", etc.
    notes: str  # Additional context


SUPPORTED_NETWORKS = {
    "ethereum": {
        "name": "Ethereum",
        "requires_api_key": True,
        "api_key_label": "Alchemy API Key",
        "api_key_hint": "Get your free API key at alchemy.com",
    },
    "bitcoin": {
        "name": "Bitcoin",
        "requires_api_key": False,
        "api_key_label": "",
        "api_key_hint": "",
    },
}


# =============================================================================
# Address Validation
# =============================================================================

def validate_ethereum_address(address: str) -> bool:
    """Validate Ethereum address format (0x + 40 hex characters)."""
    if not address:
        return False
    pattern = r"^0x[a-fA-F0-9]{40}$"
    return bool(re.match(pattern, address))


def validate_bitcoin_address(address: str) -> bool:
    """Validate Bitcoin address format (legacy, segwit, or bech32)."""
    if not address:
        return False
    # Legacy (1...), SegWit (3...), Bech32 (bc1...)
    patterns = [
        r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$",  # Legacy/SegWit
        r"^bc1[a-zA-HJ-NP-Z0-9]{39,59}$",  # Bech32
    ]
    return any(re.match(p, address) for p in patterns)


# =============================================================================
# Abstract Fetcher
# =============================================================================

class BlockchainFetcher(ABC):
    """Abstract base class for blockchain transaction fetchers."""

    @abstractmethod
    def fetch_transactions(
        self,
        wallet_address: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> list[LedgerRow]:
        """Fetch transactions for a wallet address."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Clean up resources."""
        pass


# =============================================================================
# Ethereum Fetcher
# =============================================================================

class EthereumFetcher(BlockchainFetcher):
    """Fetch Ethereum transactions via Alchemy API."""

    # Common ERC-20 token decimals
    TOKEN_DECIMALS = {
        "USDC": 6, "USDT": 6, "WBTC": 8,
        "DAI": 18, "LINK": 18, "UNI": 18, "AAVE": 18,
        "WETH": 18, "MATIC": 18, "CRV": 18, "MKR": 18,
    }

    def __init__(self, api_key: str):
        """Initialize with Alchemy API key."""
        self.api_key = api_key
        self.base_url = f"https://eth-mainnet.g.alchemy.com/v2/{api_key}"
        self.client = httpx.Client(timeout=30.0)

    def _rpc_call(self, method: str, params: list) -> dict:
        """Make a JSON-RPC call to Alchemy."""
        response = self.client.post(
            self.base_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        response.raise_for_status()
        result = response.json()
        if "error" in result:
            raise ValueError(f"RPC error: {result['error']}")
        return result.get("result", {})

    def _get_block_timestamp(self, block_hex: str) -> datetime:
        """Get timestamp for a block."""
        block = self._rpc_call("eth_getBlockByNumber", [block_hex, False])
        ts = int(block.get("timestamp", "0x0"), 16)
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    def _parse_eth_value(self, hex_value: str) -> Decimal:
        """Convert hex wei to ETH."""
        wei = int(hex_value, 16)
        return Decimal(wei) / Decimal(10 ** 18)

    def _get_token_symbol(self, contract_address: str) -> str:
        """Get token symbol from contract (simplified lookup)."""
        # Common token addresses
        known_tokens = {
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
            "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
            "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
            "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
            "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
        }
        return known_tokens.get(contract_address.lower(), "ERC20")

    def fetch_transactions(
        self,
        wallet_address: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> list[LedgerRow]:
        """Fetch all transactions for a wallet."""
        rows = []
        wallet_lower = wallet_address.lower()

        # Fetch ETH transfers
        for direction in ["from", "to"]:
            params = {
                "fromBlock": "0x0",
                "toBlock": "latest",
                direction: wallet_address,
                "category": ["external", "internal"],
            }

            transfers = self._rpc_call("alchemy_getAssetTransfers", [params])

            for tx in transfers.get("transfers", []):
                try:
                    block_num = int(tx.get("blockNum", "0x0"), 16)
                    block_hex = tx.get("blockNum", "0x0")
                    block_dt = self._get_block_timestamp(block_hex)

                    # Date filtering
                    if from_date and block_dt < from_date:
                        continue
                    if to_date and block_dt > to_date:
                        continue

                    from_addr = tx.get("from", "").lower()
                    to_addr = tx.get("to", "").lower()
                    is_incoming = to_addr == wallet_lower
                    value = Decimal(str(tx.get("value", 0)))

                    row = LedgerRow(
                        tx_hash=tx.get("hash", ""),
                        block_number=block_num,
                        block_date=block_dt.strftime("%Y-%m-%d"),
                        block_time=block_dt.strftime("%H:%M:%S"),
                        from_address=tx.get("from", ""),
                        to_address=tx.get("to", ""),
                        direction="In" if is_incoming else "Out",
                        network="Ethereum",
                        asset=tx.get("asset", "ETH"),
                        amount_raw=str(int(value * 10**18)),
                        amount_decimal=str(value),
                        tx_fee_native="0",  # Would need separate lookup
                        tx_status="Success",
                        category="Incoming Transfer" if is_incoming else "Outgoing Transfer",
                        notes="",
                    )
                    rows.append(row)

                except Exception as e:
                    logger.warning(f"Failed to parse transaction: {e}")
                    continue

        # Deduplicate by tx_hash + direction
        seen = set()
        unique_rows = []
        for row in rows:
            key = (row.tx_hash, row.direction)
            if key not in seen:
                seen.add(key)
                unique_rows.append(row)

        return sorted(unique_rows, key=lambda r: (r.block_date, r.block_time))

    def close(self) -> None:
        """Close HTTP client."""
        self.client.close()


# =============================================================================
# Bitcoin Fetcher
# =============================================================================

class BitcoinFetcher(BlockchainFetcher):
    """Fetch Bitcoin transactions via Blockstream API."""

    BASE_URL = "https://blockstream.info/api"

    def __init__(self):
        """Initialize the fetcher."""
        self.client = httpx.Client(timeout=30.0)

    def fetch_transactions(
        self,
        wallet_address: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> list[LedgerRow]:
        """Fetch all transactions for a Bitcoin wallet."""
        rows = []
        wallet_lower = wallet_address.lower()

        # Fetch transactions
        url = f"{self.BASE_URL}/address/{wallet_address}/txs"
        response = self.client.get(url)
        response.raise_for_status()
        transactions = response.json()

        for tx in transactions:
            try:
                # Get block time
                block_time = tx.get("status", {}).get("block_time")
                if not block_time:
                    continue

                block_dt = datetime.fromtimestamp(block_time, tz=timezone.utc)

                # Date filtering
                if from_date and block_dt < from_date:
                    continue
                if to_date and block_dt > to_date:
                    continue

                block_height = tx.get("status", {}).get("block_height", 0)
                tx_id = tx.get("txid", "")

                # Calculate net flow for this wallet
                total_in = 0
                total_out = 0

                # Check inputs (spending from our wallet = outgoing)
                for vin in tx.get("vin", []):
                    prevout = vin.get("prevout", {})
                    if prevout.get("scriptpubkey_address", "").lower() == wallet_lower:
                        total_out += prevout.get("value", 0)

                # Check outputs (receiving to our wallet = incoming)
                for vout in tx.get("vout", []):
                    if vout.get("scriptpubkey_address", "").lower() == wallet_lower:
                        total_in += vout.get("value", 0)

                # Determine direction and amount
                net = total_in - total_out
                if net > 0:
                    direction = "In"
                    amount_sats = net
                elif net < 0:
                    direction = "Out"
                    amount_sats = abs(net)
                else:
                    continue  # No net change for this wallet

                amount_btc = Decimal(amount_sats) / Decimal(100_000_000)
                fee = tx.get("fee", 0)
                fee_btc = Decimal(fee) / Decimal(100_000_000)

                row = LedgerRow(
                    tx_hash=tx_id,
                    block_number=block_height,
                    block_date=block_dt.strftime("%Y-%m-%d"),
                    block_time=block_dt.strftime("%H:%M:%S"),
                    from_address=wallet_address if direction == "Out" else "Various",
                    to_address=wallet_address if direction == "In" else "Various",
                    direction=direction,
                    network="Bitcoin",
                    asset="BTC",
                    amount_raw=str(amount_sats),
                    amount_decimal=str(amount_btc),
                    tx_fee_native=str(fee_btc) if direction == "Out" else "0",
                    tx_status="Success",
                    category="Incoming Transfer" if direction == "In" else "Outgoing Transfer",
                    notes="",
                )
                rows.append(row)

            except Exception as e:
                logger.warning(f"Failed to parse Bitcoin transaction: {e}")
                continue

        return sorted(rows, key=lambda r: (r.block_date, r.block_time))

    def close(self) -> None:
        """Close HTTP client."""
        self.client.close()


# =============================================================================
# Ledger Generator (Orchestrator)
# =============================================================================

class LedgerGenerator:
    """
    Orchestrates transaction fetching across different blockchains.

    Usage:
        generator = LedgerGenerator(network="ethereum", api_key="...")
        rows = generator.generate_ledger(wallet_address="0x...")
        generator.close()
    """

    def __init__(self, network: str, api_key: Optional[str] = None):
        """Initialize with network type and optional API key."""
        self.network = network.lower()

        if self.network == "ethereum":
            if not api_key:
                raise ValueError("Ethereum requires an API key")
            self.fetcher = EthereumFetcher(api_key)
        elif self.network == "bitcoin":
            self.fetcher = BitcoinFetcher()
        else:
            raise ValueError(f"Unsupported network: {network}")

    def generate_ledger(
        self,
        wallet_address: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> list[LedgerRow]:
        """Generate ledger rows for a wallet."""
        return self.fetcher.fetch_transactions(
            wallet_address=wallet_address,
            from_date=from_date,
            to_date=to_date,
        )

    def close(self) -> None:
        """Clean up resources."""
        self.fetcher.close()
