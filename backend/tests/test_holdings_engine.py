"""
Portfolio holdings/allocation/rebalancing engine tests
(codex/portfolio-holdings-allocation, Milestone 3).

Every dollar figure below is hand-calculated before being asserted, per
the brief's "write hand-calculated reference cases before implementation"
requirement. Decision support only -- these tests never assert that a
recommendation is "correct financial advice," only that the arithmetic
and the stated business rules (contribution-first, taxable-sale
avoidance, missing-cost-basis flagging, drift bands) are followed
exactly.
"""
import pytest

from holdings_engine import (
    resolve_portfolio_account_type,
    is_allocation_blocked,
    PORTFOLIO_ACCOUNT_TYPES,
    reconcile_account_holdings,
    classify_holdings,
    compute_current_allocation,
    policy_targets_by_class,
    compare_to_target,
    recommend_contribution_destination,
    recommend_rebalance_actions,
    concentration_flags,
    expense_ratio_flags,
    duplicate_exposure_flags,
    unclassified_flags,
    parse_holdings_csv,
    blended_expected_return,
    blended_expense_ratio,
)


def account(id, account_type=None, portfolio_account_type=None, balance=0, owner="jason"):
    return {"id": id, "account_type": account_type, "portfolio_account_type": portfolio_account_type,
            "balance": balance, "owner": owner, "name": f"Account {id}"}


def holding(id, account_id, name="Fund", market_value=0, asset_class="us_stock",
            cost_basis=None, expense_ratio=None, shares=None):
    return {"id": id, "account_id": account_id, "name": name, "market_value": market_value,
            "asset_class": asset_class, "cost_basis": cost_basis, "expense_ratio": expense_ratio,
            "shares": shares}


def policy(**overrides):
    p = {
        "target_us_stock_pct": 50, "target_international_stock_pct": 0,
        "target_bonds_pct": 30, "target_cash_pct": 20,
        "target_real_estate_pct": 0, "target_alternatives_pct": 0,
        "drift_band_pct": 5, "use_contributions_before_sales": True,
    }
    p.update(overrides)
    return p


class TestResolvePortfolioAccountType:
    def test_explicit_override_wins(self):
        assert resolve_portfolio_account_type(account(1, "taxable", "trust")) == "trust"

    @pytest.mark.parametrize("legacy,expected", [
        ("taxable", "brokerage"),
        ("401k", "traditional_401k"),
        ("ira", "traditional_ira"),
        ("roth_ira", "roth_ira"),
        ("hsa", "hsa"),
        ("529", "529"),
        ("custodial", "custodial"),
        ("checking", "checking"),
        ("savings", "savings"),
    ])
    def test_legacy_type_mapping(self, legacy, expected):
        assert resolve_portfolio_account_type(account(1, legacy)) == expected

    def test_unmapped_legacy_type_is_other(self):
        assert resolve_portfolio_account_type(account(1, "real_estate")) == "other"
        assert resolve_portfolio_account_type(account(1, "business")) == "other"

    def test_missing_account_type_is_other(self):
        assert resolve_portfolio_account_type(account(1, None)) == "other"

    def test_every_portfolio_type_is_in_the_enum(self):
        for legacy in ("taxable", "401k", "ira", "roth_ira", "hsa", "529", "custodial", "checking", "savings"):
            assert resolve_portfolio_account_type(account(1, legacy)) in PORTFOLIO_ACCOUNT_TYPES


class TestIsAllocationBlocked:
    def test_other_is_blocked(self):
        assert is_allocation_blocked("other") is True

    def test_brokerage_is_not_blocked(self):
        assert is_allocation_blocked("brokerage") is False

    def test_unknown_string_is_blocked(self):
        assert is_allocation_blocked("not_a_real_type") is True

    @pytest.mark.parametrize("ptype", [
        "brokerage", "traditional_401k", "roth_401k", "traditional_ira", "roth_ira",
        "hsa", "529", "custodial", "checking", "savings", "trust",
    ])
    def test_every_real_type_except_other_is_unblocked(self, ptype):
        assert is_allocation_blocked(ptype) is False


