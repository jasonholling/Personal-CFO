"""
Portfolio holdings/allocation engine tests (codex/portfolio-coach-
recommendations). Every dollar figure is hand-calculated before being
asserted. Decision support only.

Covers several of the controlling product clarification's 22 numbered
reference tests directly: #1 (exact match, no recommendation), #2 (US
large-cap underweight -> new money there), #4/#5/#6/#7 (contribution-
first / tax-advantaged-exchange / taxable-sale-last / missing-basis
warning), #12/#16 (multi-asset exposure look-through), #17 (a mix must
sum to exactly 100%, verified at the projected_allocation level).
"""
import pytest

from holdings_engine import (
    resolve_portfolio_account_type, is_allocation_blocked, PORTFOLIO_ACCOUNT_TYPES,
    reconcile_account_holdings, classify_holdings, compute_current_allocation,
    holding_exposure_weights, policy_targets_by_class, compare_to_target,
    recommend_contribution_destination, recommend_rebalance_actions,
    concentration_flags, expense_ratio_flags, duplicate_exposure_flags, unclassified_flags,
    parse_holdings_csv, blended_expected_return, blended_expense_ratio, ASSET_CLASSES,
)


def account(id, account_type=None, portfolio_account_type=None, balance=0, owner="jason"):
    return {"id": id, "account_type": account_type, "portfolio_account_type": portfolio_account_type,
            "balance": balance, "owner": owner, "name": f"Account {id}"}


def holding(id, account_id, security_name="Fund", market_value=0, asset_class="us_large_cap",
            cost_basis=None, expense_ratio=None, shares=None, ticker=None, exposures=None):
    return {"id": id, "account_id": account_id, "security_name": security_name, "ticker": ticker,
            "market_value": market_value, "asset_class": asset_class, "cost_basis": cost_basis,
            "expense_ratio": expense_ratio, "shares": shares, "exposures": exposures}


def policy(**overrides):
    p = {"target_us_large_cap_pct": 40, "target_us_mid_cap_pct": 5, "target_us_small_cap_pct": 5,
         "target_international_developed_pct": 0, "target_emerging_markets_pct": 0,
         "target_us_bonds_pct": 30, "target_international_bonds_pct": 0, "target_cash_pct": 20,
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
        result = classify_holdings(accs, [holding(1, 4, market_value=5000)])
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
        assert len(classify_holdings([], [holding(1, 999, market_value=100)])["blocked"]) == 1


class TestHoldingExposureWeights:
    def test_single_asset_class_is_100pct(self):
        assert holding_exposure_weights(holding(1, 1, asset_class="us_large_cap")) == {"us_large_cap": 1.0}

    def test_multi_exposure_normalizes_to_one(self):
        """Reference test #16: a target-date fund is NOT counted as
        100% stock. 60/40 stock/bond fund -> exact fractional weights."""
        h = holding(1, 1, exposures=[{"asset_class": "us_large_cap", "weight_pct": 60}, {"asset_class": "us_bonds", "weight_pct": 40}])
        weights = holding_exposure_weights(h)
        assert weights["us_large_cap"] == pytest.approx(0.6)
        assert weights["us_bonds"] == pytest.approx(0.4)
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_multi_exposure_not_summing_to_100_is_renormalized(self):
        """Reference test #12: a 529 age-based portfolio's exposures may
        not be hand-entered as exactly 100 -- renormalize rather than
        silently under/over-counting."""
        h = holding(1, 1, exposures=[{"asset_class": "us_large_cap", "weight_pct": 30}, {"asset_class": "us_bonds", "weight_pct": 70}])
        weights = holding_exposure_weights(h)
        assert sum(weights.values()) == pytest.approx(1.0)
        assert weights["us_bonds"] == pytest.approx(0.7)

    def test_unrecognized_asset_class_in_exposures_falls_to_unclassified(self):
        h = holding(1, 1, exposures=[{"asset_class": "crypto", "weight_pct": 100}])
        assert holding_exposure_weights(h) == {"unclassified": 1.0}


class TestComputeCurrentAllocation:
    def test_exact_dollar_and_percentage_math(self):
        hs = [holding(1, 1, market_value=60000, asset_class="us_large_cap"),
              holding(2, 1, market_value=30000, asset_class="us_bonds"),
              holding(3, 1, market_value=10000, asset_class="cash")]
        result = compute_current_allocation(hs)
        assert result["total"] == 100000
        assert result["pct_by_class"]["us_large_cap"] == 60.0
        assert result["pct_by_class"]["us_bonds"] == 30.0
        assert result["pct_by_class"]["cash"] == 10.0

    def test_multi_exposure_holding_split_across_classes(self):
        """Reference test #16, at the allocation-math level: a
        $100,000 60/40 target-date fund contributes $60,000 to
        us_large_cap and $40,000 to us_bonds, not $100,000 to one
        class."""
        hs = [holding(1, 1, market_value=100000, exposures=[
            {"asset_class": "us_large_cap", "weight_pct": 60}, {"asset_class": "us_bonds", "weight_pct": 40}])]
        result = compute_current_allocation(hs)
        assert result["by_class"]["us_large_cap"] == 60000
        assert result["by_class"]["us_bonds"] == 40000
        assert result["total"] == 100000

    def test_unclassified_counted_not_dropped(self):
        hs = [holding(1, 1, market_value=50000, asset_class="us_large_cap"),
              holding(2, 1, market_value=50000, asset_class="unclassified")]
        assert compute_current_allocation(hs)["by_class"]["unclassified"] == 50000

    def test_bogus_asset_class_treated_as_unclassified(self):
        assert compute_current_allocation([holding(1, 1, market_value=1000, asset_class="crypto")])["by_class"]["unclassified"] == 1000

    def test_empty_holdings_no_divide_by_zero(self):
        result = compute_current_allocation([])
        assert result["total"] == 0
        assert all(v == 0.0 for v in result["pct_by_class"].values())


class TestCompareToTarget:
    def test_exact_match_no_drift_reference_case_1(self):
        """Reference test #1: portfolio exactly matches policy -> no
        class is outside the drift band, i.e. no rebalance
        recommendation would be warranted."""
        current = compute_current_allocation([
            holding(1, 1, market_value=40000, asset_class="us_large_cap"),
            holding(2, 1, market_value=5000, asset_class="us_mid_cap"),
            holding(3, 1, market_value=5000, asset_class="us_small_cap"),
            holding(4, 1, market_value=30000, asset_class="us_bonds"),
            holding(5, 1, market_value=20000, asset_class="cash"),
        ])
        result = compare_to_target(current, policy())
        assert result["any_outside_band"] is False

    def test_overweight_outside_drift_band(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=70000, asset_class="us_large_cap"),
            holding(2, 1, market_value=10000, asset_class="us_bonds"),
            holding(3, 1, market_value=20000, asset_class="cash"),
        ])
        result = compare_to_target(current, policy())
        lc = result["by_class"]["us_large_cap"]
        assert lc["current_pct"] == 70.0 and lc["target_pct"] == 40.0
        assert lc["deviation_dollars"] == 30000.0
        assert lc["within_drift_band"] is False


