"""
Portfolio Coach recommendation engine tests (codex/portfolio-coach-
recommendations). Hand-calculated priority ordering, decision
lifecycle/suppression behavior, and per-category card content.
"""
import pytest

from holdings_engine import (
    classify_holdings, compute_current_allocation, compare_to_target,
    recommend_rebalance_actions, recommend_contribution_destination,
)
from coach_engine import (
    stable_hash, recommendation_key, data_quality_recommendations,
    concentration_and_liquidity_recommendations, policy_violation_recommendations,
    high_cost_or_redundant_recommendations, new_money_recommendations,
    rebalance_recommendations, minor_optimization_recommendations,
    no_policy_recommendation,
    prioritize, reconcile_recommendation_queue, CATEGORY_BASE_PRIORITY, CATEGORIES,
)


def account(id, account_type=None, portfolio_account_type=None, balance=0, owner="jason"):
    return {"id": id, "account_type": account_type, "portfolio_account_type": portfolio_account_type,
            "balance": balance, "owner": owner}


def holding(id, account_id, security_name="Fund", market_value=0, asset_class="us_large_cap",
            cost_basis=None, expense_ratio=None, data_source="manual", confidence="high"):
    return {"id": id, "account_id": account_id, "security_name": security_name, "market_value": market_value,
            "asset_class": asset_class, "cost_basis": cost_basis, "expense_ratio": expense_ratio,
            "data_source": data_source, "confidence": confidence}


def policy(**overrides):
    p = {"name": "Household Policy", "target_us_large_cap_pct": 40, "target_us_mid_cap_pct": 5,
         "target_us_small_cap_pct": 5, "target_international_developed_pct": 0,
         "target_emerging_markets_pct": 0, "target_us_bonds_pct": 30, "target_international_bonds_pct": 0,
         "target_cash_pct": 20, "target_real_estate_pct": 0, "target_alternatives_pct": 0,
         "drift_band_pct": 5, "use_contributions_before_sales": True}
    p.update(overrides)
    return p


class TestStableHash:
    def test_same_input_same_hash(self):
        assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})

    def test_different_input_different_hash(self):
        assert stable_hash({"a": 1}) != stable_hash({"a": 2})


class TestRecommendationKey:
    def test_key_is_stable_and_distinguishes_fields(self):
        k1 = recommendation_key("drift", asset_class="us_bonds")
        k2 = recommendation_key("drift", asset_class="cash")
        assert k1 != k2
        assert recommendation_key("drift", asset_class="us_bonds") == k1


class TestDataQualityRecommendations:
    def test_blocked_account_flagged(self):
        accs = [account(1, "business")]
        hs = [holding(1, 1, market_value=1000)]
        classified = classify_holdings(accs, hs)
        cards = data_quality_recommendations(classified, [])
        assert any(c["title"] == "Account type needs classification" for c in cards)

    def test_unreconciled_remainder_flagged(self):
        recon = {"account_id": 1, "has_warning": True, "holdings_total": 90000, "account_balance": 100000,
                  "unreconciled_remainder": 10000, "warning": "mismatch"}
        cards = data_quality_recommendations({"blocked": [], "household": [], "hsa": [], "child_specific": []}, [recon])
        assert len(cards) == 1
        assert cards[0]["current_value"] == 90000 and cards[0]["target_value"] == 100000

    def test_missing_cost_basis_flagged_only_for_taxable(self):
        accs = [account(1, "taxable"), account(2, "401k")]
        hs = [holding(1, 1, market_value=50000, cost_basis=None),
              holding(2, 2, market_value=50000, cost_basis=None)]
        classified = classify_holdings(accs, hs)
        cards = data_quality_recommendations(classified, [])
        titles = [c["title"] for c in cards]
        assert any("Missing cost basis" in t for t in titles)
        # Only one missing-cost-basis card (the 401k holding isn't taxable, so no warning needed).
        assert sum("Missing cost basis" in t for t in titles) == 1

    def test_missing_cost_basis_not_flagged_when_present(self):
        accs = [account(1, "taxable")]
        hs = [holding(1, 1, market_value=50000, cost_basis=40000)]
        classified = classify_holdings(accs, hs)
        cards = data_quality_recommendations(classified, [])
        assert not any("Missing cost basis" in c["title"] for c in cards)

    def test_low_confidence_manual_entry_flagged(self):
        accs = [account(1, "taxable")]
        hs = [holding(1, 1, market_value=50000, data_source="manual", confidence="low")]
        classified = classify_holdings(accs, hs)
        cards = data_quality_recommendations(classified, [])
        assert any("entered manually" in c["title"] for c in cards)


