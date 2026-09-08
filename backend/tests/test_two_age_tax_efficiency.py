"""
Independent, hand-verified reference cases for two-age Tax Efficiency --
written BEFORE jason_ret_age/justin_ret_age exist on
run_tax_efficiency_simulation (backend/docs/CALCULATION_CONTRACT.md
section 34, 2026-09-08, Milestone 3 of 4). This file is expected to
fail at collection until that implementation exists.

Verification strategy: monkeypatched random.gauss=0.0 (with the 0%
expected_return_post_retirement default in base_inputs) makes every one
of the N=1000 pre-generated trials identical, so median/p10/p90 all
collapse to the same single deterministic value -- computed by hand by
replaying the shared, already-reviewed primitives' own contracts
(_ordered_draw's DEFAULT_ORDER=taxable,pretax,hsa,roth taxed at flat
TAX_PRETAX=0.22/TAX_TAXABLE=0.15/TAX_ROTH=0.00, ROTH_FIRST_ORDER=roth,
taxable,pretax,hsa, _optimal_draw's cap-gains-then-pretax-then-taxable-
remainder policy), not inferred from the code under test.
"""

import pytest
import random as random_module

from simulation_engine import run_tax_efficiency_simulation

TAXABLE = lambda balance: [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": balance}]
PRETAX = lambda balance: [{"name": "IRA", "account_type": "pretax", "owner": "joint", "balance": balance}]


def base_inputs(**overrides):
    inputs = {
        "jason_age": 61, "justin_age": 61,
        "inflation_rate": 0.0,
        "expected_return_pre_retirement": 0.0,
        "expected_return_post_retirement": 0.0,
        "retirement_income_today_dollars": 50000,
        "annual_hsa_contribution": 0, "annual_rsu_value": 0,
        "jason_social_security": 0, "justin_social_security": 0,
        "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
        "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
        "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0,
        "annual_bonus_pct": 0,
        "pension_55": 0, "pension_60": 0, "pension_65": 0,
        "retirement_end_age": 63,
    }
    inputs.update(overrides)
    return inputs


@pytest.fixture(autouse=True)
def _deterministic_returns(monkeypatch):
    monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)


class TestBasicTaxableFirstTwoAge:
    """Both retire now at 61, 2-year horizon (ages 61-62), $80,000
    taxable / $300,000 pretax, $50,000/yr spending, 0% inflation/growth,
    no guaranteed income. taxable_first (DEFAULT_ORDER = taxable,
    pretax, hsa, roth):

    Year 0 (age 61): taxable draw min(50000,80000)=50000, untaxed ->
    taxable=30000, need fully funded, $0 tax.
    Year 1 (age 62): taxable draw min(50000,30000)=30000 (untaxed),
    remaining=20000 -> pretax grossed-up: gross=20000/0.78=25641.03,
    tax=25641.03*0.22=5641.03, pretax_after=300000-25641.03=274358.97.

    final_balance = round(274358.97) = 274359. lifetime_tax =
    round(5641.03) = 5641. Fully funded both years -> success_rate=100%
    (every one of the N=1000 identical deterministic trials)."""

    def test_two_year_taxable_first(self):
        inputs = base_inputs()
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000),
                                           jason_ret_age=61, justin_ret_age=61)
        assert r["mode"] == "two_age"
        tf = r["strategies"]["taxable_first"]
        assert tf["median_lifetime_tax"] == 5641
        assert tf["p10_lifetime_tax"] == 5641
        assert tf["p90_lifetime_tax"] == 5641
        assert tf["median_final_balance"] == 274359
        assert tf["success_rate"] == 100.0

    def test_roth_first_draws_roth_before_taxable_or_pretax(self):
        """Same household, $50,000 Roth added -- ROTH_FIRST_ORDER (roth,
        taxable, pretax, hsa) draws entirely from Roth first (untaxed),
        so with $50,000/yr spending and $50,000 Roth, year 0 is fully
        funded from Roth alone ($0 tax); year 1 draws from taxable
        (untaxed, $80,000 available) -> $0 tax both years, unlike
        taxable_first's $5,641 in the same household."""
        inputs = base_inputs()
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000) +
                                           [{"name": "Roth", "account_type": "roth_ira", "owner": "joint", "balance": 50000}],
                                           jason_ret_age=61, justin_ret_age=61)
        rf = r["strategies"]["roth_first"]
        assert rf["median_lifetime_tax"] == 0
        assert rf["median_final_balance"] == round(300000 + 80000 - 100000)  # 280000
        assert rf["success_rate"] == 100.0


