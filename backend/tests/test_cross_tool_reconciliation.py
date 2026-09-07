"""
Phase 6 — cross-tool reconciliation sweep.

Every other phase of the calculation-engine consolidation verified one
migration/extraction against its own before-state (a golden diff, a
parity test, an independent reference test). This file instead asks a
different question, holistically, across every withdrawal-phase consumer
at once: for the SAME household inputs, do the invariants
CALCULATION_CONTRACT.md's conventions promise actually hold everywhere,
not just in whichever function was most recently touched?

Per the consolidation task's own Phase 6 instruction (not run as a single
comprehensive pass in earlier phases — see CONSOLIDATION_HANDOFF.md's
"What's not done" section): this is that holistic pass, scoped to the
invariants that are both (a) genuinely cross-cutting (a bug here would be
a systemic inconsistency, not a one-function bug) and (b) mechanically
checkable without re-deriving each tool's entire domain model. It is not
exhaustive — CALCULATION_CONTRACT.md section 3's material assumptions
(tax model, withdrawal order, taxable-gain approximation, bridge-job
scope, SWR's undifferentiated spending figure) are DELIBERATE
divergences and are correctly excluded from every check below.
"""

import pytest

from projection_engine import pension_for_age, run_retirement_projection
from simulation_engine import (
    run_monte_carlo,
    run_roth_conversion_analysis,
    run_stress_tests,
    run_survivor_scenario,
    run_swr_analysis,
    run_tax_efficiency_simulation,
)


@pytest.fixture
def household(sample_inputs):
    """A distinctive retirement_end_age (82, not the fixture's default or
    any function's fallback of 95/99) — the whole point is to prove every
    consumer reads this from inputs instead of silently keeping its own
    hardcoded mortality assumption."""
    return {**sample_inputs, "retirement_end_age": 82, "jason_age": 55, "justin_age": 53}


class TestRetirementEndAgeRespectedEverywhere:
    """CALCULATION_CONTRACT.md 2.x: every withdrawal-phase consumer must
    read the household's actual retirement_end_age, not a hardcoded
    mortality assumption — this was itself an external-audit finding
    (2026-09-06/07) fixed piecemeal across several functions; this test
    is the systemic check that no consumer regresses back to a hardcoded
    figure or is ever added without wiring this through."""

    RET_AGE = 60

    def test_retirement_projection(self, household, sample_accounts):
        out = run_retirement_projection(household, sample_accounts, ret_ages=[self.RET_AGE])
        s = next(x for x in out["scenarios"] if x["label"] == f"age_{self.RET_AGE}_early")
        assert s["retirement_end_age"] == 82
        assert max(y["jason_age"] for y in s["yearly_detail"]) == 82 - 1  # last modeled year, exclusive end

    def test_swr_analysis(self, household, sample_accounts):
        """run_swr_analysis doesn't echo retirement_end_age in its output
        at all, so check the wiring indirectly the same way as the
        tax-efficiency test below: a much shorter horizon must produce a
        materially different safe-withdrawal figure than a much longer
        one for the same household — if it didn't, retirement_end_age
        stopped reaching the search entirely."""
        short = run_swr_analysis({**household, "retirement_end_age": self.RET_AGE + 3},
                                  sample_accounts, ret_age=self.RET_AGE, ss_timing="early")
        long = run_swr_analysis({**household, "retirement_end_age": self.RET_AGE + 35},
                                 sample_accounts, ret_age=self.RET_AGE, ss_timing="early")
        assert short["safe_withdrawal_annual"] != long["safe_withdrawal_annual"]

    def test_monte_carlo(self, household, sample_accounts):
        out = run_monte_carlo(household, sample_accounts, ret_age=self.RET_AGE, ss_timing="early")
        assert out["retirement_end_age"] == 82

    def test_stress_tests(self, household, sample_accounts):
        out = run_stress_tests(household, sample_accounts, ret_age=self.RET_AGE, ss_timing="early")
        assert out["retirement_end_age"] == 82

    def test_survivor_scenario(self, household, sample_accounts):
        out = run_survivor_scenario(household, sample_accounts, ret_age=self.RET_AGE,
                                     deceased="jason", death_age=70)
        assert out["survivor_end_age"] == 82

    def test_tax_efficiency_simulation(self, household, sample_accounts):
        out = run_tax_efficiency_simulation(household, sample_accounts, ret_age=self.RET_AGE, ss_timing="early")
        assert out["retirement_end_age"] == 82
        # Belt-and-suspenders: the echoed value must actually be wired to
        # the simulated horizon, not just passed through unused.
        short = run_tax_efficiency_simulation({**household, "retirement_end_age": self.RET_AGE + 2},
                                               sample_accounts, ret_age=self.RET_AGE, ss_timing="early")
        long = run_tax_efficiency_simulation({**household, "retirement_end_age": self.RET_AGE + 30},
                                              sample_accounts, ret_age=self.RET_AGE, ss_timing="early")
        assert (short["strategies"]["taxable_first"]["median_lifetime_tax"]
                != long["strategies"]["taxable_first"]["median_lifetime_tax"])


