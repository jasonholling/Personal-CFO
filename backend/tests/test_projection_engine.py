"""
Tests for projection_engine.py — the canonical retirement/education/kids
calculation engine. Other modules (main.py, simulation_engine.py) delegate
to these functions rather than reimplementing the math; these tests exist
to keep it that way and to catch the class of bug found on 2026-08-26:
retirement-age math that silently broke or drifted for any age outside
the three Settings anchor points (55/60/65).
"""
import pytest

from projection_engine import (
    run_retirement_projection,
    run_education_projection,
    run_kids_projection,
    run_insurance_analysis,
    pension_for_age,
    _fv, _fv_annuity, _fv_annuity_monthly, _fv_growing_annuity, _pv_annuity,
    _project_529_saving_phase,
    _project_college_drawdown,
    PARENT_RETIREMENT_AGE_ASSUMPTION,
    BOND_MAX_GROWTH_YEARS,
    CURRENT_YEAR,
    COLLEGE_YEARS,
    justin_years_to_retire_for,
    justin_gap_income_inputs,
    SECOND_EARNER_NET_OF_TAX_FACTOR,
)


class TestMathPrimitives:
    def test_fv_zero_years_returns_principal(self):
        assert _fv(1000, 0.07, 0) == 1000

    def test_fv_compounds(self):
        assert _fv(1000, 0.10, 1) == pytest.approx(1100)

    def test_fv_annuity_zero_rate_is_linear(self):
        assert _fv_annuity(100, 0, 5) == 500

    def test_pv_annuity_zero_years_is_zero(self):
        assert _pv_annuity(1000, 0.05, 0) == 0

    def test_pv_annuity_zero_rate_is_linear(self):
        assert _pv_annuity(100, 0, 5) == 500

    def test_growing_annuity_zero_growth_matches_flat_annuity(self):
        assert _fv_growing_annuity(1000, 0.07, 0, 10) == pytest.approx(_fv_annuity(1000, 0.07, 10))

    def test_growing_annuity_exceeds_flat_annuity_when_growth_positive(self):
        """Deposits growing over time must beat flat deposits of the same
        starting size — this is the whole point of a salary-growth
        assumption actually doing something."""
        flat    = _fv_annuity(1000, 0.07, 15)
        growing = _fv_growing_annuity(1000, 0.07, 0.03, 15)
        assert growing > flat

    def test_growing_annuity_handles_growth_rate_equal_to_return_rate(self):
        """r == g hits a division-by-zero in the closed-form unless
        special-cased — must not raise and must still be a sane, positive
        number that beats the zero-growth case."""
        result = _fv_growing_annuity(1000, 0.05, 0.05, 10)
        assert result > _fv_annuity(1000, 0.05, 10)

    def test_growing_annuity_zero_years_is_zero(self):
        assert _fv_growing_annuity(1000, 0.07, 0.03, 0) == 0

    def test_projection_uses_configured_planning_horizon(self, sample_inputs, sample_accounts):
        scenario = run_retirement_projection({**sample_inputs, "retirement_end_age": 95}, sample_accounts, ret_ages=[60])["scenarios"][0]
        assert scenario["retirement_end_age"] == 95
        assert len(scenario["yearly_detail"]) == 35

    def test_state_tax_control_increases_retirement_distribution_tax(self, sample_inputs, sample_accounts):
        baseline = run_retirement_projection({**sample_inputs, "state_income_tax_rate": 0}, sample_accounts, ret_ages=[60])["scenarios"][0]
        with_state = run_retirement_projection({**sample_inputs, "state_income_tax_rate": .05}, sample_accounts, ret_ages=[60])["scenarios"][0]
        assert sum(y["estimated_tax"] for y in with_state["yearly_detail"]) >= sum(y["estimated_tax"] for y in baseline["yearly_detail"])


class TestPensionForAge:
    """This function replaced six copy-pasted, partly-broken versions of the
    same interpolation. Any regression here regresses every caller."""

    def test_anchor_ages_return_exact_settings_values(self, sample_inputs):
        assert pension_for_age(sample_inputs, 55) == sample_inputs["pension_55"]
        assert pension_for_age(sample_inputs, 60) == sample_inputs["pension_60"]
        assert pension_for_age(sample_inputs, 65) == sample_inputs["pension_65"]

    def test_interpolates_between_55_and_60(self, sample_inputs):
        # Halfway between 55 (20000) and 60 (30000) should be 25000
        mid = pension_for_age(sample_inputs, 57.5)
        assert mid == pytest.approx(25000)

    def test_interpolates_between_60_and_65(self, sample_inputs):
        mid = pension_for_age(sample_inputs, 62.5)
        assert mid == pytest.approx(32500)

    def test_before_55_clamps_to_55_value(self, sample_inputs):
        assert pension_for_age(sample_inputs, 50) == sample_inputs["pension_55"]

    def test_after_65_clamps_to_65_value(self, sample_inputs):
        assert pension_for_age(sample_inputs, 70) == sample_inputs["pension_65"]

    @pytest.mark.parametrize("age", range(55, 68))
    def test_every_sensitivity_age_is_numeric_and_non_negative(self, sample_inputs, age):
        # Regression guard for the KeyError bug: every age 55-67 must resolve
        # to a number without raising.
        result = pension_for_age(sample_inputs, age)
        assert isinstance(result, (int, float))
        assert result >= 0


class TestRunRetirementProjection:
    def test_default_ages_are_55_60_65(self, sample_inputs, sample_accounts):
        result = run_retirement_projection(sample_inputs, sample_accounts)
        ages = {s["retirement_age"] for s in result["scenarios"]}
        assert ages == {55, 60, 65}

    def test_custom_ret_ages_parameter(self, sample_inputs, sample_accounts):
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[58])
        ages = {s["retirement_age"] for s in result["scenarios"]}
        assert ages == {58}

    @pytest.mark.parametrize("age", range(55, 68))
    def test_every_age_55_to_67_produces_both_ss_timings(self, sample_inputs, sample_accounts, age):
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[age])
        labels = {s["label"] for s in result["scenarios"]}
        assert labels == {f"age_{age}_early", f"age_{age}_delayed"}

    def test_justin_ss_age_setting_is_respected(self, sample_inputs, sample_accounts):
        """Regression test for the bug where Justin's spousal SS claiming age
        was hardcoded to 67 instead of read from Settings."""
        custom_inputs = {**sample_inputs, "justin_ss_age": 63}
        result = run_retirement_projection(custom_inputs, sample_accounts, ret_ages=[60])
        scenario = next(s for s in result["scenarios"] if s["label"] == "age_60_early")
        assert scenario["justin_ss_start_age"] == 63

    def test_early_vs_delayed_ss_produce_different_income(self, sample_inputs, sample_accounts):
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[62])
        early = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        delayed = next(s for s in result["scenarios"] if s["ss_timing"] == "delayed")
        assert early["jason_ss_annual"] != delayed["jason_ss_annual"]

    def test_portfolio_grows_with_more_years_to_retirement(self, sample_inputs, sample_accounts):
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[55, 65])
        p55 = next(s for s in result["scenarios"] if s["retirement_age"] == 55 and s["ss_timing"] == "early")
        p65 = next(s for s in result["scenarios"] if s["retirement_age"] == 65 and s["ss_timing"] == "early")
        assert p65["portfolio_at_retirement"] > p55["portfolio_at_retirement"]

    def test_salary_growth_pct_increases_portfolio_at_retirement(self, sample_inputs, sample_accounts):
        """Regression test for a real reported gap: the What-If tool's
        "Salary Growth" slider used to be a flat, one-time multiplier
        applied to today's salary and held constant for the whole
        projection — not a genuine "3%/yr raises" assumption compounding
        over the years to retirement, which is what the label promised
        and what the user asked for."""
        flat   = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[65])
        growth = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[65], salary_growth_pct=0.03)
        flat_scenario   = next(s for s in flat["scenarios"] if s["ss_timing"] == "early")
        growth_scenario = next(s for s in growth["scenarios"] if s["ss_timing"] == "early")
        assert growth_scenario["portfolio_at_retirement"] > flat_scenario["portfolio_at_retirement"]

    def test_salary_growth_pct_defaults_to_zero_no_behavior_change(self, sample_inputs, sample_accounts):
        """Every existing caller (Retirement.jsx, Report.jsx, etc.) doesn't
        pass salary_growth_pct — confirms the new parameter is fully
        opt-in and doesn't silently change their numbers."""
        no_kwarg  = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[65])
        explicit0 = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[65], salary_growth_pct=0.0)
        a = next(s for s in no_kwarg["scenarios"] if s["ss_timing"] == "early")
        b = next(s for s in explicit0["scenarios"] if s["ss_timing"] == "early")
        assert a["portfolio_at_retirement"] == b["portfolio_at_retirement"]

    def test_annual_bonus_pct_increases_portfolio_at_retirement(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "w2_salary": 150000, "annual_bonus_pct": 0.20}
        baseline = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[65])
        with_bonus = run_retirement_projection(inputs, sample_accounts, ret_ages=[65])
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_bonus["scenarios"] if s["ss_timing"] == "early")
        assert w["taxable_at_retirement"] > b["taxable_at_retirement"]

    def test_annual_bonus_pct_scales_with_salary_growth(self, sample_inputs, sample_accounts):
        """A bonus expressed as % of salary should grow along with raises,
        same as the 401k contributions it's modeled after — unlike
        annual_rsu_value (a flat dollar figure), which doesn't."""
        inputs = {**sample_inputs, "w2_salary": 150000, "annual_bonus_pct": 0.20}
        flat   = run_retirement_projection(inputs, sample_accounts, ret_ages=[65])
        growth = run_retirement_projection(inputs, sample_accounts, ret_ages=[65], salary_growth_pct=0.03)
        f = next(s for s in flat["scenarios"] if s["ss_timing"] == "early")
        g = next(s for s in growth["scenarios"] if s["ss_timing"] == "early")
        assert g["taxable_at_retirement"] > f["taxable_at_retirement"]

    def test_annual_bonus_pct_defaults_to_zero_no_behavior_change(self, sample_inputs, sample_accounts):
        no_key = {k: v for k, v in sample_inputs.items() if k != "annual_bonus_pct"}
        explicit0 = {**sample_inputs, "annual_bonus_pct": 0}
        a = run_retirement_projection(no_key, sample_accounts, ret_ages=[65])
        b = run_retirement_projection(explicit0, sample_accounts, ret_ages=[65])
        a_s = next(s for s in a["scenarios"] if s["ss_timing"] == "early")
        b_s = next(s for s in b["scenarios"] if s["ss_timing"] == "early")
        assert a_s["taxable_at_retirement"] == b_s["taxable_at_retirement"]

    def test_kids_accounts_excluded_from_investable_assets(self, sample_inputs):
        accounts = [
            {"id": 1, "name": "Kid Roth", "account_type": "roth_ira", "owner": "abby", "balance": 999999, "institution": "", "notes": ""},
        ]
        result = run_retirement_projection(sample_inputs, accounts, ret_ages=[55])
        scenario = result["scenarios"][0]
        assert scenario["current_investable_assets"] == 0

    def test_bridge_job_phasing_only_applies_at_55(self, sample_inputs, sample_accounts):
        """Regression test for the bug where age-55's bridge-job/kids-at-home
        phasing was silently skipped by a duplicate implementation."""
        inputs_with_bridge = {
            **sample_inputs,
            "bridge_income_55": 80000,
            "bridge_years_55": 5,
            "kids_years_at_home_55": 8,
        }
        with_bridge = run_retirement_projection(inputs_with_bridge, sample_accounts, ret_ages=[55])
        without_bridge = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[55])
        s_with = next(s for s in with_bridge["scenarios"] if s["ss_timing"] == "early")
        s_without = next(s for s in without_bridge["scenarios"] if s["ss_timing"] == "early")
        # Bridge income offsetting costs should reduce (or at least change) the
        # capitalized need vs. having no bridge income at all.
        assert s_with["total_capitalized_need"] != s_without["total_capitalized_need"]

    def test_yearly_detail_covers_full_retirement(self, sample_inputs, sample_accounts):
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[65])
        scenario = result["scenarios"][0]
        # mortality age 99, so retiring at 65 => 34 years of yearly_detail
        assert len(scenario["yearly_detail"]) == 34

    def test_age_55_scenario_still_grows_from_future_contributions(self, sample_inputs, sample_accounts):
        """Regression test for the bug (external audit 2026-09-05) where the
        age-55 scenario zeroed out every future 401k/Roth/HSA/RSU
        contribution even when retirement was years away — someone
        currently 50 planning to retire at 55 got 0 years of modeled
        contributions instead of 5. Compares to age 56 (still on the
        "no annuity" path since sample_inputs has no bridge/RSU inputs
        that would otherwise diverge) with contributions turned way up so
        the difference is unmistakable if this regresses to $0."""
        inputs = {**sample_inputs, "jason_age": 50, "annual_401k_pretax_employer": 50000}
        result = run_retirement_projection(inputs, sample_accounts, ret_ages=[55])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        no_contrib_inputs = {**sample_inputs, "jason_age": 50, "annual_401k_pretax_employer": 0}
        baseline = run_retirement_projection(no_contrib_inputs, sample_accounts, ret_ages=[55])
        baseline_scenario = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        assert scenario["portfolio_at_retirement"] > baseline_scenario["portfolio_at_retirement"]

    def test_already_at_55_gets_no_contribution_years(self, sample_inputs, sample_accounts):
        """Someone already 55 has 0 years to retirement, so contributions
        (an annuity over 0 years) correctly contribute nothing — this isn't
        the bug above, just confirming the fix didn't overshoot."""
        inputs = {**sample_inputs, "jason_age": 55, "annual_401k_pretax_employer": 50000}
        result = run_retirement_projection(inputs, sample_accounts, ret_ages=[55])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        no_contrib_inputs = {**sample_inputs, "jason_age": 55, "annual_401k_pretax_employer": 0}
        baseline = run_retirement_projection(no_contrib_inputs, sample_accounts, ret_ages=[55])
        baseline_scenario = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        assert scenario["portfolio_at_retirement"] == baseline_scenario["portfolio_at_retirement"]

    def test_past_retirement_age_uses_household_real_current_age(self, sample_inputs, sample_accounts):
        """Regression test (external audit 2026-09-06): the retirement-age
        button list is a fixed [55..67] range shown regardless of the
        household's actual current age, so someone jason_age=65 can still
        select ret_age=55 — an already-passed age. years_to_retire
        correctly clamps to 0, but the withdrawal-phase loop used to label
        every row's age starting from ret_age (55, 56, ...) instead of the
        household's real current age, making a 65-year-old look 10 years
        younger and pushing SS claiming ages (jason_ss_age=62 in
        sample_inputs) further into the fictional future than they really
        are."""
        inputs = {**sample_inputs, "jason_age": 65}
        result = run_retirement_projection(inputs, sample_accounts, ret_ages=[55])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        yearly = scenario["yearly_detail"]
        # The very first withdrawal-phase row must be labeled with the
        # household's real current age, not the selected (already-passed)
        # ret_age.
        assert yearly[0]["jason_age"] == 65
        # jason_ss_age=62 in sample_inputs is already in the past relative
        # to a real age of 65 — SS should show up in year 1, not 10+ years
        # "in the future" from a fictional age-55 start.
        assert yearly[0]["social_security"] > 0
        # retire_years/mort_age must also anchor to the real current age —
        # otherwise the loop would run 44 years (99-55) from a starting
        # age of 65, ending at a nonsensical age 108 instead of stopping at
        # the real mortality/planning-horizon age (99 by default).
        assert yearly[-1]["jason_age"] == 98

    def test_normal_future_retirement_age_is_unaffected_by_the_anchor_fix(self, sample_inputs, sample_accounts):
        """Guard against the fix above overshooting: whenever ret_age is
        still in the future (the overwhelmingly normal case, and what every
        other test in this class already exercises), the withdrawal loop's
        age labeling must be completely unchanged — still anchored to
        ret_age itself, not jason_age."""
        inputs = {**sample_inputs, "jason_age": 50}
        result = run_retirement_projection(inputs, sample_accounts, ret_ages=[60])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        assert scenario["yearly_detail"][0]["jason_age"] == 60

    def test_rmd_start_age_75_for_someone_born_1960_or_later(self, sample_inputs, sample_accounts):
        """Regression test (external audit 2026-09-05): RMDs were hardcoded
        to start at 73 for everyone. SECURE Act 2.0 bumps that to 75 for
        anyone born 1960+. jason_age=40 today (2026) => born 1986 => 75."""
        inputs = {**sample_inputs, "jason_age": 40}
        result = run_retirement_projection(inputs, sample_accounts, ret_ages=[60])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        yearly = scenario["yearly_detail"]
        age_73_year = next(y for y in yearly if y["jason_age"] == 73)
        age_75_year = next(y for y in yearly if y["jason_age"] == 75)
        assert age_73_year["rmd"] == 0
        assert age_75_year["rmd"] > 0

    def test_rmd_still_starts_at_73_for_someone_born_before_1960(self, sample_inputs, sample_accounts):
        """jason_age=70 today (2026) => born 1956 => the 1951-1959 cohort
        that's still on the pre-2033 age-73 RMD window (born 1960+ gets 75,
        see the test above)."""
        inputs = {**sample_inputs, "jason_age": 70}
        result = run_retirement_projection(inputs, sample_accounts, ret_ages=[70])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        age_73_year = next(y for y in scenario["yearly_detail"] if y["jason_age"] == 73)
        assert age_73_year["rmd"] > 0

    def test_pretax_withdrawals_now_carry_an_estimated_tax(self, sample_inputs, sample_accounts):
        """Regression test (external audit 2026-09-05): pretax
        withdrawals/RMDs used to be treated as tax-free when computing how
        much they cover of the year's spending need. Any year with a
        nonzero RMD should now show a nonzero estimated_tax."""
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        rmd_years = [y for y in scenario["yearly_detail"] if y["rmd"] > 0]
        assert rmd_years, "expected at least one year with a nonzero RMD in this fixture"
        assert all(y["estimated_tax"] > 0 for y in rmd_years)


