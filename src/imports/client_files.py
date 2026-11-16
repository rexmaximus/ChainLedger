"""
Client File Import

Reads CSV or Excel files supplied by a client and turns their rows into
normalized records using a column mapping chosen by the user.

Supported file types:
- ledger       A general ledger / account export for a crypto wallet account.
               Each line is expected to appear on-chain.
- exchange     An exchange transaction history. Deposits and withdrawals are
               expected on-chain; trades and other lines are not.
- receivables  A list of invoices the client issued (accounts receivable).
               Matched against incoming payments like ChainLedger invoices.

Only the first sheet of an Excel file is read, and the first non-empty row is
taken as the header row. PDF files are not supported.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

FILE_TYPES = {
    "ledger": "Client ledger / GL export",
    "exchange": "Exchange transaction history",
    "receivables": "Receivables (invoice list)",
}

# field name -> (label, required)
MAPPING_FIELDS = {
    "ledger": {
        "date": ("Date", True),
        "amount": ("Signed amount (+ received, − sent)", False),
        "debit": ("Debit (received)", False),
        "credit": ("Credit (sent)", False),
        "asset": ("Asset / currency", False),
        "tx_hash": ("Transaction hash", False),
        "counterparty": ("Counterparty / address", False),
        "reference": ("Reference / entry number", False),
        "description": ("Description / memo", False),
    },
    "exchange": {
        "date": ("Date", True),
        "type": ("Transaction type", True),
        "asset": ("Asset / currency", False),
        "amount": ("Amount", True),
        "fee": ("Fee", False),
        "tx_hash": ("Transaction hash", False),
        "counterparty": ("Address", False),
        "reference": ("Reference / ID", False),
        "description": ("Description / notes", False),
    },
    "receivables": {
        "invoice_number": ("Invoice number", True),
        "date": ("Invoice date", True),
        "amount": ("Amount", True),
        "asset": ("Asset / currency", False),
        "customer": ("Customer", False),
        "payer_wallet": ("Customer wallet", False),
        "receiving_wallet": ("Receiving wallet", False),
        "status": ("Status", False),
    },
}

DATE_FORMATS = {
    "iso": "ISO (2024-03-31 or 2024-03-31T14:00:00)",
    "mdy": "MM/DD/YYYY",
    "dmy": "DD/MM/YYYY",
}

DEFAULT_WITHDRAWAL_WORDS = ["withdraw", "send", "sent", "payout"]
DEFAULT_DEPOSIT_WORDS = ["deposit", "receive", "received"]

# Header words used to suggest a mapping
SUGGEST = {
    "date": ["date", "time", "timestamp", "posted", "created"],
    "amount": ["amount", "quantity", "qty", "value", "total"],
    "debit": ["debit", "dr"],
    "credit": ["credit", "cr"],
    "asset": ["asset", "currency", "coin", "token", "symbol", "ccy"],
    "tx_hash": ["hash", "txid", "tx id", "transaction id", "txhash", "tx hash"],
    "counterparty": ["address", "counterparty", "wallet", "to", "from", "payee", "payer"],
    "reference": ["reference", "ref", "entry", "journal", "id"],
    "description": ["description", "memo", "note", "notes", "details"],
    "type": ["type", "kind", "transaction type", "category", "side"],
    "fee": ["fee"],
    "invoice_number": ["invoice", "invoice number", "invoice #", "number", "doc"],
    "customer": ["customer", "client", "name", "bill to"],
    "payer_wallet": ["customer wallet", "payer", "from address", "client wallet"],
    "receiving_wallet": ["receiving", "pay to", "deposit address", "our wallet"],
    "status": ["status"],
}


class ImportErrorWithRows(ValueError):
    """Raised when a file cannot be read at all."""


@dataclass
class ParsedFile:
    headers: list[str]
    rows: list[dict]  # header -> raw string value; "_line" holds the file line number


@dataclass
class MappingResult:
    records: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # "line N: problem"
    skipped: int = 0  # blank rows


# =============================================================================
# Reading files
# =============================================================================

def _cell_to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_file(filename: str, content: bytes) -> ParsedFile:
    """Read a CSV or XLSX file into headers and row dicts."""
    name = filename.lower()
    if name.endswith(".xlsx"):
        table = _read_xlsx(content)
    elif name.endswith(".csv") or name.endswith(".txt"):
        table = _read_csv(content)
    else:
        raise ImportErrorWithRows("Unsupported file type. Upload a .csv or .xlsx file.")

    # First non-empty row is the header
    start = next((i for i, r in enumerate(table) if any(c.strip() for c in r)), None)
    if start is None:
        raise ImportErrorWithRows("The file is empty.")
    headers = [h.strip() or f"Column {i + 1}" for i, h in enumerate(table[start])]
    seen: dict[str, int] = {}
    for i, h in enumerate(headers):  # make duplicate headers unique
        if h in seen:
            seen[h] += 1
            headers[i] = f"{h} ({seen[h]})"
        else:
            seen[h] = 1

    rows = []
    for offset, raw in enumerate(table[start + 1:], start=start + 2):
        values = list(raw) + [""] * (len(headers) - len(raw))
        row = {h: values[i].strip() for i, h in enumerate(headers)}
        row["_line"] = offset
        rows.append(row)
    return ParsedFile(headers=headers, rows=rows)


def _read_csv(content: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def _read_xlsx(content: bytes) -> list[list[str]]:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:
        raise ImportErrorWithRows(f"Could not read the Excel file: {e}")
    ws = wb.worksheets[0]
    return [[_cell_to_str(v) for v in row] for row in ws.iter_rows(values_only=True)]


# =============================================================================
# Mapping
# =============================================================================

def suggest_mapping(file_type: str, headers: list[str]) -> dict[str, str]:
    """Guess a column for each field from header names (exact word match first)."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    lowered = {h: h.lower().strip() for h in headers}
    for field_name in MAPPING_FIELDS.get(file_type, {}):
        words = SUGGEST.get(field_name, [])
        pick = next((h for h in headers if h not in used and lowered[h] in words), None)
        if pick is None:
            pick = next((h for h in headers if h not in used
                         and any(re.search(rf"\b{re.escape(w)}\b", lowered[h]) for w in words)), None)
        if pick:
            mapping[field_name] = pick
            used.add(pick)
    return mapping