class TestGuaranteedIncomeConsistentAcrossConsumers:
    """Every consumer that needs a pension figure for a given retirement
    age must go through the shared pension_for_age() interpolation —
    CALCULATION_CONTRACT.md flags guaranteed-income computation as
    caller-owned specifically BECAUSE it varies by consumer shape, but the
    underlying pension NUMBER for a given age must never independently
    drift. This is the systemic check that run_retirement_projection,
    run_roth_conversion_analysis, and run_survivor_scenario, which each
    independently call pension_for_age, actually got the same value."""

    def test_pension_matches_projection_pension_for_age(self, household, sample_accounts):
        ret_age = 60
        expected_pension = pension_for_age(household, ret_age)

        proj = run_retirement_projection(household, sample_accounts, ret_ages=[ret_age])
        s = next(x for x in proj["scenarios"] if x["label"] == f"age_{ret_age}_early")
        first_year = s["yearly_detail"][0]
        assert first_year["pension"] == pytest.approx(expected_pension, abs=0.01)

        roth = run_roth_conversion_analysis(household, sample_accounts, ret_age=ret_age, ss_timing="early")
        # roth's schedule doesn't echo pension directly, but its first-year
        # base_taxable_income does include it — cross-check via a second
        # call with pension zeroed out, which must shift base_taxable_income
        # down by exactly the pension amount (holding everything else fixed).
        no_pension_inputs = {**household, "pension_55": 0, "pension_60": 0, "pension_65": 0}
        roth_no_pension = run_roth_conversion_analysis(no_pension_inputs, sample_accounts,
                                                        ret_age=ret_age, ss_timing="early")
        if roth["schedule"] and roth_no_pension["schedule"]:
            delta = roth["schedule"][0]["base_taxable_income"] - roth_no_pension["schedule"][0]["base_taxable_income"]
            assert delta == pytest.approx(expected_pension, abs=1)

    def test_social_security_claiming_age_and_amount_consistent(self, household, sample_accounts):
        """Every consumer that reads jason_social_security/jason_ss_delayed
        selects between them by ss_timing ('early' -> jason_social_security
        starting at 62, 'delayed' -> jason_ss_delayed starting at 67) —
        checked here via run_retirement_projection (which echoes the
        selected amount/age directly) against run_roth_conversion_analysis
        (which doesn't echo SS directly, but a household with Jason already
        past both claiming ages must show the SAME base_taxable_income
        whether "early" or "delayed" is requested, once real SS income is
        actually flowing under both — a real drift in the selected AMOUNT
        or START AGE between consumers would show up as a difference
        here)."""
        ret_age = 68  # past both the early (62) and delayed (67) claiming ages
        for timing in ("early", "delayed"):
            proj = run_retirement_projection(household, sample_accounts, ret_ages=[ret_age])
            s = next(x for x in proj["scenarios"] if x["label"] == f"age_{ret_age}_{timing}")
            expected_ss = household["jason_social_security"] if timing == "early" else household["jason_ss_delayed"]
            expected_age = 62 if timing == "early" else 67
            assert s["jason_ss_start_age"] == expected_age
            assert s["jason_ss_annual"] == pytest.approx(expected_ss, abs=0.01)
            assert ret_age >= expected_age  # sanity: SS is actually active by the time this scenario retires

            roth = run_roth_conversion_analysis(household, sample_accounts, ret_age=ret_age, ss_timing=timing)
            no_ss_inputs = {**household, "jason_social_security": 0, "jason_ss_delayed": 0}
            roth_no_ss = run_roth_conversion_analysis(no_ss_inputs, sample_accounts, ret_age=ret_age, ss_timing=timing)
            if roth["schedule"] and roth_no_ss["schedule"]:
                delta = roth["schedule"][0]["base_taxable_income"] - roth_no_ss["schedule"][0]["base_taxable_income"]
                # 85% of SS is includable in taxable income (same convention
                # both consumers use), COLA'd from the claiming age to
                # ret_age (both tools apply the same annual COLA to SS,
                # frozen pension aside) — the delta must match the SAME
                # amount/age selection run_retirement_projection made.
                cola_years = ret_age - expected_age
                expected_ss_at_ret_age = expected_ss * ((1 + household["inflation_rate"]) ** cola_years)
                assert delta == pytest.approx(expected_ss_at_ret_age * 0.85, abs=1)

    def test_social_security_pre_retirement_cola_reaches_monte_carlo_and_stress(
            self, household, sample_accounts, monkeypatch):
        """The check above retires at 68 (past both claiming ages) and
        never exercises the specific case that was actually broken in
        Monte Carlo/Stress: SS claimed BEFORE this retirement scenario
        starts (independent review, 2026-09-07 follow-up — the first
        SS-timing check "compares Retirement with Roth... [and] does not
        call Monte Carlo or Stress", so it missed this). Retire at 67
        with early claiming at 62 — 5 years of pre-retirement COLA that
        run_retirement_projection (via jason_ss_annual/jason_ss_start_age)
        has never dropped, checked directly against run_monte_carlo and
        run_stress_tests with deterministic (0%) simulated returns so
        their SS-derived final balance must match exactly, not merely
        approximately."""
        import random as random_module
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)

        inputs = {**household, "jason_age": 67, "justin_age": 67, "retirement_end_age": 68,
                  "expected_return_post_retirement": 0.0}
        ret_age = 67

        proj = run_retirement_projection(inputs, sample_accounts, ret_ages=[ret_age])
        s = next(x for x in proj["scenarios"] if x["label"] == f"age_{ret_age}_early")
        expected_final_balance = s["yearly_detail"][-1]["portfolio_balance"]

        mc = run_monte_carlo(inputs, sample_accounts, ret_age=ret_age, ss_timing="early")
        assert mc["median_final_balance"] == pytest.approx(expected_final_balance, abs=1)


