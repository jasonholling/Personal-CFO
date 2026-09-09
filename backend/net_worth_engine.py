"""
Single source of truth for net-worth bucketing — used by /api/net-worth,
/api/snapshot, and /api/report/annual, which each used to maintain their
own copy of this logic. That drifted: the report's copy was missing the
"education" (529) and "daf" categories entirely (accounts of those types
were silently dropped from its total_assets/net_worth), and the snapshot
writer didn't exclude kids' custodial/Roth/529 balances the way
/api/net-worth does — so the same real portfolio could show three
different "net worth" numbers depending which page you were on.
"""
from typing import Dict, List

from debt_engine import DEBT_TYPES

CATEGORIES = {
    "investment": ["roth_ira", "ira", "401k", "hsa", "taxable", "custodial"],
    "savings":    ["checking", "savings"],
    "education":  ["529"],
    "real_estate":["real_estate"],
    "business":   ["business"],
    "insurance":  ["insurance"],
    "daf":        ["daf"],
    "other":      ["other"],
}

# Kids-variable-count (2026-09-09): a kid's account owner is
# f"kid_{kid_id}" (that kid's own row id in the `kids` table), not a
# fixed name-derived string like the old "abby"/"cooper" — a kid can be
# renamed without orphaning their accounts, and there can be 0-5 of
# them. is_kid_owner replaces the old KIDS_OWNERS membership check with
# a prefix test so this file never needs the actual kid list, just
# "is this account a kid's."
KID_OWNER_PREFIX = "kid_"

def is_kid_owner(owner) -> bool:
    return bool(owner) and owner.startswith(KID_OWNER_PREFIX)

# Canonical set of account_type values this app actually knows how to
# categorize anywhere (net worth, retirement/Monte Carlo projections,
# allocation, debt payoff). Built from CATEGORIES + DEBT_TYPES so it can't
# drift from the bucketing logic above.
#
# Unlike compute_net_worth's own "other" fallback (added after a
# bug-hunt sandbox found a mistyped account_type silently dropping an
# account's balance from net worth), projection_engine.py's pretax/roth/
# taxable/hsa bucketing checks account_type with exact string equality and
# has no fallback at all — an account_type outside this set doesn't land
# in "other" there, it's just invisible to every retirement/Monte Carlo/
# stress-test number, silently, while still showing up normally on the
# Accounts and (now) Net Worth pages. The account_type field itself has no
# backend whitelist (main.py's Account model declared it as a bare `str`),
# and the Quicken importer's account-type mapping
# (quicken_account_map.local.json) is hand-maintained and gitignored, so a
# typo there is a live risk, not just a hypothetical one. This set is used
# to validate account_type at both entry points (manual Accounts form,
# Quicken import) instead of only guarding net worth after the fact.
VALID_ACCOUNT_TYPES = frozenset(
    {t for types in CATEGORIES.values() for t in types} | DEBT_TYPES
)


def compute_net_worth(accounts: List[Dict]) -> Dict:
    result = {cat: 0.0 for cat in CATEGORIES}
    result["liabilities"] = 0.0
    result["kids_assets"] = 0.0
    for acc in accounts:
        at    = acc["account_type"]
        owner = acc["owner"]
        if at in DEBT_TYPES:
            result["liabilities"] += acc["balance"]
        elif is_kid_owner(owner):
            result["kids_assets"] += acc["balance"]
        else:
            for cat, types in CATEGORIES.items():
                if at in types:
                    result[cat] += acc["balance"]
                    break
            else:
                # An account_type that matches none of the CATEGORIES lists
                # (a stale/mistyped value from a hand-edited Quicken mapping,
                # a direct API/DB write, or a future account type this list
                # hasn't caught up with yet) used to just vanish here —
                # counted in the plain accounts list and its balance, but
                # silently missing from total_assets/net_worth, with nothing
                # in the UI to say so. That's the exact same class of bug
                # this module's own docstring describes fixing for the
                # report/snapshot paths, just one level deeper: fold it into
                # "other" so a whole account's balance can't quietly drop out
                # of net worth.
                result["other"] += acc["balance"]
    result["total_assets"] = sum(result[c] for c in CATEGORIES)
    result["net_worth"]    = result["total_assets"] - result["liabilities"]
    return result