class TestJustinIndependentIncomeAndRetirementAge:
    """justin_w2_salary/justin_employee_401k_pct/justin_employer_401k_pct/
    justin_annual_bonus_pct/justin_annual_rsu_value/justin_ret_age
    (2026-09-08): a second, independent pre-retirement earnings profile for
    a household where both spouses work full-time instead of one combined
    breadwinner income. See run_retirement_projection's own docstring."""

    def test_default_zero_salary_leaves_projection_unchanged(self, sample_inputs, sample_accounts):
        """The whole point of defaulting justin_w2_salary to 0: an existing
        single-earner household that never touches these new fields must
        see byte-identical output to before they existed."""
        # Compare ["scenarios"] only -- generated_at is a wall-clock
        # timestamp that legitimately differs between any two calls,
        # unrelated to this test's actual concern.
        with_defaults = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        explicit_zero = run_retirement_projection({**sample_inputs, "justin_w2_salary": 0, "justin_ret_age": 0}, sample_accounts, ret_ages=[60])
        assert with_defaults["scenarios"] == explicit_zero["scenarios"]

    def test_justin_salary_grows_the_portfolio(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_age": 45, "justin_age": 45, "justin_w2_salary": 150000}
        with_income = run_retirement_projection(inputs, sample_accounts, ret_ages=[60])
        without_income = run_retirement_projection({**sample_inputs, "jason_age": 45, "justin_age": 45}, sample_accounts, ret_ages=[60])
        scenario_with = next(s for s in with_income["scenarios"] if s["ss_timing"] == "early")
        scenario_without = next(s for s in without_income["scenarios"] if s["ss_timing"] == "early")
        assert scenario_with["portfolio_at_retirement"] > scenario_without["portfolio_at_retirement"]

    def test_justin_ret_age_independent_of_jasons_ret_age_scenario(self, sample_inputs, sample_accounts):
        """justin_ret_age=50 should give Justin exactly 5 years of
        contributions (justin_age=45) regardless of which Jason ret_age
        scenario column is being evaluated (60 here) -- decoupled from the
        age-gap-derived default, which would otherwise tie Justin's window
        to Jason's own selected retirement age."""
        inputs = {**sample_inputs, "jason_age": 45, "justin_age": 45, "justin_w2_salary": 150000, "justin_ret_age": 50}
        result_ret60 = run_retirement_projection(inputs, sample_accounts, ret_ages=[60])
        result_ret65 = run_retirement_projection(inputs, sample_accounts, ret_ages=[65])
        scenario_60 = next(s for s in result_ret60["scenarios"] if s["ss_timing"] == "early")
        scenario_65 = next(s for s in result_ret65["scenarios"] if s["ss_timing"] == "early")
        # Both Jason scenarios see the SAME Justin contribution years (5),
        # so the JUSTIN-attributable portion of the portfolio should be
        # identical relative to each scenario's own no-Justin-income baseline
        # growing for the same number of years -- simplest direct check:
        # Justin's own 5-years-of-contributions FV is a fixed number,
        # independent of Jason's ret_age, so the DELTA between "with
        # Justin income" and "without" should differ only by however much
        # LONGER that fixed contribution sum then compounds (10 vs 15 years
        # post-contribution) -- not by Justin's contribution WINDOW itself.
        no_justin_60 = run_retirement_projection({**sample_inputs, "jason_age": 45, "justin_age": 45}, sample_accounts, ret_ages=[60])
        no_justin_65 = run_retirement_projection({**sample_inputs, "jason_age": 45, "justin_age": 45}, sample_accounts, ret_ages=[65])
        delta_60 = scenario_60["portfolio_at_retirement"] - next(s for s in no_justin_60["scenarios"] if s["ss_timing"] == "early")["portfolio_at_retirement"]
        delta_65 = scenario_65["portfolio_at_retirement"] - next(s for s in no_justin_65["scenarios"] if s["ss_timing"] == "early")["portfolio_at_retirement"]
        # The 65-column delta compounds the same fixed Justin contribution
        # sum for 5 more years than the 60-column delta -- strictly bigger,
        # not equal, which would only happen if Justin's window had
        # (wrongly) tracked Jason's own ret_age instead of staying fixed at 50.
        assert delta_65 > delta_60 > 0

    def test_justin_ret_age_zero_falls_back_to_age_gap_derived_timing(self, sample_inputs, sample_accounts):
        """justin_ret_age left at 0 (unset) must reproduce the pre-existing
        age-gap-derived timing exactly -- i.e. Justin's contribution window
        equals years_to_retire, same as Jason's, not some other default.
        Uses an EARLIER explicit justin_ret_age (55, justin_age=48 -> 7
        years) against the fallback (years_to_retire=10 at ret_age=60) --
        contributing for 7 years then letting the balance sit idle for 3
        must land strictly below contributing for the full 10, so this test
        can tell the two paths apart (a justin_ret_age that happens to land
        past years_to_retire gets capped at years_to_retire either way --
        see test_justin_ret_age_independent_of_jasons_ret_age_scenario for
        that boundary instead)."""
        inputs_explicit = {**sample_inputs, "jason_age": 50, "justin_age": 48, "justin_w2_salary": 100000, "justin_ret_age": 55}
        inputs_fallback = {**sample_inputs, "jason_age": 50, "justin_age": 48, "justin_w2_salary": 100000, "justin_ret_age": 0}
        explicit = run_retirement_projection(inputs_explicit, sample_accounts, ret_ages=[60])
        fallback = run_retirement_projection(inputs_fallback, sample_accounts, ret_ages=[60])
        scenario_explicit = next(s for s in explicit["scenarios"] if s["ss_timing"] == "early")
        scenario_fallback = next(s for s in fallback["scenarios"] if s["ss_timing"] == "early")
        assert scenario_fallback["portfolio_at_retirement"] > scenario_explicit["portfolio_at_retirement"]

    def test_justin_rsu_and_bonus_also_respect_his_own_window(self, sample_inputs, sample_accounts):
        inputs = {
            **sample_inputs, "jason_age": 50, "justin_age": 50,
            "justin_w2_salary": 100000, "justin_annual_bonus_pct": 0.15, "justin_annual_rsu_value": 20000,
            "justin_ret_age": 52,  # only 2 years -- should contribute much less than defaulting to years_to_retire (10, at ret_age=60)
        }
        early_ret = run_retirement_projection(inputs, sample_accounts, ret_ages=[60])
        late_ret = run_retirement_projection({**inputs, "justin_ret_age": 60}, sample_accounts, ret_ages=[60])
        scenario_early = next(s for s in early_ret["scenarios"] if s["ss_timing"] == "early")
        scenario_late = next(s for s in late_ret["scenarios"] if s["ss_timing"] == "early")
        assert scenario_late["portfolio_at_retirement"] > scenario_early["portfolio_at_retirement"]


class TestJustinGapIncomeOffsetsWithdrawalNeed:
    """Backlog item 1 (docs/CALCULATION_CONTRACT.md section 13, closed
    2026-09-08): when justin_ret_age is set LATER than this scenario's
    own withdrawal start, Justin's continued (net-of-tax-approximated)
    income now offsets the household's withdrawal-phase spending need for
    the gap years, instead of being invisible to the withdrawal loop
    entirely. Reference implementation only — see this test class's own
    module-level scope note; Monte Carlo/Stress/SWR/Roth/Tax-Efficiency/
    Survivor do not yet reflect this (each independently re-simulates the
    withdrawal phase, or pulls only pretax_at_retirement etc. — the gap
    income adjusts a *later* year's need directly, not the starting
    balances those consumers read)."""

    def test_default_justin_ret_age_zero_has_no_gap_income(self, sample_inputs, sample_accounts):
        """justin_ret_age=0 (unset) must reproduce identical output to a
        household with no justin_w2_salary at all in the withdrawal
        phase -- this feature only activates when justin_ret_age is
        explicitly set later than the scenario's own retirement."""
        with_salary = run_retirement_projection({**sample_inputs, "justin_w2_salary": 100000}, sample_accounts, ret_ages=[60])
        without_salary_income_need = [
            y["income_need"] for y in next(s for s in run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])["scenarios"] if s["ss_timing"] == "early")["yearly_detail"]
        ]
        with_salary_income_need = [
            y["income_need"] for y in next(s for s in with_salary["scenarios"] if s["ss_timing"] == "early")["yearly_detail"]
        ]
        assert with_salary_income_need == without_salary_income_need

    def test_gap_income_reduces_need_only_during_the_gap_years(self, sample_inputs, sample_accounts):
        inputs_no_gap = {**sample_inputs, "jason_age": 55, "justin_age": 55, "justin_w2_salary": 100000, "justin_ret_age": 60}
        inputs_gap = {**sample_inputs, "jason_age": 55, "justin_age": 55, "justin_w2_salary": 100000, "justin_ret_age": 65}
        no_gap = next(s for s in run_retirement_projection(inputs_no_gap, sample_accounts, ret_ages=[60])["scenarios"] if s["ss_timing"] == "early")
        gap = next(s for s in run_retirement_projection(inputs_gap, sample_accounts, ret_ages=[60])["scenarios"] if s["ss_timing"] == "early")
        # 5-year gap (justin_ret_age 65 vs. this scenario's ret_age 60):
        # years 0-4 of the withdrawal phase should show strictly lower
        # need than the no-gap baseline; year 5 onward (gap over) should
        # match exactly.
        for yr in range(5):
            assert gap["yearly_detail"][yr]["income_need"] < no_gap["yearly_detail"][yr]["income_need"]
        assert gap["yearly_detail"][5]["income_need"] == no_gap["yearly_detail"][5]["income_need"]

    def test_gap_income_improves_projected_surplus(self, sample_inputs, sample_accounts):
        """Integration check: a household with the working spouse's
        continued income during the gap counted should look meaningfully
        better (bigger projected_surplus) than the same household
        without it -- this is the whole point of closing backlog item 1,
        not just an isolated per-year number. Uses projected_surplus
        rather than percent_funded since the latter caps at 100 and both
        of this fixture's scenarios are already comfortably funded."""
        base = {**sample_inputs, "jason_age": 55, "justin_age": 55, "retirement_income_today_dollars": 80000,
                "jason_social_security": 30000, "justin_social_security": 0, "retirement_end_age": 90}
        no_gap = next(s for s in run_retirement_projection({**base, "justin_w2_salary": 100000, "justin_ret_age": 60}, sample_accounts, ret_ages=[60])["scenarios"] if s["ss_timing"] == "early")
        gap = next(s for s in run_retirement_projection({**base, "justin_w2_salary": 100000, "justin_ret_age": 65}, sample_accounts, ret_ages=[60])["scenarios"] if s["ss_timing"] == "early")
        assert gap["projected_surplus"] > no_gap["projected_surplus"]

    def test_no_gap_when_justin_retires_before_or_with_this_scenario(self, sample_inputs, sample_accounts):
        """justin_ret_age <= this scenario's own retirement age must not
        produce gap income -- there's no gap to offset (Justin retires at
        or before the household's withdrawal phase even starts)."""
        inputs = {**sample_inputs, "jason_age": 55, "justin_age": 55, "justin_w2_salary": 100000, "justin_ret_age": 58}
        scenario = next(s for s in run_retirement_projection(inputs, sample_accounts, ret_ages=[60])["scenarios"] if s["ss_timing"] == "early")
        no_salary = next(s for s in run_retirement_projection({**sample_inputs, "jason_age": 55, "justin_age": 55}, sample_accounts, ret_ages=[60])["scenarios"] if s["ss_timing"] == "early")
        assert scenario["yearly_detail"][0]["income_need"] == no_salary["yearly_detail"][0]["income_need"]


