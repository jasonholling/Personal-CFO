"""
Schwab "Positions" CSV export importer (2026-09-14, at the user's
request -- Abby's Roth, Cooper's Roth, Cooper's custodial, Abby's
custodial, and the Creative Planning brokerage account are all Schwab
accounts and each can produce this same export). Unlike a QFX/OFX
statement, this is Schwab's own downloadable "Positions" report: a
one-line account/as-of preamble, a blank line, a quoted-CSV header row,
one row per position, a "Cash & Cash Investments" row, and a
"Positions Total" summary row carrying the account's own total market
value -- the same "one file, both holdings and balance" idea as the
QFX importer, just a different, brokerage-native export shape.

Deliberately tolerant of Schwab's export quirks: every numeric cell is
quoted and dollar/percent/comma-formatted ("$99,641.22", "-1.26%"), Qty
uses thousands separators ("1,000"), and any unknown/blank cell is the
literal string "--" rather than empty -- none of that should reach the
caller as anything but a clean float or None.
"""
import csv
import re
from datetime import datetime
from io import StringIO
from typing import Dict, List, Optional

_PREAMBLE_RE = re.compile(
    r'Positions for account\s+(?P<label>.+?)\s+as of\s+.*?,\s*(?P<date>\d{4}/\d{2}/\d{2})',
    re.IGNORECASE,
)


def _money(s: Optional[str]) -> Optional[float]:
    """"$99,641.22" / "-$1,273.32" / "--" / "" -> float or None."""
    if s is None:
        return None
    s = s.strip()
    if not s or s == "--":
        return None
    neg = s.startswith("-")
    s = s.lstrip("-").replace("$", "").replace(",", "").strip()
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if neg else value


def parse_schwab_positions_csv(csv_text: str) -> Dict:
    """Returns {"positions": [...], "account_balance": float|None,
    "as_of_date": str|None, "account_label": str|None, "errors": [...]}.

    Each position: {"ticker", "security_name", "shares", "market_value",
    "cost_basis", "as_of_date"}. The "Cash & Cash Investments" row
    becomes a position of its own (ticker "CASH") rather than being
    dropped -- it's real money in the account and belongs in the sum
    just like every other row; the caller is expected to classify it
    asset_class="cash" (unambiguous from this file, unlike a security's
    real asset class) rather than "unclassified" like a brand-new
    security row. The "Positions Total" row is never treated as a
    position -- it's where account_balance comes from."""
    errors: List[str] = []
    lines = csv_text.splitlines()
    if not lines:
        return {"positions": [], "account_balance": None, "as_of_date": None,
                "account_label": None, "errors": ["Empty file."]}

    m = _PREAMBLE_RE.search(lines[0])
    account_label = m.group("label").strip() if m else None
    as_of_date = None
    if m:
        try:
            as_of_date = datetime.strptime(m.group("date"), "%Y/%m/%d").date().isoformat()
        except ValueError:
            as_of_date = None

    header_idx = next((i for i, l in enumerate(lines) if l.lstrip().startswith('"Symbol"')), None)
    if header_idx is None:
        return {"positions": [], "account_balance": None, "as_of_date": as_of_date,
                "account_label": account_label,
                "errors": ["This doesn't look like a Schwab Positions export (no \"Symbol\" header row found)."]}

    reader = csv.DictReader(StringIO("\n".join(lines[header_idx:])))
    positions = []
    account_balance = None
    for row in reader:
        symbol = (row.get("Symbol") or "").strip()
        if not symbol:
            continue
        if symbol == "Positions Total":
            account_balance = _money(row.get("Mkt Val (Market Value)"))
            continue
        mktval = _money(row.get("Mkt Val (Market Value)"))
        if mktval is None:
            errors.append(f"Skipped a row with no market value: {symbol}.")
            continue
        if symbol == "Cash & Cash Investments":
            positions.append({
                "ticker": "CASH", "security_name": "Cash & Cash Investments",
                "shares": None, "market_value": round(mktval, 2),
                "cost_basis": None, "as_of_date": as_of_date,
            })
            continue
        shares_raw = (row.get("Qty (Quantity)") or "").replace(",", "").strip()
        try:
            shares = float(shares_raw) if shares_raw and shares_raw != "--" else None
        except ValueError:
            shares = None
        positions.append({
            "ticker": symbol,
            "security_name": (row.get("Description") or symbol).strip() or symbol,
            "shares": shares,
            "market_value": round(mktval, 2),
            "cost_basis": _money(row.get("Cost Basis")),
            "as_of_date": as_of_date,
        })

    return {"positions": positions, "account_balance": account_balance,
            "as_of_date": as_of_date, "account_label": account_label, "errors": errors}
