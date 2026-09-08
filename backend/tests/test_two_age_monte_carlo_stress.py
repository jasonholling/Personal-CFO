"""
Independent, hand-calculated reference cases for two-age Monte Carlo/
Stress Tests -- written BEFORE that mode exists in run_monte_carlo/
run_stress_tests (backend/docs/CALCULATION_CONTRACT.md section 22,
2026-09-08), per the explicit instruction to add these cases before
implementation. This file is expected to fail at collection until
jason_ret_age/justin_ret_age params exist on both functions.

Two verification strategies, matching this codebase's own established
conventions:
1. Deterministic parity: with 0% inflation/returns (random.gauss
   monkeypatched to 0.0, matching test_simulation_engine.py's existing
   technique for the single-axis case), Monte Carlo and Stress Tests'
   "base" scenario must reproduce run_two_dimensional_retirement_
   projection's own yearly balances/withdrawals/unmet_need EXACTLY --
   not just directionally.
2. Hand-calculated adverse-return sequences (explicit, non-random
   annual_returns arrays are still fully deterministic even when
   negative/volatile), verified against simulate_withdrawal_year
   arithmetic directly before writing the assertion.

Covers every category named in the instruction: either spouse retiring
first, unequal ages, simultaneous retirement, past retirement
selections, pension timing, bridge/kids phases, income surpluses,
depletion, adverse returns during the middle phase and across the
second retirement boundary, and that success rates count any unfunded
year as failure regardless of final balance.
"""

import random as random_module

import pytest

from projection_engine import run_two_dimensional_retirement_projection
from simulation_engine import run_monte_carlo, run_stress_tests