def parse_amount(text: str) -> Optional[Decimal]:
    """Parse '1,234.50', '(12.5)', '-3', '$40', '0.5 ETH'. Empty -> None."""
    raw = (text or "").strip()
    if not raw:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    try:
        value = Decimal(raw.replace(",", ""))  # plain or scientific notation
        return -abs(value) if negative else value
    except InvalidOperation:
        pass
    cleaned = re.sub(r"[^0-9.\-]", "", raw.replace(",", ""))
    if cleaned in ("", "-", "."):
        raise ValueError(f"not a number: {text!r}")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"not a number: {text!r}")
    return -abs(value) if negative else value


def parse_datetime(text: str, date_format: str = "iso") -> datetime:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("date is empty")
    if date_format in ("mdy", "dmy"):
        date_part, _, time_part = raw.partition(" ")
        pattern = "%m/%d/%Y" if date_format == "mdy" else "%d/%m/%Y"
        for sep in ("/", "-", "."):
            try:
                day = datetime.strptime(date_part.replace(sep, "/"), pattern)
                break
            except ValueError:
                day = None
        if day is None:
            raise ValueError(f"date {text!r} does not match {DATE_FORMATS[date_format]}")
        if time_part:
            try:
                t = datetime.strptime(time_part[:8], "%H:%M:%S" if time_part.count(":") == 2 else "%H:%M")
                day = day.replace(hour=t.hour, minute=t.minute, second=t.second)
            except ValueError:
                pass
        return day
    cleaned = raw.replace("Z", "").replace(" UTC", "")
    try:
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is not None:  # convert to UTC, then store as naive UTC
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        raise ValueError(f"date {text!r} is not ISO format (YYYY-MM-DD); choose another date format")


def _words(text: str) -> list[str]:
    return [w.strip().lower() for w in (text or "").split(",") if w.strip()]


