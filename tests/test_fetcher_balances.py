"""Fetcher changes used by reconciliation, tested against mocked HTTP responses."""

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from src.wallet import BitcoinFetcher, EthereumFetcher, HistoricalBalanceUnavailable

ADDR = "bc1qexamplewalletone0000000000000000000000"


def btc_tx(txid, ts, spent=0, received=0, fee=0):
    vin = [{"prevout": {"scriptpubkey_address": ADDR, "value": spent}}] if spent else []
    vout = [{"scriptpubkey_address": ADDR, "value": received}] if received else []
    return {"txid": txid, "status": {"block_time": ts, "block_height": 1}, "vin": vin, "vout": vout, "fee": fee}


def test_bitcoin_outgoing_amount_excludes_fee_and_pages_are_followed():
    page1 = [btc_tx(f"t{i}", 1700000000 + i, received=1000) for i in range(25)]
    # Spend 100_000 sats input, 40_000 change back, 500 fee -> 59_500 sent to others
    page2 = [btc_tx("spend", 1700001000, spent=100_000, received=40_000, fee=500)]

    def handler(request):
        if request.url.path.endswith("/txs/chain"):
            return httpx.Response(200, json=page1)
        if request.url.path.endswith("/txs/chain/t24"):
            return httpx.Response(200, json=page2)
        return httpx.Response(404)

    fetcher = BitcoinFetcher()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = fetcher.fetch_transactions(ADDR)
    assert len(rows) == 26
    spend = next(r for r in rows if r.tx_hash == "spend")
    assert spend.direction == "Out"
    assert Decimal(spend.amount_decimal) == Decimal("0.000595")
    assert Decimal(spend.tx_fee_native) == Decimal("0.000005")


def test_bitcoin_balance_current_only():
    def handler(request):
        return httpx.Response(200, json={"chain_stats": {"funded_txo_sum": 150_000, "spent_txo_sum": 50_000}})

    fetcher = BitcoinFetcher()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetcher.get_balance(ADDR) == Decimal("0.001")
    with pytest.raises(HistoricalBalanceUnavailable):
        fetcher.get_balance(ADDR, datetime(2024, 1, 1, tzinfo=timezone.utc))


def test_ethereum_historical_balance_uses_block_at_date_and_fee_from_receipt():
    # Block n has timestamp 1_000_000 + 12 * n; latest block is 1000
    calls = []

    def handler(request):
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        calls.append(method)
        if method == "eth_blockNumber":
            result = hex(1000)
        elif method == "eth_getBlockByNumber":
            result = {"timestamp": hex(1_000_000 + 12 * int(params[0], 16))}
        elif method == "eth_getBalance":
            assert params[1] == hex(500)
            result = hex(2 * 10**18)
        elif method == "eth_getTransactionReceipt":
            result = {"gasUsed": hex(21000), "effectiveGasPrice": hex(10**10)}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    fetcher = EthereumFetcher("test-key")
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    as_of = datetime.fromtimestamp(1_000_000 + 12 * 500 + 5, tz=timezone.utc)
    assert fetcher.get_balance("0x" + "1" * 40, as_of) == Decimal(2)
    assert fetcher._get_tx_fee("0xabc") == Decimal("0.00021")