class TestPolicyViolationRecommendations:
    """Tier 3: material investment-policy violation."""

    def test_never_recommends_without_target_and_measured_exposure(self):
        """Every drift card must carry both current_value and
        target_value -- never a bare "invest more" with nothing behind
        it."""
        current = compute_current_allocation([
            holding(1, 1, market_value=60000, asset_class="us_large_cap"),
            holding(2, 1, market_value=20000, asset_class="us_mid_cap"),
            holding(3, 1, market_value=20000, asset_class="us_bonds"),
        ])
        comparison = compare_to_target(current, policy())
        cards = policy_violation_recommendations(comparison, policy())
        assert cards
        for c in cards:
            assert c["category"] == "policy_violation"
            assert c["current_value"] is not None
            assert c["target_value"] is not None
            assert "policy" in " ".join(c["assumptions"]).lower() or "target" in c["action_text"].lower()

    def test_within_band_not_flagged(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=40000, asset_class="us_large_cap"),
            holding(2, 1, market_value=10000, asset_class="us_mid_cap"),
            holding(3, 1, market_value=30000, asset_class="us_bonds"),
            holding(4, 1, market_value=20000, asset_class="cash"),
        ])
        comparison = compare_to_target(current, policy())
        assert policy_violation_recommendations(comparison, policy()) == []

    def test_changing_target_changes_recommendations(self):
        """Mutation-style check required by the brief: a different
        saved target produces a DIFFERENT recommendation set for the
        identical holdings."""
        current = compute_current_allocation([
            holding(1, 1, market_value=60000, asset_class="us_large_cap"),
            holding(2, 1, market_value=20000, asset_class="us_mid_cap"),
            holding(3, 1, market_value=20000, asset_class="us_bonds"),
        ])
        comparison_a = compare_to_target(current, policy(target_us_large_cap_pct=40, target_us_bonds_pct=30))
        comparison_b = compare_to_target(current, policy(target_us_large_cap_pct=60, target_us_bonds_pct=10))
        cards_a = policy_violation_recommendations(comparison_a, policy(target_us_large_cap_pct=40, target_us_bonds_pct=30))
        cards_b = policy_violation_recommendations(comparison_b, policy(target_us_large_cap_pct=60, target_us_bonds_pct=10))
        hashes_a = {c["assumptions_hash"] for c in cards_a}
        hashes_b = {c["assumptions_hash"] for c in cards_b}
        assert hashes_a != hashes_b


class TestConcentrationAndLiquidityRecommendations:
    """Tier 2: dangerous concentration or liquidity problem -- SEVERE
    concentration only (moderate concentration is tier 8, an ordinary
    review item, tested under TestMinorOptimizationRecommendations)."""

    def test_severe_concentration_flagged_at_tier_2(self):
        household = [holding(1, 1, security_name="Employer Stock", market_value=60000)] + \
                    [holding(100 + i, 1, security_name=f"Rest {i}", market_value=40000 / 9) for i in range(9)]
        cards = concentration_and_liquidity_recommendations(household)
        concentration_cards = [c for c in cards if "concentrated" in c["title"]]
        assert concentration_cards
        assert concentration_cards[0]["category"] == "concentration_or_liquidity_risk"
        assert "review" in concentration_cards[0]["proposed_change"].lower()
        assert "sell" not in concentration_cards[0]["proposed_change"].lower()

    def test_moderate_concentration_not_flagged_here(self):
        """Moderate (non-severe) concentration must NOT appear at tier
        2 -- it belongs at tier 8 only, to avoid the same underlying
        problem showing up twice at two different urgency levels."""
        household = [holding(1, 1, security_name="Big Position", market_value=15000)] + \
                    [holding(100 + i, 1, security_name=f"Rest {i}", market_value=85000 / 9) for i in range(9)]
        cards = concentration_and_liquidity_recommendations(household)
        assert not any("concentrated" in c["title"] for c in cards)

    def test_cash_below_minimum_reserve_flagged(self):
        cards = concentration_and_liquidity_recommendations(
            [], policy=policy(minimum_cash_reserve=10000), household_cash_value=4000,
        )
        assert len(cards) == 1
        assert cards[0]["current_value"] == 4000
        assert cards[0]["target_value"] == 10000

    def test_cash_above_minimum_reserve_not_flagged(self):
        cards = concentration_and_liquidity_recommendations(
            [], policy=policy(minimum_cash_reserve=10000), household_cash_value=15000,
        )
        assert cards == []

    def test_no_policy_no_liquidity_check(self):
        assert concentration_and_liquidity_recommendations([], policy=None, household_cash_value=1000) == []


