"""
Quicken Net Worth CSV importer.
Parses the exported Net Worth Summary CSV and maps accounts to the CFO app schema.
"""

import csv
import io
import re
import json
import os
from typing import List, Dict
from debt_engine import DEBT_TYPES as LIABILITY_TYPES


def _clean_amount(val: str):
    """Convert a Quicken amount string to float, or None if it can't be
    parsed. Distinct from a genuine "0.00" — an account paid off or
    emptied to exactly $0 is real, current data (external audit
    2026-09-07: this used to return 0.0 for both a real zero AND an
    unparseable string, and the parse loop below then skipped BOTH cases
    identically as "zero balance", so re-importing an account that's now
    genuinely empty left its old nonzero balance in the app untouched —
    the import silently succeeded without ever reflecting the payoff)."""
    if not val:
        return None
    cleaned = val.replace(',', '').replace('"', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


"""Public mappings intentionally contain only generic account labels.

Personal account, employer, property, vehicle, and routing-style labels belong
in backend/quicken_account_map.local.json (gitignored)."""
ACCOUNT_MAP = {
    # Keep this empty in the public project. Local account aliases belong in
    # quicken_account_map.local.json, which is intentionally gitignored.
}

_LOCAL_MAP_PATH = os.path.join(os.path.dirname(__file__), "quicken_account_map.local.json")
def _account_map():
    mappings = dict(ACCOUNT_MAP)
    try:
        with open(_LOCAL_MAP_PATH) as f:
            mappings.update({k.lower(): tuple(v) for k, v in json.load(f).items()})
    except (OSError, ValueError, TypeError):
        pass
    return mappings

# Account names to skip (zero-balance old accounts, credit cards we don't track)
SKIP_PATTERNS = [
    'investment 3', 'investment 4', 'investment 6', 'investment 7',
    'old 529', 'w&r ', 'waddell', 'tdameritrade',
    'etrade', 'fidelity', 'gwb', 'metro cd',
    'legacy equity plan', 'legacy 401k',  # zero-balance accounts
    'access account', 'metro overdraft',
    'capital one', 'citibank', 'credit card',  # skip CC liabilities in main tracking
    'first creditline', 'first equityline', 'chase amazon',
    'fnbo credit', 'kohls', 'lowes', 'target credit',
    'wells fargo', 'scooter', 'vehicle loan',
    'mortgage loan', 'insurance carrier', 'property loan',
    'veridian credit', 'hsabank schwab',
    '5023 seward',  # zero
]

def parse_quicken_networth_csv(csv_content: str) -> List[Dict]:
    """
    Parse a Quicken Net Worth Summary CSV export.
    Returns a list of account dicts ready to upsert into the CFO database.
    """
    accounts = []
    lines = csv_content.splitlines()

    # Find the data rows (skip header lines until we hit the dashes)
    in_data = False
    for line in lines:
        if '---' in line:
            in_data = True
            continue
        if not in_data:
            continue

        # Parse CSV line
        try:
            reader = csv.reader([line])
            parts = next(reader)
        except Exception:
            continue

        # We need at least 3 columns
        if len(parts) < 3:
            continue

        raw_name = parts[1].strip().strip('"').strip()
        raw_amount = parts[2].strip().strip('"').strip() if len(parts) > 2 else ''

        # Skip totals and section headers
        if not raw_name or not raw_amount:
            continue
        if raw_name.startswith('Total') or raw_name in ('Assets', 'Liabilities', 'Checking', 'Savings', 'Investments', 'Brokerage', 'Retirement', 'Property', 'Credit Card', 'Loan'):
            continue
        if raw_name.startswith('-  - Total') or raw_name.startswith('- Total'):
            continue

        # Clean up the name (strip leading dashes and spaces)
        clean_name = re.sub(r'^[\s\-]+', '', raw_name).strip()
        amount = _clean_amount(raw_amount)

        # Skip rows that genuinely couldn't be parsed — but NOT a real
        # $0.00 balance, which must still go through and update/zero the
        # matching account (see _clean_amount's docstring).
        if amount is None:
            continue

        # Skip certain patterns
        name_lower = clean_name.lower()
        if any(pat in name_lower for pat in SKIP_PATTERNS):
            continue

        # Look up in our mapping
        account_map = _account_map()
        mapped = account_map.get(name_lower)
        if not mapped:
            # Try partial match
            for key, val in account_map.items():
                if key in name_lower or name_lower in key:
                    mapped = val
                    break

        if not mapped:
            continue  # Skip unmapped accounts

        account_type, owner = mapped

        # Make liabilities positive (Quicken exports them negative; this
        # app stores every liability balance as a positive magnitude —
        # see debt_engine.py). Assets keep their real sign — this used to
        # apply the same abs() to both branches (external audit
        # 2026-09-07), silently flipping a negative asset balance (e.g. an
        # overdrawn checking account, or a Quicken export quirk) positive.
        balance = abs(amount) if account_type in LIABILITY_TYPES else amount

        accounts.append({
            'name': clean_name,
            'account_type': account_type,
            'owner': owner,
            'institution': _guess_institution(clean_name),
            'balance': balance,
            'notes': f'Imported from Quicken {__import__("datetime").date.today().isoformat()}',
        })

    return accounts


def _guess_institution(name: str) -> str:
    name_lower = name.lower()
    if 'schwab' in name_lower:   return 'Schwab'
    if 'empower' in name_lower:  return 'Empower'
    if 'fidelity' in name_lower: return 'Fidelity'
    if 'first national' in name_lower: return 'First National'
    if 'hsa' in name_lower:      return 'HSA Bank'
    if 'marcus' in name_lower:   return 'Marcus'
    if '529' in name_lower:      return 'Schwab'
    if 'roth' in name_lower:     return 'Schwab'
    if 'ira' in name_lower:      return 'Schwab'
    if '401k' in name_lower:     return 'Empower'
    if 'house' in name_lower or 'property' in name_lower: return 'Real Estate'
    return ''


def get_net_worth_summary(accounts: List[Dict]) -> Dict:
    """Quick summary from parsed accounts."""
    investment_types = {'roth_ira', 'ira', '401k', 'hsa', 'taxable', 'custodial'}
    total_assets = sum(a['balance'] for a in accounts if a['account_type'] not in LIABILITY_TYPES)
    liabilities = sum(a['balance'] for a in accounts if a['account_type'] in LIABILITY_TYPES)
    investments = sum(a['balance'] for a in accounts if a['account_type'] in investment_types)
    return {
        'total_assets': total_assets,
        'liabilities': liabilities,
        'net_worth': total_assets - liabilities,
        'investments': investments,
        'account_count': len(accounts),
    }