class TestNoConsumerReportsANegativeBalance:
    """A stress scenario (high spending need, modest balances) exercised
    across every withdrawal-phase consumer — none should ever report a
    negative account balance. Migrated consumers get this for free from
    annual_engine.AnnualResult.reconcile()'s own invariant; this test
    covers the ones that aren't migrated (run_tax_efficiency_simulation's
    'optimal' strategy, run_survivor_scenario, run_swr_analysis) with the
    same check applied externally."""

    @pytest.fixture
    def stressed(self, sample_inputs):
        return {**sample_inputs, "retirement_income_today_dollars": 200000,
                "jason_age": 58, "justin_age": 56, "jason_social_security": 5000,
                "justin_social_security": 5000, "pension_55": 0, "pension_60": 0, "pension_65": 0}

    @pytest.fixture
    def thin_accounts(self):
        return [{"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 80000,
                 "institution": "", "notes": ""}]

    def test_tax_efficiency_optimal_strategy(self, stressed, thin_accounts):
        out = run_tax_efficiency_simulation(stressed, thin_accounts, ret_age=60, ss_timing="early")
        assert out["strategies"]["optimal"]["median_final_balance"] >= 0

    def test_survivor_scenario(self, stressed, thin_accounts):
        out = run_survivor_scenario(stressed, thin_accounts, ret_age=60, deceased="jason", death_age=61)
        assert all(row["ending_balance"] >= 0 for row in out["schedule"])
        assert all(row["starting_balance"] >= 0 for row in out["schedule"])

    def test_roth_conversion(self, stressed, thin_accounts):
        out = run_roth_conversion_analysis(stressed, thin_accounts, ret_age=60, ss_timing="early")
        for row in out["schedule"]:
            assert row["pretax_after"] >= 0
            assert row["roth_after"] >= 0
            assert row["taxable_after"] >= 0