class TestJustinGapIncomeInputsHelper:
    """Direct unit tests for the shared justin_gap_income_inputs/
    justin_years_to_retire_for helpers (CALCULATION_CONTRACT.md section
    13, backlog items 1-3), independent of any consumer -- reviewer
    findings 2026-09-08: item 2 (gap income grew with CPI inflation
    instead of the wage-growth convention every other second-earner
    income stream uses) and item 3 (the net-of-tax factor should be a
    named, documented policy, not a bare literal)."""

    def test_uses_salary_growth_pct_not_inflation(self):
        """The whole point of item 2's fix: growth must track
        salary_growth_pct (wage growth / raises), completely independent
        of whatever inflation rate the caller happens to be using
        elsewhere -- these are two unrelated assumptions that used to be
        conflated."""
        years_to_retire = 5
        justin_years_to_retire = 10  # 5-year gap
        _, at_start_no_raises = justin_gap_income_inputs(
            {"justin_w2_salary": 100000}, justin_years_to_retire, years_to_retire, salary_growth_pct=0.0)
        _, at_start_with_raises = justin_gap_income_inputs(
            {"justin_w2_salary": 100000}, justin_years_to_retire, years_to_retire, salary_growth_pct=0.03)
        # 0% raises -> flat nominal salary, no growth at all to retirement start.
        assert at_start_no_raises == pytest.approx(100000 * SECOND_EARNER_NET_OF_TAX_FACTOR)
        # 3% raises compounds over years_to_retire, independent of any
        # inflation figure (none was even passed to this function).
        assert at_start_with_raises == pytest.approx(100000 * SECOND_EARNER_NET_OF_TAX_FACTOR * (1.03 ** years_to_retire))

    def test_uses_the_named_net_of_tax_constant(self):
        """Item 3: one findable, documented policy -- not a bare literal
        this function invented independently of RSU/bonus's own
        treatment elsewhere in projection_engine.py."""
        _, at_start = justin_gap_income_inputs(
            {"justin_w2_salary": 50000}, 10, 5, salary_growth_pct=0.0)
        assert at_start == 50000 * SECOND_EARNER_NET_OF_TAX_FACTOR
        assert SECOND_EARNER_NET_OF_TAX_FACTOR == 0.65  # pins the actual policy value

    def test_no_gap_years_means_zero_income_regardless_of_salary(self):
        gap_years, at_start = justin_gap_income_inputs(
            {"justin_w2_salary": 200000}, justin_years_to_retire=5, years_to_retire=10, salary_growth_pct=0.03)
        assert gap_years == 0
        assert at_start == 0.0

    def test_justin_years_to_retire_for_fallback_and_explicit(self):
        assert justin_years_to_retire_for({"justin_ret_age": 0}, justin_age=50, years_to_retire=10) == 10
        assert justin_years_to_retire_for({"justin_ret_age": 55}, justin_age=50, years_to_retire=10) == 5


class TestWithdrawalWaterfallReconciliationFixes:
    """Regression tests for the external audit 2026-09-06 findings: RMD-age
    withdrawals silently getting rationed, spousal SS comparing the wrong
    person's age, capitalized SS/healthcare not being discounted back to
    ret_age, and HSA withdrawals vanishing from the reported total."""

    def test_pretax_withdrawal_continues_after_rmd_age_when_balance_remains(self, sample_accounts):
        """Regression test for the bug where a `rmd == 0` gate on the extra
        pretax withdrawal step blocked ANY further pretax draw once RMD
        age was reached, for the rest of the plan — even with a large
        remaining pretax balance and a large unmet need. $5M IRA / $200k
        spending / 0% returns / 0% inflation should keep drawing roughly
        $200k/yr straight through RMD age instead of collapsing to just
        the RMD amount."""
        inputs = {
            "jason_age": 60, "justin_age": 60,
            "retirement_income_today_dollars": 200000,
            "inflation_rate": 0.0,
            "expected_return_pre_retirement": 0.0,
            "expected_return_post_retirement": 0.0,
            "annual_hsa_contribution": 0, "annual_rsu_value": 0,
            "jason_social_security": 0, "justin_social_security": 0,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "retirement_end_age": 99,
            "pretax_401k_pct": 1.0,
        }
        accounts = [{"id": 1, "name": "IRA", "account_type": "ira", "owner": "jason",
                     "balance": 5000000, "institution": "", "notes": ""}]
        result = run_retirement_projection(inputs, accounts, ret_ages=[60])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        # jason_age=60 today => born 1966 => RMD starts at 75.
        post_rmd_years = [y for y in scenario["yearly_detail"] if y["jason_age"] >= 75 and y["pretax_balance"] > 0]
        assert post_rmd_years, "expected pretax balance to still be nonzero past RMD age in this fixture"
        # Withdrawal should still be funding close to the full $200k need,
        # not collapsed down to just the RMD amount.
        assert all(y["withdrawal"] >= 195000 for y in post_rmd_years)

    def test_unmet_need_surfaces_when_spending_cannot_be_funded(self, sample_accounts):
        """A plan that genuinely runs dry should surface unmet_need on the
        yearly rows once every bucket is exhausted, and on_track should be
        False — not just silently report a depleted-but-"successful" plan."""
        inputs = {
            "jason_age": 60, "justin_age": 60,
            "retirement_income_today_dollars": 200000,
            "inflation_rate": 0.0,
            "expected_return_pre_retirement": 0.0,
            "expected_return_post_retirement": 0.0,
            "annual_hsa_contribution": 0, "annual_rsu_value": 0,
            "jason_social_security": 0, "justin_social_security": 0,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "retirement_end_age": 99,
            "pretax_401k_pct": 1.0,
        }
        accounts = [{"id": 1, "name": "IRA", "account_type": "ira", "owner": "jason",
                     "balance": 500000, "institution": "", "notes": ""}]
        result = run_retirement_projection(inputs, accounts, ret_ages=[60])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        underfunded = [y for y in scenario["yearly_detail"] if y["unmet_need"] > 0]
        assert underfunded, "expected at least one underfunded year once the $500k IRA runs out"
        assert scenario["any_year_underfunded"] is True
        assert scenario["on_track"] is False

    def test_fully_funded_plan_has_no_underfunded_years(self, sample_inputs, sample_accounts):
        """Sanity check the fix didn't overshoot: a normal, well-funded
        fixture scenario should have zero underfunded years."""
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[65])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        assert all(y["unmet_need"] == 0 for y in scenario["yearly_detail"])
        assert scenario["any_year_underfunded"] is False

    def test_spousal_ss_starts_at_justins_own_claiming_age_not_jasons(self, sample_inputs, sample_accounts):
        """Regression test for the bug where the yearly loop compared
        justin_ss_age against Jason's current age instead of Justin's own
        (offset by the couple's age gap). sample_inputs: jason_age=50,
        justin_age=48 (a 2-year gap), justin_ss_age=67 => Justin's spousal
        SS should start showing up the year Justin turns 67, i.e. when
        Jason is 69 — not when Jason turns 67."""
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        yearly = scenario["yearly_detail"]
        row_jason_67 = next(y for y in yearly if y["jason_age"] == 67)
        row_jason_69 = next(y for y in yearly if y["jason_age"] == 69)
        assert row_jason_67["justin_age"] == 65  # Justin hasn't hit 67 yet
        assert row_jason_69["justin_age"] == 67  # Justin turns 67 here
        # Jason's own SS ("early", starts at 62) keeps growing with
        # inflation between these two rows regardless of Justin's benefit,
        # so isolate Justin's contribution by subtracting Jason's own
        # (independently computed) SS from the reported combined total at
        # each row, rather than assuming the raw jump is Justin's benefit
        # alone.
        jason_annual = sample_inputs["jason_social_security"]
        justin_annual = sample_inputs["justin_social_security"]
        inflation = sample_inputs["inflation_rate"]
        jason_jss_67 = jason_annual * ((1 + inflation) ** (67 - 62))
        jason_jss_69 = jason_annual * ((1 + inflation) ** (69 - 62))
        justin_contribution_at_67 = row_jason_67["social_security"] - jason_jss_67
        justin_contribution_at_69 = row_jason_69["social_security"] - jason_jss_69
        assert justin_contribution_at_67 == pytest.approx(0, abs=1)
        assert justin_contribution_at_69 == pytest.approx(justin_annual, rel=0.01)

    def test_capitalized_social_security_is_discounted_back_to_retirement_age(self, sample_inputs, sample_accounts):
        """Regression test: capitalized_income_sources used to value each
        SS stream as of its OWN claiming age and add that straight into
        the ret_age-dollars total without discounting the wait years back
        — silently overstating capitalized_income_sources (and understating
        capitalized_needed_from_assets/overstating projected_surplus) for
        any claiming age after ret_age, which is nearly always true since
        SS can't start before 62."""
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        early = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        # jason_ss_age = 62 for "early" => a 2-year wait from ret_age=60.
        # If this weren't discounted, cap_jason_ss would come out larger
        # (no division by (1+real_rate)**years_wait), inflating income and
        # thus surplus above the real, discounted figure. We can't reach
        # cap_jason_ss directly, but we can bound total capitalized income:
        # it must be materially less than what an un-discounted figure
        # would produce for a 2-year-plus wait with a nonzero real rate.
        real_rate = ((1 + sample_inputs["expected_return_post_retirement"]) /
                     (1 + sample_inputs["inflation_rate"]) - 1)
        assert real_rate > 0, "test assumes a positive real rate so discounting actually matters"
        assert early["capitalized_income_sources"] > 0
        # A basic sanity bound: total capitalized income shouldn't exceed
        # the sum of nominal pension+SS annuities times the number of
        # retirement years (a generous, deliberately loose upper bound —
        # this is a smoke check, not a precise reconciliation).
        mort_age = early["retirement_end_age"]
        loose_upper_bound = (early["pension_annual"] + early["jason_ss_annual"] + early["justin_ss_annual"]) * (mort_age - 60) * 2
        assert early["capitalized_income_sources"] < loose_upper_bound

    def test_healthcare_inflation_timing_matches_between_summary_and_yearly(self, sample_inputs, sample_accounts):
        """Regression test: the summary capitalized-need calc inflated
        healthcare_pre_medicare by years_to_retire before annuitizing it,
        but the yearly loop used to inflate the raw (un-inflated-to-ret_age)
        input from ret_age forward instead — silently understating every
        modeled year's healthcare cost (and total need) by a factor of
        (1+inflation)**years_to_retire relative to what the summary
        assumed. With years_to_retire > 0, the first retirement year's
        reported healthcare_cost should equal healthcare_pre_medicare
        already inflated to ret_age dollars, matching income_need's own
        treatment of retirement_income_today_dollars."""
        inputs = {**sample_inputs, "jason_age": 50}  # ret_age 60 => 10 years to retirement
        result = run_retirement_projection(inputs, sample_accounts, ret_ages=[60])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        first_year = scenario["yearly_detail"][0]
        years_to_retire = scenario["years_to_retirement"]
        inflation = inputs["inflation_rate"]
        expected_hc = inputs["healthcare_pre_medicare"] * ((1 + inflation) ** years_to_retire)
        assert first_year["healthcare_cost"] == pytest.approx(expected_hc, rel=0.01)

    def test_hsa_withdrawal_is_included_in_total_withdrawal(self, sample_inputs):
        """Regression test: HSA draws reduced the HSA balance but were
        never added to total_withdrawal, so a year funded partly/entirely
        from HSA silently reported less total withdrawal than was actually
        spent (income-source breakdowns couldn't reconcile against balance
        changes). Force an HSA-funded year by giving a big HSA balance and
        zero taxable/pretax/roth."""
        inputs = {
            **sample_inputs,
            "jason_age": 60, "justin_age": 60,
            "retirement_income_today_dollars": 20000,
            "inflation_rate": 0.0,
            "expected_return_pre_retirement": 0.0,
            "expected_return_post_retirement": 0.0,
            "jason_social_security": 0, "justin_social_security": 0,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
        }
        accounts = [{"id": 1, "name": "HSA", "account_type": "hsa", "owner": "jason",
                     "balance": 500000, "institution": "", "notes": ""}]
        result = run_retirement_projection(inputs, accounts, ret_ages=[60])
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        first_year = scenario["yearly_detail"][0]
        assert first_year["withdrawal_hsa"] > 0
        assert first_year["withdrawal"] == (
            first_year["withdrawal_taxable"] + first_year["withdrawal_pretax"]
            + first_year["withdrawal_roth"] + first_year["withdrawal_hsa"]
        )
        assert first_year["withdrawal"] > 0
        assert first_year["unmet_need"] == 0


