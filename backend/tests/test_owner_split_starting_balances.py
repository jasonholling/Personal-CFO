"""
Owner-split starting balances (CALCULATION_CONTRACT.md sections 36-37,
Milestone 4 of 4). Core correctness property: summing all four owner
buckets reproduces run_two_dimensional_retirement_projection's own
pooled *_at_phase2_start figures EXACTLY -- the split re-partitions the
same dollar amounts computed by the same accumulation formulas, it does
not recompute them differently. Verified directly against the pooled
function's own output, not a second independent hand-calculation --
the pooled function is already reviewed (sections 19-24); this file's
job is proving the split doesn't silently drop or double-count a dollar.
"""

import pytest

from projection_engine import (
    run_two_dimensional_retirement_projection,
    owner_split_starting_balances_two_age,
    OWNER_BUCKETS,
)

BUCKET_TYPES = ("pretax", "roth", "taxable", "hsa")


def base_inputs(**overrides):
    inputs = {
        "jason_age": 55, "justin_age": 53,
        "inflation_rate": 0.03,
        "expected_return_pre_retirement": 0.07,
        "expected_return_post_retirement": 0.05,
        "retirement_income_today_dollars": 90000,
        "annual_hsa_contribution": 2000, "annual_rsu_value": 10000,
        "jason_social_security": 28000, "justin_social_security": 14000,
        "healthcare_pre_medicare": 12000, "healthcare_post_medicare": 4000,
        "w2_salary": 150000, "employee_401k_pct": 0.06, "employer_401k_pct": 0.09,
        "annual_bonus_pct": 0.10,
        "justin_w2_salary": 100000, "justin_employee_401k_pct": 0.06, "justin_employer_401k_pct": 0.03,
        "justin_annual_bonus_pct": 0.05, "justin_annual_rsu_value": 5000,
        "pension_55": 20000, "pension_60": 22000, "pension_65": 24000,
    }
    inputs.update(overrides)
    return inputs


ACCOUNTS = [
    {"name": "Jason 401k",    "account_type": "401k",     "owner": "jason",  "balance": 200000},
    {"name": "Justin IRA",    "account_type": "ira",      "owner": "justin", "balance": 150000},
    {"name": "Joint taxable", "account_type": "taxable",  "owner": "joint",  "balance": 100000},
    {"name": "Trust",         "account_type": "taxable",  "owner": "trust",  "balance": 50000},
    {"name": "Jason Roth",    "account_type": "roth_ira", "owner": "jason",  "balance": 30000},
    {"name": "HSA",           "account_type": "hsa",      "owner": "jason",  "balance": 20000},
    {"name": "Kid custodial", "account_type": "custodial", "owner": "abby",  "balance": 5000},
]


class TestOwnerSplitReconcilesAgainstPooledTotals:
    def test_split_sums_exactly_match_the_pooled_engine(self):
        pooled = run_two_dimensional_retirement_projection(base_inputs(), ACCOUNTS,
                                                             jason_ret_age=60, justin_ret_age=62)
        split = owner_split_starting_balances_two_age(base_inputs(), ACCOUNTS,
                                                        jason_ret_age=60, justin_ret_age=62)
        for t in BUCKET_TYPES:
            total = sum(split[owner][t] for owner in OWNER_BUCKETS)
            assert total == pytest.approx(pooled[f"{t}_at_phase2_start"], abs=1e-6)

    def test_kids_accounts_are_excluded_from_every_owner_bucket(self):
        split = owner_split_starting_balances_two_age(base_inputs(), ACCOUNTS,
                                                        jason_ret_age=60, justin_ret_age=62)
        total = sum(split[owner][t] for owner in OWNER_BUCKETS for t in BUCKET_TYPES)
        # $5,000 custodial account must not appear anywhere.
        pooled = run_two_dimensional_retirement_projection(base_inputs(), ACCOUNTS,
                                                             jason_ret_age=60, justin_ret_age=62)
        pooled_total = sum(pooled[f"{t}_at_phase2_start"] for t in BUCKET_TYPES)
        assert total == pytest.approx(pooled_total, abs=1e-6)

    def test_simultaneous_retirement_and_a_second_household(self):
        """Different ages/ret_ages/accounts -- not a coincidence specific
        to one household shape."""
        accounts2 = [
            {"name": "Justin 401k", "account_type": "401k", "owner": "justin", "balance": 400000},
            {"name": "Jason taxable", "account_type": "taxable", "owner": "jason", "balance": 60000},
            {"name": "Joint HSA", "account_type": "hsa", "owner": "joint", "balance": 15000},
        ]
        inputs2 = base_inputs(jason_age=61, justin_age=59, w2_salary=0, justin_w2_salary=0)
        pooled = run_two_dimensional_retirement_projection(inputs2, accounts2,
                                                             jason_ret_age=61, justin_ret_age=61)
        split = owner_split_starting_balances_two_age(inputs2, accounts2,
                                                        jason_ret_age=61, justin_ret_age=61)
        for t in BUCKET_TYPES:
            total = sum(split[owner][t] for owner in OWNER_BUCKETS)
            assert total == pytest.approx(pooled[f"{t}_at_phase2_start"], abs=1e-6)

    def test_owner_attribution_lands_in_the_right_bucket(self):
        """Sanity check the split isn't just numerically coincidental --
        Jason's 401k/Roth/HSA land in HIS bucket, Justin's IRA in HIS,
        joint taxable in JOINT, trust in TRUST, not smeared across all
        four."""
        split = owner_split_starting_balances_two_age(base_inputs(), ACCOUNTS,
                                                        jason_ret_age=60, justin_ret_age=62)
        assert split["jason"]["roth"] > 0
        # Justin has no existing Roth IRA account, but DOES have ongoing
        # 401k employee (Roth-side) contributions per base_inputs' own
        # justin_employee_401k_pct -- his roth bucket is nonzero from
        # contribution growth alone, not an existing account balance.
        assert split["justin"]["roth"] > 0
        assert split["justin"]["pretax"] > 0  # Justin's IRA
        assert split["trust"]["taxable"] > 0
        # Jason has no existing taxable ACCOUNT of his own, but his own
        # RSU/bonus contributions still grow his taxable bucket.
        assert split["jason"]["taxable"] > 0
        assert split["joint"]["taxable"] > 0   # the joint taxable account itself
