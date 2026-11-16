"""ERC-20 fetching and token reconciliation, against mocked Alchemy responses."""

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx

from src.reconciliation.balance import reconcile_balance
from src.reconciliation.service import _token_balance_specs
from src.reconciliation.transfer_matching import match_own_transfers
from src.wallet import EthereumFetcher
from tests.sample_data import COLD, HOT, row

USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
FAKE_USDC = "0x9999999999999999999999999999999999999999"
WALLET = HOT
TS = "2024-03-05T10:00:00.000Z"


def transfer(tx_hash, frm, to, category, value, raw_hex=None, contract=None, unique=None):
    t = {"hash": tx_hash, "from": frm, "to": to, "category": category, "value": value,
         "blockNum": "0x10", "metadata": {"blockTimestamp": TS}, "uniqueId": unique or f"{tx_hash}:{category}:{to}"}
    if contract:
        t["rawContract"] = {"address": contract, "value": raw_hex, "decimal": "0x6"}
    return t


def make_fetcher(transfers_by_query, receipts, token_balances=None):
    def handler(request):
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        if method == "alchemy_getAssetTransfers":
            p = params[0]
            key = ("erc20" if p["category"] == ["erc20"] else "native",
                   "from" if "fromAddress" in p else "to")
            if key[0] == "erc20":
                assert USDC in p["contractAddresses"]
            result = {"transfers": transfers_by_query.get(key, [])}
        elif method == "eth_getTransactionReceipt":
            result = receipts[params[0]]
        elif method == "eth_call":
            contract = params[0]["to"]
            result = hex(token_balances.get(contract, 0))
        elif method == "eth_blockNumber":
            result = hex(100)
        elif method == "eth_getBlockByNumber":
            result = {"timestamp": hex(1_700_000_000 + int(params[0], 16))}
        else:
            raise AssertionError(method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    fetcher = EthereumFetcher("test-key")
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    return fetcher


def test_erc20_rows_fee_once_per_tx_and_fake_tokens_ignored():
    gas = {"gasUsed": hex(50_000), "effectiveGasPrice": hex(10**10), "from": WALLET}
    pulled = {"gasUsed": hex(90_000), "effectiveGasPrice": hex(10**10), "from": "0xrouter"}
    fetcher = make_fetcher({
        ("erc20", "to"): [
            transfer("0xpay", "0xclient", WALLET, "erc20", 1500.0, hex(1_500_000_000), USDC),
            transfer("0xspam", "0xspammer", WALLET, "erc20", 1e6, hex(10**12), FAKE_USDC),
        ],
        ("erc20", "from"): [
            # Two USDC transfers in one tx are combined; ETH sent in the same tx carries the fee
            transfer("0xbatch", WALLET, "0xv1", "erc20", 100.0, hex(100_000_000), USDC, "b1"),
            transfer("0xbatch", WALLET, "0xv2", "erc20", 50.0, hex(50_000_000), USDC, "b2"),
            # Tokens pulled by a contract: the wallet did not send the tx, so no fee
            transfer("0xpull", WALLET, "0xrouter", "erc20", 10.0, hex(10_000_000), USDC),
        ],
        ("native", "from"): [transfer("0xbatch", WALLET, "0xv1", "external", 0.01)],
    }, receipts={"0xbatch": gas, "0xpull": pulled})

    rows = {(r.tx_hash, r.asset): r for r in fetcher.fetch_transactions(WALLET)}
    assert ("0xspam", "USDC") not in rows and len(rows) == 4
    assert rows[("0xpay", "USDC")].amount_decimal == "1500"
    assert rows[("0xpay", "USDC")].direction == "In"
    batch = rows[("0xbatch", "USDC")]
    assert (Decimal(batch.amount_decimal), batch.notes) == (Decimal("150"), "2 transfers combined")
    assert Decimal(rows[("0xbatch", "ETH")].tx_fee_native) == Decimal("0.0005")
    assert Decimal(batch.tx_fee_native) == 0
    assert Decimal(rows[("0xpull", "USDC")].tx_fee_native) == 0


def test_token_balance_uses_balance_of_and_specs_skip_idle_tokens():
    fetcher = make_fetcher({}, {}, token_balances={USDC: 2_500_000})
    assert fetcher.get_token_balance(WALLET, "USDC") == Decimal("2.5")
    now = datetime.now(timezone.utc)
    specs = _token_balance_specs(fetcher, WALLET, [], {"wallet_address": WALLET, "network": "Ethereum"},
                                 datetime(2024, 3, 1, tzinfo=timezone.utc), datetime(2024, 3, 31, tzinfo=timezone.utc), now)
    assert [s["asset"] for s in specs] == ["USDC"]  # other tokens have zero balance and no activity
    assert specs[0]["actual_closing"] == "2.5"


def test_token_balance_excludes_eth_fees():
    txs = [
        row("a", HOT, "In", "1500", "2024-03-04", asset="USDC"),
        row("b", HOT, "Out", "150", "2024-03-05", asset="USDC", fee="0.0005"),
    ]
    b = reconcile_balance(HOT, "Ethereum", txs, date(2024, 3, 1), date(2024, 3, 31),
                          Decimal("0"), Decimal("1350"), asset="USDC")
    assert (b.fees, b.expected_closing, b.reconciled) == (Decimal(0), Decimal("1350"), True)
    eth = reconcile_balance(HOT, "Ethereum", txs, date(2024, 3, 1), date(2024, 3, 31), Decimal("1"), None)
    assert eth.fees == Decimal("0.0005")


def test_same_tx_eth_and_token_rows_match_by_asset():
    rows = [
        row("0xt", HOT, "Out", "500", "2024-03-05", asset="USDC", fee="0.0004", from_address=HOT, to_address=COLD),
        row("0xt", COLD, "In", "500", "2024-03-05", asset="USDC", from_address=HOT, to_address=COLD),
        row("0xt", COLD, "In", "0.2", "2024-03-05", asset="ETH", from_address=HOT, to_address=COLD),
        row("0xt", HOT, "Out", "0.2", "2024-03-05", asset="ETH", from_address=HOT, to_address=COLD),
    ]
    result = match_own_transfers(rows, [HOT, COLD])
    assert sorted(m.asset for m in result.matches) == ["ETH", "USDC"]
    assert all(m.method == "same_txid" for m in result.matches)
    assert [f.asset for f in result.fee_lines] == ["ETH"]
