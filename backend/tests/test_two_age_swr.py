"""
Independent, hand/script-verified reference cases for two-age SWR --
written BEFORE jason_ret_age/justin_ret_age exist on run_swr_analysis
(backend/docs/CALCULATION_CONTRACT.md section 25, 2026-09-08, Milestone
1 of 4). This file is expected to fail at collection until that
implementation exists.

Verification strategy: SWR's own search result isn't a closed-form
value (it's the output of a binary search), so every boundary withdrawal
amount below was computed by directly driving the EXISTING, already-
reviewed _swr_year_step primitive (unmodified by this milestone) with a
deterministic per-year event_monthly sequence and its own 50-iteration
binary search -- ground truth derived from code that already existed
before this branch, not from the new implementation being tested. Every
value was then hand-verified by replaying _swr_year_step's own
arithmetic by hand (shown in each test's docstring) before being written
into an assertion.

Per section 25's contract: guaranteed income (pension/SS) does NOT fold
into the search's event_monthly and has no effect on the boundary
withdrawal at 0% tax (confirmed pre-existing behavior, not new for this
milestone) -- only the still-working spouse's phase2 income does, the
same event_monthly treatment the existing single-axis gap-income offset
already uses.

Covers every category named in the instruction: either retirement
order, income surpluses, depleted accounts, and a past retirement
selection, each verified with a deterministic (monkeypatched
random.gauss, 0% returns) target_success=1.0 search where the result is
a clean, hand-checkable dollar boundary -- and that a meaningfully
higher withdrawal fails the search, not just that some withdrawal
succeeds.
"""

import random as random_module

import pytest

from simulation_engine import run_swr_analysis, _swr_year_step

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


