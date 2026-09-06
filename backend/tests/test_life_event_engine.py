from life_event_engine import summarize_life_events


def test_negative_life_event_reduces_projected_retirement_capital():
    result = summarize_life_events([{
        "id": 1, "name": "Sabbatical", "event_year": 2030,
        "one_time_cash_delta": -10000, "monthly_cash_flow_delta": -2000,
        "duration_months": 12,
    }], {"jason_age": 45, "expected_return_pre_retirement": .07})
    assert result["events"][0]["retirement_impact"] < 0


def test_ongoing_positive_life_event_is_compounded_to_retirement():
    result = summarize_life_events([{
        "id": 1, "name": "Consulting income", "event_year": 2030,
        "one_time_cash_delta": 0, "monthly_cash_flow_delta": 500,
        "duration_months": 0,
    }], {"jason_age": 45, "expected_return_pre_retirement": .07})
    assert result["events"][0]["retirement_impact"] > 0
