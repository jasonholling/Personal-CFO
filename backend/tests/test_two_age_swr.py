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