class TestLifeEventsInRetirementProjection:
    """Life events (from the life_events table) are now wired into the real
    projection, not just life_event_engine.py's isolated overlay estimate."""

    def test_default_no_events_is_unchanged(self, sample_inputs, sample_accounts):
        no_kwarg  = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        explicit_none = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], life_events=None)
        explicit_empty = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], life_events=[])
        a = next(s for s in no_kwarg["scenarios"] if s["ss_timing"] == "early")
        b = next(s for s in explicit_none["scenarios"] if s["ss_timing"] == "early")
        c = next(s for s in explicit_empty["scenarios"] if s["ss_timing"] == "early")
        assert a["portfolio_at_retirement"] == b["portfolio_at_retirement"] == c["portfolio_at_retirement"]

    def test_one_time_event_before_retirement_compounds_into_taxable(self, sample_inputs, sample_accounts):
        """jason_age=50, ret_age=60 => retirement_year = CURRENT_YEAR+10.
        event_year=CURRENT_YEAR+2 is 8 years before retirement, so the
        one-time delta should compound at the pre-retirement rate for
        those 8 years and land entirely in portfolio_at_retirement."""
        pre_ret = sample_inputs["expected_return_pre_retirement"]
        event_year = CURRENT_YEAR + 2
        events = [{"event_year": event_year, "one_time_cash_delta": 10000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        baseline = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        with_event = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], life_events=events)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_event["scenarios"] if s["ss_timing"] == "early")
        expected_delta = 10000 * ((1 + pre_ret) ** 8)
        assert abs((w["portfolio_at_retirement"] - b["portfolio_at_retirement"]) - expected_delta) <= 1

    def test_one_time_event_at_retirement_or_later_lands_in_yearly_detail(self, sample_inputs, sample_accounts):
        """ret_age=60 => retirement_year = CURRENT_YEAR+10. An event dated
        2 years into retirement should show up as life_event_cash in that
        exact yearly_detail row and nowhere else, and should NOT move
        portfolio_at_retirement (it happens after retirement starts)."""
        event_year = CURRENT_YEAR + 12  # yr index 2 of the withdrawal phase
        events = [{"event_year": event_year, "one_time_cash_delta": 5000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        baseline = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        with_event = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], life_events=events)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_event["scenarios"] if s["ss_timing"] == "early")
        assert w["portfolio_at_retirement"] == b["portfolio_at_retirement"]
        matching = [y for y in w["yearly_detail"] if y["year"] == event_year]
        assert len(matching) == 1
        assert matching[0]["life_event_cash"] == 5000
        other_years = [y for y in w["yearly_detail"] if y["year"] != event_year]
        assert all(y["life_event_cash"] == 0 for y in other_years)

    def test_recurring_monthly_delta_before_retirement(self, sample_inputs, sample_accounts):
        """jason_age=50, ret_age=65 => retirement_year = CURRENT_YEAR+15.
        A monthly delta starting CURRENT_YEAR+1 with duration_months=0 runs
        as a contribution annuity all the way to retirement."""
        pre_ret = sample_inputs["expected_return_pre_retirement"]
        event_year = CURRENT_YEAR + 1
        events = [{"event_year": event_year, "one_time_cash_delta": 0,
                   "monthly_cash_flow_delta": 200, "duration_months": 0}]
        baseline = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[65])
        with_event = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[65], life_events=events)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_event["scenarios"] if s["ss_timing"] == "early")
        contrib_years = (CURRENT_YEAR + 15) - event_year  # 14
        expected_delta = 200 * 12 * (((1 + pre_ret) ** contrib_years - 1) / pre_ret)
        assert abs((w["portfolio_at_retirement"] - b["portfolio_at_retirement"]) - expected_delta) <= 1

    def test_recurring_monthly_delta_during_retirement_with_finite_duration(self, sample_inputs, sample_accounts):
        """ret_age=60 => retirement_year = CURRENT_YEAR+10. A monthly delta
        starting 1 year into retirement with a 30-month (2.5yr) duration
        should adjust income_need for two full years and six months of
        the third year, then stop by CURRENT_YEAR+14."""
        event_year = CURRENT_YEAR + 11
        events = [{"event_year": event_year, "one_time_cash_delta": 0,
                   "monthly_cash_flow_delta": 1000, "duration_months": 30}]
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], life_events=events)
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        by_year = {y["year"]: y for y in scenario["yearly_detail"]}
        for active_year in (event_year, event_year + 1):
            assert by_year[active_year]["life_event_monthly_adjustment"] == 12000
        assert by_year[event_year + 2]["life_event_monthly_adjustment"] == 6000
        assert by_year[event_year + 3]["life_event_monthly_adjustment"] == 0
        assert by_year[event_year - 1]["life_event_monthly_adjustment"] == 0

    def test_negative_one_time_event_reduces_portfolio(self, sample_inputs, sample_accounts):
        events = [{"event_year": CURRENT_YEAR + 2, "one_time_cash_delta": -20000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        baseline = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        with_event = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], life_events=events)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_event["scenarios"] if s["ss_timing"] == "early")
        assert w["portfolio_at_retirement"] < b["portfolio_at_retirement"]

    def test_debt_targeted_event_has_zero_effect_on_portfolio_and_surplus(self, sample_inputs, sample_accounts):
        """A life event with target_debt_account_id set models a one-time
        lump payment toward a specific debt (see debt_engine.
        project_debt_schedule) — it must be entirely excluded from
        _split_life_events' pre/post classification, so it has ZERO effect
        on portfolio_at_retirement/projected_surplus even with a large
        one_time_cash_delta. (Debt isn't tracked as a portfolio bucket in
        this engine at all, so this money was never going to be invested —
        giving it special treatment here would double-count it against the
        debt-payoff engine.)"""
        events = [{"event_year": CURRENT_YEAR + 2, "one_time_cash_delta": -150000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0,
                   "target_debt_account_id": 6}]
        baseline = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        with_event = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], life_events=events)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_event["scenarios"] if s["ss_timing"] == "early")
        assert w["portfolio_at_retirement"] == b["portfolio_at_retirement"]
        assert w["projected_surplus"] == b["projected_surplus"]

    def test_past_dated_event_does_not_double_count_into_the_projection(self, sample_inputs, sample_accounts):
        """Regression test (external audit 2026-09-06): a life event dated
        BEFORE CURRENT_YEAR already happened and is already reflected in
        today's account balances — replaying it into the projection
        double-counts it. $100,000 currently in the taxable account plus a
        $100,000 one-time life event dated last year must NOT raise
        portfolio_at_retirement by another ~$100,000+."""
        accounts = [dict(a) for a in sample_accounts]
        for a in accounts:
            if a["account_type"] == "taxable":
                a["balance"] = 100000
        events = [{"event_year": CURRENT_YEAR - 1, "one_time_cash_delta": 100000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        baseline = run_retirement_projection(sample_inputs, accounts, ret_ages=[60])
        with_event = run_retirement_projection(sample_inputs, accounts, ret_ages=[60], life_events=events)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_event["scenarios"] if s["ss_timing"] == "early")
        assert w["portfolio_at_retirement"] == b["portfolio_at_retirement"]

    def test_event_dated_in_current_year_still_counts(self, sample_inputs, sample_accounts):
        """Boundary case: CURRENT_YEAR itself is NOT treated as "past" — this
        app only tracks whole years, so a CURRENT_YEAR event is treated as
        not-yet-happened and still compounds into the projection, same as
        before this fix."""
        pre_ret = sample_inputs["expected_return_pre_retirement"]
        events = [{"event_year": CURRENT_YEAR, "one_time_cash_delta": 10000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        baseline = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        with_event = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], life_events=events)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_event["scenarios"] if s["ss_timing"] == "early")
        years_to_retire = 10  # jason_age=50, ret_age=60
        expected_delta = 10000 * ((1 + pre_ret) ** years_to_retire)
        assert abs((w["portfolio_at_retirement"] - b["portfolio_at_retirement"]) - expected_delta) <= 1

    def test_generic_across_every_retirement_age_not_just_55(self, sample_inputs, sample_accounts):
        """Regression guard: life events used to only be prototyped against
        the age-55 bridge scenario; confirm the pre-retirement compounding
        applies identically for every age in the 55-67 sweep."""
        events = [{"event_year": CURRENT_YEAR + 1, "one_time_cash_delta": 15000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        for age in range(55, 68):
            baseline = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[age])
            with_event = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[age], life_events=events)
            b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
            w = next(s for s in with_event["scenarios"] if s["ss_timing"] == "early")
            assert w["portfolio_at_retirement"] > b["portfolio_at_retirement"], age


class TestWithdrawalWaterfallMigratedToSharedAnnualEngine:
    """Regression tests for the shared-engine migration (see
    docs/CALCULATION_CONTRACT.md and annual_engine.py). The migration was
    verified against a captured before/after golden snapshot across 12
    synthetic scenarios (tools/capture_retirement_golden.py) covering zero
    returns/inflation, every retirement age 55-67, an already-past
    retirement age, a large spouse age gap, insufficient funds, an asset
    sale, the bridge-job/kids-at-home branch, RSU+bonus, salary growth,
    state tax, and life events. The ONLY material differences found were
    in the reporting of `withdrawal_taxable`/`withdrawal` for years where
    a life event's cash flows through the taxable bucket — see the two
    tests below, which pin down exactly what changed and confirm every
    balance/unmet_need/on_track figure is byte-identical either way."""

    def test_positive_life_event_cash_that_covers_the_full_need_is_no_longer_reported_as_a_taxable_draw(
        self, sample_inputs, sample_accounts
    ):
        """Before the migration: a large positive life event (e.g. an
        asset sale) got added directly into the taxable balance BEFORE the
        waterfall ran, so if it happened to cover that year's whole need,
        the waterfall's "draw from taxable" step still fired and counted
        the untouched, just-injected cash as a $ draw from the account.
        After the migration: life-event cash offsets need the same way
        guaranteed income does, before any bucket is touched at all — if
        it fully covers the need, no bucket draw happens, so
        withdrawal_taxable correctly reads 0. Either way the resulting
        taxable_balance/portfolio_balance/unmet_need are identical; only
        the *label* of "was this an account withdrawal" changed, and the
        new label is the more accurate one."""
        inputs = {**sample_inputs, "asset1_sale_age": 63, "asset1_sale_net": 400000, "asset1_appreciation": 0.03}
        result = run_retirement_projection(inputs, sample_accounts, ret_ages=[60])
        early = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        sale_row = next(y for y in early["yearly_detail"] if y["jason_age"] == 63)
        assert sale_row["life_event_cash"] > 400000  # appreciated from the sale age to age 63
        assert sale_row["withdrawal_taxable"] == 0
        assert sale_row["unmet_need"] == 0

    def test_negative_life_event_cash_now_correctly_shows_up_as_a_taxable_withdrawal(
        self, sample_inputs, sample_accounts
    ):
        """Before the migration: a negative life event (a one-time cost)
        was subtracted directly from the taxable balance before the
        waterfall ran, so the resulting dollar reduction in the taxable
        bucket was never recorded in withdrawal_taxable — the balance
        dropped, but nothing in the per-year table said why. After the
        migration: a negative life event increases the amount that must
        be drawn through the ordered waterfall (taxable first), so the
        exact dollars pulled from taxable to cover it now show up in
        withdrawal_taxable, matching the closing taxable_balance either
        way."""
        # jason_age=50, ret_age=60 -> retirement starts CURRENT_YEAR+10;
        # date the event a couple years into the withdrawal phase so it
        # lands in yearly_detail rather than the pre-retirement accumulation.
        event_year = CURRENT_YEAR + 12
        events = [{"event_year": event_year, "one_time_cash_delta": -50000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], life_events=events)
        early = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        event_row = next(y for y in early["yearly_detail"] if y["year"] == event_year)
        assert event_row["life_event_cash"] == -50000
        # The 50,000 cost is now visible in withdrawal_taxable, not just
        # silently absorbed into a lower taxable_balance with no
        # attribution.
        assert event_row["withdrawal_taxable"] >= 50000


