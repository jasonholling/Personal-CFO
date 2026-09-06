"""
Asset allocation/rebalancing recommendation and investment fee audit.

Both operate entirely on data you've already entered in Accounts — no new
data entry required for fee audit beyond an optional expense_ratio field,
and allocation uses a per-account stock_allocation_pct (defaults to a
reasonable assumption, flagged when unset so you know the number is a
guess, not something you told it).
"""
from typing import Dict, List

INVESTMENT_TYPES = {"401k", "ira", "roth_ira", "taxable", "hsa", "custodial"}
KIDS_OWNERS = {"abby", "cooper"}

DEFAULT_STOCK_ALLOCATION_PCT = 80  # assumed split for any account without one set
REBALANCE_THRESHOLD_PCT = 5        # deviation from target before we recommend action

LOW_COST_INDEX_EXPENSE_RATIO = 0.0003  # ~0.03%, typical broad-market index fund


def _target_stock_pct(age: int) -> float:
    """Simple age-based glide path: 110 minus age, floored at 20% so it
    never recommends going fully to bonds even very late in retirement."""
    return max(20.0, min(100.0, 110 - age))


def analyze_allocation(inputs: Dict, accounts: List[Dict]) -> Dict:
    holdings = [
        a for a in accounts
        if a.get("account_type") in INVESTMENT_TYPES and a.get("owner") not in KIDS_OWNERS and a.get("balance", 0) > 0
    ]
    total_investable = sum(a["balance"] for a in holdings)
    if total_investable <= 0:
        return {"has_data": False}

    weighted_stock = sum(
        a["balance"] * (a.get("stock_allocation_pct") if a.get("stock_allocation_pct") is not None else DEFAULT_STOCK_ALLOCATION_PCT) / 100
        for a in holdings
    )
    current_stock_pct = weighted_stock / total_investable * 100
    current_bond_pct = 100 - current_stock_pct

    age = inputs.get("jason_age", 50)
    target_stock_pct = _target_stock_pct(age)
    deviation = current_stock_pct - target_stock_pct
    needs_rebalance = abs(deviation) >= REBALANCE_THRESHOLD_PCT
    dollar_shift = abs(deviation) / 100 * total_investable

    missing_data = [a["name"] for a in holdings if a.get("stock_allocation_pct") is None]

    if needs_rebalance:
        direction = "stocks into bonds" if deviation > 0 else "bonds into stocks"
        recommendation = (
            f"You're {current_stock_pct:.0f}% stocks against a {target_stock_pct:.0f}% target for age {age}. "
            f"Shift about ${dollar_shift:,.0f} from {direction} to get back on target — do it inside your "
            f"401k/IRA first so you don't trigger capital gains in a taxable account."
        )
    else:
        recommendation = (
            f"Your {current_stock_pct:.0f}% stock allocation is within {REBALANCE_THRESHOLD_PCT} points of the "
            f"{target_stock_pct:.0f}% target for age {age} — no rebalancing needed right now."
        )
    if missing_data:
        recommendation += (
            f" Note: {len(missing_data)} account(s) don't have a stock/bond split entered, so they're assumed "
            f"{DEFAULT_STOCK_ALLOCATION_PCT}% stocks — set the real split on those accounts for an accurate number."
        )

    real_estate = sum(a["balance"] for a in accounts if a.get("account_type") == "real_estate")
    cash = sum(a["balance"] for a in accounts if a.get("account_type") in ("checking", "savings"))

    return {
        "has_data": True,
        "current_stock_pct": round(current_stock_pct, 1),
        "current_bond_pct": round(current_bond_pct, 1),
        "target_stock_pct": round(target_stock_pct, 1),
        "total_investable": round(total_investable),
        "real_estate_balance": round(real_estate),
        "cash_balance": round(cash),
        "needs_rebalance": needs_rebalance,
        "dollar_shift_needed": round(dollar_shift),
        "recommendation": recommendation,
        "accounts_missing_allocation_data": missing_data,
    }