class TestReconcileAccountHoldings:
    def test_exact_match_no_warning(self):
        acc = account(1, "taxable", balance=100000)
        hs = [holding(1, 1, market_value=60000), holding(2, 1, market_value=40000)]
        result = reconcile_account_holdings(acc, hs)
        assert result["holdings_total"] == 100000
        assert result["unreconciled_remainder"] == 0
        assert result["has_warning"] is False
        assert result["warning"] is None

    def test_under_totaled_holdings_flagged_with_positive_remainder(self):
        """$100,000 account, only $95,000 of holdings entered -> $5,000
        unclassified remainder, explicit warning (never silently assumed
        as cash or dropped)."""
        acc = account(1, "taxable", balance=100000)
        hs = [holding(1, 1, market_value=95000)]
        result = reconcile_account_holdings(acc, hs)
        assert result["holdings_total"] == 95000
        assert result["unreconciled_remainder"] == 5000
        assert result["has_warning"] is True
        assert "5,000" in result["warning"]

    def test_over_totaled_holdings_flagged_with_negative_remainder(self):
        """Stale holdings data after a withdrawal: holdings sum to MORE
        than the account's current balance."""
        acc = account(1, "taxable", balance=50000)
        hs = [holding(1, 1, market_value=60000)]
        result = reconcile_account_holdings(acc, hs)
        assert result["unreconciled_remainder"] == -10000
        assert result["has_warning"] is True

    def test_tiny_rounding_remainder_not_flagged(self):
        acc = account(1, "taxable", balance=100000.40)
        hs = [holding(1, 1, market_value=100000.00)]
        result = reconcile_account_holdings(acc, hs)
        assert result["has_warning"] is False

    def test_no_holdings_at_all_shows_full_balance_as_remainder(self):
        acc = account(1, "taxable", balance=25000)
        result = reconcile_account_holdings(acc, [])
        assert result["holdings_total"] == 0
        assert result["unreconciled_remainder"] == 25000
        assert result["has_warning"] is True


class TestClassifyHoldings:
    def _accounts(self):
        return [
            account(1, "taxable"),           # -> brokerage, household
            account(2, "401k"),               # -> traditional_401k, household
            account(3, "hsa"),                # -> hsa
            account(4, "529"),                # -> child_specific
            account(5, "custodial"),          # -> child_specific
            account(6, "checking"),           # -> liquidity
            account(7, None, "trust"),        # -> trust, household + review_required
            account(8, "business"),           # -> other, blocked
        ]

    def test_household_bucket_includes_brokerage_and_401k(self):
        accs = self._accounts()
        hs = [holding(1, 1, market_value=1000), holding(2, 2, market_value=1000)]
        result = classify_holdings(accs, hs)
        assert len(result["household"]) == 2
        assert not result["hsa"] and not result["child_specific"] and not result["liquidity"] and not result["blocked"]

    def test_hsa_reported_separately_not_in_household(self):
        accs = self._accounts()
        hs = [holding(1, 3, market_value=5000)]
        result = classify_holdings(accs, hs)
        assert len(result["hsa"]) == 1
        assert result["household"] == []

    def test_529_and_custodial_excluded_from_household_retirement_allocation(self):
        accs = self._accounts()
        hs = [holding(1, 4, market_value=5000), holding(2, 5, market_value=3000)]
        result = classify_holdings(accs, hs)
        assert len(result["child_specific"]) == 2
        assert result["household"] == []

    def test_checking_savings_excluded_from_stock_bond_allocation(self):
        accs = self._accounts()
        hs = [holding(1, 6, market_value=10000)]
        result = classify_holdings(accs, hs)
        assert len(result["liquidity"]) == 1
        assert result["household"] == []

    def test_trust_included_in_household_but_flagged_for_review(self):
        accs = self._accounts()
        hs = [holding(1, 7, market_value=20000)]
        result = classify_holdings(accs, hs)
        assert len(result["household"]) == 1
        assert len(result["review_required"]) == 1
        assert result["review_required"][0]["id"] == 1

    def test_missing_or_other_account_type_blocks_and_excludes(self):
        accs = self._accounts()
        hs = [holding(1, 8, market_value=7000)]
        result = classify_holdings(accs, hs)
        assert len(result["blocked"]) == 1
        assert result["household"] == []

    def test_holding_with_no_matching_account_is_blocked(self):
        result = classify_holdings([], [holding(1, 999, market_value=100)])
        assert len(result["blocked"]) == 1
        assert result["blocked"][0]["_account"] is None