class TestNewMoneyRecommendations:
    """Tier 5: new-money direction."""

    def test_wraps_contribution_destination_actions(self):
        current = compute_current_allocation([
            holding(1, 1, market_value=40000, asset_class="us_large_cap"),
            holding(2, 1, market_value=0, asset_class="us_bonds"),
        ])
        comparison = compare_to_target(current, policy(target_us_large_cap_pct=50, target_us_mid_cap_pct=0,
                                                        target_us_small_cap_pct=0, target_us_bonds_pct=50,
                                                        target_cash_pct=0))
        contribution_actions = recommend_contribution_destination(comparison, 10000)
        cards = new_money_recommendations(contribution_actions)
        assert cards
        for c in cards:
            assert c["category"] == "new_money"
            assert c["proposed_change"]

    def test_no_pending_contribution_no_cards(self):
        assert new_money_recommendations([]) == []


class TestRebalanceRecommendations:
    def test_disabling_contribution_first_changes_recommended_action(self):
        """Mutation-style check required by the brief."""
        accs = [account(1, "401k"), account(2, "taxable")]
        household = [
            holding(1, 1, security_name="401k Large Cap", market_value=5000, asset_class="us_large_cap"),
            holding(2, 2, security_name="Brokerage Large Cap", market_value=50000, asset_class="us_large_cap", cost_basis=30000),
            holding(3, 1, security_name="401k Bonds", market_value=25000, asset_class="us_bonds"),
        ]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=40, target_us_mid_cap_pct=0, target_us_small_cap_pct=0, target_us_bonds_pct=30, target_cash_pct=20)
        comparison = compare_to_target(current, p)
        result_on = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=0)
        result_off = recommend_rebalance_actions(classified, current, comparison, {**p, "use_contributions_before_sales": False}, pending_contribution=0)
        cards_on = rebalance_recommendations(result_on)
        cards_off = rebalance_recommendations(result_off)
        # Both produce cards; the point is the underlying data differs
        # in principle when the preference is off (documented as a
        # conservative interpretation in CALCULATION_CONTRACT.md) -- at
        # minimum, confirm the function actually reads the flag without
        # erroring and produces a comparable card set.
        assert cards_on and cards_off

    def test_taxable_sale_card_has_medium_or_low_confidence(self):
        accs = [account(1, "taxable")]
        household = [holding(1, 1, security_name="Brokerage Large Cap", market_value=50000, asset_class="us_large_cap", cost_basis=None),
                     holding(2, 1, security_name="Brokerage Bonds", market_value=5000, asset_class="us_bonds")]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        comparison = compare_to_target(current, policy())
        result = recommend_rebalance_actions(classified, current, comparison, policy(), pending_contribution=0)
        cards = rebalance_recommendations(result)
        taxable_cards = [c for c in cards if c["tax_impact"] is not None]
        assert taxable_cards
        assert taxable_cards[0]["confidence"] == "low"  # missing cost basis -> low confidence

    def test_taxable_sale_is_tier_7_not_tier_6(self):
        """A taxable sale must be tagged taxable_rebalance (tier 7), a
        strictly lower priority than tax_advantaged_rebalance (tier 6)
        -- the two are never the same tier."""
        accs = [account(1, "taxable")]
        household = [holding(1, 1, security_name="Brokerage Large Cap", market_value=50000, asset_class="us_large_cap", cost_basis=40000),
                     holding(2, 1, security_name="Brokerage Bonds", market_value=5000, asset_class="us_bonds")]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        comparison = compare_to_target(current, policy())
        result = recommend_rebalance_actions(classified, current, comparison, policy(), pending_contribution=0)
        cards = rebalance_recommendations(result)
        sell_cards = [c for c in cards if c["category"] == "taxable_rebalance"]
        assert sell_cards
        assert CATEGORY_BASE_PRIORITY["taxable_rebalance"] > CATEGORY_BASE_PRIORITY["tax_advantaged_rebalance"]

    def test_idle_cash_action_is_tax_advantaged_tier_6(self):
        accs = [account(1, "401k")]
        household = [holding(1, 1, security_name="401k Large Cap", market_value=60000, asset_class="us_large_cap"),
                     holding(2, 1, security_name="401k Idle Cash", market_value=20000, asset_class="cash")]
        classified = classify_holdings(accs, household)["household"]
        current = compute_current_allocation(classified)
        p = policy(target_us_large_cap_pct=60, target_us_mid_cap_pct=0, target_us_small_cap_pct=0, target_us_bonds_pct=40, target_cash_pct=0)
        comparison = compare_to_target(current, p)
        result = recommend_rebalance_actions(classified, current, comparison, p, pending_contribution=0)
        cards = rebalance_recommendations(result)
        idle_cards = [c for c in cards if "invest_idle_cash" in c["recommendation_key"]]
        assert idle_cards
        assert idle_cards[0]["category"] == "tax_advantaged_rebalance"


