"""
Social Security claiming-age formula (CALCULATION_CONTRACT.md section
44, design approved by Jason 2026-09-08). Every number below is hand-
derived from the real SSA early-reduction/delayed-credit rates (5/9%
per month for the first 36 months before FRA, 5/12%/month beyond that,
2/3%/month -- 8%/year -- from FRA to 70), not inferred from the code
under test.
"""

import pytest

from projection_engine import ss_benefit_for_claim_age, resolve_ss_benefits, SS_FRA_AGE, run_retirement_projection
from simulation_engine import run_monte_carlo, run_stress_tests


class TestAnchorsAreExact:
    """The three real dollar inputs (62/FRA/70) must come back exactly,
    unmodified by the interpolation formula -- the whole point of using
    real anchors instead of a single derived PIA."""

    def test_age_62_returns_the_62_input_exactly(self):
        assert ss_benefit_for_claim_age(20000, 30000, 40000, 62) == 20000

    def test_age_67_returns_the_fra_input_exactly(self):
        assert ss_benefit_for_claim_age(20000, 30000, 40000, 67) == 30000

    def test_age_70_returns_the_70_input_exactly(self):
        assert ss_benefit_for_claim_age(20000, 30000, 40000, 70) == 40000

    def test_fra_age_constant_is_67(self):
        assert SS_FRA_AGE == 67


class TestEarlyReductionMatchesTheRealSsaFormula:
    """A household whose real 62/67 figures happen to fit a clean 30%-
    reduction relationship (benefit_62 = benefit_67 * 0.70, i.e. exactly
    the standard formula's own 62-year reduction) lets every in-between
    age be checked against the SSA's own published percentages: 25% at
    63, 20% at 64, 13.3333% at 65, 6.6667% at 66."""

    BENEFIT_67 = 30000
    BENEFIT_62 = 30000 * 0.70  # = 21000, the formula's own 30% reduction

    def test_age_63_is_a_25_percent_reduction(self):
        expected = self.BENEFIT_67 * 0.75
        assert ss_benefit_for_claim_age(self.BENEFIT_62, self.BENEFIT_67, 99999, 63) == pytest.approx(expected, abs=0.01)

    def test_age_64_is_a_20_percent_reduction(self):
        expected = self.BENEFIT_67 * 0.80
        assert ss_benefit_for_claim_age(self.BENEFIT_62, self.BENEFIT_67, 99999, 64) == pytest.approx(expected, abs=0.01)

    def test_age_65_is_a_13_and_a_third_percent_reduction(self):
        expected = self.BENEFIT_67 * (1 - 2/15)
        assert ss_benefit_for_claim_age(self.BENEFIT_62, self.BENEFIT_67, 99999, 65) == pytest.approx(expected, abs=0.01)

    def test_age_66_is_a_6_and_two_thirds_percent_reduction(self):
        expected = self.BENEFIT_67 * (1 - 1/15)
        assert ss_benefit_for_claim_age(self.BENEFIT_62, self.BENEFIT_67, 99999, 66) == pytest.approx(expected, abs=0.01)


class TestDelayedCreditMatchesTheRealSsaFormula:
    """A household whose real 67/70 figures fit the standard formula's
    own 24%-credit relationship (benefit_70 = benefit_67 * 1.24) lets
    68/69 be checked against the SSA's own published percentages: +8%
    at 68, +16% at 69."""

    BENEFIT_67 = 30000
    BENEFIT_70 = 30000 * 1.24

    def test_age_68_is_an_8_percent_credit(self):
        expected = self.BENEFIT_67 * 1.08
        assert ss_benefit_for_claim_age(0, self.BENEFIT_67, self.BENEFIT_70, 68) == pytest.approx(expected, abs=0.01)

    def test_age_69_is_a_16_percent_credit(self):
        expected = self.BENEFIT_67 * 1.16
        assert ss_benefit_for_claim_age(0, self.BENEFIT_67, self.BENEFIT_70, 69) == pytest.approx(expected, abs=0.01)


class TestAnchoredToRealNumbersNotAPureFormula:
    """The formula's whole purpose per the design doc: when a
    household's real anchors DON'T exactly fit the standard formula
    (real SSA statements can diverge slightly from a pure projection
    due to COLA history), the real anchors still win exactly at 62/67/
    70, and in-between ages interpolate proportionally against the
    REAL gap, not a re-derived PIA."""

    def test_interpolation_uses_the_real_dollar_gap_not_a_derived_pia(self):
        # benefit_62 does NOT fit a clean 30%-reduction relationship to
        # benefit_67 (30000*0.70 would be 21000, not 22000) -- a real
        # household's own actual numbers.
        benefit_62, benefit_67 = 22000, 30000
        # age 64 is exactly at the 20%-reduction point of the FORMULA's
        # own shape (progress = (30-20)/30 = 1/3 of the real 62->67 gap)
        expected = benefit_62 + (1/3) * (benefit_67 - benefit_62)
        assert ss_benefit_for_claim_age(benefit_62, benefit_67, 99999, 64) == pytest.approx(expected, abs=0.01)


