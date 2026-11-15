"""
Manual Overrides, Exception Notes and the Override Log

Reconciliation state is a plain dict stored under the "reconciliation" key of
data/metadata.json:

    {
      "overrides": [ {id, type, ..., note, created_at, active, removed_at} ],
      "notes":     { exception_id: {note, updated_at} },
      "log":       [ {timestamp, action, override_id, details, note} ]
    }

Every change (adding or removing an override, editing a note) appends a
timestamped entry to "log". Overrides are never deleted; removing one marks it
inactive so the history stays complete.

Override types:
- link_transfer     out_row_id + in_row_id   force two rows to be one transfer
- unlink_transfer   row_id                   never auto-match this row as a transfer
- match_invoice     invoice_id + row_id      assign a payment to an invoice
- unmatch_payment   row_id                   never auto-assign this payment
- opening_balance   wallet_address + date + amount   manual opening balance
- closing_balance   wallet_address + date + amount   manual actual closing balance
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from .common import to_decimal, utc_now_iso

OVERRIDE_FIELDS = {
    "link_transfer": ("out_row_id", "in_row_id"),
    "unlink_transfer": ("row_id",),
    "match_invoice": ("invoice_id", "row_id"),
    "unmatch_payment": ("row_id",),
    "opening_balance": ("wallet_address", "date", "amount"),
    "closing_balance": ("wallet_address", "date", "amount"),
}


def empty_state() -> dict:
    return {"overrides": [], "notes": {}, "log": []}


def _log(state: dict, action: str, details: str, note: str = "", override_id: str = "") -> None:
    state.setdefault("log", []).append({
        "timestamp": utc_now_iso(),
        "action": action,
        "override_id": override_id,
        "details": details,
        "note": note,
    })


def log_event(state: dict, action: str, details: str, note: str = "") -> None:
    """Append a timestamped entry to the override log."""
    _log(state, action, details, note)


def add_override(state: dict, override_type: str, fields: dict, note: str = "") -> dict:
    """Add an override and log it. Raises ValueError on bad input."""
    if override_type not in OVERRIDE_FIELDS:
        raise ValueError(f"Unknown override type: {override_type}")
    missing = [f for f in OVERRIDE_FIELDS[override_type] if not str(fields.get(f, "")).strip()]
    if missing:
        raise ValueError(f"Missing fields for {override_type}: {', '.join(missing)}")
    if override_type.endswith("_balance"):
        to_decimal(fields["amount"])  # validated by conversion

    override = {
        "id": uuid.uuid4().hex[:12],
        "type": override_type,
        **{f: str(fields[f]).strip() for f in OVERRIDE_FIELDS[override_type]},
        "note": note,
        "created_at": utc_now_iso(),
        "active": True,
        "removed_at": None,
    }
    state.setdefault("overrides", []).append(override)
    details = ", ".join(f"{f}={override[f]}" for f in OVERRIDE_FIELDS[override_type])
    _log(state, f"add {override_type}", details, note, override["id"])
    return override


def remove_override(state: dict, override_id: str, note: str = "") -> bool:
    """Deactivate an override (kept for the audit trail) and log it."""
    for override in state.get("overrides", []):
        if override["id"] == override_id and override.get("active"):
            override["active"] = False
            override["removed_at"] = utc_now_iso()
            _log(state, f"remove {override['type']}", f"override {override_id}", note, override_id)
            return True
    return False


def set_note(state: dict, exception_id: str, note: str) -> None:
    """Set the free-text note on an exception and log the change."""
    state.setdefault("notes", {})[exception_id] = {"note": note, "updated_at": utc_now_iso()}
    _log(state, "note", f"exception {exception_id}", note)


def active(state: dict, override_type: str) -> list[dict]:
    return [o for o in state.get("overrides", []) if o.get("active") and o["type"] == override_type]


def manual_transfer_links(state: dict) -> list[tuple[str, str]]:
    return [(o["out_row_id"], o["in_row_id"]) for o in active(state, "link_transfer")]


def unlinked_transfer_rows(state: dict) -> set[str]:
    return {o["row_id"] for o in active(state, "unlink_transfer")}


def manual_invoice_matches(state: dict) -> list[tuple[str, str]]:
    return [(o["invoice_id"], o["row_id"]) for o in active(state, "match_invoice")]


def unmatched_payment_rows(state: dict) -> set[str]:
    return {o["row_id"] for o in active(state, "unmatch_payment")}


def manual_balance(state: dict, kind: str, wallet_address: str, on_date: str) -> Optional[Decimal]:
    """Latest active manual balance of `kind` ("opening"/"closing") for a wallet and date."""
    matches = [
        o for o in active(state, f"{kind}_balance")
        if o["wallet_address"].lower() == wallet_address.lower() and o["date"] == on_date
    ]
    return to_decimal(matches[-1]["amount"]) if matches else None


def note_for(state: dict, exception_id: str) -> str:
    return state.get("notes", {}).get(exception_id, {}).get("note", "")
