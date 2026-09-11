"""
Portfolio holdings/allocation engine tests (codex/portfolio-coach-
recommendations). Every dollar figure is hand-calculated before being
asserted. Decision support only.
"""
import pytest

from holdings_engine import (
    resolve_portfolio_account_type, is_allocation_blocked, PORTFOLIO_ACCOUNT_TYPES,
    reconcile_account_holdings, classify_holdings, compute_current_allocation,
    policy_targets_by_class, compare_to_target, recommend_contribution_destination,
    recommend_rebalance_actions, concentration_flags, expense_ratio_flags,
    duplicate_exposure_flags, unclassified_flags, parse_holdings_csv,
    blended_expected_return, blended_expense_ratio,
)


def account(id, account_type=None, portfolio_account_type=None, balance=0, owner="jason"):
    return {"id": id, "account_type": account_type, "portfolio_account_type": portfolio_account_type,
            "balance": balance, "owner": owner, "name": f"Account {id}"}


def holding(id, account_id, security_name="Fund", market_value=0, asset_class="us_large_cap",
            cost_basis=None, expense_ratio=None, shares=None, ticker=None):
    return {"id": id, "account_id": account_id, "security_name": security_name, "ticker": ticker,
            "market_value": market_value, "asset_class": asset_class, "cost_basis": cost_basis,
            "expense_ratio": expense_ratio, "shares": shares}


def policy(**overrides):
    p = {"target_us_large_cap_pct": 40, "target_us_mid_small_cap_pct": 10,
         "target_international_stock_pct": 0, "target_bonds_pct": 30, "target_cash_pct": 20,
         "target_real_estate_pct": 0, "target_alternatives_pct": 0,
         "drift_band_pct": 5, "use_contributions_before_sales": True}
    p.update(overrides)
    return p


class TestResolvePortfolioAccountType:
    def test_explicit_override_wins(self):
        assert resolve_portfolio_account_type(account(1, "taxable", "trust")) == "trust"

    @pytest.mark.parametrize("legacy,expected", [
        ("taxable", "brokerage"), ("401k", "traditional_401k"), ("ira", "traditional_ira"),
        ("roth_ira", "roth_ira"), ("hsa", "hsa"), ("529", "529"), ("custodial", "custodial"),
        ("checking", "checking"), ("savings", "savings"),
    ])
    def test_legacy_type_mapping(self, legacy, expected):
        assert resolve_portfolio_account_type(account(1, legacy)) == expected

    def test_unmapped_legacy_type_is_other(self):
        assert resolve_portfolio_account_type(account(1, "real_estate")) == "other"

    def test_missing_account_type_is_other(self):
        assert resolve_portfolio_account_type(account(1, None)) == "other"


class TestIsAllocationBlocked:
    def test_other_is_blocked(self):
        assert is_allocation_blocked("other") is True

    def test_brokerage_is_not_blocked(self):
        assert is_allocation_blocked("brokerage") is False

    def test_unknown_string_is_blocked(self):
        assert is_allocation_blocked("not_a_real_type") is True


class TestReconcileAccountHoldings:
    def test_exact_match_no_warning(self):
        acc = account(1, "taxable", balance=100000)
        hs = [holding(1, 1, market_value=60000), holding(2, 1, market_value=40000)]
        result = reconcile_account_holdings(acc, hs)
        assert result["holdings_total"] == 100000
        assert result["unreconciled_remainder"] == 0
        assert result["has_warning"] is False

    def test_under_totaled_holdings_flagged_with_positive_remainder(self):
        acc = account(1, "taxable", balance=100000)
        hs = [holding(1, 1, market_value=95000)]
        result = reconcile_account_holdings(acc, hs)
        assert result["unreconciled_remainder"] == 5000
        assert result["has_warning"] is True

    def test_over_totaled_holdings_flagged_with_negative_remainder(self):
        acc = account(1, "taxable", balance=50000)
        hs = [holding(1, 1, market_value=60000)]
        result = reconcile_account_holdings(acc, hs)
        assert result["unreconciled_remainder"] == -10000
        assert result["has_warning"] is True

    def test_tiny_rounding_remainder_not_flagged(self):
        acc = account(1, "taxable", balance=100000.40)
        hs = [holding(1, 1, market_value=100000.00)]
        assert reconcile_account_holdings(acc, hs)["has_warning"] is False

    def test_no_holdings_shows_full_balance_as_remainder(self):
        acc = account(1, "taxable", balance=25000)
        result = reconcile_account_holdings(acc, [])
        assert result["unreconciled_remainder"] == 25000
        assert result["has_warning"] is True