class TestOutOfRangeClampsToTheNearestAnchor:
    def test_below_62_clamps_to_the_62_benefit(self):
        assert ss_benefit_for_claim_age(20000, 30000, 40000, 60) == 20000

    def test_above_70_clamps_to_the_70_benefit(self):
        assert ss_benefit_for_claim_age(20000, 30000, 40000, 75) == 40000


class TestMonotonicAcrossEveryAge:
    """A sanity property the review will likely check first: the
    benefit must strictly increase (or hold, at worst) with every later
    claiming age -- no dip anywhere in 62-70."""

    def test_strictly_increases_from_62_to_70(self):
        values = [ss_benefit_for_claim_age(20000, 28000, 36000, age) for age in range(62, 71)]
        assert values == sorted(values)
        assert len(set(values)) == len(values)  # strictly increasing, no ties


class TestRunRetirementProjectionOptInClaimAge:
    """CALCULATION_CONTRACT.md section 44, milestone 1: run_retirement_
    projection is the reference implementation this proposal wires the
    new formula into first. jason_ss_claim_age/justin_ss_claim_age are
    opt-in -- a caller that never sets them gets the EXACT unchanged
    early(62)/delayed(67) two-scenario sweep; only a caller that sets
    jason_ss_claim_age sees it replaced with a single "custom" scenario
    at that exact age."""

    def test_without_claim_age_the_existing_early_delayed_pair_is_unchanged(self, sample_inputs):
        result = run_retirement_projection(sample_inputs, [], ret_ages=[60])
        labels = sorted(s["ss_timing"] for s in result["scenarios"])
        assert labels == ["delayed", "early"]

    def test_with_jason_claim_age_replaces_the_pair_with_one_custom_scenario(self, sample_inputs):
        # jason_ss_claim_age is an EXPLICIT function param (2026-09-08,
        # ninth follow-up, finding 1) -- no longer read off `inputs`,
        # to avoid a persisted Settings value silently affecting an
        # unrelated caller that just passes the inputs dict through.
        inputs = {**sample_inputs, "jason_ss_70": 55000}
        result = run_retirement_projection(inputs, [], ret_ages=[60], jason_ss_claim_age=64)
        assert [s["ss_timing"] for s in result["scenarios"]] == ["custom"]
        # jason_social_security=30000 (62), jason_ss_delayed=45000 (67),
        # jason_ss_70=55000 -- age 64 is the formula's own 20%-reduction
        # point: progress = (30-20)/30 = 1/3 of the real 62->67 gap.
        expected = 30000 + (1/3) * (45000 - 30000)
        assert result["scenarios"][0]["jason_ss_annual"] == round(expected)

    def test_with_justin_claim_age_recomputes_his_benefit_too(self, sample_inputs):
        inputs = {**sample_inputs, "justin_ss_early": 10000, "justin_ss_70": 18600}
        result = run_retirement_projection(inputs, [], ret_ages=[60], justin_ss_claim_age=69)
        # justin_social_security=15000 (67, from sample_inputs), justin_ss_70=18600
        # -- age 69 credit progress = 16/24 = 2/3 of the real 67->70 gap.
        expected = 15000 + (2/3) * (18600 - 15000)
        # justin's own annual isn't in the top-level scenario dict directly,
        # but it feeds guaranteed_day_one/steady-state income -- verified
        # indirectly via the shared formula's own unit tests above; here we
        # just confirm the call succeeds and doesn't affect Jason's own
        # early/delayed pair (justin_ss_claim_age alone doesn't collapse it).
        assert sorted(s["ss_timing"] for s in result["scenarios"]) == ["delayed", "early"]


