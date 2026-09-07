"""Phase 4 verification tool for run_roth_conversion_analysis, paired with
capture_survivor_golden.py's approach. Not expected to be byte-identical
before/after this particular migration — it also fixes a real tax-modeling
gap (see simulation_engine.py's inline account) per an explicit product
decision, so this tool is for eyeballing the SIZE of the change, not
proving zero diff.

Usage:
    venv/bin/python3 tools/capture_roth_golden.py before.json
    <migrate the code>
    venv/bin/python3 tools/capture_roth_golden.py after.json
"""
import json
import sys

from simulation_engine import run_roth_conversion_analysis

BASE_INPUTS = {
    "jason_age": 55, "justin_age": 53,
    "retirement_income_today_dollars": 100000,
    "inflation_rate": 0.02,
    "expected_return_pre_retirement": 0.07,
    "expected_return_post_retirement": 0.06,
    "jason_social_security": 30000, "jason_ss_delayed": 45000,
    "justin_social_security": 15000, "jason_ss_age": 62, "justin_ss_age": 67,
    "healthcare_pre_medicare": 20000, "healthcare_post_medicare": 5000,
    "healthcare_kids": 0, "kids_annual_cost": 0,
    "bridge_income_55": 0, "bridge_years_55": 0, "kids_years_at_home_55": 0,
    "pension_55": 15000, "pension_60": 20000, "pension_65": 25000,
    "annual_hsa_contribution": 0, "annual_rsu_value": 0,
    "pretax_401k_pct": 0.75, "employee_401k_pct": 0.06, "employer_401k_pct": 0.03,
    "w2_salary": 150000,
    "asset1_sale_age": 0, "asset1_sale_net": 0, "asset1_appreciation": 0.03,
    "asset2_sale_age": 0, "asset2_sale_net": 0,
    "retirement_end_age": 95, "state_income_tax_rate": 0,
}

BASE_ACCOUNTS = [
    {"id": 1, "name": "401k", "account_type": "401k", "owner": "jason", "balance": 1200000},
    {"id": 2, "name": "Roth IRA", "account_type": "roth_ira", "owner": "jason", "balance": 100000},
    {"id": 4, "name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 300000},
]

SCENARIOS = {
    "default": {"inputs": {}, "ret_age": 60, "ss_timing": "early"},
    "ample_taxable": {"inputs": {}, "ret_age": 60, "ss_timing": "early",
                       "accounts": [{**BASE_ACCOUNTS[2], "balance": 1_000_000}]},
    "thin_taxable_depletes_early": {"inputs": {"retirement_income_today_dollars": 130000,
                                                "pension_55": 0, "pension_60": 0, "pension_65": 0},
                                     "ret_age": 60, "ss_timing": "early",
                                     "accounts": [{**BASE_ACCOUNTS[2], "balance": 50000}]},
    "delayed_ss": {"inputs": {}, "ret_age": 60, "ss_timing": "delayed"},
    "state_tax": {"inputs": {"state_income_tax_rate": 0.05}, "ret_age": 60, "ss_timing": "early"},
    "life_events": {
        "inputs": {}, "ret_age": 60, "ss_timing": "early",
        "life_events": [
            {"event_year": 2032, "one_time_cash_delta": -50000,
             "monthly_cash_flow_delta": 0, "duration_months": 0},
            {"event_year": 2033, "one_time_cash_delta": 300000,
             "monthly_cash_flow_delta": 0, "duration_months": 0},
        ],
    },
}


def run_all():
    results = {}
    for name, cfg in SCENARIOS.items():
        inputs = {**BASE_INPUTS, **cfg.get("inputs", {})}
        accounts = cfg.get("accounts", BASE_ACCOUNTS)
        kwargs = {}
        if cfg.get("life_events") is not None:
            kwargs["life_events"] = cfg["life_events"]
        out = run_roth_conversion_analysis(inputs, accounts, ret_age=cfg["ret_age"],
                                            ss_timing=cfg["ss_timing"], **kwargs)
        results[name] = out
    return results


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/roth_golden.json"
    data = run_all()
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"wrote {out_path}")
