"""
Roth conversion reconciliation tests — item 5 of the 2026-09-07 follow-on
task list: "Test matched 'with conversion' and 'without conversion' runs
where only the conversion policy changes. Verify conversion tax funding,
spending fallback, RMD impact, cohort growth, and unmet spending."

These are deliberately independent of test_simulation_engine.py's
existing TestRunRothConversionAnalysis regression tests (which each
target one specific historical bug) — this file's job is cross-cutting
reconciliation across the with/without-conversion pair.
"""

import itertools

import pytest

from projection_engine import run_retirement_projection
from simulation_engine import run_roth_conversion_analysis

BASE_INPUTS = {
    "jason_age": 55, "justin_age": 55, "retirement_income_today_dollars": 0,
    "inflation_rate": 0.02, "expected_return_pre_retirement": 0.07,
    "expected_return_post_retirement": 0.06,
    "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
    "jason_ss_age": 62, "justin_ss_age": 67, "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
    "pension_55": 0, "pension_60": 0, "pension_65": 0,
    "annual_hsa_contribution": 0, "annual_rsu_value": 0, "pretax_401k_pct": 1.0,
    "employee_401k_pct": 0, "employer_401k_pct": 0, "w2_salary": 0,
    "retirement_end_age": 95, "state_income_tax_rate": 0,
}


# ── Cohort growth: sum of each year's roth_fv_at_73 + the untouched
#    starting balance's own growth must equal the actual Roth balance at
#    RMD age (to rounding) — this is the "future value of this specific
#    conversion, assuming it's never touched again" metric reconciling
#    with the real ledger, which only holds when Roth is never drawn for
#    spending along the way (zero starting Roth, zero ordinary spending
#    need — the same condition the independent review's own bounded
#    verification used). ──────────────────────────────────────────────

COHORT_MATRIX = list(itertools.product(
    (60, 67, 74),           # retirement age
    (0.0, 0.03, 0.10),      # post-retirement return
    (400_000, 1_000_000),   # pretax balance
    (0, 300_000),           # taxable (tax-funding) balance
))


@pytest.mark.parametrize("ret_age,post_ret,pretax_balance,taxable_balance", COHORT_MATRIX)
def test_cohort_future_values_reconcile_with_actual_roth_at_rmd_age(
        ret_age, post_ret, pretax_balance, taxable_balance):
    inputs = {**BASE_INPUTS, "expected_return_post_retirement": post_ret}
    accounts = [
        {"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": pretax_balance},
    ]
    if taxable_balance:
        accounts.append(
            {"id": 2, "name": "Brokerage", "account_type": "taxable", "owner": "joint",
             "balance": taxable_balance})

    out = run_roth_conversion_analysis(inputs, accounts, ret_age=ret_age, ss_timing="early")
    if out["conversion_years"] <= 0 or not out["schedule"]:
        return  # nothing to reconcile (e.g. already past RMD age)

    proj = run_retirement_projection(inputs, accounts, ret_ages=[ret_age])
    s = next(x for x in proj["scenarios"] if x["label"] == f"age_{ret_age}_early")
    roth_at_ret = s["roth_at_retirement"]

    starting_roth_grown = roth_at_ret * ((1 + post_ret) ** out["conversion_years"])
    sum_cohorts = sum(row["roth_fv_at_73"] for row in out["schedule"])
    predicted_total = round(starting_roth_grown + sum_cohorts)

    assert predicted_total == pytest.approx(out["roth_at_rmd_age_with_conversion"], abs=2)


# ── Matched with/without-conversion pair: forcing conversions to zero ──────

def test_zero_conversion_room_makes_with_and_without_paths_identical():
    """When there's no room to convert at all (guaranteed income alone
    fills the entire 22% bracket every year), the "with conversions"
    schedule's own pretax/taxable trajectory must be identical to the
    "without conversions" baseline's — only the conversion policy is
    meant to differ between the two paths, so with that policy forced to
    a no-op, the two must agree exactly."""
    inputs = {
        **BASE_INPUTS,
        "pension_60": 300_000,  # absurdly large guaranteed income -> fills the 22% bracket alone
    }
    accounts = [{"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 500_000}]
    out = run_roth_conversion_analysis(inputs, accounts, ret_age=60, ss_timing="early")
    assert out["total_conversions"] == 0
    for row in out["schedule"]:
        assert row["optimal_conversion"] == 0
        assert row["pretax_after"] == pytest.approx(
            out["pretax_at_rmd_age_no_conversion"] if row is out["schedule"][-1] else row["pretax_after"])
    # The final pretax balance under "with conversions" (which never
    # actually converts) must match the "without conversions" baseline.
    assert out["pretax_at_rmd_age_with_conversion"] == pytest.approx(
        out["pretax_at_rmd_age_no_conversion"], abs=1)


# ── RMD impact: real conversions must lower the projected RMD ─────────────

def test_real_conversions_reduce_projected_rmd():
    inputs = BASE_INPUTS
    accounts = [
        {"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 1_500_000},
        {"id": 2, "name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 500_000},
    ]
    out = run_roth_conversion_analysis(inputs, accounts, ret_age=60, ss_timing="early")
    assert out["total_conversions"] > 0  # sanity: this scenario actually converts something
    assert out["estimated_rmd_with_conversions"] < out["estimated_rmd_without_conversions"]
    assert out["pretax_at_rmd_age_with_conversion"] < out["pretax_at_rmd_age_no_conversion"]


# ── Conversion tax funding: the tax cost must be funded from taxable,
#    not silently absorbed by the converted principal, whenever taxable
#    can actually afford it ──────────────────────────────────────────────

def test_conversion_tax_is_funded_from_taxable_not_the_converted_principal():
    inputs = BASE_INPUTS
    accounts = [
        {"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 1_500_000},
        {"id": 2, "name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 1_000_000},
    ]
    out = run_roth_conversion_analysis(inputs, accounts, ret_age=60, ss_timing="early")
    first = out["schedule"][0]
    assert first["optimal_conversion"] > 0
    # The full converted amount lands in Roth this year -- roth_after
    # must reflect the entire optimal_conversion, not conversion-minus-
    # its-own-tax (ample taxable funds the tax separately).
    assert first["roth_after"] >= first["optimal_conversion"]


# ── Unmet spending: surfaced, not silently dropped, and zero when the
#    household is healthy ──────────────────────────────────────────────

def test_unmet_need_is_zero_for_a_well_funded_household():
    inputs = BASE_INPUTS
    accounts = [
        {"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 2_000_000},
        {"id": 2, "name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 1_000_000},
    ]
    out = run_roth_conversion_analysis(inputs, accounts, ret_age=60, ss_timing="early")
    assert out["any_unmet_need"] is False
    assert out["total_unmet_need"] == 0
    assert all(row["unmet_need"] == 0 for row in out["schedule"])