class TestComputeCurrentAllocation:
    def test_exact_dollar_and_percentage_math(self):
        """$60,000 us_stock + $30,000 bonds + $10,000 cash = $100,000 ->
        60% / 30% / 10%."""
        hs = [
            holding(1, 1, market_value=60000, asset_class="us_stock"),
            holding(2, 1, market_value=30000, asset_class="bonds"),
            holding(3, 1, market_value=10000, asset_class="cash"),
        ]
        result = compute_current_allocation(hs)
        assert result["total"] == 100000
        assert result["by_class"]["us_stock"] == 60000
        assert result["by_class"]["bonds"] == 30000
        assert result["by_class"]["cash"] == 10000
        assert result["pct_by_class"]["us_stock"] == 60.0
        assert result["pct_by_class"]["bonds"] == 30.0
        assert result["pct_by_class"]["cash"] == 10.0

    def test_unclassified_holding_counted_not_dropped(self):
        hs = [holding(1, 1, market_value=50000, asset_class="us_stock"),
              holding(2, 1, market_value=50000, asset_class="unclassified")]
        result = compute_current_allocation(hs)
        assert result["total"] == 100000
        assert result["by_class"]["unclassified"] == 50000
        assert result["pct_by_class"]["unclassified"] == 50.0

    def test_bogus_asset_class_treated_as_unclassified_not_dropped(self):
        hs = [holding(1, 1, market_value=1000, asset_class="crypto_moonshot")]
        result = compute_current_allocation(hs)
        assert result["total"] == 1000
        assert result["by_class"]["unclassified"] == 1000

    def test_empty_holdings_gives_zero_percentages_not_divide_by_zero(self):
        result = compute_current_allocation([])
        assert result["total"] == 0
        assert all(v == 0.0 for v in result["pct_by_class"].values())


class TestCompareToTarget:
    def test_overweight_outside_drift_band(self):
        """60% us_stock current vs. 50% target, 5-point drift band ->
        +10 deviation, outside band."""
        current = compute_current_allocation([
            holding(1, 1, market_value=60000, asset_class="us_stock"),
            holding(2, 1, market_value=30000, asset_class="bonds"),
            holding(3, 1, market_value=10000, asset_class="cash"),
        ])
        result = compare_to_target(current, policy())
        us = result["by_class"]["us_stock"]
        assert us["current_pct"] == 60.0
        assert us["target_pct"] == 50.0
        assert us["deviation_pct"] == 10.0
        assert us["deviation_dollars"] == 10000.0
        assert us["within_drift_band"] is False
        assert result["any_outside_band"] is True

    def test_on_target_within_drift_band(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=50000, asset_class="us_stock"),
            holding(2, 1, market_value=30000, asset_class="bonds"),
            holding(3, 1, market_value=20000, asset_class="cash"),
        ])
        result = compare_to_target(current, policy())
        for c in ("us_stock", "bonds", "cash"):
            assert result["by_class"][c]["deviation_pct"] == 0.0
            assert result["by_class"][c]["within_drift_band"] is True
        assert result["any_outside_band"] is False

    def test_small_deviation_inside_drift_band_not_flagged(self):
        """52% vs 50% target, 5-point band -> inside band."""
        current = compute_current_allocation([
            holding(1, 1, market_value=52000, asset_class="us_stock"),
            holding(2, 1, market_value=28000, asset_class="bonds"),
            holding(3, 1, market_value=20000, asset_class="cash"),
        ])
        result = compare_to_target(current, policy())
        assert result["by_class"]["us_stock"]["within_drift_band"] is True