class TestEitherRetirementOrderIsSymmetric:
    """Justin retires first (Jason's own salary funds gap income) vs.
    Jason retiring first (mirror) -- since Tax Efficiency's tax model is
    a flat rate with no bracket-capacity concept at all (section 34's
    contract), working income can only ever affect the NET spending
    offset, never a tax rate -- results must be byte-identical in both
    directions, not just similar."""

    def test_symmetric_gap_income_produces_identical_results(self):
        justin_first = base_inputs(retirement_income_today_dollars=50000, w2_salary=100000)
        jason_first  = base_inputs(retirement_income_today_dollars=50000, justin_w2_salary=100000)
        r_a = run_tax_efficiency_simulation(justin_first, PRETAX(300000) + TAXABLE(80000),
                                             jason_ret_age=63, justin_ret_age=61)
        r_b = run_tax_efficiency_simulation(jason_first, PRETAX(300000) + TAXABLE(80000),
                                             jason_ret_age=61, justin_ret_age=63)
        assert r_a["later_retiree"] == "jason"
        assert r_b["later_retiree"] == "justin"
        assert r_a["strategies"]["taxable_first"] == r_b["strategies"]["taxable_first"]
        assert r_a["strategies"]["roth_first"] == r_b["strategies"]["roth_first"]
        assert r_a["strategies"]["optimal"] == r_b["strategies"]["optimal"]
        assert r_a["still_working_spouse_income_first_year"] == r_b["still_working_spouse_income_first_year"]


class TestPastRetirementSelectionAndUnequalAges:
    def test_past_retirement_selection_starts_immediately(self):
        """Jason already 2 years past his selected retirement age (63
        today, selected 61) -- phase2 must start at his real current
        age, not the stale selection."""
        inputs = base_inputs(jason_age=63, justin_age=63, retirement_end_age=65)
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000),
                                           jason_ret_age=61, justin_ret_age=63)
        assert r["phase2_start_age"] == 63
        assert r["strategies"]["taxable_first"]["success_rate"] == 100.0

    def test_unequal_ages_use_each_spouses_own_ss_claim_age(self):
        """8-year age gap (Jason 61, Justin 53). Justin's own spousal SS
        (claim age 67 default) is nowhere near active at phase2_start."""
        inputs = base_inputs(jason_age=61, justin_age=53, justin_social_security=20000,
                              retirement_end_age=63)
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000),
                                           jason_ret_age=61, justin_ret_age=61)
        # No SS active in year 0 (Justin only 53) -- same taxable_first
        # numbers as the no-SS baseline test above.
        assert r["strategies"]["taxable_first"]["median_lifetime_tax"] == 5641


class TestNonzeroInflationAndReturns:
    def test_spending_and_taxes_scale_with_inflation_and_growth(self):
        """A positive growth rate must show up in the final balance
        (more growth -> a materially different number than the 0%
        baseline) -- 0% everywhere else in this file would mask a
        broken per-year loop entirely."""
        inputs = base_inputs(expected_return_post_retirement=0.05, inflation_rate=0.02)
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000),
                                           jason_ret_age=61, justin_ret_age=61)
        tf = r["strategies"]["taxable_first"]
        assert tf["median_final_balance"] != 274359
        assert tf["success_rate"] == 100.0


class TestShortfallReportsUnmetNeed:
    def test_insufficient_resources_fail_every_trial(self):
        """$0 everywhere, $50,000/yr spending, no guaranteed income --
        every trial fails every year -- success_rate must be 0%, not a
        false 100%."""
        inputs = base_inputs()
        r = run_tax_efficiency_simulation(inputs, PRETAX(0) + TAXABLE(0),
                                           jason_ret_age=61, justin_ret_age=61)
        for strat in ("taxable_first", "roth_first", "optimal"):
            assert r["strategies"][strat]["success_rate"] == 0.0


class TestAllThreeStrategiesShareTheSameTrialConditions:
    def test_zero_spending_makes_all_three_strategies_identical(self):
        """With $0 spending need, no strategy ever draws anything --
        all three must report the exact same (unchanged) final balance
        and $0 tax, proving they run against the same starting
        balances/timeline/returns and only differ in draw POLICY, not
        underlying trial conditions."""
        inputs = base_inputs(retirement_income_today_dollars=0)
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000),
                                           jason_ret_age=61, justin_ret_age=61)
        tf, rf, opt = (r["strategies"][s] for s in ("taxable_first", "roth_first", "optimal"))
        assert tf["median_final_balance"] == rf["median_final_balance"] == opt["median_final_balance"] == 380000
        assert tf["median_lifetime_tax"] == rf["median_lifetime_tax"] == opt["median_lifetime_tax"] == 0


class TestTwoAgeTaxEfficiencyModeRequiresBothAges:
    def test_only_jason_ret_age_raises(self):
        inputs = base_inputs()
        with pytest.raises(ValueError):
            run_tax_efficiency_simulation(inputs, PRETAX(300000), jason_ret_age=61)

    def test_only_justin_ret_age_raises(self):
        inputs = base_inputs()
        with pytest.raises(ValueError):
            run_tax_efficiency_simulation(inputs, PRETAX(300000), justin_ret_age=61)


class TestSingleAgeModeUnaffected:
    def test_default_call_has_no_two_age_fields(self, sample_inputs, sample_accounts):
        r = run_tax_efficiency_simulation(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        assert "mode" not in r
        assert "jason_ret_age" not in r
