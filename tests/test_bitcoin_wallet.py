"""Bitcoin whole-wallet support: extended public key derivation and scanning (mocked Blockstream)."""

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest

from src.reconciliation.invoice_matching import match_invoices_to_payments
from src.ui.app import _wallet_error
from src.wallet import BitcoinFetcher, InvalidExtendedKey, parse_extended_key
from src.wallet.hd_keys import _b58check_decode, _b58check_encode, _ripemd160_py, derive_address

# Published test vectors (BIP44 / BIP49 / BIP84) for the mnemonic
# "abandon abandon ... abandon about", account 0.
XPUB = "xpub6BosfCnifzxcFwrSzQiqu2DBVTshkCXacvNsWGYJVVhhawA7d4R5WSWGFNbi8Aw6ZRc1brxMyWMzG3DSSSSoekkudhUd9yLb6qx39T9nMdj"
YPUB = "ypub6Ww3ibxVfGzLrAH1PNcjyAWenMTbbAosGNB6VvmSEgytSER9azLDWCxoJwW7Ke7icmizBMXrzBx9979FfaHxHcrArf3zbeJJJUZPf663zsP"
ZPUB = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"


def test_address_derivation_matches_bip_test_vectors():
    z = parse_extended_key(ZPUB)
    assert z.address_type == "p2wpkh"
    assert derive_address(z, "p2wpkh", 0, 0) == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
    assert derive_address(z, "p2wpkh", 0, 1) == "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"
    assert derive_address(z, "p2wpkh", 1, 0) == "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el"
    assert derive_address(parse_extended_key(XPUB), "p2pkh", 0, 0) == "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"
    assert derive_address(parse_extended_key(YPUB), "p2sh-p2wpkh", 0, 0) == "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf"
    assert parse_extended_key(XPUB).address_type is None  # xpub: type must be detected


def test_ripemd160_fallback_matches_hashlib():
    try:
        hashlib.new("ripemd160")
    except ValueError:
        pytest.skip("hashlib has no RIPEMD-160 to compare against")
    for data in (b"", b"abc", b"a" * 1000):
        assert _ripemd160_py(data) == hashlib.new("ripemd160", data).digest()


def test_bad_keys_are_rejected_with_reasons():
    with pytest.raises(InvalidExtendedKey, match="Checksum"):
        parse_extended_key(ZPUB[:-1] + ("t" if ZPUB[-1] != "t" else "u"))
    # BIP32 test vector 1 master private key: must be refused, never used
    xprv = ("xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvvNKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi")
    with pytest.raises(InvalidExtendedKey, match="private key"):
        parse_extended_key(xprv)
    assert "private key" in _wallet_error("bitcoin", xprv)
    assert _wallet_error("bitcoin", ZPUB) is None
    assert _wallet_error("bitcoin", "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu") is None
    assert _wallet_error("bitcoin", "hello") is not None


# --- scanning and whole-wallet amounts (mocked Blockstream) ----------------

z = parse_extended_key(ZPUB)
R0, R1 = derive_address(z, "p2wpkh", 0, 0), derive_address(z, "p2wpkh", 0, 1)
C0 = derive_address(z, "p2wpkh", 1, 0)
OUTSIDER = "bc1qoutsider0000000000000000000000000000"
T0 = 1_709_000_000  # 2024-02-27


def tx(txid, ts, vin, vout, fee=0):
    return {"txid": txid, "status": {"block_time": ts, "block_height": 800_000},
            "vin": [{"prevout": {"scriptpubkey_address": a, "value": v}} for a, v in vin],
            "vout": [{"scriptpubkey_address": a, "value": v} for a, v in vout], "fee": fee}


HISTORY = [
    tx("fund", T0, [(OUTSIDER, 100_500)], [(R0, 100_000)], fee=500),
    # Pay 30_000 to an outsider; 69_500 change goes to a NEW change address
    tx("pay", T0 + 86_400 * 5, [(R0, 100_000)], [(OUTSIDER, 30_000), (C0, 69_500)], fee=500),
    tx("client", T0 + 86_400 * 10, [(OUTSIDER, 25_300)], [(R1, 25_000)], fee=300),
    # Consolidation: all inside the wallet, only the fee leaves
    tx("consolidate", T0 + 86_400 * 12, [(C0, 69_500), (R1, 25_000)], [(R0, 94_200)], fee=300),
]


def mock_fetcher(history=HISTORY):
    requests = []

    def touches(t, address):
        return any(i["prevout"]["scriptpubkey_address"] == address for i in t["vin"]) or \
            any(o["scriptpubkey_address"] == address for o in t["vout"])

    def handler(request):
        path = request.url.path
        requests.append(path)
        parts = path.split("/")
        address = parts[3]
        mine = [t for t in history if touches(t, address)]
        if path.endswith("/txs/chain"):
            return httpx.Response(200, json=list(reversed(mine)))
        funded = sum(o["value"] for t in mine for o in t["vout"] if o["scriptpubkey_address"] == address)
        spent = sum(i["prevout"]["value"] for t in mine for i in t["vin"] if i["prevout"]["scriptpubkey_address"] == address)
        return httpx.Response(200, json={
            "chain_stats": {"tx_count": len(mine), "funded_txo_sum": funded, "spent_txo_sum": spent},
            "mempool_stats": {"tx_count": 0},
        })

    fetcher = BitcoinFetcher()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    return fetcher, requests