class TestRecommendContributionDestination:
    def test_reference_case_2_us_large_cap_underweight_gets_new_money(self):
        """Reference test #2: US large-cap underweight -> new money
        goes toward large-cap."""
        current = compute_current_allocation([
            holding(1, 1, market_value=20000, asset_class="us_large_cap"),
            holding(2, 1, market_value=5000, asset_class="us_mid_cap"),
            holding(3, 1, market_value=5000, asset_class="us_small_cap"),
            holding(4, 1, market_value=50000, asset_class="us_bonds"),
            holding(5, 1, market_value=20000, asset_class="cash"),
        ])
        comparison = compare_to_target(current, policy())
        actions = recommend_contribution_destination(comparison, 5000)
        assert actions[0]["asset_class"] == "us_large_cap"

    def test_reference_case_3_bonds_underweight_selects_only_eligible_class(self):
        """Reference test #3 (engine half -- account-option eligibility
        is verified in test_coach_engine.py): bonds underweight and
        nothing else is -> contribution goes entirely to bonds."""
        current = compute_current_allocation([
            holding(1, 1, market_value=40000, asset_class="us_large_cap"),
            holding(2, 1, market_value=5000, asset_class="us_mid_cap"),
            holding(3, 1, market_value=5000, asset_class="us_small_cap"),
            holding(4, 1, market_value=20000, asset_class="us_bonds"),
            holding(5, 1, market_value=20000, asset_class="cash"),
        ])
        comparison = compare_to_target(current, policy())
        actions = recommend_contribution_destination(comparison, 5000)
        assert len(actions) == 1 and actions[0]["asset_class"] == "us_bonds"

    def test_contribution_never_funds_overweight_class(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=90000, asset_class="us_large_cap"),
            holding(2, 1, market_value=5000, asset_class="us_bonds"),
            holding(3, 1, market_value=5000, asset_class="cash"),
        ])
        comparison = compare_to_target(current, policy())
        actions = recommend_contribution_destination(comparison, 1000)
        assert all(a["asset_class"] != "us_large_cap" for a in actions)

    def test_zero_contribution_returns_no_actions(self):
        current = compute_current_allocation([holding(1, 1, market_value=100000, asset_class="us_large_cap")])
        assert recommend_contribution_destination(compare_to_target(current, policy()), 0) == []


