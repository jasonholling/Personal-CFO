from datetime import datetime, timedelta, timezone

from cfo_briefing_engine import _age_in_days, build_cfo_briefing


def test_briefing_identifies_missing_foundation_inputs():
    result = build_cfo_briefing([], {}, [], [], cash_flow={"has_data": False})
    titles = {item["title"] for item in result["priorities"]}
    assert "Build your household balance sheet" in titles
    assert "Build your monthly cash-flow plan" in titles
    assert result["data_health"]["planning_ready"] is False


def test_briefing_prioritizes_concrete_risks_and_stale_snapshot():
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    accounts = [
        {"account_type": "checking", "balance": 1000, "owner": "joint"},
        {"account_type": "credit_card", "balance": 5000, "interest_rate": .20, "owner": "person1"},
    ]
    result = build_cfo_briefing(
        accounts,
        {"current_monthly_expenses": 3000, "retirement_income_today_dollars": 80000},
        [{"snapshot_date": old}], [{"completed": False}],
        retirement={"scenarios": [{"label": "age_60_early", "retirement_age": 60, "percent_funded": 80, "projected_surplus": -10000}]},
        education={"goals": [{"child": "Child", "funding_gap": 1000}]},
        cash_flow={"has_data": True, "status": "shortfall", "monthly_surplus": -100},
    )
    titles = {item["title"] for item in result["priorities"]}
    assert "Resolve the monthly cash-flow shortfall" in titles
    assert "Review high-interest debt payoff" in titles
    assert _age_in_days("not-a-date") is None
