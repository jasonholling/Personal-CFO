"""Tests for pillars_engine.py — the 4 Pillars (tax/estate/risk/
investment) traffic-lighted rollup. Pure classifier over already-computed
inputs, so these tests exercise the classification logic directly rather
than re-deriving the underlying calculations (those are covered by their
own engines' tests)."""
import pytest

from pillars_engine import build_pillars_summary, _worst


class TestWorst:
    def test_orders_good_attention_critical(self):
        assert _worst("good", "attention") == "attention"
        assert _worst("attention", "critical") == "critical"
        assert _worst("good", "critical", "attention") == "critical"

    def test_single_status_returns_itself(self):
        assert _worst("good") == "good"


class TestBuildPillarsSummary:
    def _call(self, **overrides):
        base = dict(
            portfolio_recommendations=[],
            rmd_result=None,
            tax_inputs_ready=True,
            estate_documents=[{"document_type": t, "status": "executed"} for t in
                               ("trust", "wills", "fpoa", "hcpoa", "freeze")],
            estate_beneficiaries=[{"account_key": "401k", "primary_beneficiary": "Spouse"}],
            estate_tax_exposure={"exposure": 0},
            insurance_policies=[{"who": "jason", "policy_type": "term"}],
            property_policies=[{"item": "home"}],
            insurance_analysis={"jason": {"on_track": True}, "justin": {"on_track": True}},
        )
        base.update(overrides)
        return build_pillars_summary(**base)

    def test_all_clear_is_good_overall(self):
        result = self._call()
        assert result["overall_status"] == "good"
        assert {p["key"] for p in result["pillars"]} == {"tax", "estate", "risk", "investment"}
        assert all(p["status"] == "good" for p in result["pillars"])

    def test_returns_all_four_pillars_even_when_everything_is_empty(self):
        result = build_pillars_summary(
            portfolio_recommendations=[], rmd_result=None, tax_inputs_ready=False,
            estate_documents=[], estate_beneficiaries=[], estate_tax_exposure=None,
            insurance_policies=[], property_policies=[], insurance_analysis=None,
        )
        assert len(result["pillars"]) == 4
        assert result["overall_status"] == "attention"

    # -- Tax pillar --------------------------------------------------
    def test_tax_pillar_attention_when_inputs_not_ready(self):
        result = self._call(tax_inputs_ready=False)
        tax = next(p for p in result["pillars"] if p["key"] == "tax")
        assert tax["status"] == "attention"
        assert tax["destination"] == "settings"

    def test_tax_pillar_flags_tax_loss_review_candidates(self):
        result = self._call(portfolio_recommendations=[
            {"category": "minor_optimization", "recommendation_key": "tax_loss_review:123"},
        ])
        tax = next(p for p in result["pillars"] if p["key"] == "tax")
        assert tax["status"] == "attention"
        assert "unrealized loss" in tax["detail"]

    def test_tax_pillar_flags_irmaa_tier_jump_over_plain_bracket_jump(self):
        result = self._call(rmd_result={
            "has_pretax_balance": True, "bracket_jump": True, "irmaa_tier_jump": True,
            "first_rmd_bracket": 0.24, "first_rmd_irmaa": {"tier_index": 2},
        })
        tax = next(p for p in result["pillars"] if p["key"] == "tax")
        assert tax["status"] == "attention"
        assert "IRMAA" in tax["detail"]

    def test_tax_pillar_flags_plain_bracket_jump_without_irmaa(self):
        result = self._call(rmd_result={
            "has_pretax_balance": True, "bracket_jump": True, "irmaa_tier_jump": False,
            "first_rmd_bracket": 0.24,
        })
        tax = next(p for p in result["pillars"] if p["key"] == "tax")
        assert tax["status"] == "attention"
        assert "marginal bracket" in tax["detail"]
        assert "IRMAA" not in tax["detail"]

    def test_tax_pillar_ignores_rmd_result_with_no_pretax_balance(self):
        result = self._call(rmd_result={"has_pretax_balance": False})
        tax = next(p for p in result["pillars"] if p["key"] == "tax")
        assert tax["status"] == "good"

    # -- Estate pillar -------------------------------------------------
    def test_estate_pillar_critical_on_outdated_document(self):
        docs = [{"document_type": "trust", "status": "outdated"}]
        result = self._call(estate_documents=docs)
        estate = next(p for p in result["pillars"] if p["key"] == "estate")
        assert estate["status"] == "critical"

    def test_estate_pillar_attention_on_missing_documents(self):
        result = self._call(estate_documents=[])
        estate = next(p for p in result["pillars"] if p["key"] == "estate")
        assert estate["status"] == "attention"
        assert "core documents" in estate["detail"]

    def test_estate_pillar_attention_on_pending_review_not_missing(self):
        docs = [{"document_type": t, "status": "executed"} for t in ("trust", "wills", "fpoa", "hcpoa", "freeze")]
        docs[0]["status"] = "verify"  # present, but not yet confirmed executed
        result = self._call(estate_documents=docs)
        estate = next(p for p in result["pillars"] if p["key"] == "estate")
        assert estate["status"] == "attention"
        assert "pending review" in estate["detail"]

    def test_estate_pillar_attention_on_no_beneficiaries(self):
        result = self._call(estate_beneficiaries=[])
        estate = next(p for p in result["pillars"] if p["key"] == "estate")
        assert estate["status"] == "attention"

    def test_estate_pillar_attention_on_blank_primary_beneficiary(self):
        result = self._call(estate_beneficiaries=[{"account_key": "401k", "primary_beneficiary": ""}])
        estate = next(p for p in result["pillars"] if p["key"] == "estate")
        assert estate["status"] == "attention"

    def test_estate_pillar_attention_on_estate_tax_exposure(self):
        result = self._call(estate_tax_exposure={"exposure": 500000})
        estate = next(p for p in result["pillars"] if p["key"] == "estate")
        assert estate["status"] == "attention"
        assert "500,000" in estate["detail"]

    # -- Risk pillar -----------------------------------------------------
    def test_risk_pillar_attention_when_no_insurance_recorded(self):
        result = self._call(insurance_policies=[])
        risk = next(p for p in result["pillars"] if p["key"] == "risk")
        assert risk["status"] == "attention"

    def test_risk_pillar_critical_on_insurance_shortfall(self):
        result = self._call(insurance_analysis={"jason": {"on_track": False, "surplus_gap": -250000}, "justin": {"on_track": True}})
        risk = next(p for p in result["pillars"] if p["key"] == "risk")
        assert risk["status"] == "critical"
        assert "250,000" in risk["detail"]

    # -- Investment pillar -------------------------------------------------
    def test_investment_pillar_critical_on_policy_violation(self):
        result = self._call(portfolio_recommendations=[{"category": "policy_violation"}])
        investment = next(p for p in result["pillars"] if p["key"] == "investment")
        assert investment["status"] == "critical"

    def test_investment_pillar_attention_on_rebalance_recommendation(self):
        result = self._call(portfolio_recommendations=[{"category": "taxable_rebalance"}])
        investment = next(p for p in result["pillars"] if p["key"] == "investment")
        assert investment["status"] == "attention"

    def test_investment_pillar_reads_category_from_nested_payload(self):
        result = self._call(portfolio_recommendations=[{"payload": {"category": "concentration_or_liquidity_risk"}}])
        investment = next(p for p in result["pillars"] if p["key"] == "investment")
        assert investment["status"] == "critical"

    def test_overall_status_is_the_worst_of_all_four(self):
        result = self._call(portfolio_recommendations=[{"category": "policy_violation"}], estate_documents=[])
        assert result["overall_status"] == "critical"