class TestClassifyHoldings:
    def _accounts(self):
        return [
            account(1, "taxable"), account(2, "401k"), account(3, "hsa"), account(4, "529"),
            account(5, "custodial"), account(6, "checking"), account(7, None, "trust"), account(8, "business"),
        ]

    def test_household_bucket_includes_brokerage_and_401k(self):
        hs = [holding(1, 1, market_value=1000), holding(2, 2, market_value=1000)]
        result = classify_holdings(self._accounts(), hs)
        assert len(result["household"]) == 2

    def test_hsa_reported_separately(self):
        result = classify_holdings(self._accounts(), [holding(1, 3, market_value=5000)])
        assert len(result["hsa"]) == 1
        assert result["household"] == []

    def test_529_custodial_excluded_and_retains_child_owner(self):
        accs = self._accounts()
        accs[3] = account(4, "529", owner="kid_1")
        hs = [holding(1, 4, market_value=5000)]
        result = classify_holdings(accs, hs)
        assert len(result["child_specific"]) == 1
        assert result["child_specific"][0]["_account"]["owner"] == "kid_1"
        assert result["household"] == []

    def test_checking_savings_excluded(self):
        result = classify_holdings(self._accounts(), [holding(1, 6, market_value=10000)])
        assert len(result["liquidity"]) == 1
        assert result["household"] == []

    def test_trust_included_but_flagged_for_review(self):
        result = classify_holdings(self._accounts(), [holding(1, 7, market_value=20000)])
        assert len(result["household"]) == 1
        assert len(result["review_required"]) == 1

    def test_missing_or_other_type_blocks_and_excludes(self):
        result = classify_holdings(self._accounts(), [holding(1, 8, market_value=7000)])
        assert len(result["blocked"]) == 1
        assert result["household"] == []

    def test_holding_with_no_matching_account_is_blocked(self):
        result = classify_holdings([], [holding(1, 999, market_value=100)])
        assert len(result["blocked"]) == 1


class TestComputeCurrentAllocation:
    def test_exact_dollar_and_percentage_math(self):
        hs = [holding(1, 1, market_value=60000, asset_class="us_large_cap"),
              holding(2, 1, market_value=30000, asset_class="bonds"),
              holding(3, 1, market_value=10000, asset_class="cash")]
        result = compute_current_allocation(hs)
        assert result["total"] == 100000
        assert result["pct_by_class"]["us_large_cap"] == 60.0
        assert result["pct_by_class"]["bonds"] == 30.0
        assert result["pct_by_class"]["cash"] == 10.0

    def test_unclassified_counted_not_dropped(self):
        hs = [holding(1, 1, market_value=50000, asset_class="us_large_cap"),
              holding(2, 1, market_value=50000, asset_class="unclassified")]
        result = compute_current_allocation(hs)
        assert result["by_class"]["unclassified"] == 50000

    def test_bogus_asset_class_treated_as_unclassified(self):
        result = compute_current_allocation([holding(1, 1, market_value=1000, asset_class="crypto")])
        assert result["by_class"]["unclassified"] == 1000

    def test_empty_holdings_no_divide_by_zero(self):
        result = compute_current_allocation([])
        assert result["total"] == 0
        assert all(v == 0.0 for v in result["pct_by_class"].values())


