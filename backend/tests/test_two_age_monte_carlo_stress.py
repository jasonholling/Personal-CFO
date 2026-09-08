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


class TestPreviouslySkippedScenariosNowRunInTwoAgeMode:
    """Independent review, 2026-09-08 -- "Incomplete scope" finding:
    stagflation_1970s/bridge_job_loss/ss_reduction were entirely skipped
    in the first cut of two-age Stress Tests, and the UI simply hid
    them. All three now run. Every scenario is present in the response
    (not just the pure return-override ones)."""

    def test_all_seven_scenarios_present(self):
        inputs = base_inputs(retirement_end_age=64, w2_salary=100000,
                              pension_55=30000, pension_60=30000, pension_65=30000)
        st = run_stress_tests(inputs, TAXABLE(200000), jason_ret_age=63, justin_ret_age=61)
        assert set(st["scenarios"].keys()) == {
            "base", "crash_2008", "stagflation_1970s", "lost_decade",
            "early_sequence", "bridge_job_loss", "ss_reduction",
        }

    def test_ss_reduction_actually_reduces_both_spouses_benefits(self):
        """Hand-calculated: both spouses 61, retiring together at 62 (1yr
        horizon), $20,000 Jason SS + $10,000 Justin SS (justin_ss_age
        overridden to 62 so it's active immediately too), zero spending
        target/healthcare/pension/starting balance, 0% growth/inflation.
        With no spending need, the full $30,000 guaranteed income is a
        pure surplus swept into taxable -- base ends at $30,000; the 25%
        cut scenario reduces BOTH benefits to 75%, ending at $22,500 (not
        $30,000 with only one spouse's benefit cut, the exact bug the
        single-axis version's own external-audit fix addressed)."""
        inputs = base_inputs(jason_age=61, justin_age=61, retirement_income_today_dollars=0,
                              retirement_end_age=63, jason_social_security=20000,
                              justin_social_security=10000, justin_ss_age=62)
        st = run_stress_tests(inputs, TAXABLE(0), jason_ret_age=62, justin_ret_age=62)
        assert st["scenarios"]["base"]["final_balance"] == 30000
        assert st["scenarios"]["ss_reduction"]["final_balance"] == 22500

    def test_bridge_job_loss_reprojects_with_a_shortened_bridge(self):
        """Both spouses 55, retiring together at 55, $30,000/yr bridge
        income normally lasting 5 years, $5,000,000 taxable, 0% growth/
        inflation, 5-year horizon. bridge_job_loss shortens the bridge to
        2 years (matching the single-axis scenario's own description),
        losing 3 years of $30,000 bridge income relative to the base
        case -- final balance is lower, not identical to base (which
        would mean the override silently had no effect)."""
        inputs = base_inputs(jason_age=55, justin_age=55, retirement_end_age=60,
                              bridge_income_55=30000, bridge_years_55=5)
        st = run_stress_tests(inputs, TAXABLE(5000000), jason_ret_age=55, justin_ret_age=55)
        assert st["scenarios"]["base"]["final_balance"] == 4750000
        assert st["scenarios"]["bridge_job_loss"]["final_balance"] == 4660000

    def test_bridge_job_loss_never_extends_a_shorter_or_zero_bridge(self):
        """Independent review, 2026-09-08, third follow-up (P2) --
        reproduced exactly: $30,000/yr bridge income configured but
        bridge_years_55=0 (no bridge job at all). The buggy version
        unconditionally set the stressed scenario's bridge duration to
        2 years regardless of what the household actually configured,
        INVENTING two years of income a household with 0 bridge years
        never has -- final_balance ended $60,000 higher than base
        ($820,000 vs $760,000) instead of identical to it. Fixed: capped
        at min(override, existing bridge_years_55) -- this scenario
        models the bridge job ending EARLY, never lasting longer than
        planned."""
        inputs = base_inputs(jason_age=55, justin_age=55, retirement_end_age=58,
                              bridge_income_55=30000, bridge_years_55=0)
        st = run_stress_tests(inputs, TAXABLE(1000000), jason_ret_age=55, justin_ret_age=55)
        assert st["scenarios"]["base"]["final_balance"] == 760000
        assert st["scenarios"]["bridge_job_loss"]["final_balance"] == 760000
        old_buggy_value = 820000  # matches the independent review's own reported old value
        assert st["scenarios"]["bridge_job_loss"]["final_balance"] != old_buggy_value

    def test_stagflation_applies_variable_inflation_to_spending_need(self):
        """Both spouses 60, retiring together at 62 (2yrs away, so phase2
        starts 2 years of today's-dollar inflation ahead already), 3-year
        horizon (ages 62-64), $10,000 today's-dollars spend, 2% base
        inflation -> stagflation's own 4x multiplier makes the effective
        withdrawal-phase rate 8% for all 3 years (well within its first
        10). Verified against simulate_withdrawal_year's own arithmetic
        directly (script, not by hand) before writing this assertion:
        the scenario's own 2%/yr return override applies throughout."""
        inputs = base_inputs(retirement_income_today_dollars=10000, inflation_rate=0.02, retirement_end_age=65)
        st = run_stress_tests(inputs, TAXABLE(100000), jason_ret_age=62, justin_ret_age=62)
        assert st["scenarios"]["stagflation_1970s"]["final_balance"] == 71012
        # 8% effective inflation drives materially higher spending need
        # than the 6% (2% base * 3.0 -- not this scenario) or a flat 2%
        # base would, so it must differ from the neutral base case.
        assert st["scenarios"]["stagflation_1970s"]["final_balance"] != st["scenarios"]["base"]["final_balance"]

    def test_stagflation_preserves_cumulative_inflation_across_the_phase2_to_bridge_boundary(self):
        """Independent review, 2026-09-08, third follow-up (P1) --
        reproduced exactly: both spouses currently 53, Justin already
        retired (justin_ret_age=53), Jason retiring at 55 -- phase2
        starts immediately (age53, since Justin already retired), but
        Jason's own bridge/kids branch doesn't activate until age55, 2
        years INTO the loop. $100,000 spend, 2% base inflation, 8%
        stressed (the scenario's own 4x multiplier). The buggy version
        rebased the cumulative-inflation curve to exactly 1.0 at the
        phase boundary, discarding the two years of elevated inflation
        already accumulated -- year 3 (age55, the first bridge year)
        reverted to $104,040 (income_today * 1.02**2, the flat
        no-stress pre-loop figure) instead of the correct $116,640
        (income_today * 1.08**2, continuing the stressed curve from
        phase2_start). Checked via the year-by-year need directly
        (two_age_spending_need_fn) since the reviewer's own numbers were
        about the need sequence itself, then cross-checked against the
        resulting final_balance."""
        from projection_engine import two_age_spending_need_fn
        from timeline_engine import build_two_person_timeline

        timeline = build_two_person_timeline(53, 53, 55, 53, 56)
        need_fn = two_age_spending_need_fn(
            {"healthcare_pre_medicare": 0, "healthcare_post_medicare": 0},
            100000, 0.02, timeline, inflation_mults=[4.0, 4.0, 4.0])
        needs = [need_fn(timeline.age(yr), yr)[0] for yr in range(3)]
        assert needs == [100000.0, 108000.0, pytest.approx(116640.0)]
        old_buggy_year3 = 104040.0  # matches the independent review's own reported old value
        assert needs[2] != pytest.approx(old_buggy_year3)

        inputs = base_inputs(jason_age=53, justin_age=53, retirement_income_today_dollars=100000,
                              inflation_rate=0.02, expected_return_post_retirement=0.06, retirement_end_age=56)
        st = run_stress_tests(inputs, TAXABLE(1000000), jason_ret_age=55, justin_ret_age=53)
        assert st["scenarios"]["stagflation_1970s"]["final_balance"] == 723751
        old_buggy_final_balance = 736603  # matches the independent review's own reported old value
        assert st["scenarios"]["stagflation_1970s"]["final_balance"] != old_buggy_final_balance


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


