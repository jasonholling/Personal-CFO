"""Tests for allocation_engine.py — asset allocation/rebalancing
recommendation and investment fee audit."""
import pytest

from allocation_engine import (
    analyze_allocation, analyze_fees, _target_stock_pct,
    concentration_risk, CONCENTRATION_THRESHOLD_PCT, CONCENTRATION_SEVERE_PCT,
)


class TestTargetStockPct:
    def test_young_investor(self):
        assert _target_stock_pct(30) == 80

    def test_older_investor(self):
        assert _target_stock_pct(70) == 40

    def test_floors_at_20_percent_even_very_old(self):
        assert _target_stock_pct(100) == 20

    def test_caps_at_100_percent_even_very_young(self):
        assert _target_stock_pct(5) == 100


class TestAnalyzeAllocation:
    def test_no_investable_accounts(self, sample_inputs):
        result = analyze_allocation(sample_inputs, [{"account_type": "checking", "balance": 1000}])
        assert result == {"has_data": False}

    def test_computes_weighted_stock_percentage(self, sample_inputs):
        accounts = [
            {"id": 1, "name": "All Stock 401k", "account_type": "401k", "balance": 100000, "owner": "jason", "stock_allocation_pct": 100},
            {"id": 2, "name": "All Bond IRA", "account_type": "ira", "balance": 100000, "owner": "justin", "stock_allocation_pct": 0},
        ]
        result = analyze_allocation(sample_inputs, accounts)
        assert result["current_stock_pct"] == 50.0

    def test_missing_allocation_data_uses_default_and_is_flagged(self, sample_inputs):
        accounts = [{"id": 1, "name": "Unset Account", "account_type": "401k", "balance": 100000, "owner": "jason"}]
        result = analyze_allocation(sample_inputs, accounts)
        assert result["accounts_missing_allocation_data"] == ["Unset Account"]
        assert "assumed" in result["recommendation"]

    def test_kids_accounts_excluded(self, sample_inputs):
        accounts = [{"id": 1, "name": "Kid Roth", "account_type": "roth_ira", "balance": 50000, "owner": "abby", "stock_allocation_pct": 100}]
        result = analyze_allocation(sample_inputs, accounts)
        assert result == {"has_data": False}

    def test_real_estate_and_cash_reported_separately_not_in_stock_pct(self, sample_inputs):
        accounts = [
            {"id": 1, "name": "401k", "account_type": "401k", "balance": 100000, "owner": "jason", "stock_allocation_pct": 80},
            {"id": 2, "name": "House", "account_type": "real_estate", "balance": 500000, "owner": "joint"},
            {"id": 3, "name": "Checking", "account_type": "checking", "balance": 20000, "owner": "joint"},
        ]
        result = analyze_allocation(sample_inputs, accounts)
        assert result["total_investable"] == 100000  # real estate and cash excluded
        assert result["real_estate_balance"] == 500000
        assert result["cash_balance"] == 20000

    def test_needs_rebalance_when_deviation_large(self, sample_inputs):
        # sample_inputs has jason_age=50 -> target ~60%
        accounts = [{"id": 1, "name": "All Stock", "account_type": "401k", "balance": 100000, "owner": "jason", "stock_allocation_pct": 100}]
        result = analyze_allocation(sample_inputs, accounts)
        assert result["needs_rebalance"] is True
        assert result["dollar_shift_needed"] > 0

    def test_no_rebalance_needed_when_close_to_target(self, sample_inputs):
        target = 110 - sample_inputs["jason_age"]
        accounts = [{"id": 1, "name": "On Target", "account_type": "401k", "balance": 100000, "owner": "jason", "stock_allocation_pct": target}]
        result = analyze_allocation(sample_inputs, accounts)
        assert result["needs_rebalance"] is False


class TestAnalyzeFees:
    def test_no_fee_data(self):
        assert analyze_fees([{"account_type": "401k", "balance": 100000}]) == {"has_fee_data": False}

    def test_computes_annual_fee_and_drag(self):
        accounts = [{"id": 1, "name": "High Fee Fund", "account_type": "401k", "balance": 500000, "expense_ratio": 0.008}]
        result = analyze_fees(accounts)
        assert result["has_fee_data"] is True
        assert result["total_annual_fee_dollars"] == 4000  # 500000 * 0.008
        assert result["total_fee_drag_over_years"] > 0

    def test_sorted_worst_offender_first(self):
        accounts = [
            {"id": 1, "name": "Low Fee", "account_type": "ira", "balance": 100000, "expense_ratio": 0.001},
            {"id": 2, "name": "High Fee", "account_type": "401k", "balance": 100000, "expense_ratio": 0.01},
        ]
        result = analyze_fees(accounts)
        assert result["accounts"][0]["name"] == "High Fee"

    def test_recommendation_names_worst_offender(self):
        accounts = [{"id": 1, "name": "Expensive Fund", "account_type": "401k", "balance": 200000, "expense_ratio": 0.012}]
        result = analyze_fees(accounts)
        assert "Expensive Fund" in result["recommendation"]

    def test_accounts_with_zero_expense_ratio_excluded(self):
        accounts = [{"id": 1, "name": "No Fee Data", "account_type": "401k", "balance": 100000, "expense_ratio": 0}]
        assert analyze_fees(accounts) == {"has_fee_data": False}

    def test_kids_accounts_excluded_same_as_analyze_allocation(self):
        """Regression: fee-drag total used to include kids' accounts while
        analyze_allocation's total_investable excluded them — both render
        on the same page and should agree on what counts as 'yours'."""
        accounts = [{"id": 1, "name": "Kid Custodial", "account_type": "custodial", "owner": "abby", "balance": 50000, "expense_ratio": 0.01}]
        assert analyze_fees(accounts) == {"has_fee_data": False}


