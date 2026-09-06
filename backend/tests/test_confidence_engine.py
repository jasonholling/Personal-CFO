from datetime import datetime, timedelta, timezone

from confidence_engine import plan_confidence


def test_ready_plan_reports_strong_with_tax_limitation():
    current = datetime.now(timezone.utc).isoformat()
    result = plan_confidence(
        [{"updated_at": current}],
        {"jason_age": 45, "retirement_income_today_dollars": 100000, "expected_return_pre_retirement": .07},
        {"has_data": True},
    )
    assert result["label"] == "Strong"
    assert result["status"] == "ready"
    assert next(c for c in result["checks"] if c["key"] == "tax_precision")["status"] == "limited"


def test_missing_and_stale_data_reports_early_draft():
    old = (datetime.now(timezone.utc) - timedelta(days=36)).isoformat()
    result = plan_confidence([{"updated_at": old}], {}, {"has_data": False})
    assert result["label"] == "Early draft"
    assert "Refresh 1 account balance" in result["checks"][0]["detail"]


def test_invalid_timestamp_is_treated_as_stale_not_a_crash():
    result = plan_confidence([{"updated_at": "not-a-date"}], {}, {"has_data": False})
    assert result["checks"][0]["status"] == "attention"