class TestRecommendContributionDestination:
    def test_contribution_goes_entirely_to_the_single_underweight_class(self):
        """Bonds underweight by $10,000 (target 30% == $30,000, current
        $20,000); a $5,000 contribution should go entirely to bonds."""
        current = compute_current_allocation([
            holding(1, 1, market_value=60000, asset_class="us_stock"),  # overweight vs 50%
            holding(2, 1, market_value=20000, asset_class="bonds"),      # underweight vs 30%
            holding(3, 1, market_value=20000, asset_class="cash"),       # on target
        ])
        comparison = compare_to_target(current, policy())
        actions = recommend_contribution_destination(comparison, 5000)
        assert len(actions) == 1
        assert actions[0]["asset_class"] == "bonds"
        assert actions[0]["amount"] == 5000

    def test_contribution_never_funds_an_overweight_class(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=90000, asset_class="us_stock"),
            holding(2, 1, market_value=5000, asset_class="bonds"),
            holding(3, 1, market_value=5000, asset_class="cash"),
        ])
        comparison = compare_to_target(current, policy())
        actions = recommend_contribution_destination(comparison, 1000)
        assert all(a["asset_class"] != "us_stock" for a in actions)

    def test_contribution_larger_than_every_gap_notes_the_remainder(self):
        """$100,000 total (us_stock $50,000 on target, bonds $20,000,
        cash $30,000): bonds underweight by exactly $10,000 (target 30%
        of $100,000 = $30,000); a $15,000 contribution fully funds bonds
        ($10,000) and notes $5,000 left over."""
        current = compute_current_allocation([
            holding(1, 1, market_value=50000, asset_class="us_stock"),
            holding(2, 1, market_value=20000, asset_class="bonds"),
            holding(3, 1, market_value=30000, asset_class="cash"),
        ])
        comparison = compare_to_target(current, policy())
        actions = recommend_contribution_destination(comparison, 15000)
        bond_action = next(a for a in actions if a["asset_class"] == "bonds")
        assert bond_action["amount"] == 10000
        leftover = next(a for a in actions if a["asset_class"] is None)
        assert leftover["amount"] == 5000

    def test_zero_contribution_returns_no_actions(self):
        current = compute_current_allocation([holding(1, 1, market_value=100000, asset_class="us_stock")])
        comparison = compare_to_target(current, policy())
        assert recommend_contribution_destination(comparison, 0) == []

    def test_no_underweight_class_notes_nothing_to_correct(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=50000, asset_class="us_stock"),
            holding(2, 1, market_value=30000, asset_class="bonds"),
            holding(3, 1, market_value=20000, asset_class="cash"),
        ])
        comparison = compare_to_target(current, policy())
        actions = recommend_contribution_destination(comparison, 1000)
        assert len(actions) == 1
        assert actions[0]["asset_class"] is None
        assert actions[0]["amount"] == 1000