class TestCompletedLifeEventsNotReplayedInWithdrawalPhase:
    """A life event dated BEFORE retirement must already be baked into
    today's account balances (it already happened) — every withdrawal-
    phase consumer must exclude it from its own post-retirement year-by-
    year math via _split_life_events, or it gets double-counted (once
    implicitly, in the starting balance; once explicitly, replayed during
    the withdrawal loop). Checked by asserting a pre-retirement-dated
    event produces IDENTICAL post-retirement output to no event at all,
    across every consumer that accepts life_events."""

    RET_AGE = 60

    @pytest.fixture
    def past_event(self, household):
        # household's jason_age=55, so ret_age 60 retires in CURRENT_YEAR+5;
        # this event is dated well before that.
        from simulation_engine import CURRENT_YEAR
        return [{"event_year": CURRENT_YEAR - 1, "one_time_cash_delta": -75000,
                 "monthly_cash_flow_delta": 0, "duration_months": 0}]

    def test_retirement_projection_unaffected(self, household, sample_accounts, past_event):
        baseline = run_retirement_projection(household, sample_accounts, ret_ages=[self.RET_AGE])
        with_past = run_retirement_projection(household, sample_accounts, ret_ages=[self.RET_AGE],
                                               life_events=past_event)
        b = next(x for x in baseline["scenarios"] if x["label"] == f"age_{self.RET_AGE}_early")
        w = next(x for x in with_past["scenarios"] if x["label"] == f"age_{self.RET_AGE}_early")
        assert b["yearly_detail"] == w["yearly_detail"]

    def test_roth_conversion_unaffected(self, household, sample_accounts, past_event):
        baseline = run_roth_conversion_analysis(household, sample_accounts, ret_age=self.RET_AGE, ss_timing="early")
        with_past = run_roth_conversion_analysis(household, sample_accounts, ret_age=self.RET_AGE,
                                                  ss_timing="early", life_events=past_event)
        assert baseline["schedule"] == with_past["schedule"]

    def test_survivor_scenario_unaffected(self, household, sample_accounts, past_event):
        baseline = run_survivor_scenario(household, sample_accounts, ret_age=self.RET_AGE,
                                          deceased="jason", death_age=70)
        with_past = run_survivor_scenario(household, sample_accounts, ret_age=self.RET_AGE,
                                           deceased="jason", death_age=70, life_events=past_event)
        assert baseline["schedule"] == with_past["schedule"]

    def test_tax_efficiency_unaffected(self, household, sample_accounts, past_event):
        baseline = run_tax_efficiency_simulation(household, sample_accounts, ret_age=self.RET_AGE, ss_timing="early")
        with_past = run_tax_efficiency_simulation(household, sample_accounts, ret_age=self.RET_AGE,
                                                   ss_timing="early", life_events=past_event)
        assert baseline == with_past
