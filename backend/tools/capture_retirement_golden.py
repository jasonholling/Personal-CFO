"""
Phase 6 verification tool: captures run_retirement_projection()'s full
output across a battery of synthetic scenarios to a JSON file, so the
pre-migration and post-migration behavior can be diffed exactly. Not a
pytest test (deliberately run by hand around the migration commit, not
part of CI) — its whole job is a before/after snapshot, which only makes
sense run twice around one specific change.

Usage:
    venv/bin/python3 tools/capture_retirement_golden.py before.json
    <migrate the code>
    venv/bin/python3 tools/capture_retirement_golden.py after.json
    venv/bin/python3 tools/diff_retirement_golden.py before.json after.json
"""
import json
import sys
from copy import deepcopy

from projection_engine import run_retirement_projection

BASE_INPUTS = {
    "person1_name": "Alex", "person2_name": "Sam",
    "jason_age": 50, "justin_age": 48,
    "retirement_income_today_dollars": 100000,
    "inflation_rate": 0.02,
    "expected_return_pre_retirement": 0.07,
    "expected_return_post_retirement": 0.06,
    "jason_social_security": 30000,
    "jason_ss_delayed": 45000,
    "justin_social_security": 15000,
    "jason_ss_age": 62,
    "justin_ss_age": 67,
    "annual_hsa_contribution": 8000,
    "annual_rsu_value": 0,
    "pretax_401k_pct": 0.75,
    "employee_401k_pct": 0.06,
    "employer_401k_pct": 0.03,
    "w2_salary": 150000,
    "healthcare_pre_medicare": 20000,
    "healthcare_post_medicare": 5000,
    "healthcare_kids": 0,
    "kids_annual_cost": 0,
    "bridge_income_55": 0,
    "bridge_years_55": 0,
    "kids_years_at_home_55": 0,
    "pension_55": 20000,
    "pension_60": 30000,
    "pension_65": 35000,
    "asset1_sale_age": 0, "asset1_sale_net": 0, "asset1_appreciation": 0.03,
    "asset2_sale_age": 0, "asset2_sale_net": 0,
    "retirement_end_age": 95,
    "state_income_tax_rate": 0,
}

BASE_ACCOUNTS = [
    {"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 500000},
    {"id": 2, "name": "Roth IRA", "account_type": "roth_ira", "owner": "jason", "balance": 100000},
    {"id": 3, "name": "Trad IRA", "account_type": "ira", "owner": "justin", "balance": 50000},
    {"id": 4, "name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 200000},
    {"id": 5, "name": "HSA", "account_type": "hsa", "owner": "jason", "balance": 20000},
]

SCENARIOS = {
    "default_55_60_65": dict(inputs={}, accounts=None, ret_ages=None),
    "zero_returns_zero_inflation": dict(
        inputs={"expected_return_pre_retirement": 0, "expected_return_post_retirement": 0, "inflation_rate": 0},
        accounts=None, ret_ages=None),
    "every_retirement_age_55_to_67": dict(inputs={}, accounts=None, ret_ages=list(range(55, 68))),
    "already_past_retirement_age": dict(inputs={"jason_age": 66, "justin_age": 64}, accounts=None, ret_ages=[55, 60, 65]),
    "large_spouse_age_gap": dict(inputs={"jason_age": 50, "justin_age": 35}, accounts=None, ret_ages=[55, 60, 65]),
    "small_balances_insufficient_funds": dict(
        inputs={"retirement_income_today_dollars": 300000},
        accounts=[{"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 50000}],
        ret_ages=[55, 60, 65]),
    "asset_sale_mid_retirement": dict(
        inputs={"asset1_sale_age": 62, "asset1_sale_net": 150000}, accounts=None, ret_ages=[55, 60, 65]),
    "bridge_job_and_kids_at_home": dict(
        inputs={"bridge_income_55": 40000, "bridge_years_55": 5, "kids_years_at_home_55": 8,
                "kids_annual_cost": 15000}, accounts=None, ret_ages=[55, 60, 65]),
    "rsu_and_bonus": dict(
        inputs={"annual_rsu_value": 30000, "annual_bonus_pct": 0.20}, accounts=None, ret_ages=[55, 60, 65]),
    "salary_growth": dict(inputs={}, accounts=None, ret_ages=[55, 60, 65], salary_growth_pct=0.03),
    "state_tax": dict(inputs={"state_income_tax_rate": 0.05}, accounts=None, ret_ages=[55, 60, 65]),
    "life_events": dict(
        inputs={}, accounts=None, ret_ages=[55, 60, 65],
        life_events=[
            {"event_year": 2032, "one_time_cash_delta": -50000,
             "monthly_cash_flow_delta": 0, "duration_months": 0},
            {"event_year": 2040, "one_time_cash_delta": 0,
             "monthly_cash_flow_delta": 500, "duration_months": 120},
        ]),
}


def run_all():
    results = {}
    for name, cfg in SCENARIOS.items():
        inputs = {**BASE_INPUTS, **cfg.get("inputs", {})}
        accounts = cfg.get("accounts") if cfg.get("accounts") is not None else deepcopy(BASE_ACCOUNTS)
        ret_ages = cfg.get("ret_ages")
        kwargs = {}
        if cfg.get("salary_growth_pct") is not None:
            kwargs["salary_growth_pct"] = cfg["salary_growth_pct"]
        if cfg.get("life_events") is not None:
            kwargs["life_events"] = cfg["life_events"]
        result = run_retirement_projection(inputs, accounts, ret_ages=ret_ages, **kwargs)
        results[name] = result
    return results


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/retirement_golden.json"
    data = run_all()
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"wrote {out_path}")
