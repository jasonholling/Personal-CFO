"""
Proportional/blended withdrawal strategy (2026-09-13, CALCULATION_CONTRACT.md
section 81) — end-to-end coverage for the household-facing `inputs["with
drawal_strategy"]` toggle across the 4 in-scope consumer functions
(run_retirement_projection, run_two_dimensional_retirement_projection,
_run_single via run_monte_carlo/run_stress_tests, _run_single_two_age via
their two-age counterparts). The engine-level water-filling algorithm
itself (simulate_withdrawal_year's own `proportional=` parameter) has its
own hand-calculated reference tests in test_annual_engine_reference.py --
this file is about the WIRING: does each consumer actually read the
setting and pass it through, and is "taxable_first"/unset a true no-op.
"""

import pytest

import projection_engine
from projection_engine import run_retirement_projection, run_two_dimensional_retirement_projection
from simulation_engine import run_monte_carlo, run_stress_tests

# A household whose 401k dwarfs taxable+Roth combined -- the exact shape
# that motivated this feature (a $1.6M 401k sitting untouched while a much
# smaller taxable account drains first under the old, sole taxable-first
# behavior).
ACCOUNTS = [
    {"name": "401k", "account_type": "401k", "owner": "jason", "balance": 1_600_000},
    {"name": "Roth IRA", "account_type": "roth_ira", "owner": "jason", "balance": 100_000},
    {"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 300_000},
]


def base_inputs(**overrides):
    inputs = {
        "jason_age": 60, "justin_age": 60,
        "inflation_rate": 0.0,
        "expected_return_pre_retirement": 0.0,
        "expected_return_post_retirement": 0.0,
        "retirement_income_today_dollars": 120_000,
        "annual_hsa_contribution": 0, "annual_rsu_value": 0,
        "jason_social_security": 0, "justin_social_security": 0,
        "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
        "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
        "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0,
        "annual_bonus_pct": 0, "pretax_401k_pct": 1.0,  # whole 401k pretax, isolates the taxable-vs-pretax question
        # 11-year horizon (not 63's 3-year one): a short horizon here hits
        # a PRE-EXISTING, unrelated bug in run_stress_tests's "early_
        # sequence" scenario (an eager dict.get default-argument IndexError
        # when retire_yrs is shorter than the SCENARIOS override table --
        # documented, not fixed, CALCULATION_CONTRACT.md section 76).
        "retirement_end_age": 71,
    }
    inputs.update(overrides)
    return inputs


class TestZeroRegressionGuard:
    """withdrawal_strategy unset, or explicitly 'taxable_first', must be
    byte-identical to how every one of these functions already behaved."""

    def test_single_age_projection_unset_matches_explicit_taxable_first(self):
        unset = run_retirement_projection(base_inputs(), ACCOUNTS, ret_ages=[60])
        explicit = run_retirement_projection(base_inputs(withdrawal_strategy="taxable_first"), ACCOUNTS, ret_ages=[60])
        assert unset["scenarios"] == explicit["scenarios"]

    def test_two_age_projection_unset_matches_explicit_taxable_first(self):
        unset = run_two_dimensional_retirement_projection(base_inputs(), ACCOUNTS, jason_ret_age=60, justin_ret_age=60)
        explicit = run_two_dimensional_retirement_projection(base_inputs(withdrawal_strategy="taxable_first"), ACCOUNTS, jason_ret_age=60, justin_ret_age=60)
        assert unset["yearly_detail"] == explicit["yearly_detail"]

    def test_monte_carlo_unset_matches_explicit_taxable_first(self):
        unset = run_monte_carlo(base_inputs(), ACCOUNTS, ret_age=60)
        explicit = run_monte_carlo(base_inputs(withdrawal_strategy="taxable_first"), ACCOUNTS, ret_age=60)
        assert unset["median_final_balance"] == explicit["median_final_balance"]
        assert unset["success_rate"] == explicit["success_rate"]

    def test_stress_tests_unset_matches_explicit_taxable_first(self):
        unset = run_stress_tests(base_inputs(), ACCOUNTS, ret_age=60)
        explicit = run_stress_tests(base_inputs(withdrawal_strategy="taxable_first"), ACCOUNTS, ret_age=60)
        assert unset["scenarios"]["base"]["final_balance"] == explicit["scenarios"]["base"]["final_balance"]


class TestProportionalActuallyDrawsFromPretaxImmediately:
    """The user-facing claim this feature exists to satisfy: with
    proportional selected, the 401k gets drawn from starting in year one,
    not held untouched while taxable/Roth drain first."""

    def test_single_age_projection_draws_pretax_in_year_one(self):
        taxable_first = run_retirement_projection(base_inputs(), ACCOUNTS, ret_ages=[60])
        proportional = run_retirement_projection(base_inputs(withdrawal_strategy="proportional"), ACCOUNTS, ret_ages=[60])
        tf_scenario = next(s for s in taxable_first["scenarios"] if s["ss_timing"] == "early")
        prop_scenario = next(s for s in proportional["scenarios"] if s["ss_timing"] == "early")
        assert tf_scenario["yearly_detail"][0]["withdrawal_pretax"] == 0  # untouched under taxable-first
        assert prop_scenario["yearly_detail"][0]["withdrawal_pretax"] > 0  # drawn immediately under proportional

    def test_two_age_projection_draws_pretax_in_year_one(self, monkeypatch):
        # yearly_detail only exposes an aggregate "withdrawal" total here
        # (unlike the single-age scenario dict, which breaks it out per
        # bucket) -- spy on the real simulate_withdrawal_year calls to
        # check the per-bucket draw directly, same technique
        # test_annual_reconciliation.py already uses.
        captured = []
        original = projection_engine.simulate_withdrawal_year

        def spy(*args, **kw):
            r = original(*args, **kw)
            captured.append(r)
            return r

        monkeypatch.setattr(projection_engine, "simulate_withdrawal_year", spy)
        run_two_dimensional_retirement_projection(base_inputs(), ACCOUNTS, jason_ret_age=60, justin_ret_age=60)
        assert captured[0].draws.get("pretax", 0.0) == 0  # untouched under taxable-first

        captured.clear()
        run_two_dimensional_retirement_projection(base_inputs(withdrawal_strategy="proportional"), ACCOUNTS, jason_ret_age=60, justin_ret_age=60)
        assert captured[0].draws.get("pretax", 0.0) > 0  # drawn immediately under proportional

    def test_monte_carlo_median_balance_differs_between_strategies(self):
        """Not asserting a direction (tax outcomes are genuinely
        household-specific) -- just that the setting has a real, nonzero
        effect on this uneven-bucket household, proving it's actually
        wired through Monte Carlo's 1000-trial loop, not silently ignored."""
        taxable_first = run_monte_carlo(base_inputs(), ACCOUNTS, ret_age=60)
        proportional = run_monte_carlo(base_inputs(withdrawal_strategy="proportional"), ACCOUNTS, ret_age=60)
        assert taxable_first["median_final_balance"] != proportional["median_final_balance"]

    def test_stress_tests_base_case_differs_between_strategies(self):
        taxable_first = run_stress_tests(base_inputs(), ACCOUNTS, ret_age=60)
        proportional = run_stress_tests(base_inputs(withdrawal_strategy="proportional"), ACCOUNTS, ret_age=60)
        assert taxable_first["scenarios"]["base"]["final_balance"] != proportional["scenarios"]["base"]["final_balance"]


class TestProportionalReconciliation:
    """Every year of a real proportional run still passes AnnualResult's
    own authoritative per-bucket ledger check -- same standard every other
    consumer of the shared engine is held to (test_annual_reconciliation.py)."""

    def test_every_year_reconciles_under_proportional(self, monkeypatch):
        captured = []
        original = projection_engine.simulate_withdrawal_year

        def spy(*args, **kw):
            r = original(*args, **kw)
            captured.append(r)
            return r

        monkeypatch.setattr(projection_engine, "simulate_withdrawal_year", spy)
        run_retirement_projection(base_inputs(withdrawal_strategy="proportional"), ACCOUNTS, ret_ages=[60])
        assert captured, "the spy never captured a single call -- test itself is broken"
        discrepancies = [r.reconcile() for r in captured if r.reconcile() is not None]
        assert discrepancies == []