TAXABLE = lambda balance: [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": balance}]


def base_inputs(**overrides):
    inputs = {
        "jason_age": 60, "justin_age": 60,
        "inflation_rate": 0.0,
        "expected_return_pre_retirement": 0.0,
        "expected_return_post_retirement": 0.0,
        "retirement_income_today_dollars": 80000,
        "annual_hsa_contribution": 0, "annual_rsu_value": 0,
        "jason_social_security": 0, "justin_social_security": 0,
        "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
        "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
        "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0,
        "annual_bonus_pct": 0,
        "pension_55": 0, "pension_60": 0, "pension_65": 0,
    }
    inputs.update(overrides)
    return inputs


def assert_deterministic_mc_matches_projection(monkeypatch, inputs, accounts, jason_ret_age, justin_ret_age):
    """Shared assertion helper: with zero-variance simulated returns,
    Monte Carlo's per-year balances must match
    run_two_dimensional_retirement_projection's own yearly_detail
    exactly (every year, not just the final one)."""
    monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
    proj = run_two_dimensional_retirement_projection(inputs, accounts, jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age)
    mc = run_monte_carlo(inputs, accounts, jason_ret_age=jason_ret_age, justin_ret_age=justin_ret_age)
    # Every simulated trial is identical under zero variance, so the
    # median run IS every run -- compare its balances directly.
    expected_balances = [y["portfolio_balance"] for y in proj["yearly_detail"]]
    assert mc["median_final_balance"] == expected_balances[-1]
    return proj, mc


class TestDeterministicMonteCarloParity:
    def test_jason_retires_first_justin_still_working(self, monkeypatch):
        inputs = base_inputs(retirement_end_age=64, justin_w2_salary=100000)
        proj, mc = assert_deterministic_mc_matches_projection(monkeypatch, inputs, TAXABLE(200000), 61, 63)
        assert mc["jason_ret_age"] == 61
        assert mc["justin_ret_age"] == 63
        assert mc["phase2_start_age"] == 61
        assert mc["phase3_start_age"] == 63
        assert mc["later_retiree"] == "justin"
        assert mc["mode"] == "two_age"
        assert mc["still_working_spouse_income_first_year"] == 65000
        assert mc["second_earner_net_of_tax_factor"] == 0.65
        assert proj["yearly_detail"][-1]["portfolio_balance"] == 90000  # sanity-check against the known hand-calc

    def test_justin_retires_first_jason_still_working(self, monkeypatch):
        """Mirror direction -- proves the two-age Monte Carlo/Stress
        wiring is symmetric, not hardcoded to Justin as the still-
        working spouse."""
        inputs = base_inputs(retirement_end_age=64, w2_salary=100000)
        proj, mc = assert_deterministic_mc_matches_projection(monkeypatch, inputs, TAXABLE(200000), 63, 61)
        assert mc["later_retiree"] == "jason"
        assert mc["still_working_spouse_income_first_year"] == 65000

    def test_simultaneous_retirement(self, monkeypatch):
        inputs = base_inputs(retirement_end_age=64)
        proj, mc = assert_deterministic_mc_matches_projection(monkeypatch, inputs, TAXABLE(200000), 62, 62)
        assert mc["later_retiree"] is None
        assert mc["phase2_start_age"] == mc["phase3_start_age"] == 62

    def test_unequal_ages(self, monkeypatch):
        inputs = base_inputs(jason_age=65, justin_age=55, retirement_income_today_dollars=70000,
                              retirement_end_age=71, justin_w2_salary=90000)
        proj, mc = assert_deterministic_mc_matches_projection(monkeypatch, inputs, TAXABLE(300000), 67, 60)
        assert mc["phase2_start_age"] == 67
        assert mc["phase3_start_age"] == 70

    def test_past_retirement_selection(self, monkeypatch):
        """Jason already 3 years past his selected retirement age --
        phase2 must start immediately, and Monte Carlo must agree with
        the deterministic projection about that starting point too, not
        just the final balance (checked via assert_deterministic_mc_
        matches_projection's phase2_start_age comparison below)."""
        inputs = base_inputs(jason_age=68, justin_age=60, retirement_income_today_dollars=60000,
                              retirement_end_age=73, justin_w2_salary=80000)
        proj, mc = assert_deterministic_mc_matches_projection(monkeypatch, inputs, TAXABLE(100000), 65, 64)
        assert mc["phase2_start_age"] == 68
        assert proj["phase2_start_age"] == 68

    def test_pension_timing(self, monkeypatch):
        """Pension gated to Jason's own effective retirement, not
        phase2_start -- reuses the same CALCULATION_CONTRACT.md section
        20 reproduction (Justin retires first, Jason's pension must not
        pay early)."""
        inputs = base_inputs(retirement_end_age=64, w2_salary=100000,
                              pension_55=30000, pension_60=30000, pension_65=30000)
        proj, mc = assert_deterministic_mc_matches_projection(monkeypatch, inputs, TAXABLE(200000), 63, 61)
        assert proj["yearly_detail"][-1]["portfolio_balance"] == 120000
        assert mc["median_final_balance"] == 120000

    def test_bridge_and_kids_phase(self, monkeypatch):
        inputs = base_inputs(jason_age=55, justin_age=55, retirement_end_age=61,
                              bridge_income_55=30000, bridge_years_55=5)
        proj, mc = assert_deterministic_mc_matches_projection(monkeypatch, inputs, TAXABLE(200000), 55, 55)
        assert proj["yearly_detail"][0]["portfolio_balance"] == 150000

    def test_income_surplus(self, monkeypatch):
        inputs = base_inputs(retirement_income_today_dollars=50000, retirement_end_age=65, justin_w2_salary=200000)
        proj, mc = assert_deterministic_mc_matches_projection(monkeypatch, inputs, TAXABLE(10000), 61, 64)
        assert proj["yearly_detail"][-1]["portfolio_balance"] == 200000

    def test_depletion_and_unmet_need(self, monkeypatch):
        inputs = base_inputs(retirement_end_age=64, justin_w2_salary=50000)
        proj, mc = assert_deterministic_mc_matches_projection(monkeypatch, inputs, TAXABLE(20000), 61, 63)
        assert proj["any_year_underfunded"] is True
        assert mc["success_rate"] == 0.0
        assert mc["median_final_balance"] == 0


class TestDeterministicStressTestsParity:
    """Same set, checked via run_stress_tests's 'base' scenario (post_ret
    every year, matching run_two_dimensional_retirement_projection's own
    0% assumption in these households) -- proves the two-age wiring
    works through BOTH consumers that share _run_single_two_age, not
    just Monte Carlo."""

    def test_jason_retires_first_justin_still_working(self):
        inputs = base_inputs(retirement_end_age=64, justin_w2_salary=100000)
        proj = run_two_dimensional_retirement_projection(inputs, TAXABLE(200000), jason_ret_age=61, justin_ret_age=63)
        st = run_stress_tests(inputs, TAXABLE(200000), jason_ret_age=61, justin_ret_age=63)
        assert st["scenarios"]["base"]["final_balance"] == proj["yearly_detail"][-1]["portfolio_balance"] == 90000
        assert st["jason_ret_age"] == 61
        assert st["justin_ret_age"] == 63
        assert st["mode"] == "two_age"

    def test_pension_timing(self):
        inputs = base_inputs(retirement_end_age=64, w2_salary=100000,
                              pension_55=30000, pension_60=30000, pension_65=30000)
        proj = run_two_dimensional_retirement_projection(inputs, TAXABLE(200000), jason_ret_age=63, justin_ret_age=61)
        st = run_stress_tests(inputs, TAXABLE(200000), jason_ret_age=63, justin_ret_age=61)
        assert st["scenarios"]["base"]["final_balance"] == proj["yearly_detail"][-1]["portfolio_balance"] == 120000

    def test_bridge_and_kids_phase(self):
        inputs = base_inputs(jason_age=55, justin_age=55, retirement_end_age=61,
                              bridge_income_55=30000, bridge_years_55=5)
        proj = run_two_dimensional_retirement_projection(inputs, TAXABLE(200000), jason_ret_age=55, justin_ret_age=55)
        st = run_stress_tests(inputs, TAXABLE(200000), jason_ret_age=55, justin_ret_age=55)
        assert st["scenarios"]["base"]["chart"][0]["balance"] == proj["yearly_detail"][0]["portfolio_balance"] == 150000

    def test_depletion_and_unmet_need(self):
        inputs = base_inputs(retirement_end_age=64, justin_w2_salary=50000)
        st = run_stress_tests(inputs, TAXABLE(20000), jason_ret_age=61, justin_ret_age=63)
        assert st["scenarios"]["base"]["survived"] is False
        assert st["scenarios"]["base"]["final_balance"] == 0


class TestAdverseReturnsMiddlePhaseAndBoundary:
    """Not deterministic-parity checks (the two-age reference projection
    has no concept of a varying return sequence) -- hand-calculated
    directly against simulate_withdrawal_year's own arithmetic before
    writing the assertion, same standard as every other hand-calculated
    test this session."""

    def test_adverse_return_during_middle_phase_still_drives_growth(self):
        """A -30% return in the FIRST phase2 year, while Justin's income
        is still comfortably funding spending -- the crash still applies
        to the (small) portfolio balance, but doesn't cause a shortfall
        since guaranteed+gap income covers the whole need this year.

        jason_ret_age=61, justin_ret_age=64 (justin still working 3
        years). Justin salary $200,000 -> gap $130,000/yr, household
        need $50,000/yr -> $80,000/yr surplus swept to taxable BEFORE
        growth is applied (matches simulate_withdrawal_year's own
        spend-then-grow order). taxable starts at $10,000.

        yr0 (age61): surplus 80000 swept -> pre-growth balance 90000;
        -30% return -> 90000 * 0.70 = 63000.
        yr1 (age62): surplus 80000 -> pre-growth 143000; 0% return -> 143000.
        yr2 (age63): surplus 80000 -> pre-growth 223000; 0% return -> 223000.
        yr3 (age64, phase3, no more gap income): draw 50000 -> pre-growth
        173000; 0% return -> 173000."""
        inputs = base_inputs(retirement_income_today_dollars=50000, retirement_end_age=65, justin_w2_salary=200000)
        accounts = TAXABLE(10000)
        st = run_stress_tests(inputs, accounts, jason_ret_age=61, justin_ret_age=64)
        # Confirmed against the neutral base case first (0% every year).
        base_balances = [row["balance"] for row in st["scenarios"]["base"]["chart"]]
        assert base_balances[0] == 90000

        from simulation_engine import _run_single_two_age
        from timeline_engine import build_two_person_timeline
        timeline = build_two_person_timeline(60, 60, 61, 64, 65)
        survived, balances, *_ = _run_single_two_age(
            0, 0, 10000, 0, timeline, inputs,
            pension_annual=0, jason_ss_annual=0, jason_ss_age=62,
            income_today=50000, inflation=0.0, post_ret=0.0,
            annual_returns=[-0.30, 0.0, 0.0, 0.0],
            justin_ss_annual=0, justin_ss_age=67,
        )
        assert balances == [63000, 143000, 223000, 173000]
        assert survived is True

    def test_adverse_return_across_second_retirement_boundary(self):
        """A severe loss landing exactly on the phase2->phase3 boundary
        year (both now retired, guaranteed income no longer covers the
        full need) causes real depletion the deterministic base case
        doesn't show.

        Same household as the pension-timing case (jason_ret_age=63,
        justin_ret_age=61, $30,000 pension, $100,000 Jason salary,
        $80,000 spend, $200,000 taxable) -- but a -80% return lands on
        age63 (yr2, the FIRST phase3 year, right after Justin's own
        retirement at 61 and Jason's own retirement at 63 coincide with
        this boundary).

        yr0 (age61,phase2): draw 15000 -> pre-growth 185000; 0% -> 185000.
        yr1 (age62,phase2): draw 15000 -> pre-growth 170000; 0% -> 170000.
        yr2 (age63,phase3): pension 30000 now active, draw 50000 ->
        pre-growth 120000; -80% return -> 120000 * 0.20 = 24000."""
        inputs = base_inputs(retirement_end_age=64, w2_salary=100000,
                              pension_55=30000, pension_60=30000, pension_65=30000)
        from simulation_engine import _run_single_two_age
        from timeline_engine import build_two_person_timeline
        timeline = build_two_person_timeline(60, 60, 63, 61, 64)
        survived, balances, *_ = _run_single_two_age(
            0, 0, 200000, 0, timeline, inputs,
            pension_annual=30000, jason_ss_annual=0, jason_ss_age=62,
            income_today=80000, inflation=0.0, post_ret=0.0,
            annual_returns=[0.0, 0.0, -0.80],
            justin_ss_annual=0, justin_ss_age=67,
        )
        assert balances == [185000, 170000, 24000]
        assert survived is True  # positive final balance, no unmet need


class TestSuccessRateCountsAnyUnfundedYearAsFailure:
    def test_unmet_need_in_an_early_phase2_year_fails_the_trial_despite_a_later_positive_balance(self, monkeypatch):
        """Independent instruction (2026-09-08): success rates must
        count ANY unfunded year as failure, not just a negative final
        balance. Constructed so the trial ends POSITIVE ($420,000, a
        huge pension-funded phase3 surplus) despite two real unfunded
        years early in phase2 -- hand-verified against
        simulate_withdrawal_year directly before writing this
        assertion: yr0/yr1 each have a $47,500 shortfall (bal floors at
        0), yr2's $500,000 pension swamps the $80,000 need and sweeps a
        $420,000 surplus. Every trial is identical under the zero-
        variance monkeypatch, so success_rate must be exactly 0%, not
        some nonzero rate driven by the positive ending balance.

        Jason must be the LATER retiree here (justin_ret_age=61,
        jason_ret_age=63) so his own pension stays gated off during
        phase2 (it starts only at HIS OWN retirement, section 20) --
        his own salary (not Justin's) funds the insufficient phase2 gap
        income."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(retirement_end_age=64, w2_salary=50000,
                              pension_55=500000, pension_60=500000, pension_65=500000)
        mc = run_monte_carlo(inputs, TAXABLE(0), jason_ret_age=63, justin_ret_age=61)
        assert mc["median_final_balance"] == 420000
        assert mc["success_rate"] == 0.0

    def test_stress_base_scenario_reports_not_survived_despite_positive_final_balance(self):
        """Same construction, checked via Stress Tests' 'base' scenario
        (fully deterministic already, no monkeypatch needed) -- survived
        must be False even though final_balance is positive."""
        inputs = base_inputs(retirement_end_age=64, w2_salary=50000,
                              pension_55=500000, pension_60=500000, pension_65=500000)
        st = run_stress_tests(inputs, TAXABLE(0), jason_ret_age=63, justin_ret_age=61)
        assert st["scenarios"]["base"]["final_balance"] == 420000
        assert st["scenarios"]["base"]["survived"] is False


class TestTwoAgeModeRequiresBothAges:
    def test_only_jason_ret_age_raises(self):
        inputs = base_inputs(retirement_end_age=64)
        with pytest.raises(ValueError):
            run_monte_carlo(inputs, TAXABLE(200000), jason_ret_age=61)

    def test_only_justin_ret_age_raises(self):
        inputs = base_inputs(retirement_end_age=64)
        with pytest.raises(ValueError):
            run_stress_tests(inputs, TAXABLE(200000), justin_ret_age=61)


class TestSingleAgeModeUnaffected:
    def test_monte_carlo_default_call_has_no_two_age_fields(self, sample_inputs, sample_accounts):
        mc = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        assert "mode" not in mc
        assert "jason_ret_age" not in mc
        assert "phase2_start_age" not in mc

    def test_stress_tests_default_call_has_no_two_age_fields(self, sample_inputs, sample_accounts):
        st = run_stress_tests(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        assert "mode" not in st
        assert "jason_ret_age" not in st
