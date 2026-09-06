"""
Rental property analysis — cap rate, cash-on-cash return, and a sell-vs-hold
comparison against your other investment return, for the specific
real_estate account flagged as the rental (planning_inputs.rental_property_key).
"""
from typing import Dict, List, Optional


def rental_property_analysis(accounts: List[Dict], rental_property_key: str,
                              alternative_return: float = 0.07) -> Optional[Dict]:
    if not rental_property_key:
        return {"has_data": False}

    rental = next(
        (a for a in accounts if a.get("account_type") == "real_estate" and rental_property_key in a.get("name", "")),
        None,
    )
    if not rental:
        return {"has_data": False}

    property_value = rental.get("balance", 0)
    monthly_income   = rental.get("monthly_rental_income", 0)
    monthly_expenses = rental.get("monthly_rental_expenses", 0)
    annual_income    = monthly_income * 12
    annual_expenses  = monthly_expenses * 12
    annual_cash_flow = annual_income - annual_expenses

    # Cap rate: net operating income / property value — the standard
    # apples-to-apples yield metric for comparing real estate deals,
    # independent of financing.
    cap_rate = (annual_cash_flow / property_value) if property_value > 0 else 0

    # Any mortgage still owed on this specific property, so cash-on-cash
    # return (which factors in your actual equity, not the full property
    # value) is meaningful even with financing in place.
    mortgage_accounts = [
        a for a in accounts
        if a.get("account_type") == "mortgage" and rental_property_key in a.get("name", "")
    ]
    mortgage_balance = sum(a["balance"] for a in mortgage_accounts)
    # True cash-on-cash return is levered cash flow (after debt service)
    # over the cash actually invested (equity) — dividing pre-debt-service
    # cash flow by equity, as this used to, mixes an unlevered numerator
    # with a levered denominator and overstates the return whenever there's
    # a mortgage. minimum_payment on the mortgage account is its monthly
    # P&I; annual_expenses (property taxes/insurance/maintenance)
    # deliberately excludes it — see monthly_rental_expenses in main.py.
    annual_debt_service = sum(a.get("minimum_payment", 0) for a in mortgage_accounts) * 12
    levered_cash_flow = annual_cash_flow - annual_debt_service
    equity = max(0, property_value - mortgage_balance)
    cash_on_cash_return = (levered_cash_flow / equity) if equity > 0 else 0

    beats_alternative = cap_rate > alternative_return

    if annual_income <= 0:
        recommendation = (
            "No rental income entered for this property yet — add the monthly rent and operating expenses "
            "on its account in Accounts to see a cap rate and hold-vs-sell comparison."
        )
    elif beats_alternative:
        recommendation = (
            f"This property's {cap_rate*100:.1f}% cap rate beats your {alternative_return*100:.0f}% assumed "
            f"return on other investments — holding it is earning more than redeploying the equity would, "
            f"before accounting for appreciation (which isn't in this number) or the hassle of being a landlord."
        )
    else:
        recommendation = (
            f"This property's {cap_rate*100:.1f}% cap rate is below your {alternative_return*100:.0f}% assumed "
            f"return on other investments — selling and reinvesting the ${equity:,.0f} equity could outearn "
            f"holding it on cash flow alone, though that ignores appreciation and any tax cost of selling."
        )

    return {
        "has_data": True,
        "property_name": rental["name"],
        "property_value": round(property_value),
        "mortgage_balance": round(mortgage_balance),
        "equity": round(equity),
        "annual_income": round(annual_income),
        "annual_expenses": round(annual_expenses),
        "annual_debt_service": round(annual_debt_service),
        "annual_cash_flow": round(annual_cash_flow),
        "levered_cash_flow": round(levered_cash_flow),
        "cap_rate": round(cap_rate, 4),
        "cash_on_cash_return": round(cash_on_cash_return, 4),
        "alternative_return": alternative_return,
        "beats_alternative": beats_alternative,
        "recommendation": recommendation,
    }
