"""Account-level estimates for deliberate life-event scenarios.

These calculations are an overlay: they never change household accounts or the
base retirement projection.  They express the opportunity cost/value of an
event in future retirement dollars using the plan's stated pre-retirement
return.  That makes the result useful without pretending to know transactions,
tax lots, or an exact event date.
"""
from datetime import date
from typing import Dict, List


def _future_value(amount: float, rate: float, years: float) -> float:
    return amount * ((1 + rate) ** max(0, years))


def summarize_life_events(events: List[Dict], inputs: Dict) -> Dict:
    current_year = date.today().year
    current_age = int(inputs.get("jason_age") or 0)
    retirement_age = max(current_age, int(inputs.get("default_retirement_age") or 60))
    retirement_year = current_year + max(0, retirement_age - current_age)
    rate = float(inputs.get("expected_return_pre_retirement") or 0)
    summaries = []

    for event in events:
        event_year = int(event["event_year"])
        one_time = float(event.get("one_time_cash_delta") or 0)
        monthly = float(event.get("monthly_cash_flow_delta") or 0)
        duration = max(0, int(event.get("duration_months") or 0))
        # A zero duration means ongoing through retirement.  A finite duration
        # is a temporary cash-flow change (sabbatical, tuition, caregiving).
        ending_year = retirement_year if duration == 0 else min(retirement_year, event_year + duration / 12)
        annual_delta = monthly * 12
        one_time_at_retirement = _future_value(one_time, rate, retirement_year - event_year)
        recurring_at_retirement = 0.0
        if annual_delta and ending_year > event_year:
            periods = ending_year - event_year
            if rate == 0:
                recurring_at_retirement = annual_delta * periods
            else:
                # Contributions/shortfalls assumed at each year-end, then
                # compounded to retirement. Positive cash flow is favorable.
                recurring_at_retirement = annual_delta * (((1 + rate) ** periods - 1) / rate)
                recurring_at_retirement *= (1 + rate) ** max(0, retirement_year - ending_year)
        retirement_impact = one_time_at_retirement + recurring_at_retirement
        summaries.append({
            **event,
            "years_until_event": max(0, event_year - current_year),
            "monthly_impact": round(monthly),
            "retirement_impact": round(retirement_impact),
            "assumption": f"{rate * 100:.1f}% annual pre-retirement return; {'ongoing to retirement' if duration == 0 else f'{duration} month duration'}",
        })

    return {
        "events": summaries,
        "assumptions": {
            "current_year": current_year,
            "retirement_age": retirement_age,
            "retirement_year": retirement_year,
            "pre_retirement_return": rate,
        },
    }