class TestCompareToTarget:
    def test_overweight_outside_drift_band(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=60000, asset_class="us_large_cap"),
            holding(2, 1, market_value=20000, asset_class="us_mid_small_cap"),
            holding(3, 1, market_value=20000, asset_class="bonds"),
        ])
        result = compare_to_target(current, policy())
        lc = result["by_class"]["us_large_cap"]
        assert lc["current_pct"] == 60.0 and lc["target_pct"] == 40.0
        assert lc["deviation_pct"] == 20.0 and lc["deviation_dollars"] == 20000.0
        assert lc["within_drift_band"] is False

    def test_on_target_within_drift_band(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=40000, asset_class="us_large_cap"),
            holding(2, 1, market_value=10000, asset_class="us_mid_small_cap"),
            holding(3, 1, market_value=30000, asset_class="bonds"),
            holding(4, 1, market_value=20000, asset_class="cash"),
        ])
        result = compare_to_target(current, policy())
        assert result["any_outside_band"] is False


class TestRecommendContributionDestination:
    def test_contribution_goes_to_single_underweight_class(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=50000, asset_class="us_large_cap"),
            holding(2, 1, market_value=10000, asset_class="us_mid_small_cap"),
            holding(3, 1, market_value=20000, asset_class="bonds"),
            holding(4, 1, market_value=20000, asset_class="cash"),
        ])
        comparison = compare_to_target(current, policy())
        actions = recommend_contribution_destination(comparison, 5000)
        assert len(actions) == 1
        assert actions[0]["asset_class"] == "bonds"
        assert actions[0]["amount"] == 5000

    def test_contribution_never_funds_overweight_class(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=90000, asset_class="us_large_cap"),
            holding(2, 1, market_value=5000, asset_class="bonds"),
            holding(3, 1, market_value=5000, asset_class="cash"),
        ])
        comparison = compare_to_target(current, policy())
        actions = recommend_contribution_destination(comparison, 1000)
        assert all(a["asset_class"] != "us_large_cap" for a in actions)

    def test_no_underweight_notes_nothing_to_correct(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=40000, asset_class="us_large_cap"),
            holding(2, 1, market_value=10000, asset_class="us_mid_small_cap"),
            holding(3, 1, market_value=30000, asset_class="bonds"),
            holding(4, 1, market_value=20000, asset_class="cash"),
        ])
        comparison = compare_to_target(current, policy())
        actions = recommend_contribution_destination(comparison, 1000)
        assert len(actions) == 1 and actions[0]["asset_class"] is None

    def test_zero_contribution_returns_no_actions(self):
        current = compute_current_allocation([holding(1, 1, market_value=100000, asset_class="us_large_cap")])
        comparison = compare_to_target(current, policy())
        assert recommend_contribution_destination(comparison, 0) == []