def analyze_fees(accounts: List[Dict], years: int = 30, expected_return: float = 0.07) -> Dict:
    # Same owner filter as analyze_allocation — a kid's custodial account
    # with an expense ratio set used to count in this function's fee-drag
    # total while being excluded from analyze_allocation's total_investable,
    # so the two numbers on the same page could disagree about what
    # "your investable assets" even means.
    holdings = [
        a for a in accounts
        if a.get("account_type") in INVESTMENT_TYPES and a.get("owner") not in KIDS_OWNERS and a.get("expense_ratio", 0) > 0
    ]
    if not holdings:
        return {"has_fee_data": False}

    results = []
    total_annual_fee = 0.0
    total_fee_drag = 0.0
    for a in holdings:
        er = a["expense_ratio"]
        annual_fee = a["balance"] * er
        fv_at_current_fee = a["balance"] * ((1 + expected_return - er) ** years)
        fv_at_low_cost     = a["balance"] * ((1 + expected_return - LOW_COST_INDEX_EXPENSE_RATIO) ** years)
        drag = fv_at_low_cost - fv_at_current_fee
        total_annual_fee += annual_fee
        total_fee_drag += drag
        results.append({
            "id": a.get("id"), "name": a["name"], "balance": a["balance"],
            "expense_ratio_pct": round(er * 100, 3),
            "annual_fee_dollars": round(annual_fee),
            "fee_drag_over_years": round(drag),
        })
    results.sort(key=lambda r: -r["fee_drag_over_years"])

    worst = results[0]
    recommendation = (
        f"Your investment fees cost about ${total_annual_fee:,.0f}/yr today. Over {years} years, that's an "
        f"estimated ${total_fee_drag:,.0f} less than a low-cost index fund (~{LOW_COST_INDEX_EXPENSE_RATIO*100:.2f}% "
        f"expense ratio) would cost, purely from fee drag on compounding. "
        f"{worst['name']} ({worst['expense_ratio_pct']:.2f}%) is the biggest single contributor — that's the "
        f"first one worth checking for a cheaper equivalent fund."
    )

    return {
        "has_fee_data": True,
        "accounts": results,
        "total_annual_fee_dollars": round(total_annual_fee),
        "total_fee_drag_over_years": round(total_fee_drag),
        "years_assumed": years,
        "recommendation": recommendation,
    }


# % of total investable assets a single account can represent before it's
# flagged as a concentration risk — the standard advisor threshold. Above
# 2x that, it's "severe" rather than just "worth watching."
CONCENTRATION_THRESHOLD_PCT = 10
CONCENTRATION_SEVERE_PCT = 25


CONCENTRATION_ELIGIBLE_TYPES = {"taxable", "custodial"}
# 401k/IRA/Roth/HSA accounts are, in practice, always a menu of diversified
# mutual/index funds — a $ balance there being a large share of net worth
# says nothing about single-security risk, since we don't track underlying
# holdings inside an account. A big 401k balance getting flagged the same
# as a brokerage account sitting entirely in employer stock was a real,
# reported bug (advice like "sell it down" makes no sense for a diversified
# 401k). Taxable/custodial accounts are the ones realistically holding a
# concentrated single-stock position (RSU/ESPP vesting into a brokerage
# account, an inherited stock position, etc.), so only those are checked.


def concentration_risk(accounts: List[Dict], threshold_pct: float = CONCENTRATION_THRESHOLD_PCT) -> Dict:
    """Flags any single taxable/custodial ACCOUNT that's an outsized share
    of your investable assets — a proxy for single-stock concentration
    (employer stock, RSUs, ESPP, an inherited position), since accounts
    don't record what's actually held inside them. Deliberately excludes
    401k/IRA/Roth/HSA: those are effectively always a diversified fund
    lineup, so a large balance there isn't a concentration risk in the way
    a single-security taxable account is."""
    holdings = [
        a for a in accounts
        if a.get("account_type") in INVESTMENT_TYPES and a.get("owner") not in KIDS_OWNERS and a.get("balance", 0) > 0
    ]
    total_investable = sum(a["balance"] for a in holdings)
    if total_investable <= 0:
        return {"has_data": False}

    flagged = []
    for a in holdings:
        if a.get("account_type") not in CONCENTRATION_ELIGIBLE_TYPES:
            continue
        pct = a["balance"] / total_investable * 100
        if pct >= threshold_pct:
            flagged.append({
                "id": a.get("id"), "name": a["name"], "balance": round(a["balance"]),
                "pct_of_portfolio": round(pct, 1),
                "severity": "severe" if pct >= CONCENTRATION_SEVERE_PCT else "moderate",
            })
    flagged.sort(key=lambda r: -r["pct_of_portfolio"])

    if not flagged:
        recommendation = (
            f"No taxable/custodial account makes up more than {threshold_pct:.0f}% of your investable assets — "
            f"no concentration risk flagged. (401k/IRA/Roth/HSA accounts are excluded from this check since "
            f"they're effectively always diversified fund lineups, not a single security.)"
        )
    else:
        worst = flagged[0]
        recommendation = (
            f"{worst['name']} is {worst['pct_of_portfolio']:.0f}% of your investable assets — "
            f"{'well above' if worst['severity']=='severe' else 'above'} the {threshold_pct:.0f}% level advisors "
            f"typically flag as concentration risk. If this account holds a single stock (employer shares, RSUs, "
            f"ESPP, an inherited position), consider a systematic diversification plan (selling a fixed amount or "
            f"percentage each year, ideally coordinated with your capital-gains room from Tax Planning) rather "
            f"than holding it indefinitely — a single position blowing up can do outsized, permanent damage to a "
            f"plan a diversified portfolio would simply absorb."
        )

    return {
        "has_data": True,
        "total_investable": round(total_investable),
        "threshold_pct": threshold_pct,
        "flagged_positions": flagged,
        "recommendation": recommendation,
    }
