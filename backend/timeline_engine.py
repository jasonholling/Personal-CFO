"""
Shared timeline normalizer for withdrawal-phase consumers.

Extracted per an explicit consolidation task item (2026-09-07, after
three rounds of independent review — the third found that
`run_retirement_projection`'s past-ret_age fix, `withdrawal_start_age =
max(ret_age, jason_age)`, had been made once in one function years ago
and never generalized to the 5 other withdrawal-phase consumers in
simulation_engine.py). Rather than re-fix that one symptom a fourth time
somewhere else, this module is the single place every consumer computes
its forward-looking timeline: effective start age, retirement year, end
age/horizon, spousal age offsets, claiming-year indices, a cumulative
inflation index, and calendar-year mapping for life events. `ret_age`
itself (the requested/selected age, as opposed to the effective start
age this module derives) is deliberately NOT owned here — callers keep
it for whatever genuinely age-selection-dependent POLICY they apply
(pension_for_age, the age-55 bridge-job branch, "what age did the user
pick" reporting fields) has always varied by consumer on purpose and
isn't a timeline question.
"""

from dataclasses import dataclass
from typing import List, Optional

# Canonical definition — moved here (from projection_engine.py) so this
# module has no dependency on projection_engine, letting
# projection_engine.py itself import build_timeline without a circular
# import. projection_engine.py/simulation_engine.py both still import
# CURRENT_YEAR "from projection_engine" unchanged elsewhere; Python
# re-exports it there via projection_engine's own `from timeline_engine
# import CURRENT_YEAR`, so no other call site needed to change.
CURRENT_YEAR = 2026


@dataclass(frozen=True)
class Timeline:
    requested_ret_age: int          # the age the caller/user selected (kept for policy use, not timeline math)
    jason_age: int                  # Jason's actual current age
    justin_age: int                 # Justin's actual current age
    effective_start_age: int        # max(requested_ret_age, jason_age) -- every forward-looking computation anchors here
    age_gap: int                    # jason_age - justin_age (positive: Jason older)
    retirement_year: int            # calendar year the withdrawal loop's yr=0 falls in
    end_age: int                    # exclusive upper bound (mortality/horizon), already floored/capped
    retire_yrs: int                 # end_age - effective_start_age; the withdrawal loop's actual length

    def age(self, yr: int) -> int:
        """Jason's age in withdrawal-loop-relative year `yr` (0-indexed,
        yr=0 is effective_start_age)."""
        return self.effective_start_age + yr

    def justin_age_at(self, jason_age_this_year: int) -> int:
        """Justin's age in the same calendar year Jason is
        `jason_age_this_year` -- age-gap-adjusted, not `age` reused
        directly (that off-by-the-age-gap bug was fixed independently in
        several consumers before this module existed; centralizing it
        here is what keeps it fixed everywhere at once)."""
        return jason_age_this_year - self.age_gap

    def calendar_year(self, yr: int) -> int:
        return self.retirement_year + yr

    def claim_year_index(self, claim_age: int) -> int:
        """Withdrawal-loop-relative year index (>=0) at which a benefit
        claimed at Jason-age-denominated `claim_age` first becomes
        active, clamped to 0 if claiming already happened before
        effective_start_age (see pre_start_cola for the COLA that
        accrued during those already-past years)."""
        return max(0, claim_age - self.effective_start_age)

    def pre_start_cola(self, claim_age: int, inflation: float) -> float:
        """COLA multiplier already accrued between `claim_age` and
        effective_start_age, for a benefit claimed before the loop
        starts (e.g. SS claimed at 62 while the household is actually 65
        today). 1.0 (no-op) whenever claim_age >= effective_start_age --
        reduces to every pre-existing deterministic formula
        (`(1+inflation)**max(0, age-claim_age)` evaluated at age ==
        effective_start_age) exactly."""
        return (1 + inflation) ** max(0, self.effective_start_age - claim_age)