class TestSwrSearchBoundaryDeterministic:
    """monkeypatched random.gauss=0.0 + 0% returns makes every one of the
    1000 pre-generated trials identical, so success_at_withdrawal(x) is a
    clean step function between 100% and 0% right at the true boundary --
    target_success=1.0 makes the binary search converge to (within its
    own 20-iteration precision, far finer than $1) exactly that boundary."""

    def test_simultaneous_retirement_zero_returns(self, monkeypatch):
        """Both spouses 60, retiring together at 61 (1yr, no middle
        phase), $500,000 taxable, 0% growth/inflation, no guaranteed
        income, no gap income. _swr_year_step by hand: remaining =
        max(0, X-0) = X; taxable draw = min(X, 500000). Exactly funded
        (ending balance $0) at X=$500,000; X=$500,001 leaves $1 unmet."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(retirement_end_age=62)
        r = run_swr_analysis(inputs, TAXABLE(500000), jason_ret_age=61, justin_ret_age=61, target_success=1.0)
        assert r["safe_withdrawal_annual"] == 500000
        assert r["mode"] == "two_age"

    def test_justin_retires_first_jason_still_working(self, monkeypatch):
        """Justin retires at 61 (now), Jason at 63 (2 more years) --
        later_retiree=jason, his own $100,000 salary funds phase2's gap
        income ($65,000/yr at the 65% factor). $200,000 taxable, 3-year
        horizon (ages 61-63), 0% growth/inflation. Script-verified via
        _swr_year_step directly (event_monthly=[65000,65000,0]): the
        boundary is exactly $110,000 -- hand-checked below.

        yr0(age61): remaining=max(0,110000-65000)=45000; taxable draw
        45000 -> 200000-45000=155000.
        yr1(age62): same -> 155000-45000=110000.
        yr2(age63,phase3,event_monthly=0): remaining=110000; taxable
        draw 110000 -> ending balance exactly $0."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(retirement_end_age=64, w2_salary=100000)
        r = run_swr_analysis(inputs, TAXABLE(200000), jason_ret_age=63, justin_ret_age=61, target_success=1.0)
        assert r["safe_withdrawal_annual"] == 110000
        assert r["later_retiree"] == "jason"

    def test_jason_retires_first_justin_still_working(self, monkeypatch):
        """Mirror of the case above (Justin the still-working spouse) --
        proves the search is symmetric, not hardcoded to either spouse.
        Same numbers, same hand-verified $110,000 boundary."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(retirement_end_age=64, justin_w2_salary=100000)
        r = run_swr_analysis(inputs, TAXABLE(200000), jason_ret_age=61, justin_ret_age=63, target_success=1.0)
        assert r["safe_withdrawal_annual"] == 110000
        assert r["later_retiree"] == "justin"

    def test_income_surplus_during_phase2(self, monkeypatch):
        """Justin's salary $200,000 -> gap income $130,000/yr, well
        above any withdrawal this search would converge to -- the
        surplus each phase2 year is swept into the portfolio (the
        shared _swr_year_step's own existing surplus-sweep behavior,
        unmodified by this milestone). $10,000 taxable start, 4-year
        horizon (3 phase2 + 1 phase3), 0% growth/inflation.
        Script-verified boundary: exactly $100,000.

        yr0-2(phase2): event_monthly=130000 > 100000, so remaining=0
        every year; taxable grows by the $30,000/yr surplus:
        10000->40000->70000->100000.
        yr3(phase3): remaining=100000; taxable draw 100000 -> ending
        balance exactly $0."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(retirement_income_today_dollars=50000, retirement_end_age=65, justin_w2_salary=200000)
        r = run_swr_analysis(inputs, TAXABLE(10000), jason_ret_age=61, justin_ret_age=64, target_success=1.0)
        assert r["safe_withdrawal_annual"] == 100000

    def test_depleted_accounts_zero_starting_balance(self, monkeypatch):
        """Zero starting balance, no gap income, one-year horizon -- any
        positive withdrawal fails immediately (remaining > 0 the first
        year), so the search boundary is exactly $0."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(retirement_end_age=61)
        r = run_swr_analysis(inputs, TAXABLE(0), jason_ret_age=60, justin_ret_age=60, target_success=1.0)
        assert r["safe_withdrawal_annual"] == 0

    def test_past_retirement_selection(self, monkeypatch):
        """Jason already 8 years past his selected retirement age (68
        today, selected 65 clamps to 0 years-to-retire) -- phase2 must
        start immediately, at Jason's real current age, not the
        fictional 65. Justin 60, retiring at 64 (4yrs) -> gap income
        $52,000/yr at 65% of $80,000. $100,000 taxable, 5-year horizon
        (4 phase2 + 1 phase3), 0% growth/inflation. Script-verified
        boundary: exactly $61,600 (hand-checked in the design
        verification for this milestone; taxable declines by
        (61600-52000)=9600/yr for 4 years -> 100000 -> 61600, then the
        phase3 year draws the remaining $61,600 exactly to $0)."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(jason_age=68, justin_age=60, retirement_income_today_dollars=80000,
                              retirement_end_age=73, justin_w2_salary=80000)
        r = run_swr_analysis(inputs, TAXABLE(100000), jason_ret_age=65, justin_ret_age=64, target_success=1.0)
        assert r["safe_withdrawal_annual"] == 61600
        assert r["phase2_start_age"] == 68


class TestSwrSearchFailsAboveTheBoundary:
    """Direct proof that a meaningfully higher withdrawal fails the
    search, using the module-level _swr_year_step primitive directly
    (the same one the search itself calls internally) rather than only
    inferring it from the converged safe_withdrawal_annual."""

    def test_ten_thousand_above_the_boundary_fails_every_trial(self):
        # Same household as test_simultaneous_retirement_zero_returns.
        pretax = roth = hsa = 0.0
        taxable = 500000.0
        _, _, _, _, remaining_at_boundary = _swr_year_step(pretax, roth, taxable, hsa, 500000.0, 0.0, 0.0, 0.0, 0.0)
        assert remaining_at_boundary == 0  # exactly funded
        _, _, _, _, remaining_above = _swr_year_step(pretax, roth, taxable, hsa, 510000.0, 0.0, 0.0, 0.0, 0.0)
        assert remaining_above == pytest.approx(10000.0)  # $10,000 short -- a real, meaningful failure