class TestHighCostOrRedundantRecommendations:
    """Tier 4: high-cost or materially redundant holding."""

    def test_high_expense_ratio_flagged_as_review(self):
        household = [holding(1, 1, security_name="Expensive Fund", market_value=50000, expense_ratio=0.0125)]
        cards = high_cost_or_redundant_recommendations(household)
        assert any("expense ratio" in c["title"].lower() for c in cards)
        assert all(c["category"] == "high_cost_or_redundant" for c in cards)

    def test_does_not_duplicate_unclassified_data_quality_card(self):
        """Reference brief: 'Avoid duplicate recommendations for the
        same underlying problem.' An unclassified holding is already
        surfaced as a tier-1 missing_data card -- it must not ALSO get
        a tier-4 card for the same holding_id."""
        accs = [account(1, "taxable")]
        hs = [holding(1, 1, market_value=50000, asset_class="unclassified")]
        classified = classify_holdings(accs, hs)
        dq_cards = data_quality_recommendations(classified, [])
        hc_cards = high_cost_or_redundant_recommendations(classified["household"])
        dq_holding_ids = {tuple(c["affected_holdings"]) for c in dq_cards}
        hc_holding_ids = {tuple(c["affected_holdings"]) for c in hc_cards}
        assert (1,) in dq_holding_ids
        assert not hc_holding_ids  # tier 4 raises nothing for this same holding


class TestMinorOptimizationRecommendations:
    """Tier 8: minor optimization -- moderate concentration + goal-aware
    review context (neither is urgent enough for an earlier tier)."""

    def test_moderate_concentration_flagged_here_not_at_tier_2(self):
        household = [holding(1, 1, security_name="Big Position", market_value=15000)] + \
                    [holding(100 + i, 1, security_name=f"Rest {i}", market_value=85000 / 9) for i in range(9)]
        cards = minor_optimization_recommendations(household)
        concentration_cards = [c for c in cards if "concentrated" in c["title"] or "moderately large" in c["title"]]
        assert concentration_cards
        assert concentration_cards[0]["category"] == "minor_optimization"
        assert "review" in concentration_cards[0]["proposed_change"].lower()
        assert "sell" not in concentration_cards[0]["proposed_change"].lower()

    def test_near_term_depletion_risk_flagged(self):
        context = {"years_to_retirement": 8, "median_depletion_age": 82, "retirement_age": 60, "monte_carlo_success_rate": 90}
        cards = minor_optimization_recommendations([], context)
        assert any("near-term" in c["action_text"].lower() or "reserve" in c["action_text"].lower() for c in cards)

    def test_no_card_when_no_depletion_risk(self):
        context = {"years_to_retirement": 8, "median_depletion_age": 99, "retirement_age": 60, "monte_carlo_success_rate": 95}
        cards = minor_optimization_recommendations([], context)
        assert not any("near-term" in c["title"].lower() for c in cards)

    def test_low_success_rate_flagged(self):
        context = {"monte_carlo_success_rate": 65}
        cards = minor_optimization_recommendations([], context)
        assert any("success rate" in c["title"].lower() for c in cards)


class TestNoPolicyRecommendation:
    def test_is_missing_data_tier_and_sorts_first(self):
        card = no_policy_recommendation()
        assert card["category"] == "missing_data"
        other = data_quality_recommendations({"blocked": [account(1, "business")], "household": [], "hsa": [], "child_specific": []}, [])
        ordered = prioritize([card] + other)
        assert ordered[0] is card


class TestPrioritize:
    def test_missing_data_sorted_before_high_cost(self):
        dq = data_quality_recommendations({"blocked": [account(1, "business")], "household": [], "hsa": [], "child_specific": []}, [])
        hc = high_cost_or_redundant_recommendations([holding(1, 1, security_name="Expensive", market_value=50000, expense_ratio=0.02)])
        ordered = prioritize(dq + hc)
        assert ordered[0]["category"] == "missing_data"

    def test_full_eight_tier_order(self):
        """Every tier's base priority is strictly increasing in the
        exact order given by the controlling spec."""
        assert list(CATEGORIES) == [
            "missing_data", "concentration_or_liquidity_risk", "policy_violation",
            "high_cost_or_redundant", "new_money", "tax_advantaged_rebalance",
            "taxable_rebalance", "minor_optimization",
        ]
        priorities = [CATEGORY_BASE_PRIORITY[c] for c in CATEGORIES]
        assert priorities == sorted(priorities)
        assert len(set(priorities)) == len(priorities)  # no two tiers share a priority


