"""Small fictional sample data shared by the reconciliation tests and examples/."""

HOT = "0x1111111111111111111111111111111111111111"
COLD = "0x2222222222222222222222222222222222222222"
CLIENT_A = "0xaaaa000000000000000000000000000000000001"
CLIENT_B = "0xbbbb000000000000000000000000000000000002"
VENDOR = "0xcccc000000000000000000000000000000000003"


def row(tx_hash, wallet, direction, amount, date, time="12:00:00", asset="ETH",
        network="Ethereum", fee="0", from_address=None, to_address=None):
    if from_address is None:
        from_address = wallet if direction == "Out" else "0xunknown"
    if to_address is None:
        to_address = wallet if direction == "In" else "0xunknown"
    return {
        "tx_hash": tx_hash,
        "block_number": 1,
        "block_date": date,
        "block_time": time,
        "from_address": from_address,
        "to_address": to_address,
        "direction": direction,
        "network": network,
        "asset": asset,
        "amount_raw": "0",
        "amount_decimal": amount,
        "tx_fee_native": fee,
        "tx_status": "Success",
        "category": "Incoming Transfer" if direction == "In" else "Outgoing Transfer",
        "notes": "",
        "wallet_address": wallet,
    }


def sample_transactions():
    """A month of activity across a hot and a cold wallet (Ethereum)."""
    return [
        # Client A pays invoice INV-00001 (2.0 ETH) in one payment
        row("0xpay1", HOT, "In", "2.0", "2024-03-04", "10:00:00", from_address=CLIENT_A),
        # Hot -> cold transfer, same txid seen from both wallets, 0.0021 ETH gas
        row("0xmove1", HOT, "Out", "1.5", "2024-03-05", "09:00:00", fee="0.0021",
            from_address=HOT, to_address=COLD),
        row("0xmove1", COLD, "In", "1.5", "2024-03-05", "09:00:00",
            from_address=HOT, to_address=COLD),
        # Client B pays INV-00002 (1.0 ETH) in two parts; only 0.6 so far
        row("0xpay2a", HOT, "In", "0.4", "2024-03-10", "15:00:00", from_address=CLIENT_B),
        row("0xpay2b", HOT, "In", "0.2", "2024-03-12", "15:00:00", from_address=CLIENT_B),
        # Vendor payment (ordinary expense)
        row("0xvendor", HOT, "Out", "0.3", "2024-03-15", "08:00:00", fee="0.0009",
            from_address=HOT, to_address=VENDOR),
        # Unknown incoming payment, no invoice
        row("0xmystery", HOT, "In", "0.05", "2024-03-20", "18:30:00", from_address="0xdddd"),
    ]


def sample_invoices():
    contact = {"sender_name": "Sample Freelancer", "sender_email": "freelancer@example.com",
               "recipient_email": "client@example.com"}
    return [{**contact, "recipient_name": f"Client {n}", **inv} for n, inv in zip("ABC", _invoices())]


def _invoices():
    return [
        {"id": "inv-1", "invoice_number": "INV-00001", "created_at": "2024-03-01T09:00:00",
         "crypto_amount": 2.0, "token_type": "ETH", "recipient_wallet": CLIENT_A,
         "sender_wallet": HOT, "status": "unpaid"},
        {"id": "inv-2", "invoice_number": "INV-00002", "created_at": "2024-03-08T09:00:00",
         "crypto_amount": 1.0, "token_type": "ETH", "recipient_wallet": CLIENT_B,
         "sender_wallet": HOT, "status": "unpaid"},
        {"id": "inv-3", "invoice_number": "INV-00003", "created_at": "2024-03-25T09:00:00",
         "crypto_amount": 0.75, "token_type": "ETH", "recipient_wallet": "",
         "sender_wallet": HOT, "status": "unpaid"},
    ]


def sample_balances():
    # HOT: 1.0 + 2.0 + 0.4 + 0.2 + 0.05 - 1.5 - 0.3 - 0.0021 - 0.0009 = 1.847
    # The "actual" closing is 0.0005 lower to show an unexplained difference.
    return [
        {"wallet_address": HOT, "network": "Ethereum", "period_start": "2024-03-01",
         "period_end": "2024-03-31", "opening_balance": "1.0", "opening_source": "sample",
         "actual_closing": "1.8465", "actual_source": "sample"},
        {"wallet_address": COLD, "network": "Ethereum", "period_start": "2024-03-01",
         "period_end": "2024-03-31", "opening_balance": "5.0", "opening_source": "sample",
         "actual_closing": "6.5", "actual_source": "sample"},
    ]