class TestSummaryFieldsGatedToJasonsOwnRetirement:
    def test_pension_absent_from_guaranteed_income_annual_until_jason_actually_retires(self):
        """Justin retires first (61), Jason later (63), $30,000 pension
        -- guaranteed_income_annual (the DAY-ONE figure, at phase2_start
        = age 61) must NOT include the pension yet, since Jason hasn't
        retired. guaranteed_income_steadystate (once pension AND both
        SS claims are active) must include it."""
        inputs = base_inputs(retirement_end_age=64, w2_salary=100000,
                              pension_55=30000, pension_60=30000, pension_65=30000)
        r = run_swr_analysis(inputs, TAXABLE(200000), jason_ret_age=63, justin_ret_age=61, target_success=0.5)
        assert r["phase2_start_age"] == 61
        assert r["guaranteed_income_annual"] == 0
        assert r["guaranteed_income_steadystate"] == 30000

    def test_on_track_and_income_target_fields_present(self):
        inputs = base_inputs(retirement_end_age=64)
        r = run_swr_analysis(inputs, TAXABLE(500000), jason_ret_age=61, justin_ret_age=61, target_success=0.5)
        assert "income_target" in r
        assert "cushion_pct" in r
        assert "on_track" in r


class TestHouseholdSpendingSearchIsGenuinelyValidated:
    """Independent review, 2026-09-08, second follow-up, P1/P2: the
    first fix compared the household's ORIGINAL stated spending target
    against run_two_dimensional_retirement_projection's on_track flag --
    that answers "is the current target affordable," not "is the
    RECOMMENDED number (total_safe_spend) itself safe" (total_safe_spend
    was left as safe_withdrawal + guaranteed_day_one, never validated
    against bridge/kids/healthcare or the target_success rate at all).
    total_safe_spend now comes directly from
    _household_spending_success_rate_two_age's own search boundary --
    the largest total household spending (income_today + the household's
    own fixed costs like healthcare_pre_medicare) that funds every
    single year in full across the N randomized trials at target_success
    -- so replaying it back is guaranteed safe by construction, and
    cushion_pct is genuine spare capacity relative to the stated target,
    not a proportion-unfunded figure (which was always <= 0 for a funded
    plan, regardless of how much MORE it could actually afford).

    Every case below monkeypatches random.gauss to a fixed 0.0 (with the
    0%-return default from base_inputs) so every one of the N trials is
    identical -- success_at_household_spending(x) becomes a clean 100%/
    0% step function right at the true boundary, and target_success=1.0
    makes the search converge to exactly that boundary. Each boundary is
    then hand-verified by replaying the arithmetic directly (shown in
    each docstring) -- ground truth independent of the code under test."""

    def test_kids_and_healthcare_costs_are_held_fixed_while_income_is_searched(self, monkeypatch):
        """$220,000 taxable, both retire now at 61, 2-year horizon,
        $100,000 stated income + $30,000 fixed healthcare cost (via
        healthcare_pre_medicare), 0% inflation/growth. healthcare_pre
        stays fixed at $30,000 every year (it's a household cost, not
        part of the recommendation) while income_today is searched --
        2*(candidate+30000) = 220000 -> candidate = 80000 exactly.
        total_safe_spend = (80000+30000)*1 = 110000 -- against a
        $130,000 stated target (100000+30000), a real ~15.4% shortfall,
        NOT the safe_withdrawal_annual search's own portfolio-only
        number (still computed independently, asserted unaffected)."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(jason_age=61, justin_age=61, retirement_end_age=63,
                              retirement_income_today_dollars=100000, healthcare_pre_medicare=30000)
        r = run_swr_analysis(inputs, TAXABLE(220000), jason_ret_age=61, justin_ret_age=61, target_success=1.0)
        assert r["total_safe_spend"] == pytest.approx(110000, abs=1)
        assert r["on_track"] is False
        assert r["cushion_pct"] == -15.4
        assert r["shortfall_pct"] == 15.4
        # The portfolio-only search is untouched by this fix -- with no
        # guaranteed income/gap income here, its own boundary is simply
        # $220,000 / 2 years = $110,000 (matches
        # TestSwrSearchBoundaryDeterministic's own simultaneous-
        # retirement case, same household shape).
        assert r["safe_withdrawal_annual"] == 110000

    def test_replaying_the_recommendation_against_a_frozen_pension_and_inflation_is_safe(self, monkeypatch):
        """$200,000 taxable, both retire now at 61, 2-year horizon,
        $125,000 stated income, 10% inflation, a frozen (no-COLA)
        $30,000 pension, no separate healthcare cost. The boundary
        candidate income_today satisfies (candidate) + (1.1*candidate) -
        2*30000 = 200000 (total spend over 2 years, minus 2 years of
        frozen pension, funded exactly from the $200,000 portfolio) ->
        2.1*candidate = 260000 -> candidate = 123809.52. Replaying THIS
        number (not the original $125,000 target) is, by construction,
        exactly at the edge of funded -- the fix's whole point: the
        OLD total_safe_spend (safe_withdrawal + guaranteed_day_one, an
        unvalidated sum) could recommend a number that comes up short
        when replayed; this one cannot, since it IS the searched
        boundary."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(jason_age=61, justin_age=61, retirement_end_age=63,
                              retirement_income_today_dollars=125000, inflation_rate=0.10,
                              pension_55=30000, pension_60=30000, pension_65=30000)
        r = run_swr_analysis(inputs, TAXABLE(200000), jason_ret_age=61, justin_ret_age=61, target_success=1.0)
        assert r["total_safe_spend"] == pytest.approx(123809.52, abs=1)
        assert r["on_track"] is False
        assert r["cushion_pct"] == -1.0

    def test_a_much_larger_portfolio_reports_a_larger_cushion_for_the_same_target(self, monkeypatch):
        """Independent review, 2026-09-08, second follow-up, P2:
        cushion_pct used to measure the proportion of a FIXED target
        left unfunded, which is always exactly 0% for any funded plan --
        a $200,000 portfolio and a $1,000,000 portfolio against the same
        $80,000 target both reported 0% cushion despite very different
        real spending capacity. cushion_pct now comes from the searched
        household-spending boundary itself, so a materially larger
        portfolio reports a materially larger (not just non-negative)
        cushion for the identical target."""
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        smaller = base_inputs(jason_age=61, justin_age=61, retirement_end_age=63,
                               retirement_income_today_dollars=80000)
        larger = base_inputs(jason_age=61, justin_age=61, retirement_end_age=63,
                              retirement_income_today_dollars=80000)
        r_small = run_swr_analysis(smaller, TAXABLE(200000), jason_ret_age=61, justin_ret_age=61, target_success=1.0)
        r_large = run_swr_analysis(larger, TAXABLE(1000000), jason_ret_age=61, justin_ret_age=61, target_success=1.0)
        assert r_small["on_track"] is True
        assert r_large["on_track"] is True
        assert r_large["cushion_pct"] > r_small["cushion_pct"] > 0
        assert r_large["total_safe_spend"] > r_small["total_safe_spend"]