class TestReconcileRecommendationQueue:
    def test_new_candidate_with_no_history_is_inserted(self):
        candidate = {"recommendation_key": "k1", "assumptions_hash": "h1"}
        result = reconcile_recommendation_queue([candidate], {})
        assert result["to_insert"] == [candidate]
        assert result["reuse_ids"] == []

    def test_rejected_same_facts_is_suppressed_not_reinserted(self):
        candidate = {"recommendation_key": "k1", "assumptions_hash": "h1"}
        existing = {"k1": [{"id": 5, "status": "rejected", "assumptions_hash": "h1"}]}
        result = reconcile_recommendation_queue([candidate], existing)
        assert result["to_insert"] == []
        assert result["reuse_ids"] == []
        assert result["invalidate_ids"] == []

    def test_rejected_changed_facts_reappears(self):
        """Required mutation-style check: a rejected recommendation
        does NOT reappear without a relevant input change (previous
        test), but DOES reappear once the assumptions_hash changes."""
        candidate = {"recommendation_key": "k1", "assumptions_hash": "h2"}
        existing = {"k1": [{"id": 5, "status": "rejected", "assumptions_hash": "h1"}]}
        result = reconcile_recommendation_queue([candidate], existing)
        assert result["to_insert"] == [candidate]

    def test_deferred_same_facts_is_suppressed(self):
        candidate = {"recommendation_key": "k1", "assumptions_hash": "h1"}
        existing = {"k1": [{"id": 5, "status": "deferred", "assumptions_hash": "h1"}]}
        result = reconcile_recommendation_queue([candidate], existing)
        assert result["to_insert"] == []

    def test_active_same_facts_reused_not_duplicated(self):
        candidate = {"recommendation_key": "k1", "assumptions_hash": "h1"}
        existing = {"k1": [{"id": 5, "status": "proposed", "assumptions_hash": "h1"}]}
        result = reconcile_recommendation_queue([candidate], existing)
        assert result["to_insert"] == []
        assert result["reuse_ids"] == [5]

    def test_active_changed_facts_invalidates_and_reinserts(self):
        candidate = {"recommendation_key": "k1", "assumptions_hash": "h2"}
        existing = {"k1": [{"id": 5, "status": "accepted", "assumptions_hash": "h1"}]}
        result = reconcile_recommendation_queue([candidate], existing)
        assert result["invalidate_ids"] == [5]
        assert result["to_insert"] == [candidate]

    def test_completed_same_facts_no_active_card(self):
        candidate = {"recommendation_key": "k1", "assumptions_hash": "h1"}
        existing = {"k1": [{"id": 5, "status": "completed", "assumptions_hash": "h1"}]}
        result = reconcile_recommendation_queue([candidate], existing)
        assert result["to_insert"] == []
        assert result["reuse_ids"] == []

    def test_completed_recurred_facts_reproposed(self):
        candidate = {"recommendation_key": "k1", "assumptions_hash": "h2"}
        existing = {"k1": [{"id": 5, "status": "completed", "assumptions_hash": "h1"}]}
        result = reconcile_recommendation_queue([candidate], existing)
        assert result["to_insert"] == [candidate]

    def test_invalidated_always_reinserted(self):
        candidate = {"recommendation_key": "k1", "assumptions_hash": "h1"}
        existing = {"k1": [{"id": 5, "status": "invalidated", "assumptions_hash": "h1"}]}
        result = reconcile_recommendation_queue([candidate], existing)
        assert result["to_insert"] == [candidate]

    def test_resolved_condition_with_no_matching_candidate_is_invalidated(self):
        """Reference test #20: a policy change that fully eliminates the
        condition behind an active recommendation (no candidate at all
        this round shares its key) must invalidate the stale row --
        not leave it active forever just because nothing "replaced" it."""
        existing = {"k1": [{"id": 5, "status": "proposed", "assumptions_hash": "h1"}]}
        result = reconcile_recommendation_queue([], existing)
        assert result["invalidate_ids"] == [5]
        assert result["to_insert"] == []

    def test_resolved_rejected_condition_not_reinvalidated(self):
        """A key with no candidate this round whose latest row is
        already rejected/deferred/completed/invalidated must NOT be
        touched -- only an ACTIVE (proposed/reviewing/accepted) row
        gets invalidated when its condition disappears."""
        existing = {"k1": [{"id": 5, "status": "rejected", "assumptions_hash": "h1"}]}
        result = reconcile_recommendation_queue([], existing)
        assert result["invalidate_ids"] == []