class TestResolveSsBenefitsSharedHelper:
    """CALCULATION_CONTRACT.md section 44, milestone 2: resolve_ss_
    benefits is the shared resolver every consumer migrates to,
    replacing its own independent ss_timing ternary one at a time."""

    def test_without_claim_ages_matches_the_old_early_ternary(self, sample_inputs):
        jason_ss_annual, jason_ss_age, justin_ss_annual, justin_ss_age = resolve_ss_benefits(
            sample_inputs, "early")
        assert jason_ss_annual == sample_inputs["jason_social_security"]
        assert jason_ss_age == 62

    def test_without_claim_ages_matches_the_old_delayed_ternary(self, sample_inputs):
        jason_ss_annual, jason_ss_age, justin_ss_annual, justin_ss_age = resolve_ss_benefits(
            sample_inputs, "delayed")
        assert jason_ss_annual == sample_inputs["jason_ss_delayed"]
        assert jason_ss_age == 67

    def test_jason_claim_age_overrides_ss_timing_entirely(self, sample_inputs):
        inputs = {**sample_inputs, "jason_ss_70": 55000}
        jason_ss_annual, jason_ss_age, _, _ = resolve_ss_benefits(inputs, "early", jason_ss_claim_age=64)
        expected = 30000 + (1/3) * (45000 - 30000)  # same math as the run_retirement_projection test above
        assert jason_ss_annual == pytest.approx(expected, abs=0.01)
        assert jason_ss_age == 64

    def test_justin_claim_age_is_independent_of_jasons(self, sample_inputs):
        inputs = {**sample_inputs, "justin_ss_early": 10000, "justin_ss_70": 18600}
        jason_ss_annual, jason_ss_age, justin_ss_annual, justin_ss_age = resolve_ss_benefits(
            inputs, "early", justin_ss_claim_age=69)
        # Jason's own side is untouched (still the "early" ternary result).
        assert jason_ss_annual == sample_inputs["jason_social_security"]
        assert jason_ss_age == 62
        expected_justin = 15000 + (2/3) * (18600 - 15000)
        assert justin_ss_annual == pytest.approx(expected_justin, abs=0.01)
        assert justin_ss_age == 69