def test_scan_finds_receive_and_change_addresses_and_stops_at_gap_limit():
    fetcher, requests = mock_fetcher()
    assert set(fetcher.wallet_addresses(ZPUB)) == {R0, R1, C0}
    stats_calls = [p for p in requests if not p.endswith("/txs/chain")]
    assert len(stats_calls) == (2 + 20) + (1 + 20)  # used + 20 unused on each chain
    assert "3 used addresses" in fetcher.wallet_summary(ZPUB)


def test_change_is_not_counted_as_sent_and_all_receipts_are_seen():
    fetcher, _ = mock_fetcher()
    rows = {r.tx_hash: r for r in fetcher.fetch_transactions(ZPUB)}
    pay = rows["pay"]
    assert (pay.direction, Decimal(pay.amount_decimal), Decimal(pay.tx_fee_native)) == ("Out", Decimal("0.0003"), Decimal("0.000005"))
    client = rows["client"]  # paid to the wallet's second receive address
    assert (client.direction, Decimal(client.amount_decimal), client.to_address) == ("In", Decimal("0.00025"), ZPUB)
    assert client.wallet_addresses == R1
    internal = rows["consolidate"]
    assert (Decimal(internal.amount_decimal), Decimal(internal.tx_fee_native)) == (0, Decimal("0.000003"))
    assert "own addresses" in internal.notes


def test_single_address_view_shows_the_old_problem():
    # The same payment seen from one address only: the change looks like it was sent
    fetcher, _ = mock_fetcher()
    pay = next(r for r in fetcher.fetch_transactions(R0) if r.tx_hash == "pay")
    assert Decimal(pay.amount_decimal) == Decimal("0.000995")


def test_wallet_balance_current_and_from_history():
    fetcher, _ = mock_fetcher()
    assert fetcher.get_balance(ZPUB) == Decimal("0.000942")
    as_of = datetime.fromtimestamp(T0 + 86_400 * 11, tz=timezone.utc)
    # 100_000 in, 100_000 out + 69_500 change back, 25_000 in
    assert fetcher.balance_from_history(ZPUB, as_of) == Decimal("0.000945")
    # History up to now agrees with Blockstream's address totals
    assert fetcher.balance_from_history(ZPUB, datetime.now(timezone.utc)) == fetcher.get_balance(ZPUB)


def test_xpub_detects_the_address_type_in_use():
    # Some apps (e.g. Ledger Live) show a native SegWit account's key as "xpub":
    # the same key data as the zpub, with xpub version bytes.
    as_xpub = _b58check_encode(bytes.fromhex("0488b21e") + _b58check_decode(ZPUB)[4:])
    assert as_xpub.startswith("xpub")
    assert derive_address(parse_extended_key(as_xpub), "p2wpkh", 0, 0) == R0
    fetcher, requests = mock_fetcher()
    assert set(fetcher.wallet_addresses(as_xpub)) == {R0, R1, C0}
    assert "native SegWit" in fetcher.wallet_summary(as_xpub)
    assert len([p for p in requests if not p.endswith("/txs/chain")]) == 2 * 20 * 2 + 43  # two unused types


def test_invoice_paid_to_one_address_of_the_wallet_is_matched():
    fetcher, _ = mock_fetcher()
    payments = []
    for r in fetcher.fetch_transactions(ZPUB):
        d = r.__dict__.copy()
        d["wallet_address"] = ZPUB
        payments.append(d)
    invoice = {"id": "i1", "invoice_number": "INV-1", "created_at": "2024-03-01", "crypto_amount": "0.00025",
               "token_type": "BTC", "sender_wallet": R1, "recipient_wallet": "", "status": "sent"}
    result = match_invoices_to_payments([invoice], payments)
    assert result.invoice_matches[0].status == "paid"
    assert result.invoice_matches[0].payment_tx_hashes == ["client"]
    # The same invoice addressed to an address outside the wallet is not matched
    other = dict(invoice, sender_wallet=OUTSIDER)
    assert match_invoices_to_payments([other], payments).invoice_matches[0].status == "unpaid"


def test_reconciliation_inputs_for_a_whole_wallet(monkeypatch):
    from src.reconciliation.service import fetch_inputs
    from src.reconciliation.balance import reconcile_balance
    import src.wallet.fetch_transactions as ft

    mocked, _ = mock_fetcher()
    monkeypatch.setattr(ft.httpx, "Client", lambda **kw: mocked.client)
    inputs = fetch_inputs("bitcoin", [ZPUB], None, date(2024, 3, 1), datetime.now(timezone.utc).date())
    spec = inputs["balances"][0]
    assert Decimal(spec["opening_balance"]) == Decimal("0.001")  # funded on 2024-02-27, before the period
    assert spec["opening_source"].startswith("computed from the wallet's full transaction history")
    assert "3 used addresses" in spec["wallet_scope"]
    assert [t["tx_hash"] for t in inputs["transactions"]] == ["pay", "client", "consolidate"]
    end = datetime.now(timezone.utc).date()
    b = reconcile_balance(ZPUB, "Bitcoin", inputs["transactions"], date(2024, 3, 1), end,
                          Decimal(spec["opening_balance"]), Decimal(spec["actual_closing"]))
    # 0.001 opening - 0.0003 sent - fees + 0.00025 received = 0.000942, Blockstream's total
    assert (b.expected_closing, b.reconciled) == (Decimal("0.000942"), True)


def test_small_amounts_are_plain_decimals():
    from src.wallet.fetch_transactions import _sats_to_btc
    assert (_sats_to_btc(50), _sats_to_btc(100_000_000), _sats_to_btc(0)) == ("0.0000005", "1", "0")