class TestZeroPortfolioSearchBoundIsIndependentOfPortfolioSize:
    """Independent review, 2026-09-08, P2: with a $0 starting portfolio,
    both search bounds used to collapse to $0 regardless of future
    working income, since the expansion loop was gated entirely behind
    `if portfolio > 0`. Reproduced: $0 taxable, Jason retires now (60),
    Justin works one more year at a $200,000 salary (-> $130,000
    net-of-tax via the existing 65% factor) then retires too, $65,000/yr
    spending. Hand-verified via _swr_year_step directly: year0 (Justin
    still working) -- remaining=max(0,65000-130000)=0, surplus
    130000-65000=65000 swept into taxable; year1 (both retired) --
    taxable=65000 exactly covers the 65000 draw, remaining=0. A dollar
    above (65001) leaves $1 unmet in year1. Old code returned $0
    regardless of this fully-funded plan."""

    def test_zero_portfolio_with_working_spouse_income_still_searches_a_real_boundary(self, monkeypatch):
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = base_inputs(jason_age=60, justin_age=60, retirement_end_age=62, justin_w2_salary=200000)
        r = run_swr_analysis(inputs, TAXABLE(0), jason_ret_age=60, justin_ret_age=61, target_success=1.0)
        assert r["safe_withdrawal_annual"] == 65000
        assert r["later_retiree"] == "justin"