class TestRecommendRebalanceActions:
    def test_reference_case_4_taxable_overweight_but_contribution_corrects_it_no_sale(self):
        """Reference test #4: taxable equity overweight but
        contributions can correct it -> no taxable sale."""
        accs = [account(1, "taxable")]
        household = [
            holding(1, 1, security_name="Taxable Large Cap", market_value=60000, asset_class="us_large_cap"),
            holding(2, 1, security_name="Taxable Bonds", market_value=20000, asset_class="us_bonds"),
            holding(3, 1, security_name="Taxable Cash", market_value=20000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=40, target_us_mid_cap_pct=0, target_us_small_cap_pct=0, target_us_bonds_pct=40, target_cash_pct=20)
        comparison = compare_to_target(current, p)
        # bonds underweight by 20000, exactly matching a 20000 contribution.
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=20000)
        assert [a for a in result["rebalance_actions"] if a["action"] == "sell"] == []

    def test_reference_case_5_insufficient_contribution_uses_tax_advantaged_exchange_first(self):
        """Reference test #5: contributions insufficient -> recommend
        tax-advantaged exchange before any taxable sale."""
        accs = [account(1, "401k"), account(2, "taxable")]
        household = [
            holding(1, 1, security_name="401k Large Cap", market_value=30000, asset_class="us_large_cap"),
            holding(2, 2, security_name="Taxable Large Cap", market_value=30000, asset_class="us_large_cap", cost_basis=25000),
            holding(3, 1, security_name="401k Bonds", market_value=20000, asset_class="us_bonds"),
            holding(4, 1, security_name="401k Cash", market_value=20000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=40, target_us_mid_cap_pct=0, target_us_small_cap_pct=0, target_us_bonds_pct=40, target_cash_pct=20)
        comparison = compare_to_target(current, p)
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=5000)
        sells = [a for a in result["rebalance_actions"] if a["action"] == "sell"]
        assert sells
        assert all(not a["is_taxable_sale"] for a in sells)  # 401k Large Cap alone covers the remaining need

    def test_reference_case_6_only_taxable_sale_can_address_concentration_shows_tax_warning(self):
        """Reference test #6."""
        accs = [account(1, "taxable")]
        household = [
            holding(1, 1, security_name="Concentrated Stock", market_value=60000, asset_class="us_large_cap", cost_basis=20000),
            holding(2, 1, security_name="Taxable Bonds", market_value=20000, asset_class="us_bonds"),
            holding(3, 1, security_name="Taxable Cash", market_value=20000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=40, target_us_mid_cap_pct=0, target_us_small_cap_pct=0, target_us_bonds_pct=40, target_cash_pct=20)
        comparison = compare_to_target(current, p)
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=0)
        taxable_sells = [a for a in result["rebalance_actions"] if a["action"] == "sell" and a["is_taxable_sale"]]
        assert taxable_sells
        assert taxable_sells[0]["tax_warning"]["has_cost_basis"] is True
        assert taxable_sells[0]["tax_warning"]["estimated_gain"] > 0

    def test_reference_case_7_missing_basis_does_not_invent_tax_cost(self):
        """Reference test #7."""
        accs = [account(1, "taxable")]
        household = [
            holding(1, 1, security_name="No Basis Stock", market_value=60000, asset_class="us_large_cap", cost_basis=None),
            holding(2, 1, security_name="Taxable Bonds", market_value=20000, asset_class="us_bonds"),
            holding(3, 1, security_name="Taxable Cash", market_value=20000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=40, target_us_mid_cap_pct=0, target_us_small_cap_pct=0, target_us_bonds_pct=40, target_cash_pct=20)
        comparison = compare_to_target(current, p)
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=0)
        taxable_sells = [a for a in result["rebalance_actions"] if a["action"] == "sell" and a["is_taxable_sale"]]
        assert taxable_sells
        assert taxable_sells[0]["tax_warning"]["has_cost_basis"] is False
        assert "estimated_gain" not in taxable_sells[0]["tax_warning"]

    def test_idle_cash_deployed_before_any_exchange_or_sale(self):
        """Rebalance order step 2: idle cash already in an investment
        account is deployed BEFORE any exchange/sale -- no tax
        consequence, own action type. Policy has NO cash target (0%),
        so the full $20,000 cash holding is genuinely idle -- none of
        it is protected as a deliberate reserve. $80,000 total:
        large_cap $60,000 (75%, target 60% -> $12,000 over), cash
        $20,000 (25%, target 0% -> $20,000 over, all idle), bonds $0
        (target 40% -> $32,000 under). Idle cash covers $20,000 of the
        $32,000 bond gap; the remaining $12,000 is funded by selling
        the exact large_cap excess."""
        accs = [account(1, "401k")]
        household = [
            holding(1, 1, security_name="401k Large Cap", market_value=60000, asset_class="us_large_cap"),
            holding(2, 1, security_name="401k Idle Cash", market_value=20000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=60, target_us_mid_cap_pct=0, target_us_small_cap_pct=0, target_us_bonds_pct=40, target_cash_pct=0)
        comparison = compare_to_target(current, p)
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=0)
        idle_actions = [a for a in result["rebalance_actions"] if a["action"] == "invest_idle_cash"]
        assert idle_actions
        assert idle_actions[0]["is_taxable_sale"] is False
        assert idle_actions[0]["asset_class"] == "us_bonds"
        assert idle_actions[0]["amount"] == 20000
        sells = [a for a in result["rebalance_actions"] if a["action"] == "sell"]
        assert sells and sells[0]["amount"] == 12000

    def test_multi_exposure_holding_excluded_from_sell_candidates(self):
        """A target-date/balanced fund is never a sell candidate --
        "selling only the bond portion" of one security isn't a real
        transaction."""
        accs = [account(1, "taxable")]
        household = [
            holding(1, 1, security_name="Target Date Fund", market_value=80000,
                    exposures=[{"asset_class": "us_large_cap", "weight_pct": 100}]),
            holding(2, 1, security_name="Taxable Bonds", market_value=10000, asset_class="us_bonds"),
            holding(3, 1, security_name="Taxable Cash", market_value=10000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=40, target_us_mid_cap_pct=0, target_us_small_cap_pct=0, target_us_bonds_pct=40, target_cash_pct=20)
        comparison = compare_to_target(current, p)
        # This fund has a SINGLE exposure entry (100% one class), so it
        # should NOT be excluded -- confirms the single-entry case still
        # participates normally.
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=0)
        sells = [a for a in result["rebalance_actions"] if a["action"] == "sell"]
        assert any(a["holding_id"] == 1 for a in sells)

        # Now make it genuinely multi-exposure -- must be excluded.
        household[0]["exposures"] = [{"asset_class": "us_large_cap", "weight_pct": 60}, {"asset_class": "us_bonds", "weight_pct": 40}]
        classified2 = classify_holdings(accs, household)["household"]
        current2 = compute_current_allocation(classified2)
        comparison2 = compare_to_target(current2, p)
        result2 = recommend_rebalance_actions(classified2, current2, comparison2, p, pending_contribution=0)
        sells2 = [a for a in result2["rebalance_actions"] if a["action"] == "sell"]
        assert not any(a["holding_id"] == 1 for a in sells2)

    def test_contribution_first_eliminates_need_for_sale(self):
        """$100,000 total, all round percentages (avoids any rounding
        noise from deviation_pct's own 2-decimal rounding): large_cap
        $50,000 (50%, target 40% -> $10,000 over), bonds $20,000 (20%,
        target 30% -> $10,000 under), cash $30,000 (30%, target 20% ->
        $10,000 over, but idle-cash deployment is capped at what's
        overweight so this doesn't interfere), mid/small-cap $0 each
        (target 5% each -> $5,000 under each). Total underweight need:
        10,000 (bonds) + 5,000 (mid) + 5,000 (small) = $20,000, exactly
        matching the contribution -- no sale needed."""
        accs = [account(1, "taxable")]
        household = [
            holding(1, 1, security_name="Large Cap", market_value=50000, asset_class="us_large_cap"),
            holding(2, 1, security_name="Bonds", market_value=20000, asset_class="us_bonds"),
            holding(3, 1, security_name="Cash", market_value=30000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        comparison = compare_to_target(current, policy())
        result = recommend_rebalance_actions(classified, current, comparison, policy(), pending_contribution=20000)
        assert [a for a in result["rebalance_actions"] if a["action"] == "sell"] == []

    def test_exact_before_after_projected_allocation_math(self):
        accs = [account(1, "401k")]
        household = [
            holding(1, 1, security_name="401k Large Cap", market_value=70000, asset_class="us_large_cap"),
            holding(2, 1, security_name="401k Bonds", market_value=10000, asset_class="us_bonds"),
            holding(3, 1, security_name="401k Cash", market_value=20000, asset_class="cash"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=50, target_us_mid_cap_pct=0, target_us_small_cap_pct=0, target_us_bonds_pct=30, target_cash_pct=20)
        comparison = compare_to_target(current, p)
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=0)
        projected = result["projected_allocation"]
        assert projected["total"] == 100000
        assert projected["by_class"]["us_large_cap"] == 50000
        assert projected["by_class"]["us_bonds"] == 30000
        assert projected["by_class"]["cash"] == 20000


class TestFundQualityFlags:
    def _rest(self, n, each_value):
        return [holding(100 + i, 1, security_name=f"Rest {i}", market_value=each_value) for i in range(n)]

    def test_moderate_concentration_flagged(self):
        household = [holding(1, 1, security_name="Big Position", market_value=15000)] + self._rest(9, 85000 / 9)
        assert concentration_flags(household)[0]["severity"] == "moderate"

    def test_severe_concentration_flagged(self):
        household = [holding(1, 1, security_name="Huge Position", market_value=30000)] + self._rest(9, 70000 / 9)
        assert concentration_flags(household)[0]["severity"] == "severe"

    def test_below_threshold_not_flagged(self):
        household = [holding(1, 1, security_name="Small", market_value=5000)] + self._rest(10, 9500)
        assert concentration_flags(household) == []

    def test_high_expense_ratio_flagged_with_exact_dollar_fee(self):
        household = [holding(1, 1, security_name="Expensive Fund", market_value=50000, expense_ratio=0.0125)]
        assert expense_ratio_flags(household)[0]["annual_fee_dollars"] == 625.0

    def test_missing_expense_ratio_not_flagged(self):
        assert expense_ratio_flags([holding(1, 1, security_name="Unknown", market_value=50000, expense_ratio=None)]) == []

    def test_duplicate_exposure_across_accounts_flagged(self):
        household = [holding(1, 1, security_name="VTI", market_value=10000), holding(2, 2, security_name="VTI", market_value=20000)]
        flags = duplicate_exposure_flags(household)
        assert len(flags) == 1 and flags[0]["total_market_value"] == 30000

    def test_same_account_duplicate_name_not_flagged(self):
        household = [holding(1, 1, security_name="VTI", market_value=10000), holding(2, 1, security_name="VTI", market_value=5000)]
        assert duplicate_exposure_flags(household) == []

    def test_unclassified_flagged(self):
        assert len(unclassified_flags([holding(1, 1, security_name="Mystery", market_value=8000, asset_class="unclassified")])) == 1

    def test_classified_not_flagged(self):
        assert unclassified_flags([holding(1, 1, security_name="Fund", market_value=1000, asset_class="us_large_cap")]) == []


class TestParseHoldingsCsv:
    HEADER = "account_id,ticker,security_name,shares,market_value,asset_class,expense_ratio,cost_basis,notes"

    def test_valid_row_parsed(self):
        csv_text = self.HEADER + "\n1,VTI,Vanguard Total Market,100,50000,us_large_cap,0.0003,45000,core\n"
        result = parse_holdings_csv(csv_text, {1})
        assert result["valid_count"] == 1
        assert result["rows"][0]["market_value"] == 50000

    def test_unknown_account_id_invalid(self):
        csv_text = self.HEADER + "\n999,VTI,Vanguard,,50000,us_large_cap,,,\n"
        assert parse_holdings_csv(csv_text, {1})["invalid_count"] == 1

    def test_missing_security_name_invalid(self):
        csv_text = self.HEADER + "\n1,VTI,,,50000,us_large_cap,,,\n"
        assert not parse_holdings_csv(csv_text, {1})["rows"][0]["valid"]

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
        assert blended_expected_return({"us_large_cap": 60, "us_bonds": 40}) == pytest.approx(0.072, abs=1e-3)

    def test_unclassified_excluded_from_both_sides(self):
        assert blended_expected_return({"us_large_cap": 50, "unclassified": 50}) == blended_expected_return({"us_large_cap": 100})

    def test_entirely_unclassified_returns_none(self):
        assert blended_expected_return({"unclassified": 100}) is None

    def test_empty_returns_none(self):
        assert blended_expected_return({}) is None


class TestBlendedExpenseRatio:
    def test_exact_weighted_fee(self):
        household = [holding(1, 1, market_value=60000, expense_ratio=0.0005), holding(2, 1, market_value=40000, expense_ratio=0.002)]
        result = blended_expense_ratio(household)
        assert result["blended_expense_ratio_pct"] == 0.11
        assert result["annual_fee_dollars"] == 110.0

    def test_no_data_returns_none(self):
        assert blended_expense_ratio([holding(1, 1, market_value=50000, expense_ratio=None)]) is None