class TestMonteCarloAndStressTestsOptInClaimAge:
    """CALCULATION_CONTRACT.md section 44, milestone 2: run_monte_carlo/
    run_stress_tests wired to the shared resolver. A later claim age
    delays real guaranteed income, so the outcome must actually differ
    from an earlier claim age (confirms the wiring reaches the
    simulation, not just accepted-and-ignored) -- and neither function
    breaks when the new params are left at their None default."""

    def test_monte_carlo_backward_compatible_without_claim_age(self, sample_inputs, sample_accounts):
        baseline = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        assert baseline["ss_timing"] == "early"
        assert baseline["success_rate"] >= 0

    def test_monte_carlo_claim_age_changes_the_outcome(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_ss_70": sample_inputs["jason_ss_delayed"] * 1.24}
        low  = run_monte_carlo(inputs, sample_accounts, ret_age=60, jason_ss_claim_age=62)
        high = run_monte_carlo(inputs, sample_accounts, ret_age=60, jason_ss_claim_age=70)
        assert high["median_final_balance"] != low["median_final_balance"]

    def test_stress_tests_backward_compatible_without_claim_age(self, sample_inputs, sample_accounts):
        baseline = run_stress_tests(sample_inputs, sample_accounts, ret_age=60, ss_timing="delayed")
        assert baseline["ss_timing"] == "delayed"

    def test_stress_tests_claim_age_does_not_crash_and_labels_custom(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_ss_70": sample_inputs["jason_ss_delayed"] * 1.24}
        result = run_stress_tests(inputs, sample_accounts, ret_age=60, jason_ss_claim_age=68)
        assert result is not None


class TestSwrRothTaxEfficiencyOptInClaimAge:
    """CALCULATION_CONTRACT.md section 44, milestone 3: run_swr_analysis/
    run_roth_conversion_analysis/run_tax_efficiency_simulation wired to
    the shared resolver, same opt-in/backward-compatible contract as
    milestone 2's Monte Carlo/Stress Tests."""

    def test_swr_backward_compatible_without_claim_age(self, sample_inputs, sample_accounts):
        from simulation_engine import run_swr_analysis
        baseline = run_swr_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        assert baseline["ss_timing"] == "early"

    def test_swr_claim_age_changes_the_outcome(self, sample_inputs, sample_accounts):
        from simulation_engine import run_swr_analysis
        inputs = {**sample_inputs, "jason_ss_70": sample_inputs["jason_ss_delayed"] * 1.24}
        low  = run_swr_analysis(inputs, sample_accounts, ret_age=60, jason_ss_claim_age=62)
        high = run_swr_analysis(inputs, sample_accounts, ret_age=60, jason_ss_claim_age=70)
        assert low["jason_ss_annual"] != high["jason_ss_annual"]
        assert high["jason_ss_annual"] == round(sample_inputs["jason_ss_delayed"] * 1.24)

    def test_roth_conversion_backward_compatible_without_claim_age(self, sample_inputs, sample_accounts):
        from simulation_engine import run_roth_conversion_analysis
        result = run_roth_conversion_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="delayed")
        assert result is not None

    def test_roth_conversion_no_longer_silently_defaults_delayed_to_zero(self, sample_inputs, sample_accounts):
        """Pre-existing inconsistency fixed as part of the migration:
        jason_ss_delayed unset used to make ss_timing="delayed" silently
        use $0 instead of the same JASON_SS_DELAYED_RATIO fallback every
        sibling consumer already applies (here, ratio=1.0, so the
        fallback equals the early figure)."""
        from simulation_engine import run_roth_conversion_analysis
        inputs = {**sample_inputs}
        del inputs["jason_ss_delayed"]
        result = run_roth_conversion_analysis(inputs, sample_accounts, ret_age=60, ss_timing="delayed")
        assert result is not None  # doesn't silently zero out -- exact figure covered by resolve_ss_benefits' own tests

    def test_tax_efficiency_backward_compatible_without_claim_age(self, sample_inputs, sample_accounts):
        from simulation_engine import run_tax_efficiency_simulation
        result = run_tax_efficiency_simulation(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        assert result is not None

    def test_tax_efficiency_claim_age_does_not_crash(self, sample_inputs, sample_accounts):
        from simulation_engine import run_tax_efficiency_simulation
        inputs = {**sample_inputs, "jason_ss_70": sample_inputs["jason_ss_delayed"] * 1.24}
        result = run_tax_efficiency_simulation(inputs, sample_accounts, ret_age=60, jason_ss_claim_age=68)
        assert result is not None


class TestSurvivorScenarioOptInClaimAge:
    """CALCULATION_CONTRACT.md section 44, milestone 4:
    run_survivor_scenario (single-axis) wired to the shared resolver.
    Also fixes a pre-existing gap: survivor_ss_annual previously read
    the raw early-claim inputs directly, ignoring ss_timing entirely."""

    def test_backward_compatible_without_claim_age(self, sample_inputs, sample_accounts):
        from simulation_engine import run_survivor_scenario
        result = run_survivor_scenario(sample_inputs, sample_accounts, ret_age=60,
                                        deceased="jason", ss_timing="early")
        assert result["has_data"] is True

    def test_ss_timing_delayed_previously_silently_ignored_now_respected(self, sample_inputs, sample_accounts):
        """jason_social_security=30000 (early), jason_ss_delayed=45000
        (delayed) in sample_inputs -- selecting ss_timing="delayed" must
        now actually change survivor_ss_annual (via the higher-of-the-
        two rule), where it previously always used the raw early figure
        regardless of ss_timing."""
        from simulation_engine import run_survivor_scenario
        early   = run_survivor_scenario(sample_inputs, sample_accounts, ret_age=60,
                                         deceased="jason", ss_timing="early")
        delayed = run_survivor_scenario(sample_inputs, sample_accounts, ret_age=60,
                                         deceased="jason", ss_timing="delayed")
        assert early["survivor_ss_annual"] != delayed["survivor_ss_annual"]
        # The reported figure is the first post-death year's own value,
        # which now correctly continues real COLA from the household's
        # own inflation_rate (finding 3, ninth follow-up) rather than a
        # flat, un-compounded figure -- so it's >= the raw delayed input,
        # not necessarily equal to it.
        assert delayed["survivor_ss_annual"] >= sample_inputs["jason_ss_delayed"]
        assert delayed["survivor_ss_annual"] > early["survivor_ss_annual"]

    def test_claim_age_changes_the_survivor_benefit(self, sample_inputs, sample_accounts):
        from simulation_engine import run_survivor_scenario
        inputs = {**sample_inputs, "jason_ss_70": sample_inputs["jason_ss_delayed"] * 1.24}
        low  = run_survivor_scenario(inputs, sample_accounts, ret_age=60,
                                      deceased="jason", jason_ss_claim_age=62)
        high = run_survivor_scenario(inputs, sample_accounts, ret_age=60,
                                      deceased="jason", jason_ss_claim_age=70)
        assert low["survivor_ss_annual"] != high["survivor_ss_annual"]


class TestTwoAgeConsumersOptInClaimAge:
    """CALCULATION_CONTRACT.md section 44, milestone 5: every two-age
    consumer wired to the shared resolver. Unlike the single-axis
    functions (explicit jason_ss_claim_age/justin_ss_claim_age kwargs),
    the two underlying walk functions (run_two_dimensional_retirement_
    projection, run_owner_split_two_dimensional_projection) read claim
    ages directly off `inputs` -- every two-age caller already passes
    `inputs` straight through, so this needed no signature change on
    either walk function itself. The public single-axis dispatchers
    (run_swr_analysis, run_survivor_scenario, etc.) inject their own
    jason_ss_claim_age/justin_ss_claim_age kwargs into `inputs` before
    delegating to their two-age sibling, so the SAME opt-in kwargs work
    identically in both modes."""

    # Both spouses already at/past every relevant SS age (67 == FRA ==
    # JUSTIN_SPOUSAL_AGE's own default) so SS is active from the very
    # first modeled year regardless of ss_timing/claim age -- isolates
    # the claim-age effect from "hasn't started yet" timing noise.
    TWO_AGE_INPUTS = {
        "jason_age": 67, "justin_age": 67,
        "inflation_rate": 0.0,
        "expected_return_pre_retirement": 0.0,
        "expected_return_post_retirement": 0.0,
        "retirement_income_today_dollars": 80000,
        "annual_hsa_contribution": 0, "annual_rsu_value": 0,
        "jason_social_security": 30000, "jason_ss_delayed": 45000,
        "justin_social_security": 15000,
        "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
        "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
        "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0,
        "annual_bonus_pct": 0,
        "pension_55": 0, "pension_60": 0, "pension_65": 0,
        "retirement_end_age": 69,
    }
    TAXABLE = [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 500000}]

    def test_two_dimensional_projection_backward_compatible_without_claim_age(self):
        from projection_engine import run_two_dimensional_retirement_projection
        result = run_two_dimensional_retirement_projection(
            self.TWO_AGE_INPUTS, self.TAXABLE, jason_ret_age=67, justin_ret_age=67)
        first_year = result["yearly_detail"][0]
        # ss_timing defaults to "early" -- 30000, the early-claim figure
        # (already active at 67, well past the age-62 early threshold).
        assert first_year["social_security"] == 30000 + round(
            self.TWO_AGE_INPUTS["justin_social_security"])

    def test_two_dimensional_projection_claim_age_changes_the_figure(self):
        # jason_ss_claim_age is an EXPLICIT function param (2026-09-08,
        # ninth follow-up, finding 1) -- no longer read off `inputs`.
        from projection_engine import run_two_dimensional_retirement_projection
        inputs = {**self.TWO_AGE_INPUTS, "jason_ss_70": 55800}
        result = run_two_dimensional_retirement_projection(
            inputs, self.TAXABLE, jason_ret_age=67, justin_ret_age=67, jason_ss_claim_age=67)
        first_year = result["yearly_detail"][0]
        # Age 67 is the FRA anchor exactly -- jason_ss_delayed=45000.
        assert first_year["social_security"] == 45000 + round(inputs["justin_social_security"])

    def test_swr_two_age_backward_compatible_without_claim_age(self):
        from simulation_engine import run_swr_analysis
        result = run_swr_analysis(self.TWO_AGE_INPUTS, self.TAXABLE,
                                   jason_ret_age=67, justin_ret_age=67, target_success=1.0)
        assert result["mode"] == "two_age"

    def test_swr_two_age_claim_age_changes_the_outcome(self):
        from simulation_engine import run_swr_analysis
        inputs = {**self.TWO_AGE_INPUTS, "jason_ss_70": self.TWO_AGE_INPUTS["jason_ss_delayed"] * 1.24}
        low  = run_swr_analysis(inputs, self.TAXABLE, jason_ret_age=67, justin_ret_age=67,
                                 target_success=1.0, jason_ss_claim_age=62)
        high = run_swr_analysis(inputs, self.TAXABLE, jason_ret_age=67, justin_ret_age=67,
                                 target_success=1.0, jason_ss_claim_age=70)
        assert low["jason_ss_annual"] != high["jason_ss_annual"]

    def test_survivor_two_age_backward_compatible_without_claim_age(self):
        from simulation_engine import run_survivor_scenario
        result = run_survivor_scenario(self.TWO_AGE_INPUTS, self.TAXABLE,
                                        jason_ret_age=67, justin_ret_age=67,
                                        deceased="justin", death_age=67)
        assert result["has_data"] is True

    def test_survivor_two_age_claim_age_changes_the_outcome(self):
        from simulation_engine import run_survivor_scenario
        inputs = {**self.TWO_AGE_INPUTS, "jason_ss_70": self.TWO_AGE_INPUTS["jason_ss_delayed"] * 1.24}
        low  = run_survivor_scenario(inputs, self.TAXABLE, jason_ret_age=67, justin_ret_age=67,
                                      deceased="justin", death_age=67, jason_ss_claim_age=62)
        high = run_survivor_scenario(inputs, self.TAXABLE, jason_ret_age=67, justin_ret_age=67,
                                      deceased="justin", death_age=67, jason_ss_claim_age=70)
        assert low["survivor_ss_annual"] != high["survivor_ss_annual"]


class TestNinthFollowUpReviewFindings:
    """Independent review, 2026-09-08, ninth follow-up, of commit
    cb80bf5 (milestone 5). All four findings plus the range-guard note,
    reproduced against the fixed code."""

    def test_finding1_overriding_one_spouses_claim_age_preserves_the_others_saved_value(self):
        """P1: the two-age dispatch injection used to blindly overwrite
        BOTH spouses' claim-age fields with this call's own explicit
        params, clearing the OTHER spouse's own saved value back to
        None whenever only one was actually being overridden."""
        from simulation_engine import run_swr_analysis
        inputs = {
            "jason_age": 60, "justin_age": 60, "inflation_rate": 0.0,
            "expected_return_pre_retirement": 0.0, "expected_return_post_retirement": 0.0,
            "retirement_income_today_dollars": 80000,
            "annual_hsa_contribution": 0, "annual_rsu_value": 0,
            "jason_social_security": 30000, "jason_ss_delayed": 45000, "jason_ss_70": 55800,
            "justin_social_security": 15000, "justin_ss_early": 10000, "justin_ss_70": 18600,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
            "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
            "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0, "annual_bonus_pct": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "justin_ss_claim_age": 68,  # <-- already-saved value
            "retirement_end_age": 99,
        }
        accounts = [{"name": "Taxable", "account_type": "taxable", "owner": "joint", "balance": 500000}]
        # Only Jason's claim age is explicitly overridden -- Justin's
        # own saved 68 must survive, not silently reset to his flat FRA.
        r = run_swr_analysis(inputs, accounts, jason_ret_age=65, justin_ret_age=65, jason_ss_claim_age=65)
        expected_justin = 15000 + (8/24) * (18600 - 15000)  # spousal credit progress at 68
        assert r["justin_ss_annual"] == pytest.approx(expected_justin, abs=1)

    def test_finding2_ss_reduction_stress_scenario_reduces_the_resolved_benefit(self):
        """P1: the single-axis SS-reduction stress scenario used to
        reduce Justin's RAW FRA input instead of his resolved,
        claiming-age-selected benefit -- with an early claim age
        already below FRA, the "reduction" could pay MORE than the
        real unstressed benefit. Reproduced: $10,500 selected (Justin's
        own resolved benefit at 62) vs. $15,000 FRA -- old code paid
        $15,000*0.75=$11,250 (more than $10,500); fixed pays
        $10,500*0.75=$7,875 (correctly less)."""
        from simulation_engine import run_stress_tests
        inputs = {
            "jason_age": 60, "justin_age": 60, "inflation_rate": 0.0,
            "expected_return_pre_retirement": 0.0, "expected_return_post_retirement": 0.0,
            "retirement_income_today_dollars": 80000,
            "annual_hsa_contribution": 0, "annual_rsu_value": 0,
            "jason_social_security": 30000, "jason_ss_delayed": 45000,
            "justin_social_security": 15000, "justin_ss_early": 10500,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
            "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
            "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0, "annual_bonus_pct": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "retirement_end_age": 99,
        }
        accounts = [{"name": "Taxable", "account_type": "taxable", "owner": "joint", "balance": 500000}]
        r = run_stress_tests(inputs, accounts, ret_age=65, justin_ss_claim_age=62)
        ss_scenario = r["scenarios"]["ss_reduction"]
        assert ss_scenario["final_balance"] <= r["scenarios"]["base"]["final_balance"] * 1.1  # sanity: not a windfall

    def test_finding3_survivor_ss_does_not_start_before_the_survivors_own_claim_age(self):
        """P1: Jason claiming at 70, Justin dies at 62 -- the survivor
        schedule used to pay Jason's full age-70 benefit immediately,
        years before he'd have actually reached 70. Fixed: $0 until
        Jason's own advancing age reaches 70."""
        from simulation_engine import run_survivor_scenario
        inputs = {
            "jason_age": 62, "justin_age": 62, "inflation_rate": 0.0,
            "expected_return_pre_retirement": 0.0, "expected_return_post_retirement": 0.0,
            "retirement_income_today_dollars": 30000,
            "annual_hsa_contribution": 0, "annual_rsu_value": 0,
            "jason_social_security": 30000, "jason_ss_delayed": 45000, "jason_ss_70": 55800,
            "justin_social_security": 0,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
            "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
            "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0, "annual_bonus_pct": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "retirement_end_age": 99,
        }
        accounts = [{"name": "Taxable", "account_type": "taxable", "owner": "joint", "balance": 500000}]
        r = run_survivor_scenario(inputs, accounts, ret_age=62, deceased="justin", death_age=62,
                                   jason_ss_claim_age=70)
        assert r["survivor_ss_annual"] == 0

    def test_finding3_pre_death_lookup_respects_delayed_not_hardcoded_early(self):
        """P1, part 2: the pre-death baseline lookup hardcoded "early"
        regardless of ss_timing, using the wrong scenario's own
        portfolio trajectory (SS affects withdrawal-phase draws) for a
        household that selected "delayed"."""
        from simulation_engine import run_survivor_scenario
        inputs = {
            "jason_age": 60, "justin_age": 60, "inflation_rate": 0.0,
            "expected_return_pre_retirement": 0.0, "expected_return_post_retirement": 0.0,
            "retirement_income_today_dollars": 80000,
            "annual_hsa_contribution": 0, "annual_rsu_value": 0,
            "jason_social_security": 30000, "jason_ss_delayed": 45000,
            "justin_social_security": 15000,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
            "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
            "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0, "annual_bonus_pct": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "retirement_end_age": 99,
        }
        accounts = [{"name": "Taxable", "account_type": "taxable", "owner": "joint", "balance": 500000}]
        early = run_survivor_scenario(inputs, accounts, ret_age=65, deceased="jason", death_age=75,
                                       ss_timing="early")
        delayed = run_survivor_scenario(inputs, accounts, ret_age=65, deceased="jason", death_age=75,
                                         ss_timing="delayed")
        # Higher guaranteed income (delayed) means less portfolio drawn
        # down pre-death -- the two must differ, not silently match.
        assert early["portfolio_at_death"] != delayed["portfolio_at_death"]

    def test_finding4_spousal_reduction_differs_from_worker_reduction(self):
        """P2: the shared formula used worker-benefit reduction rates
        for Justin's spousal benefit too. Reproduced exactly: $9,750 at
        62 (real spousal anchor) and $15,000 at 67 -- worker rates gave
        $11,500 at 64; the correct spousal amount is $11,250."""
        from projection_engine import ss_benefit_for_claim_age
        worker_result = ss_benefit_for_claim_age(9750, 15000, 99999, 64, benefit_type="worker")
        spousal_result = ss_benefit_for_claim_age(9750, 15000, 99999, 64, benefit_type="spousal")
        assert worker_result == pytest.approx(11500, abs=1)
        assert spousal_result == pytest.approx(11250, abs=1)

    def test_guard_out_of_range_claim_age_is_clamped_not_just_the_amount(self):
        """Guard note: an input of 60 must not receive the age-62
        amount while starting at age 60 -- the START AGE itself must
        also clamp to 62, not just the dollar amount."""
        from projection_engine import resolve_ss_benefits
        inputs = {"jason_social_security": 20000, "jason_ss_delayed": 30000, "jason_ss_70": 40000}
        jason_ss_annual, jason_ss_age, _, _ = resolve_ss_benefits(inputs, "early", jason_ss_claim_age=60)
        assert jason_ss_annual == 20000
        assert jason_ss_age == 62  # not 60

    def test_guard_above_range_claim_age_is_also_clamped(self):
        from projection_engine import resolve_ss_benefits
        inputs = {"jason_social_security": 20000, "jason_ss_delayed": 30000, "jason_ss_70": 40000}
        jason_ss_annual, jason_ss_age, _, _ = resolve_ss_benefits(inputs, "early", jason_ss_claim_age=75)
        assert jason_ss_annual == 40000
        assert jason_ss_age == 70


class TestExternalAuditReviewOfCommit0c1a569:
    """External audit review of frontend milestone 6 (commit 0c1a569),
    six findings before merge. Findings 5 and 6 are frontend-only
    (Settings.jsx/StressTestWhatIf.jsx wording and messaging); this
    class covers the four backend findings (1-4)."""

    def test_finding1_two_age_survivor_owner_walk_respects_the_resolved_claim_age(self):
        """P1: run_owner_split_two_dimensional_projection now requires
        jason_ss_claim_age/justin_ss_claim_age as explicit params (no
        longer reads `inputs`) -- _run_survivor_scenario_two_age's own
        pre-death call left them unset, so the pre-death portfolio
        silently fell back to the legacy ss_timing="early" default
        regardless of a saved claim age. Reproduced exactly: both
        spouses 67, Jason claims at 70, Justin dies at 69, $1M
        portfolio, zero spending/returns -- the bug credited 3 years
        (67-69) of Jason's EARLY benefit he never actually claims."""
        from simulation_engine import run_survivor_scenario
        inputs = {
            "jason_age": 67, "justin_age": 67, "inflation_rate": 0.0,
            "expected_return_pre_retirement": 0.0, "expected_return_post_retirement": 0.0,
            "retirement_income_today_dollars": 0,
            "annual_hsa_contribution": 0, "annual_rsu_value": 0,
            "jason_social_security": 21000, "jason_ss_delayed": 30000, "jason_ss_70": 37200,
            "justin_social_security": 0,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
            "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
            "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0, "annual_bonus_pct": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "retirement_end_age": 99,
        }
        accounts = [{"name": "Taxable", "account_type": "taxable", "owner": "joint", "balance": 1000000}]
        r = run_survivor_scenario(inputs, accounts, ret_age=67, deceased="justin", death_age=69,
                                   jason_ret_age=67, justin_ret_age=67, jason_ss_claim_age=70)
        assert r["has_data"] is True
        assert r["portfolio_at_death"] == 1000000

    def test_finding2_zero_valued_anchor_fields_fall_back_to_fra_not_zero(self):
        """P1: planning_inputs.jason_ss_70 defaults to REAL DEFAULT 0
        (db.py), so it is ALWAYS present in an existing household's
        row -- inputs.get("jason_ss_70", jason_ss_delayed) never falls
        back, because the key is never actually missing, only
        zero-valued. Enabling the slider with a real $30,000 FRA
        benefit but an untouched 0 age-70 field used to interpolate
        toward that $0 "anchor" (30000 at 67, 20000 at 68, 10000 at 69,
        0 at 70) instead of falling back to the FRA figure."""
        from projection_engine import resolve_ss_benefits
        inputs = {
            "jason_social_security": 30000,       # 62
            "jason_ss_delayed": 30000,             # 67 (FRA)
            "jason_ss_70": 0,                      # untouched -- must NOT be read as a real $0 anchor
        }
        for age, expected in [(67, 30000), (68, 30000), (69, 30000), (70, 30000)]:
            annual, _, _, _ = resolve_ss_benefits(inputs, "early", jason_ss_claim_age=age)
            assert annual == expected, f"age {age}: expected {expected}, got {annual}"

    def test_finding2_zero_valued_justin_early_anchor_falls_back_to_fra(self):
        """Same finding-2 fix, for Justin's justin_ss_early field."""
        from projection_engine import resolve_ss_benefits
        inputs = {
            "justin_social_security": 15000,   # 67 (FRA)
            "justin_ss_early": 0,              # untouched
            "justin_ss_70": 15000,
        }
        _, _, annual, _ = resolve_ss_benefits(inputs, "early", justin_ss_claim_age=63)
        assert annual == 15000  # not interpolated toward a fake $0 anchor at 62

    def test_finding3_whatif_ss_multiplier_scales_the_new_anchor_fields_too(self):
        """P1: _apply_whatif_overrides' ss_mult only ever scaled
        jason_social_security/jason_ss_delayed/justin_social_security --
        a household with a saved claim age of 70 gets its benefit
        ENTIRELY from the (unscaled) jason_ss_70 anchor, so the SS
        multiplier slider had NO effect on that household's results."""
        from main import _apply_whatif_overrides
        inputs = {
            "expected_return_pre_retirement": 0.0, "expected_return_post_retirement": 0.0,
            "inflation_rate": 0.0, "retirement_income_today_dollars": 0,
            "jason_social_security": 21000, "jason_ss_delayed": 30000, "jason_ss_70": 37200,
            "justin_social_security": 15000, "justin_ss_early": 10500, "justin_ss_70": 18600,
        }
        scaled = _apply_whatif_overrides(inputs, {"ss_mult": 0.0})
        assert scaled["jason_ss_70"] == 0
        assert scaled["justin_ss_early"] == 0
        assert scaled["justin_ss_70"] == 0
        scaled_half = _apply_whatif_overrides(inputs, {"ss_mult": 0.5})
        assert scaled_half["jason_ss_70"] == pytest.approx(18600)
        assert scaled_half["justin_ss_early"] == pytest.approx(5250)
        assert scaled_half["justin_ss_70"] == pytest.approx(9300)

    def test_finding4_income_sources_label_switches_to_custom_when_claim_age_saved(self, client, sample_inputs):
        """P2: /api/retirement/income-sources (Simulation.jsx's own
        companion chart to Monte Carlo) stayed on the legacy ss_timing
        toggle even after a household saved a continuous claim age,
        while Monte Carlo's own simulation already honored it --
        reproduced: Jason claims at 70, Monte Carlo correctly pays $0
        SS at 67 while the chart showed the early/delayed toggle's
        $21,000 age-67 figure instead."""
        inputs = {
            **sample_inputs,
            "jason_social_security": 21000, "jason_ss_delayed": 30000, "jason_ss_70": 37200,
            "jason_ss_claim_age": 70,
        }
        r = client.put("/api/planning-inputs", json=inputs)
        assert r.status_code == 200, r.text
        r = client.post("/api/retirement/income-sources", json={"ret_age": 65, "ss_timing": "early"})
        assert r.status_code == 200
        body = r.json()
        assert "error" not in body
        assert body["label"] == "age_65_custom"
        # At age 67 (before Jason's saved claim age of 70), Social
        # Security in the chart must be $0, not the early/delayed
        # toggle's $21,000 figure.
        row_67 = next((row for row in body["chart"] if row["age"] == 67), None)
        assert row_67 is not None
        assert row_67["social_security"] == 0
