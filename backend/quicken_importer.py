"""
Quicken Net Worth CSV importer.
Parses the exported Net Worth Summary CSV and maps accounts to the CFO app schema.
"""

import csv
import io
import re
from typing import List, Dict
from debt_engine import DEBT_TYPES as LIABILITY_TYPES


def _clean_amount(val: str) -> float:
    """Convert Quicken amount string to float."""
    if not val:
        return 0.0
    cleaned = val.replace(',', '').replace('"', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# Map Quicken account names to our schema (account_type, owner)
ACCOUNT_MAP = {
    # Checking
    'first national checking':          ('checking', 'joint'),
    'jason spending':                   ('checking', 'jason'),
    'justin spending':                  ('checking', 'justin'),
    'optum bank':                       ('checking', 'joint'),

    # Savings
    "first national abby's savings":    ('savings', 'abby'),
    "first national cooper's savings":  ('savings', 'cooper'),
    'first national savings':           ('savings', 'joint'),
    'first national savings (justin)':  ('savings', 'justin'),
    'hsabank demand account':           ('savings', 'jason'),
    'marcus online emergency savings':  ('savings', 'joint'),
    'metro fcu savings':                ('savings', 'joint'),

    # HSA investments
    'hsa bank invest':                  ('hsa', 'jason'),

    # Taxable brokerage
    'schwab brokerage creative planning': ('taxable', 'joint'),
    'holling-karas family foundation':   ('daf', 'joint'),
    'sweater cashmere fund':             ('taxable', 'joint'),
    'sweater inc':                       ('taxable', 'joint'),
    'conagra equity plan':              ('taxable', 'jason'),
    'abby - schwab custodial':          ('custodial', 'abby'),
    'cooper - schwab custodial':        ('custodial', 'cooper'),
    'sweater cashmere fund':            ('taxable', 'joint'),
    'sweater inc':                      ('taxable', 'joint'),

    # Retirement
    'empower 401k':                     ('401k', 'jason'),
    'conagra 401k':                     ('401k', 'jason'),
    # conagra pension omitted -- modeled as income stream not asset
    'schwab jason roth':                ('roth_ira', 'jason'),
    'schwab justin ira':                ('ira', 'justin'),
    'abby roth':                        ('roth_ira', 'abby'),
    'cooper roth':                      ('roth_ira', 'cooper'),

    # 529
    'abby 529':                         ('529', 'abby'),
    'cooper 529':                       ('529', 'cooper'),

    # Real estate
    '9013 house':                       ('real_estate', 'joint'),
    '3155 jackson':                     ('real_estate', 'joint'),
    '9315 sterling circle property':    ('real_estate', 'joint'),
    'oakland lot':                      ('real_estate', 'joint'),

    # Business
    'j squared vending llc':            ('business', 'joint'),
    'holling-karas family foundation':  ('other', 'joint'),

    # Insurance
    'justin whole/universal life insurance': ('insurance', 'justin'),

    # Personal property (we'll skip vehicles, track separately)
    '2024 highlander':                  ('other', 'joint'),
    '2024 tundra':                      ('other', 'joint'),
    'bayliner 185 br boat':             ('other', 'joint'),

    # Savings bonds
    'abby savings bonds':               ('other', 'abby'),
    'us savings bonds (x-884-956-001)': ('other', 'joint'),
    'us savings bonds series i (s-552-959-059)': ('other', 'joint'),

    # Mortgages (liabilities)
    'first national mortgage - 9013':   ('mortgage', 'joint'),
    'first national mortgage - 3155':   ('mortgage', 'joint'),
    'first national mortgage - 9315':   ('mortgage', 'joint'),
    'veridian - 9315 sterling circle':  ('mortgage', 'joint'),
}

# Account names to skip (zero-balance old accounts, credit cards we don't track)
SKIP_PATTERNS = [
    'investment 3', 'investment 4', 'investment 6', 'investment 7',
    'old 529', 'w&r ', 'waddell', 'tdameritrade',
    'etrade', 'fidelity', 'gwb', 'metro cd',
    'cag equity', 'conagra 401k',  # zero
    'access account', 'metro overdraft',
    'capital one', 'citibank', 'credit card',  # skip CC liabilities in main tracking
    'first creditline', 'first equityline', 'chase amazon',
    'fnbo credit', 'kohls', 'lowes', 'target credit',
    'wells fargo', 'honda scooter', 'metro fcu tundra',
    'pnc mortgage', 'american national', '9315 sterling circle loan',
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

        # Skip zero balances
        if amount == 0.0:
            continue

        # Skip certain patterns
        name_lower = clean_name.lower()
        if any(pat in name_lower for pat in SKIP_PATTERNS):
            continue

        # Look up in our mapping
        mapped = ACCOUNT_MAP.get(name_lower)
        if not mapped:
            # Try partial match
            for key, val in ACCOUNT_MAP.items():
                if key in name_lower or name_lower in key:
                    mapped = val
                    break

        if not mapped:
            continue  # Skip unmapped accounts

        account_type, owner = mapped

        # Make liabilities positive (store as positive, display as negative)
        if account_type in LIABILITY_TYPES:
            balance = abs(amount)
        else:
            balance = abs(amount)

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
    if 'conagra' in name_lower:  return 'ConAgra'
    if 'fidelity' in name_lower: return 'Fidelity'
    if 'first national' in name_lower: return 'First National'
    if 'hsa' in name_lower:      return 'HSA Bank'
    if 'marcus' in name_lower:   return 'Marcus'
    if 'vending' in name_lower:  return 'J Squared Vending'
    if '529' in name_lower:      return 'Schwab'
    if 'roth' in name_lower:     return 'Schwab'
    if 'ira' in name_lower:      return 'Schwab'
    if '401k' in name_lower:     return 'Empower'
    if 'pension' in name_lower:  return 'ConAgra'
    if 'house' in name_lower or 'jackson' in name_lower or 'sterling' in name_lower: return 'Real Estate'
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