class TestGuaranteedIncomeSummaryUsesEachSpousesOwnClaimDate:
    """Independent review, 2026-09-08, P2: guaranteed_income_annual
    (day-one) never compounded Social Security by any elapsed years at
    all, and guaranteed_income_steadystate compounded BOTH spouses'
    benefits from one shared years_to_steadystate instead of each one's
    own years since claiming -- inconsistent with the per-year search
    loop's own year_jss/year_uss formulas, which already do this
    correctly per spouse."""

    def test_day_one_ss_compounds_from_its_own_claim_age(self):
        """$30,000 claimed at 62 (ss_timing='early'), retiring (and thus
        measuring day-one guaranteed income) at 67, 3% inflation --
        5 years of COLA: 30000 * 1.03**5 = 34778.22 -> 34778."""
        inputs = base_inputs(jason_age=67, justin_age=67, retirement_end_age=70,
                              inflation_rate=0.03, jason_social_security=30000)
        r = run_swr_analysis(inputs, TAXABLE(500000), jason_ret_age=67, justin_ret_age=67,
                              ss_timing="early", target_success=0.5)
        assert r["guaranteed_income_annual"] == 34778

    def test_steadystate_compounds_each_spouse_from_their_own_claim_date_not_a_shared_exponent(self):
        """Jason (5yrs older, gap=5) already claimed early SS at 62 by
        the time he retires at 64 -- his own benefit. Justin, retiring at
        the same calendar time (age 59), doesn't claim his spousal
        benefit until his own age 67 (the JUSTIN_SPOUSAL_AGE default) --
        8 years after phase2 starts. At that steady-state point (Jason's
        age 72), Jason's OWN elapsed-since-claim is 10 years (72-62), not
        Justin's 8; Justin's own elapsed-since-claim is exactly 0
        (67-67), not 8. Correct: 20000*1.03**10 + 15000*1.03**0 =
        41878.33 -> 41878. The old shared-exponent formula compounded
        both benefits by the same 8 years instead: (20000+15000)*
        1.03**8 = 44337 -- a different, wrong number."""
        inputs = base_inputs(jason_age=64, justin_age=59, retirement_end_age=90,
                              inflation_rate=0.03, jason_social_security=20000,
                              justin_social_security=15000)
        r = run_swr_analysis(inputs, TAXABLE(500000), jason_ret_age=64, justin_ret_age=64,
                              ss_timing="early", target_success=0.5)
        assert r["guaranteed_income_steadystate"] == 41878

    def test_steadystate_waits_for_the_later_of_ss_and_pension(self):
        """Independent review, 2026-09-08, second follow-up, P2: fixing
        the per-spouse SS claim-date exponent (test above) dropped the
        previous code's implicit max() against years_to_pension --
        steadystate_age became "both SS claims active" only, so a
        pension starting LATER than that could get included before it
        actually starts. Both spouses 65 today, Justin retires now,
        Jason at 70 (age_gap=0). SS is fully active by 67 (Justin's
        spousal claim, the later of the two), but Jason's pension not
        until 70 -- three years later. Correct steady-state is measured
        at 70 (the later of the two): 25000 pension + 20000*1.03**8 +
        15000*1.03**3 = 66726. The regressed formula measured at 67
        (SS-only) but still added the age-70-only pension: 25000 +
        20000*1.03**5 + 15000*1.03**0 = 63185 -- a different, wrong
        number that includes a pension three years before it starts."""
        inputs = base_inputs(jason_age=65, justin_age=65, retirement_end_age=90,
                              inflation_rate=0.03, jason_social_security=20000,
                              justin_social_security=15000,
                              pension_55=25000, pension_60=25000, pension_65=25000)
        r = run_swr_analysis(inputs, TAXABLE(500000), jason_ret_age=70, justin_ret_age=65,
                              ss_timing="early", target_success=0.5)
        assert r["guaranteed_income_steadystate"] == 66726


class TestTwoAgeSwrModeRequiresBothAges:
    def test_only_jason_ret_age_raises(self):
        inputs = base_inputs(retirement_end_age=64)
        with pytest.raises(ValueError):
            run_swr_analysis(inputs, TAXABLE(200000), jason_ret_age=61)

    def test_only_justin_ret_age_raises(self):
        inputs = base_inputs(retirement_end_age=64)
        with pytest.raises(ValueError):
            run_swr_analysis(inputs, TAXABLE(200000), justin_ret_age=61)


class TestSingleAgeModeUnaffected:
    def test_default_call_has_no_two_age_fields(self, sample_inputs, sample_accounts):
        r = run_swr_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        assert "mode" not in r
        assert "jason_ret_age" not in r