def build_timeline(jason_age: int, justin_age: int, ret_age: int,
                    retirement_end_age: Optional[float] = None) -> Timeline:
    """The single source of truth for `effective_start_age = max(ret_age,
    jason_age)` and everything derived from it. Every withdrawal-phase
    consumer (run_retirement_projection, Monte Carlo/Stress via
    _run_single, SWR, Roth conversion, tax-efficiency) should build its
    timeline through this function instead of recomputing these fields
    inline."""
    effective_start_age = max(ret_age, jason_age)
    retirement_year = CURRENT_YEAR + (effective_start_age - jason_age)
    end_age = max(effective_start_age + 1, min(110, int(retirement_end_age or 99)))
    retire_yrs = end_age - effective_start_age
    return Timeline(
        requested_ret_age=ret_age,
        jason_age=jason_age,
        justin_age=justin_age,
        effective_start_age=effective_start_age,
        age_gap=jason_age - justin_age,
        retirement_year=retirement_year,
        end_age=end_age,
        retire_yrs=retire_yrs,
    )


@dataclass(frozen=True)
class TwoPersonTimeline:
    """Two independent retirement ages instead of Timeline's one --
    additive, doesn't touch Timeline/build_timeline above, so the 6
    existing withdrawal-phase consumers built on those are completely
    unaffected (backend/docs/TWO_DIMENSIONAL_RETIREMENT_DESIGN.md
    section 7.2, v1 scope: explicit ages for both spouses, Retirement
    Projection reference implementation only).

    A household passes through up to three phases: both working (not
    modeled here -- this timeline starts where withdrawal starts),
    ONE retired/one still working ("phase 2"), then both retired
    ("phase 3"). phase2_start_years is the earlier of the two spouses'
    own years-to-retirement (each already clamped to 0 if that spouse's
    selected age is already in the past, same per-person convention
    Timeline applies to Jason alone); phase3_start_years is the later.
    When the two ages are equal (or justin_ret_age falls back to
    Jason's date via the existing 0/unset sentinel), phase2_start_years
    == phase3_start_years and there is no middle phase at all -- the
    model reduces exactly to today's single-axis withdrawal loop
    starting at that age (the regression property the hand-calculated
    "simultaneous retirement" test checks numerically against
    run_retirement_projection's own output)."""
    jason_age: int
    justin_age: int
    jason_ret_age: int
    justin_ret_age: int
    age_gap: int                    # jason_age - justin_age
    jason_years_to_retire: int       # max(0, jason_ret_age - jason_age)
    justin_years_to_retire: int      # max(0, justin_ret_age - justin_age)
    phase2_start_years: int          # min(...) -- years from today until the FIRST spouse retires
    phase3_start_years: int          # max(...) -- years from today until BOTH have retired
    later_retiree: Optional[str]     # "jason" | "justin" | None (simultaneous -- no phase 2)
    retirement_year: int             # calendar year phase2 starts
    end_age: int                     # exclusive upper bound (Jason-age terms), already floored/capped
    retire_yrs: int                  # end_age - phase2_start_age; the withdrawal loop's actual length

    def age(self, yr: int) -> int:
        """Jason's age in withdrawal-loop-relative year `yr` (0-indexed,
        yr=0 is phase2_start_age -- NOT phase3_start_age, since the loop
        must cover the middle phase too)."""
        return self.jason_age + self.phase2_start_years + yr

    def justin_age_at(self, jason_age_this_year: int) -> int:
        return jason_age_this_year - self.age_gap

    def calendar_year(self, yr: int) -> int:
        return self.retirement_year + yr

    def in_phase2(self, jason_age_this_year: int) -> bool:
        """True for a year strictly before both spouses have retired --
        the still-working spouse's income should offset need this year.
        Always False when later_retiree is None (simultaneous
        retirement, zero-length phase 2)."""
        return jason_age_this_year < self.jason_age + self.phase3_start_years

    @property
    def jason_effective_start_age(self) -> int:
        """Jason's own real retirement start age -- max(jason_ret_age,
        jason_age), the same "already past" clamp every per-person field
        on this dataclass already applies, expressed as an absolute age
        (jason_years_to_retire is already clamped to 0 for an
        already-past selection, so this differs from jason_ret_age only
        in that case). Single source of truth for two independent uses
        in projection_engine.py's two-age withdrawal loop and its
        Monte Carlo/Stress Tests counterparts (CALCULATION_CONTRACT.md
        sections 20-22): Jason's own pension start, and the age-55
        bridge/kids spending phases' own year-zero anchor -- both must
        count elapsed years from HERE, not from the raw selected
        jason_ret_age, which can be years stale for a household already
        past it today."""
        return self.jason_age + self.jason_years_to_retire


