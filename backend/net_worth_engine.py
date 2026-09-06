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

KIDS_OWNERS = {"abby", "cooper"}


def compute_net_worth(accounts: List[Dict]) -> Dict:
    result = {cat: 0.0 for cat in CATEGORIES}
    result["liabilities"] = 0.0
    result["kids_assets"] = 0.0
    for acc in accounts:
        at    = acc["account_type"]
        owner = acc["owner"]
        if at in DEBT_TYPES:
            result["liabilities"] += acc["balance"]
        elif owner in KIDS_OWNERS:
            result["kids_assets"] += acc["balance"]
        else:
            for cat, types in CATEGORIES.items():
                if at in types:
                    result[cat] += acc["balance"]
                    break
    result["total_assets"] = sum(result[c] for c in CATEGORIES)
    result["net_worth"]    = result["total_assets"] - result["liabilities"]
    return result


# Standard advisor guidance: 3-6 months of expenses held liquid (checking/
# savings — not invested, not retirement accounts) as a cushion against
# job loss or an emergency without having to sell investments or go into
# debt. 3 months is the floor, 6 is fully funded.
EMERGENCY_FUND_MIN_MONTHS  = 3
EMERGENCY_FUND_FULL_MONTHS = 6


def emergency_fund_check(accounts, monthly_expenses: float) -> Dict:
    liquid_assets = sum(
        a["balance"] for a in accounts
        if a.get("account_type") in ("checking", "savings") and a.get("owner") not in ("abby", "cooper")
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