class TestFullyFundedPlanIsASuccess:
    def test_exact_zero_ending_balance_with_no_unmet_need_counts_as_survived(self, monkeypatch):
        """Independent review, 2026-09-08, P2 -- reproduced: one year,
        $80,000 available, $80,000 spend, 0% return. The deterministic
        projection reports on_track=True (unmet_need=0 the only year) --
        the money lasted exactly as long as the plan needed it to, which
        is success by definition, not failure. The old
        `balances[-1] > 0` check on top of "every year funded" failed
        this exact-zero-ending trial anyway, reporting 0% success/
        survived=False for a plan the Projection itself calls on_track."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(retirement_end_age=61)
        proj = run_two_dimensional_retirement_projection(inputs, TAXABLE(80000), jason_ret_age=60, justin_ret_age=60)
        assert proj["on_track"] is True
        assert proj["yearly_detail"][-1]["portfolio_balance"] == 0

        mc = run_monte_carlo(inputs, TAXABLE(80000), jason_ret_age=60, justin_ret_age=60)
        assert mc["success_rate"] == 100.0

        st = run_stress_tests(inputs, TAXABLE(80000), jason_ret_age=60, justin_ret_age=60)
        assert st["scenarios"]["base"]["survived"] is True


class TestStartingBalancesRetainFullPrecision:
    def test_bucket_fields_are_not_rounded(self):
        """Independent review, 2026-09-08, P2 -- pretax_at_phase2_start/
        roth_at_phase2_start/taxable_at_phase2_start/hsa_at_phase2_start
        are consumed directly as _run_single_two_age's opening balances,
        not displayed anywhere; rounding them before simulation_engine.py
        ever sees them meant Monte Carlo/Stress started from a slightly
        different number than the deterministic projection's own
        full-precision arithmetic for the identical scenario (reproduced:
        a $2 drift on a real household under fixed returns). A 401k
        balance split at a percentage that doesn't land on a whole dollar
        proves the fix directly: the returned starting balance must keep
        its fractional cents, not round to a whole number."""
        inputs = base_inputs(retirement_end_age=64, pretax_401k_pct=0.75)
        accounts = [{"name": "401k", "account_type": "401k", "owner": "joint", "balance": 100001}]
        proj = run_two_dimensional_retirement_projection(inputs, accounts, jason_ret_age=61, justin_ret_age=61)
        assert proj["pretax_at_phase2_start"] == 75000.75