def build_two_person_timeline(jason_age: int, justin_age: int, jason_ret_age: int, justin_ret_age: int,
                               retirement_end_age: Optional[float] = None) -> TwoPersonTimeline:
    """The single source of truth for two-dimensional retirement timing
    (v1: Retirement Projection reference implementation only -- see
    docs/TWO_DIMENSIONAL_RETIREMENT_DESIGN.md section 7). Mirrors
    build_timeline's per-person "already past this age" clamp
    (max(0, ret_age - age)), applied independently to each spouse rather
    than only to Jason."""
    jason_years_to_retire  = max(0, jason_ret_age - jason_age)
    justin_years_to_retire = max(0, justin_ret_age - justin_age)
    phase2_start_years = min(jason_years_to_retire, justin_years_to_retire)
    phase3_start_years = max(jason_years_to_retire, justin_years_to_retire)
    if jason_years_to_retire == justin_years_to_retire:
        later_retiree = None
    elif jason_years_to_retire > justin_years_to_retire:
        later_retiree = "jason"
    else:
        later_retiree = "justin"
    phase2_start_age = jason_age + phase2_start_years
    retirement_year = CURRENT_YEAR + phase2_start_years
    end_age = max(phase2_start_age + 1, min(110, int(retirement_end_age or 99)))
    retire_yrs = end_age - phase2_start_age
    return TwoPersonTimeline(
        jason_age=jason_age,
        justin_age=justin_age,
        jason_ret_age=jason_ret_age,
        justin_ret_age=justin_ret_age,
        age_gap=jason_age - justin_age,
        jason_years_to_retire=jason_years_to_retire,
        justin_years_to_retire=justin_years_to_retire,
        phase2_start_years=phase2_start_years,
        phase3_start_years=phase3_start_years,
        later_retiree=later_retiree,
        retirement_year=retirement_year,
        end_age=end_age,
        retire_yrs=retire_yrs,
    )


def healthcare_for_age(age: int, healthcare_pre: float, healthcare_post: float) -> float:
    """The pre/post-Medicare healthcare split every withdrawal-phase
    consumer applies identically: full cost before 65, the (typically
    lower) Medicare-supplement cost from 65 on. Extracted (consolidation
    follow-up, 2026-09-07, item 9's duplicate-formula inventory) from 5
    independent copies of this exact conditional across
    run_retirement_projection, _run_single, run_swr_analysis,
    run_roth_conversion_analysis, and run_tax_efficiency_simulation."""
    return healthcare_pre if age < 65 else healthcare_post


def build_cumulative_inflation(inflation: float, retire_yrs: int,
                                inflation_mults: Optional[List[float]] = None) -> List[float]:
    """cum_inflation[0] == 1.0 (today's/effective_start_age's dollars);
    cum_inflation[k] is the accumulated price-growth factor through the
    start of withdrawal-loop year k. Extracted from _run_single (Phase 4
    of the calculation-engine consolidation) -- the correct way to
    accumulate inflation when the rate can vary year to year (some
    stress scenarios deliberately do), since `(1+eff_inf)**yr` using
    THIS year's rate retroactively re-derives the entire price history
    instead of accumulating it, silently erasing compounding already
    banked from earlier, differently-rated years."""
    cum = [1.0]
    for k in range(retire_yrs):
        mult = inflation_mults[k] if inflation_mults and k < len(inflation_mults) else 1.0
        cum.append(cum[-1] * (1 + inflation * mult))
    return cum