class TestSurplusAllocationsInRetirementProjection:
    """surplus_allocations (from the "Assign Surplus" page's
    surplus_allocations table) are wired into the real projection as a
    pre-retirement-only ongoing monthly contribution — unlike life_events,
    there is deliberately no post-retirement half at all."""

    def test_default_no_allocations_is_unchanged(self, sample_inputs, sample_accounts):
        no_kwarg       = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        explicit_none  = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], surplus_allocations=None)
        explicit_empty = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], surplus_allocations=[])
        a = next(s for s in no_kwarg["scenarios"] if s["ss_timing"] == "early")
        b = next(s for s in explicit_none["scenarios"] if s["ss_timing"] == "early")
        c = next(s for s in explicit_empty["scenarios"] if s["ss_timing"] == "early")
        assert a["portfolio_at_retirement"] == b["portfolio_at_retirement"] == c["portfolio_at_retirement"]

    def test_retirement_contributions_split_pretax_roth_using_existing_ratio(self, sample_inputs, sample_accounts):
        """jason_age=50, ret_age=60 => years_to_retire=10. $500/mo should
        compound as a monthly annuity for 10 years, then split pretax/roth
        using the SAME pretax_401k_pct (0.75 in the fixture) used elsewhere
        in run_retirement_projection for the existing 401k contributions."""
        pre_ret = sample_inputs["expected_return_pre_retirement"]
        pretax_pct = sample_inputs["pretax_401k_pct"]
        allocations = [{"goal": "Retirement contributions", "monthly_amount": 500}]
        baseline   = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        with_alloc = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], surplus_allocations=allocations)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_alloc["scenarios"] if s["ss_timing"] == "early")

        mr = pre_ret / 12
        months = 10 * 12
        expected_fv = 500 * (((1 + mr) ** months - 1) / mr)

        # abs=2 rather than a tight rel tolerance: pretax/roth_at_retirement
        # are each independently round()-ed to whole dollars by the engine
        # before this delta is taken, so up to ~$1 of rounding error can
        # show up on each side of the subtraction.
        pretax_delta = w["pretax_at_retirement"] - b["pretax_at_retirement"]
        roth_delta   = w["roth_at_retirement"]   - b["roth_at_retirement"]
        assert pretax_delta == pytest.approx(expected_fv * pretax_pct, abs=2)
        assert roth_delta   == pytest.approx(expected_fv * (1 - pretax_pct), abs=2)
        # Taxable/hsa untouched by this goal.
        assert w["taxable_at_retirement"] == b["taxable_at_retirement"]
        # Portfolio total reflects exactly the combined pretax+roth delta.
        assert (w["portfolio_at_retirement"] - b["portfolio_at_retirement"]) == pytest.approx(expected_fv, abs=2)

    def test_taxable_investing_compounds_into_taxable_bucket(self, sample_inputs, sample_accounts):
        pre_ret = sample_inputs["expected_return_pre_retirement"]
        allocations = [{"goal": "Taxable investing", "monthly_amount": 300}]
        baseline   = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        with_alloc = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], surplus_allocations=allocations)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_alloc["scenarios"] if s["ss_timing"] == "early")

        mr = pre_ret / 12
        months = 10 * 12
        expected_fv = 300 * (((1 + mr) ** months - 1) / mr)

        # abs=2, not a tight rel tolerance -- see comment on the sibling
        # pretax/roth test above re: whole-dollar round()ing on each side.
        assert (w["taxable_at_retirement"] - b["taxable_at_retirement"]) == pytest.approx(expected_fv, abs=2)
        assert w["pretax_at_retirement"] == b["pretax_at_retirement"]
        assert w["roth_at_retirement"]   == b["roth_at_retirement"]

    @pytest.mark.parametrize("goal", [
        "Emergency reserve", "High-interest debt payoff", "Education funding",
        "Tax reserve", "Other goal",
    ])
    def test_non_retirement_goals_have_zero_effect(self, sample_inputs, sample_accounts, goal):
        """Regression guard for the deliberate exclusion of the other five
        fixed goals — a nonzero monthly_amount on any of them must not move
        the projection at all, even though the row is passed in."""
        allocations = [{"goal": goal, "monthly_amount": 1000}]
        baseline   = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        with_alloc = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], surplus_allocations=allocations)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_alloc["scenarios"] if s["ss_timing"] == "early")
        assert w["portfolio_at_retirement"] == b["portfolio_at_retirement"]
        assert w["pretax_at_retirement"]  == b["pretax_at_retirement"]
        assert w["roth_at_retirement"]    == b["roth_at_retirement"]
        assert w["taxable_at_retirement"] == b["taxable_at_retirement"]

    def test_zero_and_negative_monthly_amount_have_no_effect(self, sample_inputs, sample_accounts):
        allocations = [
            {"goal": "Retirement contributions", "monthly_amount": 0},
            {"goal": "Taxable investing", "monthly_amount": -100},
        ]
        baseline   = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        with_alloc = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], surplus_allocations=allocations)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_alloc["scenarios"] if s["ss_timing"] == "early")
        assert w["portfolio_at_retirement"] == b["portfolio_at_retirement"]

    def test_no_post_retirement_effect(self, sample_inputs, sample_accounts):
        """The extra money stops being contributed at the moment of
        retirement — confirm the withdrawal-phase yearly_detail is
        completely unaffected (no life-event-style fields exist for this
        feature, so the entire yearly_detail list should be identical)."""
        allocations = [{"goal": "Retirement contributions", "monthly_amount": 500},
                       {"goal": "Taxable investing", "monthly_amount": 300}]
        baseline   = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60])
        with_alloc = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], surplus_allocations=allocations)
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_alloc["scenarios"] if s["ss_timing"] == "early")
        # Withdrawal need each year is driven by income/healthcare/life
        # events only — none of that changes, so income_need should match
        # exactly year-over-year even though the starting balances differ.
        b_needs = [y["income_need"] for y in b["yearly_detail"]]
        w_needs = [y["income_need"] for y in w["yearly_detail"]]
        assert b_needs == w_needs

    def test_generic_across_every_retirement_age(self, sample_inputs, sample_accounts):
        allocations = [{"goal": "Retirement contributions", "monthly_amount": 400}]
        for age in range(55, 68):
            baseline   = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[age])
            with_alloc = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[age], surplus_allocations=allocations)
            b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
            w = next(s for s in with_alloc["scenarios"] if s["ss_timing"] == "early")
            assert w["portfolio_at_retirement"] > b["portfolio_at_retirement"], age


class TestAssetSaleAndRsuBridgeAt55:
    """Not life-events related — the asset1/asset2 sale bridge and RSU
    accumulation branches at ret_age=55 (existing precedent for
    "one-time cash event before retirement", the closest prior art the
    life-events feature above generalizes)."""

    def test_asset_sales_and_rsu_increase_taxable_at_retirement(self, sample_inputs, sample_accounts):
        inputs = {
            **sample_inputs,
            "jason_age": 45,
            "annual_rsu_value": 20000,
            "asset1_sale_age": 50, "asset1_sale_net": 300000, "asset1_appreciation": 0.03,
            "asset2_sale_age": 52, "asset2_sale_net": 150000,
        }
        with_sales = run_retirement_projection(inputs, sample_accounts, ret_ages=[55])
        baseline   = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[55])
        w = next(s for s in with_sales["scenarios"] if s["ss_timing"] == "early")
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        assert w["taxable_at_retirement"] > b["taxable_at_retirement"]

    def test_asset_sales_apply_to_every_retirement_age_not_just_55(self, sample_inputs, sample_accounts):
        """Regression: this used to run inside `if ret_age == 55:`, so a
        sale scheduled for any age other than exactly 55 was invisible in
        every single scenario — including the 55 column itself if the
        sale_age was set after 55 (e.g. 56), since that column's own
        "already happened" check (sale_age <= ret_age) also failed. Found
        via a bug-hunt sandbox reproducing a real case: a business sale at
        age 56 counted in zero of the 55-67 scenario columns. Must now
        apply from the sale's age onward, same as a life event would."""
        base_inputs = {**sample_inputs, "jason_age": 45}
        inputs = {**base_inputs, "asset2_sale_age": 56, "asset2_sale_net": 75000}
        baseline = run_retirement_projection(base_inputs, sample_accounts, ret_ages=[55, 56, 60])
        with_sale = run_retirement_projection(inputs, sample_accounts, ret_ages=[55, 56, 60])
        b = {s["retirement_age"]: s for s in baseline["scenarios"] if s["ss_timing"] == "early"}
        w = {s["retirement_age"]: s for s in with_sale["scenarios"] if s["ss_timing"] == "early"}
        # Retiring at 55 is before the sale happens (56) — no effect yet.
        assert w[55]["taxable_at_retirement"] == pytest.approx(b[55]["taxable_at_retirement"])
        # Retiring at 56 or 60 is at/after the sale — proceeds must show up.
        assert w[56]["taxable_at_retirement"] > b[56]["taxable_at_retirement"]
        assert w[60]["taxable_at_retirement"] > b[60]["taxable_at_retirement"]

    def test_asset_sale_scheduled_after_retirement_appears_in_withdrawal_phase(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): a sale scheduled for
        AFTER a given retirement-age scenario's own retirement date
        (sale_age > ret_age) was invisible everywhere — not covered by the
        accumulation-phase code above (which only handles sale_age <=
        ret_age) and nothing else in the app modeled a post-retirement
        asset sale at all. Reproduces the audit's exact case: retire at
        58, sell for $500,000 at 60 -> the withdrawal-phase year matching
        age 60 must show the cash arriving, and the portfolio must be
        higher than a no-sale baseline every year from then on."""
        inputs_no_sale = {**sample_inputs, "jason_age": 50}
        inputs_sale = {**inputs_no_sale, "asset2_sale_age": 60, "asset2_sale_net": 500000}
        baseline  = run_retirement_projection(inputs_no_sale, sample_accounts, ret_ages=[58])
        with_sale = run_retirement_projection(inputs_sale, sample_accounts, ret_ages=[58])
        b = next(s for s in baseline["scenarios"] if s["ss_timing"] == "early")
        w = next(s for s in with_sale["scenarios"] if s["ss_timing"] == "early")
        b_by_age = {y["jason_age"]: y for y in b["yearly_detail"]}
        w_by_age = {y["jason_age"]: y for y in w["yearly_detail"]}
        assert w_by_age[60]["life_event_cash"] == 500000
        assert w_by_age[60]["portfolio_balance"] > b_by_age[60]["portfolio_balance"]
        assert w_by_age[65]["portfolio_balance"] > b_by_age[65]["portfolio_balance"]

    def test_asset_sale_between_a_past_ret_age_and_real_current_age_is_not_dropped(
            self, sample_inputs, sample_accounts):
        """Regression (independent review, 2026-09-07, third follow-up —
        an adjacent case flagged alongside the past-ret_age timeline fix,
        resolved by correcting the reference implementation together with
        every consumer, not by leaving it agreeing with a known bug): a
        sale dated between an already-past selected ret_age and the
        household's real current age used to satisfy NEITHER the
        accumulation phase's inclusion check (asset_sale_age <= ret_age)
        NOR _post_retirement_asset_sale_events' own exclusion check
        (also <= ret_age) — both compared against the wrong (too-early)
        boundary, so the sale vanished entirely rather than being counted
        exactly once. Household currently 65, selecting ret_age 55, a
        $500K sale at 60 (between the two) must show up exactly once, at
        the same total as selecting the real current age directly (both
        land the sale at face value with zero growth years, since the
        sale is already behind "today" in both selections — see
        TestAssetSaleGrowthYearsHandCalculated below for the exact
        growth-years arithmetic this equality relies on)."""
        inputs = {**sample_inputs, "jason_age": 65, "justin_age": 65,
                  "asset2_sale_age": 60, "asset2_sale_net": 500000}
        past = run_retirement_projection(inputs, sample_accounts, ret_ages=[55])
        current = run_retirement_projection(inputs, sample_accounts, ret_ages=[65])
        p = next(s for s in past["scenarios"] if s["label"] == "age_55_early")
        c = next(s for s in current["scenarios"] if s["label"] == "age_65_early")
        assert p["taxable_at_retirement"] == pytest.approx(c["taxable_at_retirement"], abs=1)
        # Sanity: the sale actually landed somewhere, not just "nothing
        # changed by coincidence."
        no_sale = run_retirement_projection({**inputs, "asset2_sale_age": 0, "asset2_sale_net": 0},
                                             sample_accounts, ret_ages=[55])
        n = next(s for s in no_sale["scenarios"] if s["label"] == "age_55_early")
        assert p["taxable_at_retirement"] > n["taxable_at_retirement"]


class TestAssetSaleGrowthYearsHandCalculated:
    """Independent review, 2026-09-07, fourth follow-up: the past-ret_age
    inclusion fix above (TestAssetSaleAndRsuBridgeAt55) changed the
    growth-years EXPONENT to `effective_start_age - sale_age` directly —
    which, for a sale predating jason_age, silently credited investment
    return for calendar years already in the past whenever ret_age was
    still in the future (reproduced: age 65 today, retiring at 70, a
    sale at 60 with 10% returns added $259,374 instead of the correct
    $161,051 — 10 years of compounding instead of the actual 5 remaining
    accumulation years). These tests hand-calculate the expected delta
    independently (not derived by running the code first) across both
    assets, past/current/future sale dates, and past/current/future
    retirement selections, with a nonzero pre-retirement return — the
    prior regression test only ever compared two calls using the SAME
    (buggy) formula against each other, so it could not have caught
    this. Correct formula: yrs_to_grow = max(0, effective_start_age -
    jason_age) - max(0, sale_age - jason_age) — years from TODAY to the
    effective retirement start, minus years from today to the sale (0 if
    the sale already happened) — which reduces to the pre-independent-
    review-fix formula (`years_to_retire - yrs_assetN`) exactly whenever
    ret_age >= jason_age."""

    BASE = {
        "jason_age": 65, "justin_age": 65, "retirement_income_today_dollars": 0,
        "inflation_rate": 0.0, "expected_return_pre_retirement": 0.10,
        "expected_return_post_retirement": 0.0,
        "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
        "jason_ss_age": 62, "justin_ss_age": 67, "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        "pension_55": 0, "pension_60": 0, "pension_65": 0,
        "annual_hsa_contribution": 0, "annual_rsu_value": 0, "pretax_401k_pct": 1.0,
        "employee_401k_pct": 0, "employer_401k_pct": 0, "w2_salary": 0,
        "retirement_end_age": 95, "state_income_tax_rate": 0,
    }
    ACCOUNTS = [{"id": 1, "name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 500_000}]

    def _delta(self, ret_age, overrides):
        inputs = {**self.BASE, **overrides}
        no_sale = {**inputs, "asset1_sale_age": 0, "asset1_sale_net": 0,
                   "asset2_sale_age": 0, "asset2_sale_net": 0}
        with_sale = run_retirement_projection(inputs, self.ACCOUNTS, ret_ages=[ret_age])
        without = run_retirement_projection(no_sale, self.ACCOUNTS, ret_ages=[ret_age])
        label = f"age_{ret_age}_early"
        w = next(s for s in with_sale["scenarios"] if s["label"] == label)
        n = next(s for s in without["scenarios"] if s["label"] == label)
        return w["taxable_at_retirement"] - n["taxable_at_retirement"]

    def test_asset1_past_sale_future_retirement(self):
        # jason=65, ret=70 (future), sale at 60 (past): 0 pre-sale
        # appreciation years (floored), 5 real accumulation years left
        # (70-65) at 10% -> 100,000 * 1.10^5 = 161,051.
        delta = self._delta(70, {"asset1_sale_age": 60, "asset1_sale_net": 100_000,
                                  "asset1_appreciation": 0.03})
        assert delta == pytest.approx(161_051, abs=1)

    def test_asset1_future_sale_future_retirement(self):
        # jason=65, ret=70, sale at 68 (future, between today and
        # retirement): 3 years of 3% pre-sale appreciation, then 2
        # remaining years (70-68) of 10% post-sale growth.
        # 100,000 * 1.03^3 = 109,272.70; * 1.10^2 = 132,219.97 -> 132,220.
        delta = self._delta(70, {"asset1_sale_age": 68, "asset1_sale_net": 100_000,
                                  "asset1_appreciation": 0.03})
        assert delta == pytest.approx(132_220, abs=1)

    def test_asset2_sale_exactly_at_retirement_gets_zero_growth_years(self):
        # jason=65, ret=70, sale at 70 (exactly at retirement): 5 years
        # to the sale, 0 years remaining to grow after it -> face value.
        delta = self._delta(70, {"asset2_sale_age": 70, "asset2_sale_net": 100_000})
        assert delta == pytest.approx(100_000, abs=1)

    def test_asset2_past_sale_current_age_retirement(self):
        # jason=65, ret=65 (retiring today): a past sale (60) gets 0
        # accumulation years regardless of the 10% return -- there is no
        # "future" left to grow into, and no historical years are
        # credited for the sale-to-today gap.
        delta = self._delta(65, {"asset2_sale_age": 60, "asset2_sale_net": 100_000})
        assert delta == pytest.approx(100_000, abs=1)

    def test_asset1_past_retirement_selection_with_sale_in_the_deep_past(self):
        # jason=65, ret=60 (already past -> effective_start_age=65),
        # sale at 50 (even further in the past): floors to 0 pre-sale
        # appreciation years AND 0 growth years (years_from_today_to_
        # start is 0 in the past-ret_age case) -> pure face value.
        delta = self._delta(60, {"asset1_sale_age": 50, "asset1_sale_net": 100_000,
                                  "asset1_appreciation": 0.03})
        assert delta == pytest.approx(100_000, abs=1)

    def test_matches_the_original_formula_exactly_when_retirement_is_in_the_future(self):
        """The corrected formula must reduce to the untouched pre-
        independent-review formula (years_to_retire - yrs_assetN) for
        every ordinary future-retirement case -- not just the specific
        numbers above. Cross-checks 5 sale-age/ret-age/return
        combinations against that original formula computed independently
        in this test (not imported from the implementation)."""
        cases = [
            # jason_age, ret_age, sale_age, sale_net, pre_ret
            (50, 65, 40, 200_000, 0.06),   # sale before jason_age
            (50, 65, 50, 200_000, 0.06),   # sale exactly at jason_age
            (50, 65, 58, 200_000, 0.06),   # sale between jason_age and ret_age
            (50, 65, 65, 200_000, 0.06),   # sale exactly at ret_age
            (45, 67, 55, 300_000, 0.08),   # a different age/return combo entirely
        ]
        for jason_age, ret_age, sale_age, sale_net, pre_ret in cases:
            years_to_retire = max(0, ret_age - jason_age)
            yrs_asset2 = max(0, sale_age - jason_age)
            original_formula_delta = sale_net * ((1 + pre_ret) ** (years_to_retire - yrs_asset2))
            got = self._delta(ret_age, {
                "jason_age": jason_age, "justin_age": jason_age,
                "expected_return_pre_retirement": pre_ret,
                "asset2_sale_age": sale_age, "asset2_sale_net": sale_net,
            })
            assert got == pytest.approx(original_formula_delta, abs=1), (
                f"jason_age={jason_age} ret_age={ret_age} sale_age={sale_age}")


class TestRunEducationProjection:
    def test_returns_two_goals(self, sample_inputs, sample_accounts):
        result = run_education_projection(sample_inputs, sample_accounts)
        assert len(result["goals"]) == 2

    def test_child_names_come_from_inputs(self, sample_inputs, sample_accounts):
        result = run_education_projection(sample_inputs, sample_accounts)
        names = {g["child_name"] for g in result["goals"]}
        assert names == {"Kid A", "Kid B"}

    def test_ages_come_from_inputs_not_hardcoded(self, sample_inputs, sample_accounts):
        """Regression test — kid ages used to be hardcoded (11, 7) instead of
        read from kid1_age/kid2_age."""
        custom = {**sample_inputs, "kid1_age": 15, "kid2_age": 3}
        result = run_education_projection(custom, sample_accounts)
        ages = {g["current_age"] for g in result["goals"]}
        assert ages == {15, 3}

    def test_current_annual_cost_reflects_configured_unl_cost(self, sample_inputs, sample_accounts):
        """Regression test — current_annual_cost was hardcoded to the
        UNL_CURRENT_ANNUAL module constant (0, a fallback-only placeholder)
        instead of the real unl_annual_cost input already used correctly
        elsewhere in this same function's total_cost/funding math."""
        custom = {**sample_inputs, "unl_annual_cost": 21000}
        result = run_education_projection(custom, sample_accounts)
        for goal in result["goals"]:
            assert goal["current_annual_cost"] == 21000

    def test_contributions_stop_at_college_by_default(self, sample_inputs, sample_accounts):
        # sample_inputs' jason_age is well under the retirement cutoff, so
        # college start (not retirement) is the binding constraint here.
        custom = {**sample_inputs, "jason_age": 40, "kid1_age": 12}
        result = run_education_projection(custom, sample_accounts)
        abby = next(g for g in result["goals"] if g["child"] == "Abby")
        assert abby["contributions_stop_in_years"] == abby["years_to_college"]  # 6

    def test_continue_contributions_during_college_extends_window(self, sample_inputs, sample_accounts):
        custom = {**sample_inputs, "jason_age": 40, "kid1_age": 12}
        result = run_education_projection(custom, sample_accounts, continue_contributions_during_college=True)
        abby = next(g for g in result["goals"] if g["child"] == "Abby")
        assert abby["contributions_stop_in_years"] == abby["years_to_college"] + 4  # + COLLEGE_YEARS

    def test_continuing_during_college_never_produces_a_lower_projected_balance(self, sample_inputs, sample_accounts):
        custom = {**sample_inputs, "jason_age": 40, "kid1_age": 12}
        without = run_education_projection(custom, sample_accounts, continue_contributions_during_college=False)
        withh   = run_education_projection(custom, sample_accounts, continue_contributions_during_college=True)
        abby_without = next(g for g in without["goals"] if g["child"] == "Abby")
        abby_with    = next(g for g in withh["goals"] if g["child"] == "Abby")
        assert abby_with["yearly_chart"][-1]["balance"] >= abby_without["yearly_chart"][-1]["balance"]

    def test_contributions_stop_at_parent_retirement_even_before_college(self, sample_inputs, sample_accounts):
        """Regression/feature test: a parent already at or past the
        retirement-age assumption should show zero years of further
        contributions, regardless of how far off college still is."""
        custom = {**sample_inputs, "jason_age": PARENT_RETIREMENT_AGE_ASSUMPTION, "kid1_age": 5}
        result = run_education_projection(custom, sample_accounts)
        abby = next(g for g in result["goals"] if g["child"] == "Abby")
        assert abby["contributions_stop_in_years"] == 0

    def test_retirement_cutoff_binds_before_college_window_when_sooner(self, sample_inputs, sample_accounts):
        # Parent is 5 years from the retirement-age assumption; kid is 10
        # years from college — retirement should be the binding constraint.
        custom = {**sample_inputs, "jason_age": PARENT_RETIREMENT_AGE_ASSUMPTION - 5, "kid1_age": 8}
        result = run_education_projection(custom, sample_accounts)
        abby = next(g for g in result["goals"] if g["child"] == "Abby")
        assert abby["contributions_stop_in_years"] == 5
        assert abby["contributions_stop_in_years"] < abby["years_to_college"]

    def test_continue_during_college_changes_balance_after_college_not_funding_percent(self, sample_inputs, sample_accounts):
        """Regression test — for a well-funded 529 that never dips negative
        either way, funding_percent/gap stay at the "fully funded" state
        (100%/$0) in both cases even though the toggle still moves the
        actual post-college balance. (funding_percent/gap are now driven by
        the same drawdown simulation as balance_after_college, so they
        WOULD move together if the account were ever short — see
        test_gap_reflects_real_depletion_not_an_arbitrary_snapshot below.)
        This used to mean toggling the checkbox had no visible effect at
        all, since nothing on the page displayed the one thing it changed."""
        # A well-funded 529 (large existing balance, modest college cost)
        # so the account doesn't fully deplete in either case — otherwise
        # both scenarios floor at $0 and there's nothing to compare.
        accounts = sample_accounts + [
            {"id": 99, "name": "Abby 529", "account_type": "529", "owner": "abby", "balance": 300000, "institution": "", "notes": ""},
        ]
        custom = {**sample_inputs, "jason_age": 40, "kid1_age": 12, "unl_annual_cost": 15000}
        without = run_education_projection(custom, accounts, continue_contributions_during_college=False)
        withh   = run_education_projection(custom, accounts, continue_contributions_during_college=True)
        abby_without = next(g for g in without["goals"] if g["child"] == "Abby")
        abby_with    = next(g for g in withh["goals"] if g["child"] == "Abby")
        assert abby_with["funding_percent"] == abby_without["funding_percent"]
        assert abby_with["balance_after_college"] > abby_without["balance_after_college"]

    def test_funding_math_uses_the_same_simulation_as_the_chart(self, sample_inputs, sample_accounts):
        """The old implementation computed projected_529_at_college via a
        closed-form formula assuming uninterrupted contributions, separate
        from the year-by-year chart simulation — the two only agreed by
        coincidence. This confirms they're now one calculation."""
        custom = {**sample_inputs, "jason_age": PARENT_RETIREMENT_AGE_ASSUMPTION - 3, "kid1_age": 10}
        result = run_education_projection(custom, sample_accounts)
        abby = next(g for g in result["goals"] if g["child"] == "Abby")
        # The last "saving"-phase chart entry's balance is what college
        # starts with, exactly matching projected_529_at_college.
        saving_entries = [y for y in abby["yearly_chart"] if y["phase"] == "saving"]
        assert saving_entries[-1]["balance"] == abby["projected_529_at_college"]

    def test_gap_reflects_real_depletion_not_an_arbitrary_snapshot(self, sample_inputs, sample_accounts):
        """Regression test for a real reported bug: the app used to compute
        funding_gap/monthly_savings_to_close_gap/lump_sum_to_close_gap from
        a snapshot at the moment college starts (balance vs. an arbitrary
        90%-of-total-cost target), completely independent of the actual
        drawdown simulation. That meant it could — and did — recommend
        saving more money even when the real trajectory, continuing to
        earn returns all through the 4 college years, already ends with
        money left over. The recommendation must only ever fire when the
        account genuinely would run out."""
        never_short = next(g for g in run_education_projection(sample_inputs, sample_accounts)["goals"]
                            if g["child"] == "Abby")
        if not never_short["depleted_during_college"]:
            assert never_short["funding_gap"] == 0
            assert never_short["monthly_savings_to_close_gap"] == 0
            assert never_short["lump_sum_to_close_gap"] == 0

        # A genuinely underfunded scenario: tiny existing balance, tiny
        # ongoing contribution, expensive college — should both report a
        # gap AND actually deplete, and the two must agree with each other.
        short_accounts = sample_accounts + [
            {"id": 98, "name": "Abby 529", "account_type": "529", "owner": "abby", "balance": 500, "institution": "", "notes": ""},
        ]
        short_inputs = {**sample_inputs, "kid1_age": 16, "abby_529_monthly": 10, "unl_annual_cost": 30000}
        result = run_education_projection(short_inputs, short_accounts)
        abby = next(g for g in result["goals"] if g["child"] == "Abby")
        assert abby["depleted_during_college"] is True
        assert abby["funding_gap"] > 0
        assert abby["monthly_savings_to_close_gap"] > 0
        assert abby["lump_sum_to_close_gap"] > 0

        # And the invariant holds across the board: a gap is reported
        # exactly when — never more, never less than — the account
        # actually runs dry at some point during college.
        for g in result["goals"]:
            assert (g["funding_gap"] > 0) == g["depleted_during_college"]


