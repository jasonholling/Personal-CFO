"""Phase 4 verification tool for run_survivor_scenario, paired with
capture_retirement_golden.py's approach: captures full output across
synthetic scenarios so pre/post-migration behavior can be diffed exactly.

Usage:
    venv/bin/python3 tools/capture_survivor_golden.py before.json
    <migrate the code>
    venv/bin/python3 tools/capture_survivor_golden.py after.json
    venv/bin/python3 tools/diff_retirement_golden.py before.json after.json
"""
import json
import sys

from simulation_engine import run_survivor_scenario

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
    "jason_life_basic": 100000,
    "jason_life_supplemental": 250000,
    "jason_life_term": 500000,
    "justin_life_ul": 50000,
    "justin_life_whole": 25000,
    "person2_life_employer": 100000,
    "justin_life_term": 200000,
    "justin_life_kids": 0,
}

BASE_ACCOUNTS = [
    {"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 500000},
    {"id": 2, "name": "Roth IRA", "account_type": "roth_ira", "owner": "jason", "balance": 100000},
    {"id": 3, "name": "Trad IRA", "account_type": "ira", "owner": "justin", "balance": 50000},
    {"id": 4, "name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 200000},
    {"id": 5, "name": "HSA", "account_type": "hsa", "owner": "jason", "balance": 20000},
]

SCENARIOS = {
    "default_jason_dies": {"ret_age": 60, "deceased": "jason", "death_age": None},
    "justin_dies_early": {"ret_age": 60, "deceased": "justin", "death_age": 65},
    "jason_dies_late_low_insurance": {
        "ret_age": 60, "deceased": "jason", "death_age": 85,
        "inputs": {"jason_life_basic": 0, "jason_life_supplemental": 0, "jason_life_term": 0},
    },
    "small_starting_balance_depletes": {
        "ret_age": 60, "deceased": "jason", "death_age": 61,
        "accounts": [
            {"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 50000},
        ],
        "inputs": {"jason_life_basic": 0, "jason_life_supplemental": 0, "jason_life_term": 0,
                    "jason_social_security": 5000, "justin_social_security": 5000,
                    "pension_60": 5000},
    },
    "large_spouse_age_gap": {"ret_age": 60, "deceased": "jason", "death_age": 70,
                              "inputs": {"jason_age": 50, "justin_age": 35}},
    "life_events": {
        "ret_age": 60, "deceased": "jason", "death_age": None,
        "life_events": [
            {"event_year": 2032, "one_time_cash_delta": -50000,
             "monthly_cash_flow_delta": 0, "duration_months": 0},
        ],
    },
    "already_depleted_at_death": {
        "ret_age": 60, "deceased": "jason", "death_age": 90,
        "inputs": {"jason_life_basic": 0, "jason_life_supplemental": 0, "jason_life_term": 0,
                    "retirement_income_today_dollars": 500000},
    },
    "survivor_need_factor_1": {"ret_age": 60, "deceased": "jason", "death_age": None,
                                "survivor_need_factor": 1.0},
}


def run_all():
    results = {}
    for name, cfg in SCENARIOS.items():
        inputs = {**BASE_INPUTS, **cfg.get("inputs", {})}
        accounts = cfg.get("accounts", BASE_ACCOUNTS)
        kwargs = {}
        if cfg.get("life_events") is not None:
            kwargs["life_events"] = cfg["life_events"]
        if "survivor_need_factor" in cfg:
            kwargs["survivor_need_factor"] = cfg["survivor_need_factor"]
        out = run_survivor_scenario(
            inputs, accounts,
            ret_age=cfg["ret_age"], deceased=cfg["deceased"], death_age=cfg["death_age"],
            **kwargs,
        )
        results[name] = out
    return results


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/survivor_golden.json"
    data = run_all()
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"wrote {out_path}")
