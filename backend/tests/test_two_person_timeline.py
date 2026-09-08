"""
Independent reference tests for timeline_engine.build_two_person_timeline
/ TwoPersonTimeline — hand-calculated expected values, not derived by
running the code first (same standard as test_timeline_engine.py and
test_annual_engine_reference.py).

Written BEFORE build_two_person_timeline exists (backend/docs/
TWO_DIMENSIONAL_RETIREMENT_DESIGN.md section 7, 2026-09-08) — this file
is expected to fail at import/collection until that function and
TwoPersonTimeline are implemented in timeline_engine.py. It specifies the
timeline contract independently of the implementation, per the explicit
instruction this branch was scoped under.
"""

import pytest

from timeline_engine import build_two_person_timeline


class TestPhaseStartYears:
    def test_jason_retires_first(self):
        # jason_years_to_retire=1, justin_years_to_retire=3 -> phase2
        # starts at the earlier (1), phase3 at the later (3).
        t = build_two_person_timeline(jason_age=60, justin_age=60, jason_ret_age=61, justin_ret_age=63)
        assert t.jason_years_to_retire == 1
        assert t.justin_years_to_retire == 3
        assert t.phase2_start_years == 1
        assert t.phase3_start_years == 3
        assert t.later_retiree == "justin"

    def test_justin_retires_first(self):
        t = build_two_person_timeline(jason_age=60, justin_age=60, jason_ret_age=63, justin_ret_age=61)
        assert t.jason_years_to_retire == 3
        assert t.justin_years_to_retire == 1
        assert t.phase2_start_years == 1
        assert t.phase3_start_years == 3
        assert t.later_retiree == "jason"

    def test_simultaneous_retirement_has_no_phase2(self):
        t = build_two_person_timeline(jason_age=60, justin_age=60, jason_ret_age=62, justin_ret_age=62)
        assert t.phase2_start_years == t.phase3_start_years == 2
        assert t.later_retiree is None

    def test_unequal_current_ages(self):
        # jason 65 (2 yrs to 67), justin 55 (5 yrs to 60) -- age_gap=10.
        t = build_two_person_timeline(jason_age=65, justin_age=55, jason_ret_age=67, justin_ret_age=60)
        assert t.age_gap == 10
        assert t.jason_years_to_retire == 2
        assert t.justin_years_to_retire == 5
        assert t.phase2_start_years == 2
        assert t.phase3_start_years == 5
        assert t.later_retiree == "justin"
        # Justin's age in the same calendar year Jason turns 70 (phase3
        # start): 65 + 5 years elapsed - 10 age_gap = 60, matching his
        # own selected retirement age exactly.
        assert t.justin_age_at(70) == 60

    def test_already_past_retirement_age_clamps_to_zero_for_that_person_only(self):
        # Jason 68, selected ret_age 65 (3 years already past) -> 0, not
        # negative. Justin unaffected, still has a real runway.
        t = build_two_person_timeline(jason_age=68, justin_age=60, jason_ret_age=65, justin_ret_age=64)
        assert t.jason_years_to_retire == 0
        assert t.justin_years_to_retire == 4
        assert t.phase2_start_years == 0   # withdrawal begins immediately, this year
        assert t.phase3_start_years == 4
        assert t.later_retiree == "justin"

    def test_both_already_past_retirement_age(self):
        t = build_two_person_timeline(jason_age=70, justin_age=68, jason_ret_age=60, justin_ret_age=62)
        assert t.jason_years_to_retire == 0
        assert t.justin_years_to_retire == 0
        assert t.phase2_start_years == 0
        assert t.phase3_start_years == 0
        assert t.later_retiree is None  # both clamp to 0 -- no real middle phase


class TestEndAgeAndRetireYrs:
    def test_end_age_anchored_to_phase2_start_not_phase3(self):
        t = build_two_person_timeline(jason_age=60, justin_age=60, jason_ret_age=61, justin_ret_age=63,
                                       retirement_end_age=64)
        assert t.end_age == 64
        # retire_yrs counts from phase2_start_age (61), not phase3 (63) --
        # the loop must cover the middle phase too.
        phase2_start_age = 60 + t.phase2_start_years
        assert phase2_start_age == 61
        assert t.retire_yrs == 64 - 61 == 3

    def test_end_age_floor_guards_against_a_horizon_before_phase2_even_starts(self):
        # Mirrors build_timeline's own guardrail: end_age is never less
        # than phase2_start_age + 1, even if retirement_end_age was typo'd
        # below it.
        t = build_two_person_timeline(jason_age=60, justin_age=60, jason_ret_age=65, justin_ret_age=65,
                                       retirement_end_age=63)
        phase2_start_age = 60 + t.phase2_start_years
        assert t.end_age == phase2_start_age + 1

    def test_default_end_age_is_99_when_unset(self):
        t = build_two_person_timeline(jason_age=60, justin_age=60, jason_ret_age=65, justin_ret_age=65)
        assert t.end_age == 99


class TestAgeHelpers:
    def test_age_is_jason_age_at_phase2_relative_year(self):
        t = build_two_person_timeline(jason_age=60, justin_age=58, jason_ret_age=62, justin_ret_age=65,
                                       retirement_end_age=70)
        assert t.age(0) == 62   # phase2_start_age
        assert t.age(3) == 65

    def test_in_phase2_true_before_phase3_start_false_at_and_after(self):
        t = build_two_person_timeline(jason_age=60, justin_age=60, jason_ret_age=61, justin_ret_age=63,
                                       retirement_end_age=65)
        assert t.in_phase2(61) is True
        assert t.in_phase2(62) is True
        assert t.in_phase2(63) is False   # phase3 starts here
        assert t.in_phase2(64) is False

    def test_in_phase2_always_false_when_simultaneous(self):
        t = build_two_person_timeline(jason_age=60, justin_age=60, jason_ret_age=62, justin_ret_age=62,
                                       retirement_end_age=65)
        assert t.in_phase2(62) is False
        assert t.in_phase2(63) is False