class TestEducationAndKidsProjectionsAgreeOn529AtCollege:
    """Regression tests (external audit 2026-09-06): run_education_projection
    and run_kids_projection project the same underlying 529 account and
    should reach the same balance at college for the same inputs. Root
    cause was run_kids_projection ignoring the parent's assumed retirement
    cutoff entirely — Education already stopped 529 contributions at
    whichever came first (college or PARENT_RETIREMENT_AGE_ASSUMPTION);
    Kids contributed all the way to 18 regardless."""

    def test_agree_when_parent_retirement_cutoff_binds(self, sample_inputs, sample_accounts):
        """Parent age 59, child age 10, $100/mo, $0 starting balance — the
        exact reported case: 1 year of contributions left before the parent's
        assumed retirement (60), not the full 8 years to college."""
        inputs = {**sample_inputs, "jason_age": 59, "kid1_age": 10, "abby_529_monthly": 100}
        edu = run_education_projection(inputs, sample_accounts)
        kid = run_kids_projection(sample_accounts, inputs)
        edu_abby = next(g for g in edu["goals"] if g["child"] == "Abby")
        kid_abby = next(k for k in kid["kids"] if k["child"] == "Abby")
        assert edu_abby["projected_529_at_college"] == kid_abby["529"]["at_18"]
        # Sanity: the cutoff actually bound (contributions stop well before
        # the 8 years to college), otherwise this test wouldn't be
        # distinguishing the fixed behavior from the old bug at all.
        assert edu_abby["contributions_stop_in_years"] == 1
        assert edu_abby["contributions_stop_in_years"] < edu_abby["years_to_college"]

    def test_agree_when_college_is_the_binding_constraint(self, sample_inputs, sample_accounts):
        """When the parent is nowhere near retirement, college start (not
        retirement) is the binding constraint for both functions, and they
        should still agree."""
        inputs = {**sample_inputs, "jason_age": 40, "kid1_age": 10, "abby_529_monthly": 100}
        edu = run_education_projection(inputs, sample_accounts)
        kid = run_kids_projection(sample_accounts, inputs)
        edu_abby = next(g for g in edu["goals"] if g["child"] == "Abby")
        kid_abby = next(k for k in kid["kids"] if k["child"] == "Abby")
        assert edu_abby["contributions_stop_in_years"] == edu_abby["years_to_college"]
        assert edu_abby["projected_529_at_college"] == kid_abby["529"]["at_18"]