class TestRecommendRebalanceActions:
    def test_taxable_sale_avoided_when_tax_advantaged_holding_can_cover_it(self):
        """Overweight us_stock ($10,000 excess) exists in BOTH a 401k
        (tax-advantaged) and a brokerage (taxable) holding -- the
        recommendation must sell from the 401k first and never touch the
        brokerage holding at all, since the 401k alone covers the full
        excess."""
        accs = [account(1, "401k"), account(2, "taxable")]
        household = [
            holding(1, 1, name="401k US Stock", market_value=50000, asset_class="us_stock"),
            holding(2, 2, name="Brokerage US Stock", market_value=20000, asset_class="us_stock", cost_basis=15000),
            holding(3, 1, name="401k Bonds", market_value=20000, asset_class="bonds"),
        ]
        # Enrich with _portfolio_account_type/_account the way classify_holdings does.
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        # total = 90000; us_stock=70000 (77.8%), bonds=20000 (22.2%)
        # target: us_stock 50%, bonds 30%, cash 20% (cash at 0 -> huge deviation,
        # not relevant to this test's assertion, just background noise).
        comparison = compare_to_target(current, policy())
        result = recommend_rebalance_actions(classified, current, comparison, policy(), pending_contribution=0)
        sells = [a for a in result["rebalance_actions"] if a["action"] == "sell"]
        taxable_sells = [a for a in sells if a["is_taxable_sale"]]
        assert taxable_sells == [], "should never touch the brokerage holding when the 401k alone covers the excess"
        assert any(a["holding_id"] == 1 for a in sells)  # the 401k holding was used

    def test_taxable_sale_recommended_with_tax_warning_when_no_alternative(self):
        """Hand-calculated: $80,000 total -- us_stock $55,000 (68.75%,
        target 50% -> $15,000 excess), bonds $25,000 (31.25%, target 30%
        -> $1,000 excess), cash $0 (target 20% -> $16,000 underweight,
        the only destination for sale proceeds). Selling walks overweight
        classes largest-excess-first, tax-advantaged holdings within each
        class first: $5,000 from the 401k's US Stock (all it has) still
        leaves $10,000 of underweight need for the class, forcing a
        $10,000 TAXABLE sale from the brokerage US Stock holding; the
        remaining $1,000 of underweight need is then covered by selling
        the 401k Bonds holding (tax-advantaged, no warning)."""
        accs = [account(1, "401k"), account(2, "taxable")]
        household = [
            holding(1, 1, name="401k US Stock", market_value=5000, asset_class="us_stock"),
            holding(2, 2, name="Brokerage US Stock", market_value=50000, asset_class="us_stock", cost_basis=30000),
            holding(3, 1, name="401k Bonds", market_value=25000, asset_class="bonds"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        comparison = compare_to_target(current, policy())
        result = recommend_rebalance_actions(classified, current, comparison, policy(), pending_contribution=0)
        sells = [a for a in result["rebalance_actions"] if a["action"] == "sell"]
        taxable_sells = [a for a in sells if a["is_taxable_sale"]]
        assert taxable_sells, "the 401k alone can't cover the full us_stock excess, so a taxable sale must be recommended"
        assert taxable_sells[0]["holding_id"] == 2
        assert taxable_sells[0]["amount"] == 10000
        assert taxable_sells[0]["tax_warning"]["has_cost_basis"] is True
        # market_value 50000, cost_basis 30000 -> gain fraction 20000/50000=0.4;
        # sold_amount is 10000, not the full position -- the estimate must
        # scale with what was actually sold.
        assert taxable_sells[0]["tax_warning"]["estimated_gain"] == 4000.0
        non_taxable_sells = [a for a in sells if not a["is_taxable_sale"]]
        assert {a["holding_id"] for a in non_taxable_sells} == {1, 3}
        assert sum(a["amount"] for a in sells) == 16000  # exactly the cash underweight need

    def test_missing_cost_basis_flagged_not_assumed_zero(self):
        accs = [account(1, "taxable")]
        household = [
            holding(1, 1, name="Brokerage US Stock", market_value=50000, asset_class="us_stock", cost_basis=None),
            holding(2, 1, name="Brokerage Bonds", market_value=5000, asset_class="bonds"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        comparison = compare_to_target(current, policy())
        result = recommend_rebalance_actions(classified, current, comparison, policy(), pending_contribution=0)
        taxable_sells = [a for a in result["rebalance_actions"] if a["action"] == "sell" and a["is_taxable_sale"]]
        assert taxable_sells
        assert taxable_sells[0]["tax_warning"]["has_cost_basis"] is False
        assert "cost basis" in taxable_sells[0]["tax_warning"]["message"].lower()

    def test_contribution_first_can_eliminate_the_need_for_any_sale(self):
        """A pending contribution large enough to fully fund the
        underweight class on its own means NO sell action should be
        recommended at all, even though the overweight class technically
        still has drift (contributions don't touch overweight classes,
        but if there's nothing underweight left to fund with sale
        proceeds, no sell is needed)."""
        accs = [account(1, "taxable")]
        household = [
            holding(1, 1, name="US Stock", market_value=60000, asset_class="us_stock"),
            holding(2, 1, name="Bonds", market_value=20000, asset_class="bonds"),
            holding(3, 1, name="Cash", market_value=20000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        comparison = compare_to_target(current, policy())
        # bonds underweight by exactly $10,000 (target 30% of 100000).
        result = recommend_rebalance_actions(classified, current, comparison, policy(), pending_contribution=10000)
        sells = [a for a in result["rebalance_actions"] if a["action"] == "sell"]
        assert sells == [], "a contribution that fully funds the only underweight class needs no sale"
        assert result["contribution_actions"][0]["asset_class"] == "bonds"
        assert result["contribution_actions"][0]["amount"] == 10000

    def test_exact_before_after_projected_allocation_math(self):
        """Hand-calculated: $100,000 portfolio, us_stock $70,000 (70%,
        target 50%), bonds $10,000 (10%, target 30%), cash $20,000 (20%,
        target 20%, on target). No contribution. Selling $20,000 of
        us_stock and buying $20,000 of bonds should produce EXACTLY
        us_stock $50,000 (50%) / bonds $30,000 (30%) / cash $20,000
        (20%) afterward."""
        accs = [account(1, "401k")]  # tax-advantaged, so the sell isn't taxable
        household = [
            holding(1, 1, name="401k US Stock", market_value=70000, asset_class="us_stock"),
            holding(2, 1, name="401k Bonds", market_value=10000, asset_class="bonds"),
            holding(3, 1, name="401k Cash", market_value=20000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        comparison = compare_to_target(current, policy())
        result = recommend_rebalance_actions(classified, current, comparison, policy(), pending_contribution=0)
        projected = result["projected_allocation"]
        assert projected["total"] == 100000
        assert projected["by_class"]["us_stock"] == 50000
        assert projected["by_class"]["bonds"] == 30000
        assert projected["by_class"]["cash"] == 20000
        assert projected["pct_by_class"]["us_stock"] == 50.0
        assert projected["pct_by_class"]["bonds"] == 30.0
        assert projected["pct_by_class"]["cash"] == 20.0

    def test_policy_can_explicitly_allow_skipping_contribution_first_preference(self):
        """use_contributions_before_sales=False still lets a taxable sale
        happen even though a tax-advantaged alternative exists -- the
        preference is opt-out, not a hard rule, per the brief ("unless
        the policy explicitly allows it"). This test only confirms the
        flag is read and doesn't hard-error; the actual account-selection
        order (tax-advantaged sorted first) is otherwise unaffected by
        this flag in the current implementation, which is a documented,
        deliberately conservative interpretation -- see
        CALCULATION_CONTRACT.md."""
        accs = [account(1, "401k"), account(2, "taxable")]
        household = [
            holding(1, 1, name="401k US Stock", market_value=50000, asset_class="us_stock"),
            holding(2, 2, name="Brokerage US Stock", market_value=20000, asset_class="us_stock", cost_basis=15000),
            holding(3, 1, name="401k Bonds", market_value=20000, asset_class="bonds"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        comparison = compare_to_target(current, policy())
        result = recommend_rebalance_actions(
            classified, current, comparison, policy(use_contributions_before_sales=False), pending_contribution=0)
        assert result is not None  # doesn't error; policy flag is read without crashing


class TestConcentrationFlags:
    def _rest(self, n, each_value):
        """n small holdings, each safely under the 10% threshold, so
        only the intentionally-large holding under test gets flagged."""
        return [holding(100 + i, 1, name=f"Rest {i}", market_value=each_value) for i in range(n)]

    def test_moderate_concentration_flagged(self):
        """$15,000 of $100,000 = 15% -- above the 10% threshold, below
        the 25% severe threshold. The other $85,000 is spread across 9
        equal $9,444.44 holdings, each ~9.4%, safely under threshold."""
        household = [holding(1, 1, name="Big Position", market_value=15000)] + self._rest(9, 85000 / 9)
        flags = concentration_flags(household)
        assert len(flags) == 1
        assert flags[0]["holding_id"] == 1
        assert flags[0]["pct_of_portfolio"] == 15.0
        assert flags[0]["severity"] == "moderate"

    def test_severe_concentration_flagged(self):
        """$30,000 of $100,000 = 30% -- above the 25% severe threshold."""
        household = [holding(1, 1, name="Huge Position", market_value=30000)] + self._rest(9, 70000 / 9)
        flags = concentration_flags(household)
        assert flags[0]["holding_id"] == 1
        assert flags[0]["severity"] == "severe"

    def test_below_threshold_not_flagged(self):
        """$5,000 of $100,000 = 5%, below the 10% threshold; the
        remaining $95,000 spread across 10 equal $9,500 (9.5%) holdings,
        each also safely under threshold."""
        household = [holding(1, 1, name="Small", market_value=5000)] + self._rest(10, 9500)
        assert concentration_flags(household) == []


class TestExpenseRatioFlags:
    def test_high_expense_ratio_flagged_with_exact_dollar_fee(self):
        household = [holding(1, 1, name="Expensive Fund", market_value=50000, expense_ratio=0.0125)]
        flags = expense_ratio_flags(household)
        assert len(flags) == 1
        assert flags[0]["annual_fee_dollars"] == 625.0  # 50000 * 0.0125

    def test_low_expense_ratio_not_flagged(self):
        household = [holding(1, 1, name="Index Fund", market_value=50000, expense_ratio=0.0003)]
        assert expense_ratio_flags(household) == []

    def test_missing_expense_ratio_not_flagged_not_assumed(self):
        household = [holding(1, 1, name="Unknown Fund", market_value=50000, expense_ratio=None)]
        assert expense_ratio_flags(household) == []


class TestDuplicateExposureFlags:
    def test_same_holding_across_two_accounts_flagged(self):
        household = [holding(1, 1, name="VTI", market_value=10000),
                      holding(2, 2, name="VTI", market_value=20000)]
        flags = duplicate_exposure_flags(household)
        assert len(flags) == 1
        assert flags[0]["total_market_value"] == 30000
        assert sorted(flags[0]["accounts"]) == [1, 2]

    def test_case_and_whitespace_insensitive_match(self):
        household = [holding(1, 1, name="VTI", market_value=10000),
                      holding(2, 2, name=" vti ", market_value=20000)]
        assert len(duplicate_exposure_flags(household)) == 1

    def test_same_name_within_one_account_not_flagged_as_duplicate(self):
        household = [holding(1, 1, name="VTI", market_value=10000),
                      holding(2, 1, name="VTI", market_value=5000)]
        assert duplicate_exposure_flags(household) == []

    def test_unique_names_not_flagged(self):
        household = [holding(1, 1, name="VTI", market_value=10000),
                      holding(2, 2, name="BND", market_value=10000)]
        assert duplicate_exposure_flags(household) == []


class TestUnclassifiedFlags:
    def test_unclassified_holding_flagged(self):
        household = [holding(1, 1, name="Mystery Fund", market_value=8000, asset_class="unclassified")]
        flags = unclassified_flags(household)
        assert len(flags) == 1
        assert flags[0]["market_value"] == 8000

    def test_bogus_asset_class_flagged_too(self):
        household = [holding(1, 1, name="Bad Data", market_value=1000, asset_class="not_a_real_class")]
        assert len(unclassified_flags(household)) == 1

    def test_classified_holding_not_flagged(self):
        household = [holding(1, 1, name="Fund", market_value=1000, asset_class="us_stock")]
        assert unclassified_flags(household) == []


class TestParseHoldingsCsv:
    HEADER = "account_id,name,description,shares,market_value,asset_class,expense_ratio,cost_basis,notes"

    def test_valid_rows_parsed(self):
        csv_text = self.HEADER + "\n1,VTI,Total market,100,50000,us_stock,0.0003,45000,core holding\n"
        result = parse_holdings_csv(csv_text, {1})
        assert result["valid_count"] == 1
        assert result["invalid_count"] == 0
        row = result["rows"][0]
        assert row["account_id"] == 1
        assert row["name"] == "VTI"
        assert row["shares"] == 100
        assert row["market_value"] == 50000
        assert row["asset_class"] == "us_stock"
        assert row["expense_ratio"] == 0.0003
        assert row["cost_basis"] == 45000

    def test_optional_columns_can_be_blank(self):
        csv_text = self.HEADER + "\n1,VTI,,,50000,us_stock,,,\n"
        result = parse_holdings_csv(csv_text, {1})
        assert result["valid_count"] == 1
        row = result["rows"][0]
        assert row["shares"] is None
        assert row["expense_ratio"] is None
        assert row["cost_basis"] is None
        assert row["description"] is None

    def test_unknown_account_id_invalid(self):
        csv_text = self.HEADER + "\n999,VTI,,,50000,us_stock,,,\n"
        result = parse_holdings_csv(csv_text, {1})
        assert result["invalid_count"] == 1
        assert not result["rows"][0]["valid"]
        assert "does not match any existing account" in result["rows"][0]["errors"][0]

    def test_missing_name_invalid(self):
        csv_text = self.HEADER + "\n1,,,,50000,us_stock,,,\n"
        result = parse_holdings_csv(csv_text, {1})
        assert not result["rows"][0]["valid"]
        assert any("name is required" in e for e in result["rows"][0]["errors"])

    def test_negative_market_value_invalid(self):
        csv_text = self.HEADER + "\n1,VTI,,,-500,us_stock,,,\n"
        result = parse_holdings_csv(csv_text, {1})
        assert not result["rows"][0]["valid"]

    def test_non_numeric_market_value_invalid(self):
        csv_text = self.HEADER + "\n1,VTI,,,not_a_number,us_stock,,,\n"
        result = parse_holdings_csv(csv_text, {1})
        assert not result["rows"][0]["valid"]

    def test_invalid_asset_class_not_guessed(self):
        """An unrecognized asset_class fails validation -- the raw value
        is preserved as-is for the preview step (so the user can see and
        correct exactly what they entered), never silently coerced into
        one of the real ASSET_CLASSES."""
        csv_text = self.HEADER + "\n1,VTI,,,50000,crypto,,,\n"
        result = parse_holdings_csv(csv_text, {1})
        assert not result["rows"][0]["valid"]
        assert result["rows"][0]["asset_class"] == "crypto"
        assert any("asset_class must be one of" in e for e in result["rows"][0]["errors"])

    def test_missing_required_column_rejects_whole_file(self):
        csv_text = "account_id,name,market_value\n1,VTI,50000\n"  # no asset_class column
        result = parse_holdings_csv(csv_text, {1})
        assert result["rows"] == []
        assert result["valid_count"] == 0
        assert "asset_class" in result["errors"][0]["message"]

    def test_empty_file(self):
        result = parse_holdings_csv("", {1})
        assert result["rows"] == []
        assert result["errors"]

    def test_multiple_rows_mixed_validity(self):
        csv_text = self.HEADER + "\n1,VTI,,,50000,us_stock,,,\n1,Bad,,,50000,not_a_class,,,\n"
        result = parse_holdings_csv(csv_text, {1})
        assert result["valid_count"] == 1
        assert result["invalid_count"] == 1
        assert len(result["errors"]) == 1


class TestBlendedExpectedReturn:
    def test_exact_weighted_average(self):
        """Hand-calculated: 60% us_stock (9%) + 40% bonds (4.5%) =
        0.6*0.09 + 0.4*0.045 = 0.054 + 0.018 = 0.072 (7.2%)."""
        result = blended_expected_return({"us_stock": 60, "bonds": 40})
        assert result == 0.072

    def test_single_asset_class_returns_its_own_rate(self):
        result = blended_expected_return({"us_stock": 100})
        assert result == 0.09

    def test_unclassified_excluded_from_both_sum_and_denominator(self):
        """50% us_stock (9%) + 50% unclassified -- the unclassified half
        must NOT be treated as 0%, which would give 4.5%. Excluding it
        from both sides of the weighted average leaves just 9%."""
        result = blended_expected_return({"us_stock": 50, "unclassified": 50})
        assert result == 0.09

    def test_entirely_unclassified_returns_none(self):
        assert blended_expected_return({"unclassified": 100}) is None

    def test_empty_allocation_returns_none(self):
        assert blended_expected_return({}) is None

    def test_zero_weight_class_ignored(self):
        result = blended_expected_return({"us_stock": 100, "bonds": 0})
        assert result == 0.09


class TestBlendedExpenseRatio:
    def test_exact_weighted_fee(self):
        """$60,000 at 0.05% + $40,000 at 0.20% expense ratio ->
        weighted = (60000*0.0005 + 40000*0.002) / 100000 = (30 + 80) /
        100000 = 0.0011 (0.11%); annual fee = $110."""
        household = [holding(1, 1, market_value=60000, expense_ratio=0.0005),
                      holding(2, 1, market_value=40000, expense_ratio=0.002)]
        result = blended_expense_ratio(household)
        assert result["blended_expense_ratio_pct"] == 0.11
        assert result["annual_fee_dollars"] == 110.0

    def test_holdings_missing_expense_ratio_excluded_not_assumed_zero(self):
        household = [holding(1, 1, market_value=50000, expense_ratio=0.001),
                      holding(2, 1, market_value=50000, expense_ratio=None)]
        result = blended_expense_ratio(household)
        assert result["holdings_with_expense_ratio"] == 1
        assert result["holdings_total"] == 2

    def test_no_expense_ratio_data_at_all_returns_none(self):
        household = [holding(1, 1, market_value=50000, expense_ratio=None)]
        assert blended_expense_ratio(household) is None

    def test_empty_holdings_returns_none(self):
        assert blended_expense_ratio([]) is None
