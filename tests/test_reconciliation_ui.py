"""Flask routes for the Reconciliation page, with the chain fetch replaced by sample data."""

import json

import pytest

import src.ui.app as app_module
from tests.sample_data import COLD, HOT, sample_balances, sample_invoices, sample_transactions


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "metadata.json").write_text(json.dumps({
        "invoices": sample_invoices(), "exports": [], "sender_profile": None,
        "settings": {"default_currency": "USD", "invoice_prefix": "INV", "next_invoice_number": 4},
        "wallets": [], "classification_overrides": {},
    }))

    def fake_fetch(network, wallets, api_key, start, end):
        return {"fetched_at": "2024-04-01T00:00:00+00:00", "network": network, "wallet_addresses": wallets,
                "period_start": start.isoformat(), "period_end": end.isoformat(),
                "transactions": sample_transactions(), "balances": sample_balances()}

    monkeypatch.setattr(app_module, "fetch_inputs", fake_fetch)
    app = app_module.create_app(data_dir=tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def run(client):
    return client.post("/reconciliation/api/run", json={
        "blockchain": "ethereum", "api_key": "test", "wallet_addresses": f"{HOT}, {COLD}",
        "period_start": "2024-03-01", "period_end": "2024-03-31",
    })


def test_page_renders(client):
    res = client.get("/reconciliation")
    assert res.status_code == 200
    assert b"Run Reconciliation" in res.data


def test_run_override_note_and_downloads(client):
    res = run(client)
    data = res.get_json()
    assert res.status_code == 200, data
    assert data["summary"]["matched_transfers"] == 1
    assert data["summary"]["invoice_statuses"] == {"paid": 1, "partially paid": 1, "unpaid": 1}
    assert client.get(data["download_xlsx"]).status_code == 200
    assert client.get(data["download_csv"]).status_code == 200

    # Override without a reason is refused
    payment = data["unmatched_payments"][0]["row_id"]
    bad = client.post("/reconciliation/api/overrides",
                      json={"type": "match_invoice", "fields": {"invoice_id": "inv-3", "row_id": payment}})
    assert bad.status_code == 400

    ok = client.post("/reconciliation/api/overrides", json={
        "type": "match_invoice", "fields": {"invoice_id": "inv-3", "row_id": payment}, "note": "client confirmed"})
    assert ok.status_code == 200
    client.put("/reconciliation/api/notes", json={"exception_id": "invoice:inv-3", "note": "follow up"})

    rerun = client.post("/reconciliation/api/rerun").get_json()
    assert rerun["unmatched_payments"] == []
    assert [e["action"] for e in rerun["override_log"]] == ["add match_invoice", "note"]
    assert next(e for e in rerun["exceptions"] if e["exception_id"] == "invoice:inv-3")["note"] == "follow up"

    applied = client.post("/reconciliation/api/apply-transfers").get_json()
    assert applied["count"] == 1  # one tx hash (same txid on both sides)
    assert client.get("/accounting/api/overrides").get_json() == {"0xmove1": "TRANSFER"}


def test_rerun_without_previous_run_is_an_error(client):
    assert client.post("/reconciliation/api/rerun").status_code == 400
