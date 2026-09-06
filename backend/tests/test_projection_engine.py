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
    _fv, _fv_annuity, _fv_growing_annuity, _pv_annuity,
    PARENT_RETIREMENT_AGE_ASSUMPTION,
    BOND_MAX_GROWTH_YEARS,
    CURRENT_YEAR,
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
        should adjust income_need for the 3 years it's active
        (CURRENT_YEAR+11, +12, +13) and stop by CURRENT_YEAR+14 — i.e. the
        duration ends partway through what would be its 4th active year."""
        event_year = CURRENT_YEAR + 11
        events = [{"event_year": event_year, "one_time_cash_delta": 0,
                   "monthly_cash_flow_delta": 1000, "duration_months": 30}]
        result = run_retirement_projection(sample_inputs, sample_accounts, ret_ages=[60], life_events=events)
        scenario = next(s for s in result["scenarios"] if s["ss_timing"] == "early")
        by_year = {y["year"]: y for y in scenario["yearly_detail"]}
        for active_year in (event_year, event_year + 1, event_year + 2):
            assert by_year[active_year]["life_event_monthly_adjustment"] == 12000
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