class TestConcentrationRisk:
    def test_no_investable_accounts(self):
        assert concentration_risk([]) == {"has_data": False}

    def test_diversified_401k_never_flagged_even_when_most_of_portfolio(self):
        """Regression test for a real reported bug: a large, diversified
        401k (or IRA/Roth/HSA) got flagged as a "concentration risk" the
        same as a single-stock brokerage account, with advice to sell it
        down — nonsensical for an account that's actually a menu of
        diversified funds, not one security. Only taxable/custodial
        accounts (the ones plausibly holding a single concentrated
        position, e.g. RSUs/ESPP) should ever be flagged."""
        accounts = [
            {"id": 1, "name": "Empower 401k", "account_type": "401k", "owner": "jason", "balance": 700000},
            {"id": 2, "name": "Small IRA", "account_type": "ira", "owner": "jason", "balance": 100000},
            {"id": 3, "name": "HSA", "account_type": "hsa", "owner": "jason", "balance": 50000},
            {"id": 4, "name": "Small Taxable", "account_type": "taxable", "owner": "jason", "balance": 5000},
        ]
        result = concentration_risk(accounts)
        assert result["flagged_positions"] == []
        names_never_flagged = {"Empower 401k", "Small IRA", "HSA"}
        assert names_never_flagged.isdisjoint({p["name"] for p in result["flagged_positions"]})

    def test_no_flag_when_no_eligible_account_over_threshold(self):
        # 11 equal taxable accounts, each under 10%.
        accounts = [
            {"id": i, "name": f"Position {i}", "account_type": "taxable", "owner": "jason", "balance": 10000}
            for i in range(11)
        ]
        result = concentration_risk(accounts)
        assert result["flagged_positions"] == []

    def test_flags_taxable_position_over_threshold(self):
        accounts = [
            {"id": 1, "name": "Company Stock", "account_type": "taxable", "owner": "jason", "balance": 20000},
            {"id": 2, "name": "Diversified Fund", "account_type": "401k", "owner": "jason", "balance": 75000},
            {"id": 3, "name": "Small IRA", "account_type": "ira", "owner": "jason", "balance": 5000},
        ]
        result = concentration_risk(accounts)
        names = [p["name"] for p in result["flagged_positions"]]
        assert "Small IRA" not in names       # 401k/IRA never eligible
        assert "Diversified Fund" not in names  # 401k/IRA never eligible
        assert "Company Stock" in names
        company = next(p for p in result["flagged_positions"] if p["name"] == "Company Stock")
        assert company["pct_of_portfolio"] == 20.0
        assert company["severity"] == "moderate"

    def test_severe_severity_above_25_pct(self):
        accounts = [
            {"id": 1, "name": "Company Stock", "account_type": "taxable", "owner": "jason", "balance": 300000},
            {"id": 2, "name": "Diversified Fund", "account_type": "401k", "owner": "jason", "balance": 700000},
        ]
        result = concentration_risk(accounts)
        assert result["flagged_positions"][0]["severity"] == "severe"

    def test_kids_accounts_excluded(self):
        accounts = [{"id": 1, "name": "Kid Custodial", "account_type": "custodial", "owner": "abby", "balance": 100000}]
        assert concentration_risk(accounts) == {"has_data": False}

    def test_sorted_worst_first(self):
        accounts = [
            {"id": 1, "name": "Medium", "account_type": "taxable", "owner": "jason", "balance": 15000},
            {"id": 2, "name": "Biggest", "account_type": "taxable", "owner": "jason", "balance": 60000},
            {"id": 3, "name": "Also Flagged", "account_type": "custodial", "owner": "jason", "balance": 25000},
        ]
        result = concentration_risk(accounts)
        assert result["flagged_positions"][0]["name"] == "Biggest"

    def test_custom_threshold(self):
        accounts = [
            {"id": 1, "name": "Small Position", "account_type": "taxable", "owner": "jason", "balance": 6000},
            {"id": 2, "name": "Rest", "account_type": "taxable", "owner": "jason", "balance": 94000},
        ]
        # At a 10% threshold only "Rest" (94%) is flagged; "Small Position"
        # (6%) is under it either way, but a 5% threshold pulls it in too.
        assert len(concentration_risk(accounts, threshold_pct=10)["flagged_positions"]) == 1
        assert len(concentration_risk(accounts, threshold_pct=5)["flagged_positions"]) == 2
