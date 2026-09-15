"""
QFX/OFX investment-statement importer (2026-09-14, at the user's request
-- "it's going to be hard to keep the balance and holdings in sync all
the time"). A custodian-exported QFX file (Quicken's flavor of OFX) for
an investment/401(k) account carries BOTH the current per-security
positions (<INVPOSLIST>) AND the account's own total balance
(<INV401KBAL> for a 401(k), <INVBAL>/<BALLIST> for a taxable/brokerage
account) in one file -- importing it keeps holdings and the parent
account's balance in sync automatically (both come from the same
custodian snapshot, verified reconcilable by construction), instead of
the household hand-typing two separate numbers that drift apart.

Deliberately regex-based, not a strict XML parser -- real-world OFX/QFX
files are SGML (older exports omit closing tags on leaf elements), so a
strict parser would reject files a well-behaved regex extractor handles
fine. Every tag is optional; a section this custodian doesn't emit
(e.g. no <SECLIST> ticker for a security) degrades to a best-effort
label rather than a hard failure.
"""
import re
from typing import Dict, List, Optional


def _tag(name: str, text: str) -> Optional[str]:
    """First occurrence of <name>value(</name>)? -- value runs to the
    next '<' since OFX SGML leaf tags aren't reliably closed."""
    m = re.search(rf'<{name}>\s*([^<\r\n]*)', text, re.IGNORECASE)
    return m.group(1).strip() or None if m else None


def _blocks(tag: str, text: str) -> List[str]:
    """Every <tag>...</tag> block, non-greedy -- used for repeating
    elements (POSMF/POSSTOCK/POSOTHER, MFINFO/STOCKINFO/OTHERINFO)."""
    return re.findall(rf'<{tag}>(.*?)</{tag}>', text, re.IGNORECASE | re.DOTALL)


def _float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# Every position-wrapper OFX defines (mutual fund, individual stock/ETF,
# or an "other" security type -- money market, stable value, etc.). All
# three carry the identical <INVPOS> shape inside.
_POSITION_WRAPPERS = ("POSMF", "POSSTOCK", "POSOPT", "POSOTHER", "POSDEBT")
# Matching SECLIST wrapper for each security type.
_SECINFO_WRAPPERS = ("MFINFO", "STOCKINFO", "OPTINFO", "OTHERINFO", "DEBTINFO")


def parse_qfx_positions(qfx_text: str) -> Dict:
    """Returns {"positions": [...], "account_balance": float|None,
    "as_of_date": str|None, "account_id_in_file": str|None, "errors": [...]}.

    Each position: {"cusip", "ticker", "security_name", "shares",
    "market_value", "as_of_date"}. `ticker` falls back to the security's
    own CUSIP/UNIQUEID when the file has no real ticker (common for a
    401(k) plan's proprietary/collective-trust funds) -- never blank,
    since every downstream holding needs SOME identifying label.

    `account_balance`: the file's own total, read from whichever balance
    tag this custodian actually emits (<INV401KBAL><TOTAL> for a 401(k)
    export, <INVBAL><BALLIST><BAL><NAME>...VALUE for a brokerage export,
    or a plain <AVAILCASH>+positions fallback) -- None if the file has
    no balance section at all (some custodians omit it; the caller
    should then fall back to summing the parsed positions, which is
    exactly what the sum SHOULD equal when the custodian doesn't
    mis-state it)."""
    errors: List[str] = []
    if "<OFX>" not in qfx_text.upper():
        return {"positions": [], "account_balance": None, "as_of_date": None,
                "account_id_in_file": None, "errors": ["This doesn't look like a QFX/OFX file (no <OFX> section found)."]}

    account_id_in_file = _tag("ACCTID", qfx_text)

    # SECLIST: CUSIP/UNIQUEID -> (security_name, ticker). Built once,
    # looked up per position below.
    sec_by_id: Dict[str, Dict] = {}
    for wrapper in _SECINFO_WRAPPERS:
        for block in _blocks(wrapper, qfx_text):
            uid = _tag("UNIQUEID", block)
            if not uid:
                continue
            sec_by_id[uid] = {
                "security_name": _tag("SECNAME", block),
                "ticker": _tag("TICKER", block),
            }

    positions = []
    for wrapper in _POSITION_WRAPPERS:
        for block in _blocks(wrapper, qfx_text):
            uid = _tag("UNIQUEID", block)
            units = _float(_tag("UNITS", block))
            mktval = _float(_tag("MKTVAL", block))
            if uid is None or mktval is None:
                errors.append(f"Skipped a position missing a security id or market value ({wrapper}).")
                continue
            sec = sec_by_id.get(uid, {})
            ticker = sec.get("ticker") or uid
            positions.append({
                "cusip": uid,
                "ticker": ticker,
                "security_name": sec.get("security_name") or ticker,
                "shares": units,
                "market_value": round(mktval, 2),
                "as_of_date": _ofx_date(_tag("DTPRICEASOF", block)),
            })

    # Balance: try every shape a real custodian export uses, in order of
    # how unambiguous each one is. <INV401KBAL><TOTAL> is 401(k)-specific
    # and always the household's real total when present. A brokerage
    # export's <INVBAL> instead nests a <BALLIST> of named balances (no
    # single guaranteed "total" tag across custodians) -- AVAILCASH is
    # the closest reliable total-cash-and-securities figure OFX defines
    # for that shape; if neither is present, the caller falls back to
    # summing the parsed positions itself.
    account_balance = _float(_tag("TOTAL", _first_block("INV401KBAL", qfx_text) or ""))
    if account_balance is None:
        invbal = _first_block("INVBAL", qfx_text)
        if invbal is not None:
            account_balance = _float(_tag("AVAILCASH", invbal))

    as_of_date = _ofx_date(_tag("DTASOF", qfx_text))
    return {"positions": positions, "account_balance": account_balance,
            "as_of_date": as_of_date, "account_id_in_file": account_id_in_file, "errors": errors}


def _first_block(tag: str, text: str) -> Optional[str]:
    blocks = _blocks(tag, text)
    return blocks[0] if blocks else None


def _ofx_date(raw: Optional[str]) -> Optional[str]:
    """OFX packs a date as YYYYMMDDHHMMSS.mmm -- this app's date fields
    are plain YYYY-MM-DD everywhere else, so normalize at the parser
    boundary rather than leaking the OFX format into holdings rows."""
    if not raw or len(raw) < 8:
        return None
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
