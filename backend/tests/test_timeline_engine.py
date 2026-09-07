"""
Independent reference tests for timeline_engine.py — hand-calculated
expected values, not derived by running the code first (same standard as
test_annual_engine_reference.py). This module centralizes exactly the
fields the consolidation task named: effective start age, retirement
year, end age, spouse age offsets, claiming years, inflation index, and
event years.
"""

import pytest

from projection_engine import CURRENT_YEAR
from timeline_engine import build_cumulative_inflation, build_timeline


class TestBuildTimelineNormalCase:
    """ret_age in the future (>= jason_age) — effective_start_age should
    just equal ret_age, reducing to what every consumer already did
    before this module existed."""

    def test_effective_start_age_equals_ret_age(self):
        t = build_timeline(jason_age=50, justin_age=48, ret_age=60, retirement_end_age=95)
        assert t.effective_start_age == 60
        assert t.requested_ret_age == 60

    def test_retirement_year_is_current_year_plus_years_to_retire(self):
        t = build_timeline(jason_age=50, justin_age=48, ret_age=60, retirement_end_age=95)
        assert t.retirement_year == CURRENT_YEAR + 10

    def test_end_age_and_retire_yrs(self):
        t = build_timeline(jason_age=50, justin_age=48, ret_age=60, retirement_end_age=95)
        assert t.end_age == 95
        assert t.retire_yrs == 35

    def test_age_gap_positive_when_jason_older(self):
        t = build_timeline(jason_age=50, justin_age=45, ret_age=60)
        assert t.age_gap == 5

    def test_age_gap_negative_when_justin_older(self):
        t = build_timeline(jason_age=50, justin_age=55, ret_age=60)
        assert t.age_gap == -5

    def test_age_helper(self):
        t = build_timeline(jason_age=50, justin_age=48, ret_age=60, retirement_end_age=95)
        assert t.age(0) == 60
        assert t.age(5) == 65

    def test_justin_age_at(self):
        t = build_timeline(jason_age=50, justin_age=45, ret_age=60)  # age_gap = 5
        # Jason age 62 -> Justin is 5 years younger -> 57
        assert t.justin_age_at(62) == 57

    def test_calendar_year(self):
        t = build_timeline(jason_age=50, justin_age=48, ret_age=60, retirement_end_age=95)
        assert t.calendar_year(0) == CURRENT_YEAR + 10
        assert t.calendar_year(3) == CURRENT_YEAR + 13

    def test_claim_year_index_future_claim(self):
        t = build_timeline(jason_age=50, justin_age=48, ret_age=60, retirement_end_age=95)
        # Claims at 62, retires at 60 -> claim happens 2 years INTO retirement
        assert t.claim_year_index(62) == 2

    def test_claim_year_index_already_claiming_at_start(self):
        t = build_timeline(jason_age=50, justin_age=48, ret_age=65, retirement_end_age=95)
        # Claims at 62, retires at 65 -> already claiming when the loop starts
        assert t.claim_year_index(62) == 0

    def test_pre_start_cola_is_noop_for_future_claim(self):
        t = build_timeline(jason_age=50, justin_age=48, ret_age=60, retirement_end_age=95)
        assert t.pre_start_cola(claim_age=62, inflation=0.03) == 1.0

    def test_pre_start_cola_for_already_claiming(self):
        t = build_timeline(jason_age=50, justin_age=48, ret_age=65, retirement_end_age=95)
        # Claimed at 62, loop starts at 65 -> 3 years of COLA already accrued
        assert t.pre_start_cola(claim_age=62, inflation=0.03) == pytest.approx(1.03 ** 3)


class TestBuildTimelinePastRetirementAge:
    """ret_age already behind the household's actual current age -- the
    exact case the third independent review found broken across 5
    consumers (2026-09-07)."""

    def test_effective_start_age_uses_current_age_not_selected_age(self):
        t = build_timeline(jason_age=65, justin_age=65, ret_age=55, retirement_end_age=69)
        assert t.effective_start_age == 65
        assert t.requested_ret_age == 55  # preserved for policy use

    def test_retirement_year_is_today_not_a_decade_ago(self):
        t = build_timeline(jason_age=65, justin_age=65, ret_age=55, retirement_end_age=69)
        assert t.retirement_year == CURRENT_YEAR

    def test_retire_yrs_is_the_real_remaining_horizon(self):
        t = build_timeline(jason_age=65, justin_age=65, ret_age=55, retirement_end_age=69)
        # 69 - 65 = 4 real years left, not 69 - 55 = 14
        assert t.retire_yrs == 4
        assert t.end_age == 69

    def test_produces_the_same_effective_timeline_as_selecting_the_current_age_directly(self):
        """requested_ret_age is deliberately preserved as-selected (55 vs
        65) for policy use elsewhere — every OTHER, effective field must
        agree between the two selections."""
        past = build_timeline(jason_age=65, justin_age=65, ret_age=55, retirement_end_age=69)
        current = build_timeline(jason_age=65, justin_age=65, ret_age=65, retirement_end_age=69)
        assert past.effective_start_age == current.effective_start_age
        assert past.retirement_year == current.retirement_year
        assert past.end_age == current.end_age
        assert past.retire_yrs == current.retire_yrs
        assert past.age_gap == current.age_gap


class TestBuildCumulativeInflation:
    def test_length_is_retire_yrs_plus_one(self):
        cum = build_cumulative_inflation(0.03, retire_yrs=5)
        assert len(cum) == 6

    def test_first_entry_is_today_s_dollars(self):
        cum = build_cumulative_inflation(0.03, retire_yrs=5)
        assert cum[0] == 1.0

    def test_constant_rate_matches_naive_power_formula(self):
        cum = build_cumulative_inflation(0.03, retire_yrs=4)
        for yr in range(5):
            assert cum[yr] == pytest.approx(1.03 ** yr)

    def test_variable_rate_accumulates_rather_than_retroactively_erasing_history(self):
        """Year 0 at 2x the base rate, year 1 back to 1x -- year 1's
        cumulative factor must still reflect year 0's higher rate, not
        recompute as if the whole history had been at the lower rate."""
        cum = build_cumulative_inflation(0.03, retire_yrs=2, inflation_mults=[2.0, 1.0])
        assert cum[1] == pytest.approx(1.06)          # one year at 6%
        assert cum[2] == pytest.approx(1.06 * 1.03)   # then one year at 3%
