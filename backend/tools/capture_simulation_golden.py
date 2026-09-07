"""Phase 6 verification tool for run_monte_carlo / run_stress_tests,
paired with capture_retirement_golden.py's approach: captures full output
across synthetic scenarios so pre/post-migration behavior can be diffed
exactly. Both functions seed random.seed(42) internally, so repeated runs
are deterministic."""
import json
import sys

from simulation_engine import run_monte_carlo, run_stress_tests

BASE_INPUTS = {
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
    "annual_hsa_contribution": 8000,
    "annual_rsu_value": 0,
    "pretax_401k_pct": 0.75,
    "employee_401k_pct": 0.06,
    "employer_401k_pct": 0.03,
    "w2_salary": 150000,
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
    "default": {"inputs": {}, "ret_age": 60, "ss_timing": "early"},
    "delayed_ss": {"inputs": {}, "ret_age": 60, "ss_timing": "delayed"},
    "age_55_bridge": {"inputs": {"bridge_income_55": 40000, "bridge_years_55": 5,
                                  "kids_years_at_home_55": 8, "kids_annual_cost": 15000},
                       "ret_age": 55, "ss_timing": "early"},
    "large_spouse_age_gap": {"inputs": {"jason_age": 50, "justin_age": 35}, "ret_age": 60, "ss_timing": "early"},
    "state_tax": {"inputs": {"state_income_tax_rate": 0.05}, "ret_age": 60, "ss_timing": "early"},
    "life_events": {
        "inputs": {},
        "ret_age": 60, "ss_timing": "early",
        "life_events": [
            {"event_year": 2032, "one_time_cash_delta": -50000,
             "monthly_cash_flow_delta": 0, "duration_months": 0},
            {"event_year": 2040, "one_time_cash_delta": 0,
             "monthly_cash_flow_delta": 500, "duration_months": 120},
        ],
    },
}


def run_all():
    results = {}
    for name, cfg in SCENARIOS.items():
        inputs = {**BASE_INPUTS, **cfg.get("inputs", {})}
        kwargs = {}
        if cfg.get("life_events") is not None:
            kwargs["life_events"] = cfg["life_events"]
        mc = run_monte_carlo(inputs, BASE_ACCOUNTS, ret_age=cfg["ret_age"], ss_timing=cfg["ss_timing"], **kwargs)
        st = run_stress_tests(inputs, BASE_ACCOUNTS, ret_age=cfg["ret_age"], ss_timing=cfg["ss_timing"], **kwargs)
        results[f"{name}__monte_carlo"] = mc
        results[f"{name}__stress_tests"] = st
    return results


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/simulation_golden.json"
    data = run_all()
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"wrote {out_path}")
