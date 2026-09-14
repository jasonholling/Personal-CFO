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


def test_briefing_surfaces_at_most_two_high_signal_portfolio_items():
    result = build_cfo_briefing(
        [{"account_type": "checking", "balance": 1000, "owner": "joint"}],
        {"current_monthly_expenses": 1000, "retirement_income_today_dollars": 80000}, [], [],
        cash_flow={"has_data": True, "status": "surplus", "monthly_surplus": 100},
        portfolio_recommendations=[
            {"category": "policy_violation", "priority": 0, "payload": {"category": "policy_violation", "title": "Large cap is overweight", "action_text": "Review the allocation.", "recommendation_key": "policy_violation:1"}},
            {"category": "missing_data", "priority": 1, "payload": {"category": "missing_data", "title": "Brokerage needs a refresh", "action_text": "Refresh the account.", "recommendation_key": "stale_holding_value:1"}},
            {"category": "taxable_rebalance", "priority": 3, "payload": {"category": "taxable_rebalance", "title": "Sell overweight fund", "action_text": "Review the sale.", "recommendation_key": "rebalance:1"}},
            {"category": "concentration_or_liquidity_risk", "priority": 1, "payload": {"category": "concentration_or_liquidity_risk", "title": "Emergency reserve", "action_text": "Rebuild cash.", "recommendation_key": "liquidity_shortfall"}},
        ],
    )
    portfolio = [item for item in result["priorities"] if item["destination"] == "coach"]
    assert len(portfolio) == 2
    assert all("Emergency reserve" not in item["title"] for item in portfolio)


def test_briefing_surfaces_due_annual_review_task():
    result = build_cfo_briefing(
        [{"account_type": "checking", "balance": 1000, "owner": "joint"}],
        {"current_monthly_expenses": 1000, "retirement_income_today_dollars": 80000}, [],
        [{"completed": False, "auto_key": "annual_review_2026"}],
        cash_flow={"has_data": True, "status": "surplus", "monthly_surplus": 100},
    )
    review = [item for item in result["priorities"] if item["destination"] == "annualreview"]
    assert len(review) == 1
    assert review[0]["title"] == "Complete annual review checklist"