def apply_mapping(
    file_id: str,
    file_type: str,
    parsed: ParsedFile,
    mapping: dict,
) -> MappingResult:
    """
    Convert rows to normalized records.

    `mapping` holds field -> column, plus options:
        date_format       "iso" | "mdy" | "dmy"
        default_asset     asset when no asset column is mapped (e.g. "ETH")
        withdrawal_words  exchange: comma-separated words meaning withdrawal
        deposit_words     exchange: comma-separated words meaning deposit

    Ledger/exchange records:
        record_id, file_id, line, date (ISO), asset, amount (positive str),
        chain_direction ("In" = into a wallet, "Out" = out of a wallet, "" = off-chain),
        fee, tx_hash, counterparty, reference, description, raw_type
    Receivables records are invoice dicts compatible with the invoice matcher.
    """
    if file_type not in MAPPING_FIELDS:
        raise ValueError(f"Unknown file type: {file_type}")
    fields = MAPPING_FIELDS[file_type]
    result = MappingResult()

    missing = [label for f, (label, required) in fields.items() if required and not mapping.get(f)]
    if file_type == "ledger" and not mapping.get("amount") and not (mapping.get("debit") or mapping.get("credit")):
        missing.append("Signed amount, or Debit/Credit")
    if not mapping.get("asset") and not (mapping.get("default_asset") or "").strip():
        missing.append("Asset column or a default asset")
    if missing:
        result.errors.append("Mapping incomplete: " + ", ".join(missing))
        return result
    for f in fields:
        column = mapping.get(f)
        if column and column not in parsed.headers:
            result.errors.append(f"Column {column!r} not found in file")
    if result.errors:
        return result

    date_format = mapping.get("date_format") or "iso"
    default_asset = (mapping.get("default_asset") or "").strip().upper()
    withdrawal_words = _words(mapping.get("withdrawal_words", "")) or DEFAULT_WITHDRAWAL_WORDS
    deposit_words = _words(mapping.get("deposit_words", "")) or DEFAULT_DEPOSIT_WORDS

    def get(row, f):
        column = mapping.get(f)
        return row.get(column, "").strip() if column else ""

    for row in parsed.rows:
        line = row["_line"]
        if not any(str(v).strip() for k, v in row.items() if k != "_line"):
            result.skipped += 1
            continue
        try:
            when = parse_datetime(get(row, "date"), date_format)
            asset = (get(row, "asset") or default_asset).upper()
            if not asset:
                raise ValueError("asset is empty")
            record = {
                "record_id": f"{file_id}:{line}",
                "file_id": file_id,
                "line": line,
                "date": when.isoformat(),
                "asset": asset,
            }

            if file_type == "receivables":
                amount = parse_amount(get(row, "amount"))
                if amount is None:
                    raise ValueError("amount is empty")
                result.records.append({
                    **record,
                    "id": f"file:{file_id}:{line}",
                    "invoice_number": get(row, "invoice_number"),
                    "created_at": when.isoformat(),
                    "crypto_amount": str(abs(amount)),
                    "token_type": asset,
                    "recipient_name": get(row, "customer"),
                    "recipient_wallet": get(row, "payer_wallet"),
                    "sender_wallet": get(row, "receiving_wallet"),
                    "status": (get(row, "status") or "").lower(),
                    "source": "client file",
                })
                continue

            if file_type == "ledger":
                if mapping.get("amount"):
                    signed = parse_amount(get(row, "amount"))
                else:
                    debit = parse_amount(get(row, "debit")) or Decimal(0)
                    credit = parse_amount(get(row, "credit")) or Decimal(0)
                    signed = abs(debit) - abs(credit)
                if signed is None or signed == 0:
                    raise ValueError("amount is empty or zero")
                direction = "In" if signed > 0 else "Out"
                amount = abs(signed)
                raw_type = ""
            else:  # exchange
                amount = parse_amount(get(row, "amount"))
                if amount is None:
                    raise ValueError("amount is empty")
                amount = abs(amount)
                raw_type = get(row, "type")
                kind = raw_type.lower()
                # Withdrawal from the exchange arrives in a wallet (In);
                # a deposit to the exchange leaves a wallet (Out).
                if any(w in kind for w in withdrawal_words):
                    direction = "In"
                elif any(w in kind for w in deposit_words):
                    direction = "Out"
                else:
                    direction = ""  # trade, interest, etc.: not expected on-chain

            fee = parse_amount(get(row, "fee")) if mapping.get("fee") else None
            result.records.append({
                **record,
                "amount": str(amount),
                "chain_direction": direction,
                "fee": str(abs(fee)) if fee else "0",
                "tx_hash": get(row, "tx_hash"),
                "counterparty": get(row, "counterparty"),
                "reference": get(row, "reference"),
                "description": get(row, "description"),
                "raw_type": raw_type,
                "file_type": file_type,
            })
        except ValueError as e:
            result.errors.append(f"line {line}: {e}")
    return result