# Standard advisor guidance: 3-6 months of expenses held liquid (checking/
# savings — not invested, not retirement accounts) as a cushion against
# job loss or an emergency without having to sell investments or go into
# debt. 3 months is the floor, 6 is fully funded.
EMERGENCY_FUND_MIN_MONTHS  = 3
EMERGENCY_FUND_FULL_MONTHS = 6


def effective_monthly_expenses(inputs: Dict, cash_flow_summary: Dict = None) -> float:
    """Prefer the itemized Monthly Cash Flow total over the coarse
    `current_monthly_expenses` Settings estimate, once one exists.

    Found via a bug-hunt sandbox: after building out a real Monthly Cash
    Flow plan (actual line items — $3,400/mo), the Emergency Fund check
    (and /api/financial-runway, which computes this exact cash-flow summary
    for its own response in the same request and then didn't use it here)
    kept silently using the old $9,000/mo Settings guess instead — same
    accounts, same liquid assets, but a materially different "months
    covered" (4.4 vs. the correct 11.8) and status ("adequate" vs.
    "funded") depending on which number happened to still be there. Once
    the household has entered actual expense line items, those are more
    accurate than a single manually-typed estimate, so they should win."""
    if cash_flow_summary and cash_flow_summary.get("has_data") and cash_flow_summary.get("monthly_expenses", 0) > 0:
        return cash_flow_summary["monthly_expenses"]
    return inputs.get("current_monthly_expenses", 0)


def emergency_fund_check(accounts, monthly_expenses: float) -> Dict:
    liquid_assets = sum(
        a["balance"] for a in accounts
        if a.get("account_type") in ("checking", "savings") and not is_kid_owner(a.get("owner"))
    )
    if monthly_expenses <= 0:
        return {"has_data": False}

    months_covered = liquid_assets / monthly_expenses
    target_min  = monthly_expenses * EMERGENCY_FUND_MIN_MONTHS
    target_full = monthly_expenses * EMERGENCY_FUND_FULL_MONTHS
    gap_to_min  = max(0, target_min - liquid_assets)
    gap_to_full = max(0, target_full - liquid_assets)

    if months_covered >= EMERGENCY_FUND_FULL_MONTHS:
        status = "funded"
        recommendation = (
            f"You have {months_covered:.1f} months of expenses in checking/savings — fully funded. "
            f"Consider whether cash beyond {EMERGENCY_FUND_FULL_MONTHS} months would work harder invested."
        )
    elif months_covered >= EMERGENCY_FUND_MIN_MONTHS:
        status = "adequate"
        recommendation = (
            f"You have {months_covered:.1f} months of expenses liquid — above the {EMERGENCY_FUND_MIN_MONTHS}-month "
            f"floor, but about ${gap_to_full:,.0f} short of a fully-funded {EMERGENCY_FUND_FULL_MONTHS}-month cushion."
        )
    else:
        status = "underfunded"
        recommendation = (
            f"You have {months_covered:.1f} months of expenses liquid — below the {EMERGENCY_FUND_MIN_MONTHS}-month "
            f"minimum. About ${gap_to_min:,.0f} more in checking/savings would get you to that floor before "
            f"anything else, since it's what stands between a job loss/emergency and having to sell investments "
            f"or go into debt."
        )

    return {
        "has_data": True,
        "liquid_assets": round(liquid_assets),
        "monthly_expenses": round(monthly_expenses),
        "months_covered": round(months_covered, 1),
        "target_min_months": EMERGENCY_FUND_MIN_MONTHS,
        "target_full_months": EMERGENCY_FUND_FULL_MONTHS,
        "gap_to_min": round(gap_to_min),
        "gap_to_full": round(gap_to_full),
        "status": status,
        "recommendation": recommendation,
    }