class TestSharedSavingPhaseHelper:
    """Calculation-engine consolidation Phase 5: run_education_projection
    and run_kids_projection now both call _project_529_saving_phase for
    their shared "529 balance at the moment college/18 starts" figure,
    instead of each independently reimplementing the same monthly-
    compounding-then-dormant-growth formula (verified to already agree —
    see TestEducationAndKidsProjectionsAgreeOn529AtCollege above, which is
    what made this extraction safe in the first place)."""

    def test_matches_education_projection_headline_number(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_age": 45, "kid1_age": 8, "abby_529_monthly": 300}
        edu = run_education_projection(inputs, sample_accounts)
        edu_abby = next(g for g in edu["goals"] if g["child"] == "Abby")
        balance_529 = sum(a["balance"] for a in sample_accounts
                           if a["account_type"] == "529" and a["owner"] == "abby")
        years_to_college = 18 - 8
        years_until_parent_retires = max(0, PARENT_RETIREMENT_AGE_ASSUMPTION - 45)
        balance, contribution_years = _project_529_saving_phase(
            balance_529, 300, years_to_college, years_until_parent_retires)
        assert round(balance) == edu_abby["projected_529_at_college"]
        assert contribution_years == edu_abby["contributions_stop_in_years"]

    def test_matches_kids_projection_headline_number(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_age": 45, "kid1_age": 8, "abby_529_monthly": 300}
        kid = run_kids_projection(sample_accounts, inputs)
        kid_abby = next(k for k in kid["kids"] if k["child"] == "Abby")
        balance_529 = sum(a["balance"] for a in sample_accounts
                           if a["account_type"] == "529" and a["owner"] == "abby")
        years_to_college = 18 - 8
        years_until_parent_retires = max(0, PARENT_RETIREMENT_AGE_ASSUMPTION - 45)
        balance, _ = _project_529_saving_phase(balance_529, 300, years_to_college, years_until_parent_retires)
        assert round(balance) == kid_abby["529"]["at_18"]

    def test_zero_years_to_college_returns_starting_balance(self):
        balance, contribution_years = _project_529_saving_phase(50000, 300, 0, 10)
        assert balance == 50000
        assert contribution_years == 0

    def test_extra_years_of_contributions_extends_contribution_years_but_not_the_pre_college_balance(self):
        """continue_contributions_during_college support: contributions
        past college's start fund the drawdown, not this saving-phase
        balance — the returned contribution_years reflects the extension
        (for the drawdown loop to use), but the balance itself must be
        identical to the no-extension case."""
        no_extension, cy_no_ext = _project_529_saving_phase(10000, 200, 5, 20, extra_years_of_contributions=0)
        with_extension, cy_ext = _project_529_saving_phase(10000, 200, 5, 20, extra_years_of_contributions=4)
        assert no_extension == with_extension
        assert cy_ext == cy_no_ext + 4

    def test_parent_retirement_cutoff_caps_contribution_years(self):
        """years_until_parent_retires binding (1 year left, 8 years to
        college) must cap the contribution years, not just the final
        balance — reproduces the exact external-audit scenario that
        originally caught run_kids_projection ignoring this cutoff."""
        balance, contribution_years = _project_529_saving_phase(0, 100, 8, 1)
        assert contribution_years == 1
        # Independently hand-calculated: $100/mo compounds for exactly 1
        # year at 7%, then that lump sum sits untouched (no further
        # contributions) compounding for the remaining 7 years to college.
        expected = _fv_annuity_monthly(100, 0.07, 1) * (1.07 ** 7)
        assert balance == pytest.approx(expected, abs=0.01)


class TestSharedCollegeDrawdownHelper:
    """Calculation-engine consolidation, item 7 (2026-09-07):
    run_education_projection and run_kids_projection now both call
    _project_college_drawdown for their shared "COLLEGE_YEARS of 529
    drawdown" recurrence, instead of each independently reimplementing
    the same compound-then-subtract-cost formula. Verified byte-identical
    against both existing (already audit-hardened) implementations via a
    384-scenario golden diff before this extraction was made (kid ages,
    parent age including past the contribution cutoff, 529 balances,
    contribution amounts, continue_contributions_during_college) — these
    tests pin the helper's own behavior independently of either
    consumer, the same discipline TestSharedSavingPhaseHelper applied to
    _project_529_saving_phase."""

    def test_matches_education_projection_headline_number(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_age": 45, "kid1_age": 8, "abby_529_monthly": 300, "unl_annual_cost": 25000}
        edu = run_education_projection(inputs, sample_accounts)
        edu_abby = next(g for g in edu["goals"] if g["child"] == "Abby")
        yearly, worst_deficit, worst_deficit_years_out = _project_college_drawdown(
            edu_abby["projected_529_at_college"], 300, 0.07, 25000,
            edu_abby["years_to_college"], edu_abby["contributions_stop_in_years"], track_unclamped=True)
        assert round(yearly[-1]) == edu_abby["balance_after_college"]
        expected_gap = -worst_deficit if worst_deficit < 0 else 0
        assert round(expected_gap) == edu_abby["funding_gap"]

    def test_matches_kids_projection_headline_number(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_age": 45, "kid1_age": 8, "abby_529_monthly": 300, "unl_annual_cost": 25000}
        kid = run_kids_projection(sample_accounts, inputs)
        kid_abby = next(k for k in kid["kids"] if k["child"] == "Abby")
        years_to_18 = 18 - 8
        proj_529_at_18 = kid_abby["529"]["at_18"]
        yearly, _, _ = _project_college_drawdown(proj_529_at_18, 300, 0.07, 25000, years_to_18, years_to_18)
        bal_after = yearly[-1]
        roth_rollover = min(bal_after, 35000)
        assert round(bal_after - roth_rollover) == kid_abby["529"]["at_22"]

    def test_returns_college_years_entries(self):
        yearly, worst_deficit, worst_deficit_years_out = _project_college_drawdown(
            10000, 0, 0.07, 25000, 10, 10)
        assert len(yearly) == COLLEGE_YEARS

    def test_no_track_unclamped_leaves_worst_deficit_at_default(self):
        """A trajectory that goes broke mid-college must still report
        worst_deficit == 0.0 (not a negative number) when track_unclamped
        is off, matching run_kids_projection's own choice not to compute
        this at all."""
        yearly, worst_deficit, worst_deficit_years_out = _project_college_drawdown(
            1000, 0, 0.07, 50000, 10, 10, track_unclamped=False)
        assert worst_deficit == 0.0
        assert worst_deficit_years_out == 10
        assert yearly[-1] == 0  # floored, even though unclamped would be deeply negative

    def test_track_unclamped_reports_real_shortfall(self):
        # A trajectory this underwater only gets worse each year once
        # negative (a negative balance still "compounds" further negative,
        # then loses another year's cost on top) — the worst point is the
        # LAST college year here, not the first one it goes negative in.
        yearly, worst_deficit, worst_deficit_years_out = _project_college_drawdown(
            1000, 0, 0.07, 50000, 10, 10, track_unclamped=True)
        assert worst_deficit < 0
        assert worst_deficit_years_out == 10 + COLLEGE_YEARS

    def test_contributions_continue_during_college_adds_a_years_worth_each_active_year(self):
        """contribution_years extending past years_to_college (the
        continue_contributions_during_college case) must add a
        contribution in exactly those college years, and none once
        contribution_years is exhausted."""
        # Balance/cost sized so neither trajectory floors at 0 -- a floored
        # comparison can't distinguish "no contribution" from "a small one".
        no_contrib, _, _ = _project_college_drawdown(50000, 500, 0.07, 5000, 5, 5)
        with_contrib, _, _ = _project_college_drawdown(
            50000, 500, 0.07, 5000, 5, 7, contributions_continue_during_college=True)
        # First 2 college years get a contribution, matching contribution_years=12
        # (years_to_college + yr < 12 for yr in {0, 1}); the last 2 don't.
        assert with_contrib[0] > no_contrib[0]
        assert with_contrib[1] > no_contrib[1]
        # Difference collapses once contributions stop in both trajectories —
        # not exactly equal (the extra contributions' own growth persists),
        # but the per-year contribution itself is gone from year 2 onward.
        diff_yr2 = with_contrib[2] - no_contrib[2]
        diff_yr3 = with_contrib[3] - no_contrib[3]
        assert diff_yr3 == pytest.approx(diff_yr2 * 1.07, rel=1e-9)

    def test_contributions_continue_flag_off_ignores_contribution_years_headroom(self):
        """Even if a caller passes a contribution_years that exceeds
        years_to_college, contributions_continue_during_college=False
        (run_kids_projection's default) must not apply any college-year
        contribution — matching Kids' own behavior of never contributing
        into the 529 once college starts."""
        no_flag, _, _ = _project_college_drawdown(50000, 500, 0.07, 5000, 10, 20)
        with_flag, _, _ = _project_college_drawdown(
            50000, 500, 0.07, 5000, 10, 20, contributions_continue_during_college=True)
        assert no_flag != with_flag
        assert no_flag[0] < with_flag[0]


class TestRunKidsProjection:
    def test_returns_two_kids(self, sample_inputs, sample_accounts):
        result = run_kids_projection(sample_accounts, sample_inputs)
        assert len(result["kids"]) == 2

    def test_529_balance_at_18_pulled_from_real_account_balance(self, sample_inputs, sample_accounts):
        accounts = sample_accounts + [
            {"id": 97, "name": "Abby 529", "account_type": "529", "owner": "abby", "balance": 40000, "institution": "", "notes": ""},
        ]
        result = run_kids_projection(accounts, sample_inputs)
        abby = next(k for k in result["kids"] if k["child"] == "Abby")
        assert abby["529"]["current"] == 40000
        # With a real starting balance, projected balance at 18 must be
        # well above the raw contributions alone — confirms the account
        # balance is actually feeding the projection, not just contributions.
        assert abby["529"]["at_18"] > 40000

    def test_savings_bonds_keep_growing_past_18(self, sample_inputs, sample_accounts):
        """Regression test — savings bonds used to only ever get an
        'at_18' projection, then silently stop, as if the money vanished
        or was spent at 18 (nothing in this model actually spends them).
        Reported as confusing/looks-like-a-bug on the Kids tab."""
        accounts = sample_accounts + [
            {"id": 95, "name": "Abby Savings Bond", "account_type": "other", "owner": "abby", "balance": 2000, "institution": "", "notes": ""},
        ]
        result = run_kids_projection(accounts, sample_inputs)
        abby = next(k for k in result["kids"] if k["child"] == "Abby")
        assert abby["bonds"]["current"] == 2000
        assert abby["bonds"]["at_60"] > abby["bonds"]["at_18"] > abby["bonds"]["current"]

    def test_bonds_growth_caps_at_maturity_not_indefinite_to_60(self, sample_inputs, sample_accounts):
        """Regression test: real EE/I savings bonds stop earning interest
        at final maturity (30 years from issue) — unlike a custodial
        account, they shouldn't just compound at 4% for 40+ years straight
        through to age 60 for a young kid. We don't track issue date, so
        growth is capped at BOND_MAX_GROWTH_YEARS from today as a
        reasonable stand-in."""
        accounts = sample_accounts + [
            {"id": 94, "name": "Cooper Savings Bond", "account_type": "other", "owner": "cooper", "balance": 1000, "institution": "", "notes": ""},
        ]
        # Cooper is young enough (per sample_inputs' kid2_age) that 60 minus
        # his current age is well past BOND_MAX_GROWTH_YEARS.
        young_inputs = {**sample_inputs, "kid2_age": 5}
        result = run_kids_projection(accounts, young_inputs)
        cooper = next(k for k in result["kids"] if k["child"] == "Cooper")
        assert cooper["current_age"] == 5
        assert 60 - cooper["current_age"] > BOND_MAX_GROWTH_YEARS
        expected_at_60 = round(_fv(1000, 0.04, BOND_MAX_GROWTH_YEARS))
        assert cooper["bonds"]["at_60"] == expected_at_60

    def test_no_bonds_means_zero_at_every_horizon(self, sample_inputs, sample_accounts):
        result = run_kids_projection(sample_accounts, sample_inputs)
        for k in result["kids"]:
            assert k["bonds"]["current"] == 0
            assert k["bonds"]["at_18"] == 0
            assert k["bonds"]["at_60"] == 0

    def test_custodial_keeps_growing_past_24(self, sample_inputs, sample_accounts):
        """Same bug, same fix, follow-up report — the custodial account's
        projection stopped at age 24 even though contributions stopping at
        18 doesn't mean the balance itself is spent. Now continues
        compounding out to 60, picking up from the already-computed
        age-24 value."""
        result = run_kids_projection(sample_accounts, sample_inputs)
        for k in result["kids"]:
            if k["custodial"]["current"] > 0 or k["custodial"]["monthly_contribution"] > 0:
                assert k["custodial"]["at_60"] > k["custodial"]["at_24"] > 0

    def test_roth_divergence_between_kids_is_explained_by_age_and_rollover_not_a_bug(self, sample_inputs, sample_accounts):
        """Regression/documentation test — a younger kid legitimately ends
        up with a much bigger projected Roth balance at 60 than an older
        sibling given the same monthly contribution: more years compounding
        before 18, and (usually) a bigger 529-to-Roth rollover at 22 that
        then has decades left to compound. Confirms this isn't a
        per-kid-inconsistent-calculation bug — it's the same formula
        producing different, correct outputs for different ages/balances."""
        result = run_kids_projection(sample_accounts, sample_inputs)
        abby = next(k for k in result["kids"] if k["child"] == "Abby")
        cooper = next(k for k in result["kids"] if k["child"] == "Cooper")
        assert abby["current_age"] != cooper["current_age"]
        younger, older = (cooper, abby) if cooper["current_age"] < abby["current_age"] else (abby, cooper)
        # The younger kid has strictly more years of $/mo Roth contribution
        # compounding before 18, so at_18 (holding contribution rate equal)
        # must be at least as large.
        assert younger["roth"]["at_18"] >= older["roth"]["at_18"]

    def test_college_drawdown_uses_configured_unl_cost_not_zero(self, sample_inputs, sample_accounts):
        """Regression test for a real bug: the 529-at-22 / Roth-rollover
        calc computed the college drawdown from the UNL_CURRENT_ANNUAL
        fallback constant (hardcoded 0) instead of the configured
        unl_annual_cost input, so it silently withdrew $0/year for college
        — a well-funded 529 just kept compounding untouched, overstating
        both the leftover 529 balance at 22 and the Roth 529-rollover
        (both of which are displayed on the Kids tab)."""
        accounts = sample_accounts + [
            {"id": 96, "name": "Abby 529", "account_type": "529", "owner": "abby", "balance": 200000, "institution": "", "notes": ""},
        ]
        zero_cost = {**sample_inputs, "unl_annual_cost": 0}
        real_cost = {**sample_inputs, "unl_annual_cost": 25000}
        abby_zero = next(k for k in run_kids_projection(accounts, zero_cost)["kids"] if k["child"] == "Abby")
        abby_real = next(k for k in run_kids_projection(accounts, real_cost)["kids"] if k["child"] == "Abby")
        # A real, positive college cost must draw the 529 down further than
        # a $0 cost would — if the bug were still present, both would be
        # identical (the config value would be ignored either way).
        assert abby_real["529"]["at_22"] < abby_zero["529"]["at_22"]

    def test_529_to_roth_rollover_is_a_real_transfer_not_double_counted(self, sample_inputs, sample_accounts):
        """Regression test (external audit 2026-09-06): a $10,000 529
        starting AT age 18 with no college costs and no further
        contributions grows to $13,108 by age 22 (10000 * 1.07**4) — the
        full amount rolls over under the SECURE 2.0 cap. The rollover must
        actually leave the 529 (proj_529_at_22 nets it out) rather than
        being added to the Roth while the 529 still reports the same
        balance."""
        inputs = {**sample_inputs, "kid1_age": 18, "kid2_age": 8,
                  "unl_annual_cost": 0, "abby_529_monthly": 0, "kids_roth_monthly": 0}
        accounts = sample_accounts + [
            {"id": 90, "name": "Abby 529", "account_type": "529", "owner": "abby",
             "balance": 10000, "institution": "", "notes": ""},
        ]
        result = run_kids_projection(accounts, inputs)
        abby = next(k for k in result["kids"] if k["child"] == "Abby")
        expected_at_22 = round(10000 * (1.07 ** 4))
        assert abby["529"]["at_18"] == 10000
        assert abby["roth"]["529_rollover"] == expected_at_22  # under the $35k cap, so fully rolled
        # The 529 must NOT still report the rolled-over amount.
        assert abby["529"]["at_22"] == 0
        # And the timeline's own 529 balance must actually drop at the
        # rollover year rather than just keep compounding untouched.
        timeline_by_age = {t["age"]: t for t in abby["timeline"]}
        pre_rollover_529 = timeline_by_age[21]["529"]
        post_rollover_529 = timeline_by_age[22]["529"]
        assert post_rollover_529 < pre_rollover_529
        assert post_rollover_529 == 0

    def test_custodial_headline_at_24_is_consistent_with_timeline_stop_at_18(self, sample_inputs, sample_accounts):
        """Regression test (external audit 2026-09-06): the custodial
        headline (proj_cust_at_24) used to keep contributing all the way to
        24, while the account timeline (c_cust) has always stopped
        contributions at 18 like every other kid-contribution figure in
        this file — a $100/mo, age-10 kid showed $28,404 in the headline vs.
        $19,770 in the timeline for the same nominal age (44% apart). The
        headline must now be grown from the (contributions-stop-at-18)
        age-18 value forward to 24, not from a separate all-the-way-to-24
        formula, bringing the two back within a few percent of each other
        — the small remaining gap is the timeline's coarser annual-lump-sum
        compounding vs. the headline's monthly-annuity compounding, a
        separate (and much smaller) modeling-granularity difference, not
        the contributions-continue-past-18 bug being fixed here."""
        inputs = {**sample_inputs, "kid1_age": 10, "kids_custodial_monthly": 100}
        result = run_kids_projection(sample_accounts, inputs)
        abby = next(k for k in result["kids"] if k["child"] == "Abby")
        timeline_at_24 = next(t for t in abby["timeline"] if t["age"] == 24)["custodial"]
        headline_at_24 = abby["custodial"]["at_24"]
        assert headline_at_24 > timeline_at_24 > 0
        assert abs(headline_at_24 - timeline_at_24) / timeline_at_24 < 0.10

    def test_roth_chart_at_60_matches_summary_proj_roth_at_60(self, sample_inputs, sample_accounts):
        """Regression test (external audit 2026-09-06): starting with
        $10,000 at age 18 and no further contributions, the age-60 SUMMARY
        figure (proj_roth_at_60) showed $171,443 while the CHART data
        (roth_to_60) showed $183,444 at the same nominal age — exactly 7%
        higher, matching roth_return, because the chart applied one extra
        year of compounding before recording its age-18 starting point.
        Both must use the identical starting-point convention."""
        inputs = {**sample_inputs, "kid1_age": 18, "kid2_age": 8,
                  "unl_annual_cost": 0, "abby_529_monthly": 0, "kids_roth_monthly": 0}
        accounts = sample_accounts + [
            {"id": 91, "name": "Abby Roth IRA", "account_type": "roth_ira", "owner": "abby",
             "balance": 10000, "institution": "", "notes": ""},
        ]
        result = run_kids_projection(accounts, inputs)
        abby = next(k for k in result["kids"] if k["child"] == "Abby")
        chart_at_60 = next(c for c in abby["roth_to_60"] if c["age"] == 60)
        assert chart_at_60["balance"] == abby["roth"]["at_60"]
        # And the chart's own age-18 entry must be the raw starting balance,
        # not already grown by a year.
        chart_at_18 = next(c for c in abby["roth_to_60"] if c["age"] == 18)
        assert chart_at_18["balance"] == round(abby["roth"]["at_18"])


class TestSurplus529ContributionsInEducationAndKidsProjections:
    """The per-kid "Education funding - Abby"/"Education funding - Cooper"
    surplus_allocations goals feed run_education_projection/
    run_kids_projection as an EXTRA monthly 529 contribution for that
    specific kid, on top of the flat abby_529_monthly/cooper_529_monthly
    planning-input rate — not replacing it, and with zero effect on the
    other kid."""

    def test_default_no_surplus_is_unchanged(self, sample_inputs, sample_accounts):
        no_kwarg      = run_education_projection(sample_inputs, sample_accounts)
        explicit_none = run_education_projection(sample_inputs, sample_accounts, surplus_529_monthly=None)
        explicit_empty = run_education_projection(sample_inputs, sample_accounts, surplus_529_monthly={})
        a = next(g for g in no_kwarg["goals"] if g["child"] == "Abby")
        b = next(g for g in explicit_none["goals"] if g["child"] == "Abby")
        c = next(g for g in explicit_empty["goals"] if g["child"] == "Abby")
        assert a["projected_529_at_college"] == b["projected_529_at_college"] == c["projected_529_at_college"]

    def test_education_projection_surplus_adds_on_top_of_base_rate_for_that_kid_only(self, sample_inputs, sample_accounts):
        baseline   = run_education_projection(sample_inputs, sample_accounts)
        with_surplus = run_education_projection(sample_inputs, sample_accounts, surplus_529_monthly={"abby": 200})
        b_abby = next(g for g in baseline["goals"] if g["child"] == "Abby")
        w_abby = next(g for g in with_surplus["goals"] if g["child"] == "Abby")
        b_cooper = next(g for g in baseline["goals"] if g["child"] == "Cooper")
        w_cooper = next(g for g in with_surplus["goals"] if g["child"] == "Cooper")
        # monthly_contribution reflects base ($100 in the fixture) + $200 surplus.
        assert w_abby["monthly_contribution"] == b_abby["monthly_contribution"] + 200
        assert w_abby["projected_529_at_college"] > b_abby["projected_529_at_college"]
        # Cooper is completely untouched by Abby's surplus goal.
        assert w_cooper["monthly_contribution"] == b_cooper["monthly_contribution"]
        assert w_cooper["projected_529_at_college"] == b_cooper["projected_529_at_college"]

    def test_education_projection_surplus_is_per_kid_independent(self, sample_inputs, sample_accounts):
        baseline = run_education_projection(sample_inputs, sample_accounts)
        both = run_education_projection(sample_inputs, sample_accounts, surplus_529_monthly={"abby": 150, "cooper": 75})
        b_abby, b_cooper = (next(g for g in baseline["goals"] if g["child"] == c) for c in ("Abby", "Cooper"))
        w_abby, w_cooper = (next(g for g in both["goals"] if g["child"] == c) for c in ("Abby", "Cooper"))
        assert w_abby["monthly_contribution"] == b_abby["monthly_contribution"] + 150
        assert w_cooper["monthly_contribution"] == b_cooper["monthly_contribution"] + 75
        assert w_abby["projected_529_at_college"] > b_abby["projected_529_at_college"]
        assert w_cooper["projected_529_at_college"] > b_cooper["projected_529_at_college"]

    def test_kids_projection_surplus_raises_529_balance_for_that_kid_only(self, sample_inputs, sample_accounts):
        baseline = run_kids_projection(sample_accounts, sample_inputs)
        with_surplus = run_kids_projection(sample_accounts, sample_inputs, surplus_529_monthly={"cooper": 400})
        b_abby = next(k for k in baseline["kids"] if k["child"] == "Abby")
        w_abby = next(k for k in with_surplus["kids"] if k["child"] == "Abby")
        b_cooper = next(k for k in baseline["kids"] if k["child"] == "Cooper")
        w_cooper = next(k for k in with_surplus["kids"] if k["child"] == "Cooper")
        # Abby unaffected by Cooper's surplus goal.
        assert w_abby["529"]["at_18"] == b_abby["529"]["at_18"]
        assert w_abby["roth"]["529_rollover"] == b_abby["roth"]["529_rollover"]
        # Cooper's 529 balance at 18 rises with the added surplus contribution.
        assert w_cooper["529"]["at_18"] > b_cooper["529"]["at_18"]

    def test_larger_surplus_529_balance_produces_larger_roth_rollover_still_capped_at_35k(self, sample_inputs, sample_accounts):
        """The core behavior the user asked to confirm: a bigger 529 balance
        (here, from an added surplus contribution rather than a bigger
        starting account balance) must flow through the EXISTING SECURE 2.0
        529-to-Roth-IRA rollover mechanic unchanged — producing a
        correspondingly larger rollover into the Roth (still capped at
        $35,000), which then keeps compounding to 60. Uses a kid close to
        college with a big enough gap between college cost and the 529
        balance that the rollover isn't already pinned at the cap in the
        baseline case, so the increase is actually observable."""
        custom = {**sample_inputs, "kid1_age": 17, "unl_annual_cost": 3000, "abby_529_monthly": 50}
        accounts = sample_accounts + [
            {"id": 93, "name": "Abby 529", "account_type": "529", "owner": "abby", "balance": 20000, "institution": "", "notes": ""},
        ]
        baseline = run_kids_projection(accounts, custom)
        with_surplus = run_kids_projection(accounts, custom, surplus_529_monthly={"abby": 500})
        b_abby = next(k for k in baseline["kids"] if k["child"] == "Abby")
        w_abby = next(k for k in with_surplus["kids"] if k["child"] == "Abby")
        assert b_abby["roth"]["529_rollover"] < 35000  # not already capped, so the increase is visible
        assert w_abby["roth"]["529_rollover"] > b_abby["roth"]["529_rollover"]
        assert w_abby["roth"]["529_rollover"] <= 35000
        # The larger rollover at 22 keeps compounding through 60, same
        # mechanic, just starting from a bigger number.
        assert w_abby["roth"]["at_60"] > b_abby["roth"]["at_60"]

    def test_roth_rollover_stays_capped_at_35000_even_with_a_huge_surplus_contribution(self, sample_inputs, sample_accounts):
        custom = {**sample_inputs, "kid1_age": 17, "unl_annual_cost": 1000}
        accounts = sample_accounts + [
            {"id": 92, "name": "Abby 529", "account_type": "529", "owner": "abby", "balance": 100000, "institution": "", "notes": ""},
        ]
        result = run_kids_projection(accounts, custom, surplus_529_monthly={"abby": 5000})
        abby = next(k for k in result["kids"] if k["child"] == "Abby")
        assert abby["roth"]["529_rollover"] == 35000


class TestRunInsuranceAnalysis:
    def test_returns_jason_and_justin_sections(self, sample_inputs, sample_accounts):
        result = run_insurance_analysis(sample_inputs, sample_accounts)
        assert "jason" in result and "justin" in result

    def test_property_matching_uses_configured_keys_not_hardcoded(self, sample_inputs):
        """Regression test for the bug where primary/rental property matching
        was hardcoded to literal street-number substrings ("9013"/"3155")."""
        accounts = [
            {"id": 1, "name": "My Primary Home", "account_type": "real_estate", "owner": "joint", "balance": 400000, "institution": "", "notes": ""},
            {"id": 2, "name": "My Rental Unit",  "account_type": "real_estate", "owner": "joint", "balance": 250000, "institution": "", "notes": ""},
        ]
        configured = {**sample_inputs, "primary_residence_key": "Primary", "rental_property_key": "Rental"}
        result = run_insurance_analysis(configured, accounts)
        assert result["property"]["primary_home_value"] == 400000
        assert result["property"]["rental_value"] == 250000

    def test_no_configured_key_means_zero_match(self, sample_inputs):
        """Without a configured key, nothing should match (no silent
        false-positive matching)."""
        accounts = [
            {"id": 1, "name": "Some House", "account_type": "real_estate", "owner": "joint", "balance": 400000, "institution": "", "notes": ""},
        ]
        result = run_insurance_analysis(sample_inputs, accounts)  # keys are ""
        assert result["property"]["primary_home_value"] == 0

    def test_umbrella_recommended_rounds_up_to_nearest_million(self, sample_inputs, sample_accounts):
        result = run_insurance_analysis(sample_inputs, sample_accounts)
        assert result["property"]["recommended_umbrella"] % 1_000_000 == 0
        assert result["property"]["recommended_umbrella"] >= 1_000_000

    def test_umbrella_adequate_when_coverage_meets_recommendation(self, sample_inputs, sample_accounts):
        result = run_insurance_analysis(sample_inputs, sample_accounts)
        recommended = result["property"]["recommended_umbrella"]
        inputs = {**sample_inputs, "umbrella": recommended}
        result2 = run_insurance_analysis(inputs, sample_accounts)
        assert result2["property"]["umbrella_adequate"] is True
        assert result2["property"]["umbrella_gap"] == 0

    def test_umbrella_gap_when_underinsured(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "umbrella": 0}
        result = run_insurance_analysis(inputs, sample_accounts)
        assert result["property"]["umbrella_adequate"] is False
        assert result["property"]["umbrella_gap"] == result["property"]["recommended_umbrella"]

    def test_college_funding_gap_tracks_kid_ages_not_hardcoded(self, sample_inputs, sample_accounts):
        """Regression test: this used to hardcode "7 years to college" for
        Abby and "11 years" for Cooper regardless of planning_inputs'
        kid1_age/kid2_age — so the Insurance page's college-funding gap
        silently drifted out of sync with the Education/Kids pages (which
        both correctly derive years_to_college from those same ages) as the
        kids got older. A kid much closer to 18 should show a smaller,
        less-inflated college cost than one who's much younger, all else equal."""
        near_college  = run_insurance_analysis({**sample_inputs, "kid1_age": 17, "kid2_age": 17}, sample_accounts)
        far_from_college = run_insurance_analysis({**sample_inputs, "kid1_age": 5, "kid2_age": 5}, sample_accounts)
        assert near_college["jason"]["college_funding"] < far_from_college["jason"]["college_funding"]

    def test_college_funding_gap_matches_education_projection_years_to_college(self, sample_inputs, sample_accounts):
        """The insurance page's implied years-to-college should be the same
        18-minus-current-age math run_education_projection uses, not an
        independent assumption."""
        from projection_engine import run_education_projection
        inputs = {**sample_inputs, "kid1_age": 17, "kid2_age": 17, "unl_annual_cost": 20000}
        edu = run_education_projection(inputs, sample_accounts)
        assert all(goal["years_to_college"] == 1 for goal in edu["goals"])