class TestRecommendRebalanceActions:
    def test_taxable_sale_avoided_when_tax_advantaged_can_cover_it(self):
        accs = [account(1, "401k"), account(2, "taxable")]
        household = [
            holding(1, 1, security_name="401k Large Cap", market_value=50000, asset_class="us_large_cap"),
            holding(2, 2, security_name="Brokerage Large Cap", market_value=20000, asset_class="us_large_cap", cost_basis=15000),
            holding(3, 1, security_name="401k Bonds", market_value=30000, asset_class="bonds"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        comparison = compare_to_target(current, policy())
        result = recommend_rebalance_actions(classified, current, comparison, policy(), pending_contribution=0)
        taxable_sells = [a for a in result["rebalance_actions"] if a["action"] == "sell" and a["is_taxable_sale"]]
        assert taxable_sells == []

    def test_taxable_sale_recommended_with_tax_warning_when_no_alternative(self):
        """$80,000 total: us_large_cap $55,000 (68.75%, target 40% ->
        $23,000 excess), bonds $25,000 (31.25%, target 30% -> $1,000
        excess), cash $0 (target 20% -> $16,000 underweight). Selling
        walks overweight classes largest-first, tax-advantaged within
        each class first: $5,000 from 401k Large Cap (all it has)
        leaves $11,000 more underweight need for that class, forcing an
        $11,000 taxable sale from brokerage."""
        accs = [account(1, "401k"), account(2, "taxable")]
        household = [
            holding(1, 1, security_name="401k Large Cap", market_value=5000, asset_class="us_large_cap"),
            holding(2, 2, security_name="Brokerage Large Cap", market_value=50000, asset_class="us_large_cap", cost_basis=30000),
            holding(3, 1, security_name="401k Bonds", market_value=25000, asset_class="bonds"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=40, target_us_mid_small_cap_pct=0, target_bonds_pct=30, target_cash_pct=20)
        comparison = compare_to_target(current, p)
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=0)
        taxable_sells = [a for a in result["rebalance_actions"] if a["action"] == "sell" and a["is_taxable_sale"]]
        assert taxable_sells
        assert taxable_sells[0]["holding_id"] == 2
        assert taxable_sells[0]["tax_warning"]["has_cost_basis"] is True

    def test_missing_cost_basis_flagged_not_assumed_zero(self):
        accs = [account(1, "taxable")]
        household = [
            holding(1, 1, security_name="Brokerage Large Cap", market_value=50000, asset_class="us_large_cap", cost_basis=None),
            holding(2, 1, security_name="Brokerage Bonds", market_value=5000, asset_class="bonds"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        comparison = compare_to_target(current, policy())
        result = recommend_rebalance_actions(classified, current, comparison, policy(), pending_contribution=0)
        taxable_sells = [a for a in result["rebalance_actions"] if a["action"] == "sell" and a["is_taxable_sale"]]
        assert taxable_sells
        assert taxable_sells[0]["tax_warning"]["has_cost_basis"] is False

    def test_contribution_first_eliminates_need_for_sale(self):
        accs = [account(1, "taxable")]
        household = [
            holding(1, 1, security_name="Large Cap", market_value=40000, asset_class="us_large_cap"),
            holding(2, 1, security_name="Mid/Small Cap", market_value=40000, asset_class="us_mid_small_cap"),
            holding(3, 1, security_name="Bonds", market_value=20000, asset_class="bonds"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=40, target_us_mid_small_cap_pct=10, target_bonds_pct=30, target_cash_pct=20)
        comparison = compare_to_target(current, p)
        # cash underweight by 20000, bonds underweight by 10000 -> 30000 total need.
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=30000)
        assert [a for a in result["rebalance_actions"] if a["action"] == "sell"] == []

    def test_exact_before_after_projected_allocation_math(self):
        accs = [account(1, "401k")]
        household = [
            holding(1, 1, security_name="401k Large Cap", market_value=70000, asset_class="us_large_cap"),
            holding(2, 1, security_name="401k Bonds", market_value=10000, asset_class="bonds"),
            holding(3, 1, security_name="401k Cash", market_value=20000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=50, target_us_mid_small_cap_pct=0, target_bonds_pct=30, target_cash_pct=20)
        comparison = compare_to_target(current, p)
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=0)
        projected = result["projected_allocation"]
        assert projected["total"] == 100000
        assert projected["by_class"]["us_large_cap"] == 50000
        assert projected["by_class"]["bonds"] == 30000
        assert projected["by_class"]["cash"] == 20000


class TestFundQualityFlags:
    def _rest(self, n, each_value):
        return [holding(100 + i, 1, security_name=f"Rest {i}", market_value=each_value) for i in range(n)]

    def test_moderate_concentration_flagged(self):
        household = [holding(1, 1, security_name="Big Position", market_value=15000)] + self._rest(9, 85000 / 9)
        flags = concentration_flags(household)
        assert len(flags) == 1 and flags[0]["severity"] == "moderate"

    def test_severe_concentration_flagged(self):
        household = [holding(1, 1, security_name="Huge Position", market_value=30000)] + self._rest(9, 70000 / 9)
        assert concentration_flags(household)[0]["severity"] == "severe"

    def test_below_threshold_not_flagged(self):
        household = [holding(1, 1, security_name="Small", market_value=5000)] + self._rest(10, 9500)
        assert concentration_flags(household) == []

    def test_high_expense_ratio_flagged_with_exact_dollar_fee(self):
        household = [holding(1, 1, security_name="Expensive Fund", market_value=50000, expense_ratio=0.0125)]
        flags = expense_ratio_flags(household)
        assert flags[0]["annual_fee_dollars"] == 625.0

    def test_missing_expense_ratio_not_flagged(self):
        household = [holding(1, 1, security_name="Unknown Fund", market_value=50000, expense_ratio=None)]
        assert expense_ratio_flags(household) == []

    def test_duplicate_exposure_across_accounts_flagged(self):
        household = [holding(1, 1, security_name="VTI", market_value=10000),
                      holding(2, 2, security_name="VTI", market_value=20000)]
        flags = duplicate_exposure_flags(household)
        assert len(flags) == 1 and flags[0]["total_market_value"] == 30000

    def test_same_account_duplicate_name_not_flagged(self):
        household = [holding(1, 1, security_name="VTI", market_value=10000),
                      holding(2, 1, security_name="VTI", market_value=5000)]
        assert duplicate_exposure_flags(household) == []

    def test_unclassified_flagged(self):
        household = [holding(1, 1, security_name="Mystery Fund", market_value=8000, asset_class="unclassified")]
        assert len(unclassified_flags(household)) == 1

    def test_classified_not_flagged(self):
        household = [holding(1, 1, security_name="Fund", market_value=1000, asset_class="us_large_cap")]
        assert unclassified_flags(household) == []


class TestParseHoldingsCsv:
    HEADER = "account_id,ticker,security_name,shares,market_value,asset_class,expense_ratio,cost_basis,notes"

    def test_valid_row_parsed(self):
        csv_text = self.HEADER + "\n1,VTI,Vanguard Total Market,100,50000,us_large_cap,0.0003,45000,core\n"
        result = parse_holdings_csv(csv_text, {1})
        assert result["valid_count"] == 1
        row = result["rows"][0]
        assert row["ticker"] == "VTI" and row["market_value"] == 50000

    def test_unknown_account_id_invalid(self):
        csv_text = self.HEADER + "\n999,VTI,Vanguard,,50000,us_large_cap,,,\n"
        result = parse_holdings_csv(csv_text, {1})
        assert result["invalid_count"] == 1

    def test_missing_security_name_invalid(self):
        csv_text = self.HEADER + "\n1,VTI,,,50000,us_large_cap,,,\n"
        result = parse_holdings_csv(csv_text, {1})
        assert not result["rows"][0]["valid"]

    def test_invalid_asset_class_preserved_for_review(self):
        csv_text = self.HEADER + "\n1,X,Bad Fund,,50000,crypto,,,\n"
        result = parse_holdings_csv(csv_text, {1})
        assert not result["rows"][0]["valid"]
        assert result["rows"][0]["asset_class"] == "crypto"

    def test_missing_required_column_rejects_whole_file(self):
        csv_text = "account_id,security_name,market_value\n1,VTI,50000\n"
        result = parse_holdings_csv(csv_text, {1})
        assert result["rows"] == []
        assert "asset_class" in result["errors"][0]["message"]


class TestBlendedExpectedReturn:
    def test_exact_weighted_average(self):
        assert blended_expected_return({"us_large_cap": 60, "bonds": 40}) == pytest.approx(0.072, abs=1e-9)

    def test_unclassified_excluded_from_both_sides(self):
        assert blended_expected_return({"us_large_cap": 50, "unclassified": 50}) == 0.09

    def test_entirely_unclassified_returns_none(self):
        assert blended_expected_return({"unclassified": 100}) is None

    def test_empty_returns_none(self):
        assert blended_expected_return({}) is None


class TestBlendedExpenseRatio:
    def test_exact_weighted_fee(self):
        household = [holding(1, 1, market_value=60000, expense_ratio=0.0005),
                      holding(2, 1, market_value=40000, expense_ratio=0.002)]
        result = blended_expense_ratio(household)
        assert result["blended_expense_ratio_pct"] == 0.11
        assert result["annual_fee_dollars"] == 110.0

    def test_no_data_returns_none(self):
        assert blended_expense_ratio([holding(1, 1, market_value=50000, expense_ratio=None)]) is None
